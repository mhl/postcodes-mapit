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

parser = argparse.ArgumentParser(
    description="Find postcodes that are split across more than one EUR region"
)
parser.add_argument("-r", "--regions-shapefile", metavar="REGIONS-SHAPEFILE")
parser.add_argument("nsul_csv_filenames", metavar="NSUL-CSV-FILE", nargs="+")

args = parser.parse_args()

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

postcode_to_regions = defaultdict(set)

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
            postcode_to_regions[pc].add(region_code)

cross_region_postcodes = [
    (postcode, region_codes)
    for postcode, region_codes in postcode_to_regions.items()
    if len(region_codes) > 1
]

cross_region_postcodes.sort()

for postcode, region_codes in cross_region_postcodes:
    print(f"{postcode} => {sorted(region_codes)}")

# Now output just the outcodes that contain cross-region postcodes
# so I can use that to selectively regenerate just the data that might
# be affected by a cross-region postcode bug
cross_region_outcodes = sorted(set(pc.split()[0] for pc, _ in cross_region_postcodes))
print("Cross region outcodes are:")
print(cross_region_outcodes)
