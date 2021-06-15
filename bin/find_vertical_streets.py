#!/usr/bin/env python3

import argparse
from collections import defaultdict
import csv
import errno
import json
import math
from multiprocessing import Pool, cpu_count
import os
from os.path import basename, join, splitext
import re

from django.contrib.gis.geos import Point, Polygon
from django.contrib.gis.gdal import DataSource
from lxml import etree
import numpy as np
from scipy.spatial import Voronoi, voronoi_plot_2d
from tqdm import tqdm

from mapit.management.command_utils import fix_invalid_geos_geometry

COLUMN_POSTCODE = "pcds"
COLUMN_E = "gridgb1e"
COLUMN_N = "gridgb1n"
COLUMN_UPRN = "uprn"

region_code_to_name = {
    "EE": 'Eastern Euro Region',
    "EM": 'East Midlands Euro Region',
    "LN": 'London Euro Region',
    "NE": 'North East Euro Region',
    "NW": 'North West Euro Region',
    "SC": 'Scotland Euro Region',
    "SE": 'South East Euro Region',
    "SW": 'South West Euro Region',
    "WA": 'Wales Euro Region',
    "WM": 'West Midlands Euro Region',
    "YH": 'Yorkshire and the Humber Euro Region',
}


if __name__ == '__main__':

    parser = argparse.ArgumentParser(
        description="Find coordinates where there are multiple postcodes")
    parser.add_argument('-s', '--startswith', metavar='PREFIX',
                        help='Only process postcodes that start with PREFIX')
    parser.add_argument('nsul_csv_filenames', metavar='NSUL-CSV-FILE', nargs='+')

    args = parser.parse_args()
    required_pc_prefix = args.startswith

    # A modified version of one of the regular expressions suggested here:
    #    http://en.wikipedia.org/wiki/Postcodes_in_the_United_Kingdom

    postcode_matcher = re.compile(
        r'^([A-PR-UWYZ]([0-9][0-9A-HJKPS-UW]?|[A-HK-Y][0-9][0-9ABEHMNPRV-Y]?)) *([0-9][ABD-HJLNP-UW-Z]{2})$'
    )

    position_to_uprns_and_postcodes = defaultdict(set)

    total_postcodes = 0

    for csv_filename in args.nsul_csv_filenames:
        print("Processing", csv_filename)
        m = re.search(r'NSUL_\w+_\d+_(EE|EM|LN|NE|NW|SC|SE|SW|WA|WM|YH).csv', basename(csv_filename))
        if not m:
            raise Exception(f"Unexpected format of CSV filename: {basename(csv_filename)} - is this really from NSUL?")
        region_name = region_code_to_name[m.group(1)]

        print("Region name is:", region_name)

        with open(csv_filename) as fp:
            reader = csv.DictReader(fp)
            for i, row in enumerate(reader):
                if i > 0 and (i % 10000 == 0):
                    print("{0} postcodes processed".format(i))
                pc = row[COLUMN_POSTCODE]
                if required_pc_prefix and not pc.startswith(required_pc_prefix):
                    continue
                # Exclude Girobank postcodes:
                if pc.startswith('GIR'):
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
                # Remove commas from the eastings and northings
                row[COLUMN_E] = re.sub(r',', '', row[COLUMN_E])
                row[COLUMN_N] = re.sub(r',', '', row[COLUMN_N])
                lon = int(re.sub(r',', '', row[COLUMN_E]))
                lat = int(re.sub(r',', '', row[COLUMN_N]))
                position_tuple = (lon, lat)
                position_to_uprns_and_postcodes[position_tuple].add((pc, row[COLUMN_UPRN]))

    for position, postcodes_and_uprns in position_to_uprns_and_postcodes.items():
        if len(postcodes_and_uprns) <= 1:
            continue
        outcodes = set()
        for postcode, uprn in postcodes_and_uprns:
            outcode = postcode.split()[0]
            outcodes.add(outcode)
        if len(outcodes) > 1:
            print("Multiple outcodes in a vertical street!")
            print(position, postcodes_and_uprns)
