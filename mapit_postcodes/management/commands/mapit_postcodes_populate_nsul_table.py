from collections import defaultdict
import csv
import math
from os.path import basename
import re

from django.contrib.gis.geos import Point, Polygon
from django.contrib.gis.gdal import DataSource
from django.core.management.base import BaseCommand
from lxml import etree
import numpy as np
from scipy.spatial import Voronoi
from tqdm import tqdm

from mapit_postcodes.models import VoronoiRegion, NSULRow

COLUMN_POSTCODE = "pcds"
COLUMN_E = "gridgb1e"
COLUMN_N = "gridgb1n"
COLUMN_UPRN = "uprn"

BATCH_SIZE = 1000

# This doesn't need to be in any sense precise - it's used for the centre
# of our ring of "points at infinity". Taken from:
# https://www.ordnancesurvey.co.uk/blog/2014/08/where-is-the-centre-of-great-britain-2/
CENTRE_OF_GB_E = 364188
CENTRE_OF_GB_N = 456541

UK_MAX_NORTHINGS = 1219109
UK_MIN_NORTHINGS = 3706

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

terminated_postcodes = set()

def output_postcode_points_kml(filename, postcodes_and_points):
    kml = etree.Element("kml", nsmap={None: "http://earth.google.com/kml/2.1"})
    document = etree.SubElement(kml, "Document")
    for postcode, wgs84_point in postcodes_and_points:
        placemark = etree.SubElement(document, "Placemark")
        name = etree.SubElement(placemark, "name")
        name.text = postcode
        point = etree.SubElement(placemark, "Point")
        coordinates = etree.SubElement(point, "coordinates")
        coordinates.text = "{0.x},{0.y}".format(wgs84_point)
    with open(filename, "wb") as f:
        f.write(
            etree.tostring(
                kml, pretty_print=True, encoding="utf-8", xml_declaration=True
            )
        )


class Command(BaseCommand):
    help = "Generate Voronoi polygons from NSUL postcode coordinates"

    def add_arguments(self, parser):
        parser.add_argument(
            "-s",
            "--startswith",
            metavar="PREFIX",
            help="Only process postcodes that start with PREFIX",
        )
        parser.add_argument(
            "-p",
            "--postcode-points",
            action="store_true",
            help="Also output a KML file with a Placemark per postcode",
        )
        parser.add_argument("nsul_csv_filenames", metavar="NSUL-CSV-FILE", nargs="+")
        parser.add_argument("-r", "--regions-shapefile", metavar="REGIONS-SHAPEFILE")
        parser.add_argument(
            "-f",
            "--force-delete",
            action="store_true",
            help="Delete all NSULRow and VoronoiRegion objects before repopulating",
        )
        parser.add_argument("-n", "--onspd", metavar="ONSPD-CSV")

    def handle(self, **options):
        # FIXME: it might be best to drop the index on the postcode column before
        # and adding it again at the end in a `finally:` block
        if options["force_delete"]:
            NSULRow.objects.all().delete()
            VoronoiRegion.objects.all().delete()
        else:
            existing_nsul_row = NSULRow.objects.count()
            existing_voronoi_region = VoronoiRegion.objects.count()
            if existing_nsul_row:
                print(
                    f"There are {existing_nsul_row} rows already in the mapit_postcodes_nsulrow table"
                )
            if existing_voronoi_region:
                print(
                    f"There are {existing_voronoi_region} rows already in the mapit_postcodes_voronoiregion table"
                )
            if existing_nsul_row or existing_voronoi_region:
                print(
                    "You must delete these rows yourself, or re-run with -f to get the script to do it."
                )
                return

        required_pc_prefix = options["startswith"]

        if options["onspd"]:
            with open(options["onspd"]) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row["doterm"]:
                        terminated_postcodes.add(row["pcds"])

        # ------------------------------------------------------------------------

        # A modified version of one of the regular expressions suggested here:
        #    http://en.wikipedia.org/wiki/Postcodes_in_the_United_Kingdom

        postcode_matcher = re.compile(
            r"^([A-PR-UWYZ]([0-9][0-9A-HJKPS-UW]?|[A-HK-Y][0-9][0-9ABEHMNPRV-Y]?)) *([0-9][ABD-HJLNP-UW-Z]{2})$"
        )

        positions_list = []

        position_to_row_ids = defaultdict(set)
        position_to_row_objects = defaultdict(list)

        def bulk_create_batch_of_new_row_objects():
            nonlocal position_to_row_ids, position_to_row_objects
            nr_to_create = []
            for position_tuple, row_objects in position_to_row_objects.items():
                nr_to_create += row_objects
            NSULRow.objects.bulk_create(nr_to_create)
            # bulk_create modifies the passed in objects to set the primary key on
            # them - we only need the IDs at this stage, so copy them to the more
            # compact data structure.
            for position_tuple, row_objects in position_to_row_objects.items():
                position_to_row_ids[position_tuple].update(
                    [row_object.id for row_object in row_objects]
                )
            # Now we can clear position_to_row_objects
            position_to_row_objects = defaultdict(list)

        wgs84_postcode_and_points = []

        gb_region_geoms = {}

        for csv_filename in options["nsul_csv_filenames"]:
            print("Processing", csv_filename)
            m = re.search(
                r"NSUL_\w+_\d+_(EE|EM|LN|NE|NW|SC|SE|SW|WA|WM|YH).csv",
                basename(csv_filename),
            )
            if not m:
                raise Exception(
                    f"Unexpected format of CSV filename: {basename(csv_filename)} - is this really from NSUL?"
                )
            region_code = m.group(1)
            region_name = region_code_to_name[region_code]

            print("Region name is:", region_name)

            # ------------------------------------------------------------------------
            # Load the corresponding boundary of that region of Great Britain, so we
            # can clip the postcode regions that cross that boundary.

            regions_ds = DataSource(options["regions_shapefile"])
            if len(regions_ds) != 1:
                raise Exception("Expected the regions shapefile to only have one layer")
            regions_layer = next(iter(regions_ds))

            gb_region_geom = None
            for feature in regions_layer:
                if feature.get("NAME") == region_name:
                    gb_region_geom = feature.geom.geos
            if not gb_region_geom:
                raise Exception(
                    f"Failed to find the geometry of ‘{region_name}’ in {options['regions_shapefile']}"
                )

            gb_region_geoms[region_code] = gb_region_geom

            with open(csv_filename) as fp:
                reader = csv.DictReader(fp)
                for i, row in enumerate(reader):
                    if i > 0 and (i % 100000 == 0):
                        print("{0} postcodes processed".format(i))
                    if i > 0 and (i % BATCH_SIZE == 0):
                        bulk_create_batch_of_new_row_objects()
                    pc = row[COLUMN_POSTCODE]
                    if required_pc_prefix and not pc.startswith(required_pc_prefix):
                        continue
                    # Exclude Girobank postcodes:
                    if pc.startswith("GIR"):
                        continue
                    # Exclude rows where the postcode is missing:
                    if not pc:
                        continue
                    m = postcode_matcher.search(pc)
                    if not m:
                        raise Exception(
                            "Couldn't parse postcode:" + pc + "from row" + str(row)
                        )
                    # Normalize the postcode's format to put a space in the
                    # right place:
                    pc = m.group(1) + " " + m.group(3)
                    # Exclude any terminated postcodes:
                    if pc in terminated_postcodes:
                        continue
                    # Remove commas from the eastings and northings
                    row[COLUMN_E] = re.sub(r",", "", row[COLUMN_E])
                    row[COLUMN_N] = re.sub(r",", "", row[COLUMN_N])
                    lon = int(re.sub(r",", "", row[COLUMN_E]))
                    lat = int(re.sub(r",", "", row[COLUMN_N]))
                    osgb_point = Point(lon, lat, srid=27700)

                    new_row = NSULRow(
                        point=osgb_point,
                        postcode=pc,
                        uprn=row[COLUMN_UPRN],
                        region_code=region_code,
                    )

                    if options["postcode_points"]:
                        wgs84_point = osgb_point.transform(4326, clone=True)
                        wgs84_postcode_and_points.append((pc, wgs84_point))
                    position_tuple = (lon, lat)
                    if (
                        position_tuple not in position_to_row_ids
                        and position_tuple not in position_to_row_objects
                    ):
                        positions_list.append((lon, lat))
                    position_to_row_objects[position_tuple].append(new_row)

            bulk_create_batch_of_new_row_objects()

        if options["postcode_points"]:
            output_postcode_points_kml("postcode-points.kml", wgs84_postcode_and_points)
