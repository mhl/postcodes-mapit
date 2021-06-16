import errno
import json
import os
from pathlib import Path
import re

from django.db import connection
from django.contrib.gis.db.models import Collect
from django.contrib.gis.geos import GEOSGeometry, Point
from django.contrib.gis.gdal import DataSource
from django.core.management.base import BaseCommand, CommandError

from mapit.management.command_utils import fix_invalid_geos_geometry
from mapit_postcodes.models import VoronoiRegion, NSULRow


region_code_to_name = {
    "EE": "Eastern Euro Region",
    "EM": "East Midlands Euro Region",
    "LN": "London Euro Region",
    "NE": "North East Euro Region",
    "NW": "North West Euro Region",
    "SC": "Scotland Euro Region",
    "SE": "South East Euro Region",
    "SW": "South West Euro Region",
    "WA": "Wales Euro Region",
    "WM": "West Midlands Euro Region",
    "YH": "Yorkshire and the Humber Euro Region",
}
region_name_to_code = {v: k for k, v in region_code_to_name.items()}


def mkdir_p(path):
    try:
        os.makedirs(path)
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            pass
        else:
            raise


def postcode_to_sector(postcode):
    return re.sub(r"(^\S+ \S).*", "\\1", postcode)


def fast_geojson_output(output_filename, postcodes_and_polygons):
    with open(output_filename, "w") as f:
        f.write('{"type": "FeatureCollection", "features": [')
        first_item = True
        for postcode, polygon in postcodes_and_polygons:
            if not first_item:
                f.write(",")
            f.write('{"type": "Feature", "geometry": ')
            f.write(polygon.json)
            f.write(f', "properties": {{"postcodes": {json.dumps(postcode)}}}')
            f.write('}')
            first_item = False
        f.write(']}')


class Command(BaseCommand):
    help = "Generate Voronoi polygons from NSUL postcode coordinates"

    def add_arguments(self, parser):
        parser.add_argument(
            "-s",
            "--startswith",
            metavar="PREFIX",
            help="Only process postcodes that start with PREFIX",
        )
        parser.add_argument("-r", "--regions-shapefile", metavar="REGIONS-SHAPEFILE")
        parser.add_argument("-o", "--output-directory", metavar="OUTPUT-DIRECTORY")
        parser.add_argument(
            "-i", "--inland-sectors-file", metavar="INLAND-SECTORS-JSON"
        )

    def polygon_requires_clipping(self, polygon, region_codes, postcode):
        if self.inland_sectors_by_region_code is not None:
            # Then in some cases we can skip the expensive later
            # check.
            postcode_sector = postcode_to_sector(postcode)
            for region_code in region_codes:
                if postcode_sector in self.inland_sectors_by_region_code[region_code]:
                    return False

        # Check whether any of the points in the polygon or
        # multipolygon are in the sea - if so, we need to clip the
        # polygon to the coastline
        geom_type = polygon.geom_type
        if geom_type == "MultiPolygon":
            polygons = polygon.coords
        elif geom_type == "Polygon":
            polygons = [polygon.coords]
        else:
            raise Exception("Unknown geom_type {0}".format(geom_type))
        region_geometry = self.get_regions_geometry(region_codes)
        for p in polygons:
            for t in p:
                for x, y in t:
                    point = Point(x, y)
                    if not region_geometry.contains(point):
                        return True
        return False

    def get_regions_geometry(self, region_codes):
        key = ",".join(sorted(region_codes))
        cached = self.region_code_to_geometry_cache.get(key)
        if cached:
            return cached
        # Otherwise union those regions' geometries and put them in the cache
        region_geometries = [self.region_code_to_geometry_cache[rc] for rc in region_codes]
        unioned = region_geometries[0]
        for region_geometry in region_geometries[1:]:
            unioned = unioned.union(region_geometry)
        self.region_code_to_geometry_cache[key] = unioned
        return unioned

    def clip_unioned(self, polygon, region_codes, postcode):
        if not self.polygon_requires_clipping(polygon, region_codes, postcode):
            return polygon
        gb_region_geom = self.get_regions_geometry(region_codes)
        if not polygon.intersects(gb_region_geom):
            return polygon
        return polygon.intersection(gb_region_geom)

    def process_outcode(self, outcode, options):
        output_directory = self.postcodes_output_directory / outcode
        mkdir_p(output_directory)
        prefix = None
        if options["startswith"]:
            prefix = options["startswith"].upper()
        # Deal with individual postcodes first, leaving vertical streets to later:
        print("Finding postcodes in outcode", outcode)
        qs = NSULRow.objects.values("postcode").filter(
            postcode__startswith=(outcode + " ")
        )
        if prefix:
            qs = qs.filter(postcode__startswith=prefix)
        qs = qs.order_by("postcode").distinct()
        postcodes = [row["postcode"] for row in qs]

        postcode_multipolygons = []
        for row in qs:
            postcode = row["postcode"]
            print("  ", postcode)
            region_codes = list(
                NSULRow.objects.filter(postcode=postcode)
                .values_list("region_code", flat=True)
                .distinct()
            )
            result = VoronoiRegion.objects.filter(nsulrow__postcode=postcode).aggregate(
                Collect("polygon")
            )
            unioned = result["polygon__collect"].unary_union

            clipped = self.clip_unioned(unioned, region_codes, postcode)
            wgs_84_clipped_polygon = clipped.transform(4326, clone=True)
            # If the polygon isn't valid after transformation, try to
            # fix it. (There has been at least one such case with the old dataset.
            if not wgs_84_clipped_polygon.valid:
                print(f"Warning: had to fix polygon for postcode {postcode}")
                wgs_84_clipped_polygon = fix_invalid_geos_geometry(
                    wgs_84_clipped_polygon
                )

            postcode_multipolygons.append((postcode, wgs_84_clipped_polygon))

        output_filename = outcode
        if prefix:
            output_filename += f"-just-{prefix}"
        output_filename += ".geojson"

        fast_geojson_output(output_directory / output_filename, postcode_multipolygons)

    def handle(self, **options):
        # Ensure the output directory exists
        if not options["output_directory"]:
            raise CommandError(
                "You must specify an output directory with -o or --output-directory"
            )
        self.postcodes_output_directory = Path(options["output_directory"])
        mkdir_p(self.postcodes_output_directory)

        # If a JSON file indicating which postcode sectors are inland has been
        # supplied, then load it:
        self.inland_sectors_by_region_code = None
        if options["inland_sectors_file"]:
            with open(options["inland_sectors_file"]) as f:
                self.inland_sectors_by_region_code = json.load(f)
            # Convert the postcode sector arrays into sets for quicker
            # lookup.
            for (
                region_code,
                postcode_sectors,
            ) in self.inland_sectors_by_region_code.items():
                self.inland_sectors_by_region_code[region_code] = set(postcode_sectors)
        else:
            print("WARNING: considering specifying --inland-sectors-file to speed this up a lot")

        # Set up a dictionary for caching the coastline geometries for
        # each region:
        self.region_code_to_geometry_cache = {}
        if not options["regions_shapefile"]:
            raise CommandError(
                "You must supply a regions shapefile with -r or --regions-shapefile"
            )
        regions_ds = DataSource(options["regions_shapefile"])
        if len(regions_ds) != 1:
            raise CommandError("Expected the regions shapefile to only have one layer")
        regions_layer = next(iter(regions_ds))

        # Load the coastline geometries for each region
        for feature in regions_layer:
            region_name = feature.get("NAME")
            region_code = region_name_to_code[region_name]
            if region_code in self.region_code_to_geometry_cache:
                raise CommandError(
                    f"There were multiple regions for {region_code} ({region_name}) in the regions shapefile"
                )
            self.region_code_to_geometry_cache[region_code] = feature.geom.geos

        # Handle one outcode at a time:
        print("Finding all the outcodes to process...")
        with connection.cursor() as cursor:
            cursor.execute(
                "select distinct regexp_replace(postcode, ' .*', '') from mapit_postcodes_nsulrow"
            )
            outcodes = [row[0] for row in cursor.fetchall()]
            for i, outcode in enumerate(outcodes):
                print(f"==== Processing {outcode} ({i+1}/{len(outcodes)}")
                self.process_outcode(outcode, options)
