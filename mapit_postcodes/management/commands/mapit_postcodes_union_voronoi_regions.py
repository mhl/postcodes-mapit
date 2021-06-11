import errno
import json
import os
from pathlib import Path
import re

from django.db import connection
from django.contrib.gis.db.models import Union
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

    def polygon_requires_clipping(self, polygon, region_code, postcode):
        if self.inland_sectors_by_region_code is not None:
            # Then in some cases we can skip the expensive later
            # check.
            postcode_sector = postcode_to_sector(postcode)
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
        region_geometry = self.region_code_to_geometry[region_code]
        for p in polygons:
            for t in p:
                for x, y in t:
                    point = Point(x, y)
                    if not region_geometry.contains(point):
                        return True
        return False

    def clip_unioned(self, polygon, region_code, postcode):
        if not self.polygon_requires_clipping(polygon, region_code, postcode):
            return polygon
        gb_region_geom = self.region_code_to_geometry[region_code]
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
        qs = NSULRow.objects.values("postcode").filter(
            postcode__startswith=(outcode + " ")
        )
        if prefix:
            qs = qs.filter(postcode__startswith=prefix)
        qs = qs.order_by("postcode").distinct()
        postcode_multipolygons = []
        for row in qs:
            postcode = row["postcode"]
            region_code_rows = (
                NSULRow.objects.filter(postcode=postcode)
                .values("region_code")
                .distinct()
            )
            if len(region_code_rows) > 1:
                raise CommandError(
                    f"The postcode {postcode} lay in multiple regions: {region_code_rows}"
                )
            region_code = region_code_rows[0]["region_code"]
            result = VoronoiRegion.objects.filter(nsulrow__postcode=postcode).aggregate(
                Union("polygon")
            )
            unioned = result["polygon__union"]

            clipped = self.clip_unioned(unioned, region_code, postcode)
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

        # Now find all the vertical streets. These are points where there is
        # more that one different postcode at a single point. (The classic
        # example of this is the DVLA building in Swansea.)
        postcode_multipolygons = []

        where_clause = f"where postcode like '{outcode} %'"
        if prefix:
            where_clause += f" and postcode like '{prefix}%'"
        with connection.cursor() as cursor:
            cursor.execute(
                "with t as " \
                + f"(select distinct point, postcode from mapit_postcodes_nsulrow {where_clause}) " \
                + "select ST_AsText(point), array_agg(postcode order by postcode) from t group by point having count(*) > 1"
            )
            for row in cursor.fetchall():
                point_wkt, postcodes = row
                point = GEOSGeometry(point_wkt, srid=27700)
                # Find the corresponding VoronoiRegion for that point:
                first_matching_row = NSULRow.objects.filter(point=point).first()
                voronoi_region = first_matching_row.voronoi_region
                postcode_multipolygons.append((", ".join(postcodes), voronoi_region.polygon.transform(4326, clone=True)))

        output_filename = outcode + "-vertical-streets"
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

        # Set up a dictionary for caching the coastline geometries for
        # each region:
        self.region_code_to_geometry = {}
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
            if region_code in self.region_code_to_geometry:
                raise CommandError(
                    f"There were multiple regions for {region_code} ({region_name}) in the regions shapefile"
                )
            self.region_code_to_geometry[region_code] = feature.geom.geos

        # Handle one outcode at a time:
        with connection.cursor() as cursor:
            cursor.execute(
                "select distinct regexp_replace(postcode, ' .*', '') from mapit_postcodes_nsulrow"
            )
            for row in cursor.fetchall():
                outcode = row[0]
                self.process_outcode(outcode, options)
