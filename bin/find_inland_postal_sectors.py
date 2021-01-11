#!/usr/bin/env python3

import argparse
from collections import defaultdict
import csv
import json
from os.path import basename
import re

from django.contrib.gis.gdal import DataSource
from django.contrib.gis.geos import GEOSGeometry
import requests

COLUMN_POSTCODE = "pcds"

MAPIT_BASE_URL = "https://postcodes.mapit.longair.net"

parser = argparse.ArgumentParser(
    description="Make a list of Scottish postcode sectors that we definitely don't need to clip"
)
parser.add_argument("-r", "--regions-shapefile", metavar="REGIONS-SHAPEFILE")
parser.add_argument("-a", "--mapit-areas-csv", metavar="MAPIT-AREA-CSV")
parser.add_argument("nsul_csv_filenames", metavar="NSUL-CSV-FILE", nargs="+")
parser.add_argument("-o", "--output_file", metavar="OUTPUT-JSON-FILE")

args = parser.parse_args()

with open(args.mapit_areas_csv) as f:
    sector_to_mapit_area_id = {row[1]: int(row[0]) for row in csv.reader(f)}

region_to_sectors_within_mainland = defaultdict(list)

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

# A modified version of one of the regular expressions suggested here:
#    http://en.wikipedia.org/wiki/Postcodes_in_the_United_Kingdom

postcode_matcher = re.compile(
    r"^([A-PR-UWYZ]([0-9][0-9A-HJKPS-UW]?|[A-HK-Y][0-9][0-9ABEHMNPRV-Y]?)) *(([0-9])[ABD-HJLNP-UW-Z]{2})$"
)

for csv_filename in args.nsul_csv_filenames:
    print("Processing", csv_filename)
    m = re.search(
        r"NSUL_\w+_\d+_(EE|EM|LN|NE|NW|SC|SE|SW|WA|WM|YH).csv", basename(csv_filename)
    )
    if not m:
        raise Exception(
            f"Unexpected format of CSV filename: {basename(csv_filename)} - is this really from NSUL?"
        )
    region_code = m.group(1)
    region_name = region_code_to_name[region_code]

    print("Region name is:", region_name)

    # ------------------------------------------------------------------------
    # Load the corresponding boundary of that region of Great Britain. There
    # are lots of features for Scotland, so we pick the feature with the largest
    # area.

    regions_ds = DataSource(args.regions_shapefile)
    regions_layer = next(iter(regions_ds))
    mainland_geom = max(
        (
            feature.geom.geos.transform("4326", clone=True)
            for feature in regions_layer
            if feature.get("NAME") == region_name
        ),
        key=lambda g: g.area,
    )

    postcode_sectors_seen = set()

    with open(csv_filename) as fp:
        reader = csv.DictReader(fp)
        for i, row in enumerate(reader):
            # if i > 0 and (i % 100000 == 0):
            #     print("{0} postcodes processed".format(i))
            pc = row[COLUMN_POSTCODE]
            # Exclude Girobank postcodes:
            if pc.startswith("GIR"):
                continue
            # Exclude rows where the postcode is missing:
            if not pc:
                continue
            m = postcode_matcher.search(pc)
            if not m:
                raise Exception("Couldn't parse postcode:" + pc + "from row" + str(row))
            # Normalize the postcode's format to put a space in the
            # right place:
            pc = m.group(1) + " " + m.group(3)

            sector = m.group(1) + " " + m.group(4)
            if sector in postcode_sectors_seen:
                continue
            postcode_sectors_seen.add(sector)

            if sector not in sector_to_mapit_area_id:
                print("Sector", sector, "not found in MapIt data")
                continue

            area_kml_url = (
                MAPIT_BASE_URL
                + "/area/"
                + str(sector_to_mapit_area_id[sector])
                + ".wkt"
            )
            r = requests.get(area_kml_url)
            area_geometry = GEOSGeometry(r.text)

            inside = mainland_geom.contains(area_geometry)
            print(
                f"[{region_code}]:",
                "INSIDE" if inside else "OUTSIDE",
                sector,
                f"https://postcodes.mapit.longair.net/area/{sector_to_mapit_area_id[sector]}.html",
            )
            if inside:
                region_to_sectors_within_mainland[region_code].append(sector)

with open(args.output_file, "w") as f:
    json.dump(region_to_sectors_within_mainland, f, indent=2)
