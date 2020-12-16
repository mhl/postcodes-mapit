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
from scipy.spatial import Voronoi
from tqdm import tqdm

from mapit.management.command_utils import fix_invalid_geos_geometry

COLUMN_POSTCODE = "pcds"
COLUMN_E = "gridgb1e"
COLUMN_N = "gridgb1n"
COLUMN_UPRN = "uprn"

REGION_BATCH_SIZE = 500_000

# This doesn't need to be in any sense precise - it's used for the centre
# of our ring of "points at infinity". Taken from:
# https://www.ordnancesurvey.co.uk/blog/2014/08/where-is-the-centre-of-great-britain-2/
CENTRE_OF_GB_E = 364188
CENTRE_OF_GB_N = 456541

UK_MAX_NORTHINGS = 1219109
UK_MIN_NORTHINGS = 3706

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


def mkdir_p(path):
    try:
        os.makedirs(path)
    except OSError as exc:
        if exc.errno == errno.EEXIST:
            pass
        else:
            raise


def output_postcode_points_kml(filename, postcodes_and_points):
    kml = etree.Element('kml', nsmap={None: 'http://earth.google.com/kml/2.1'})
    document = etree.SubElement(kml, 'Document')
    for postcode, wgs84_point in postcodes_and_points:
        placemark = etree.SubElement(document, 'Placemark')
        name = etree.SubElement(placemark, 'name')
        name.text = postcode
        point = etree.SubElement(placemark, 'Point')
        coordinates = etree.SubElement(point, 'coordinates')
        coordinates.text = '{0.x},{0.y}'.format(wgs84_point)
    with open(filename, 'wb') as f:
        f.write(etree.tostring(
            kml, pretty_print=True, encoding='utf-8', xml_declaration=True))


def output_boundary_kml(filename, edge, postcodes, polygon):
    kml = etree.Element('kml', nsmap={None: 'http://earth.google.com/kml/2.1'})
    folder = etree.SubElement(kml, 'Folder')
    name = etree.SubElement(folder, 'name')
    if postcodes:
        name.text = ', '.join(postcodes)
    else:
        name.text = splitext(basename(filename))[0]
    placemark = etree.SubElement(folder, 'Placemark')
    extended_data = etree.SubElement(placemark, 'ExtendedData')
    data = etree.SubElement(
        extended_data,
        'Data',
        attrib={'name': 'edge'})
    value = etree.SubElement(data, 'value')
    value.text = str(edge)
    for i, postcode in enumerate(postcodes):
        data = etree.SubElement(
            extended_data,
            'Data',
            attrib={'name': 'postcode{0:04d}'.format(i)})
        value = etree.SubElement(data, 'value')
        value.text = postcode
    placemark.append(etree.fromstring(polygon.kml))
    with open(filename, 'wb') as f:
        f.write(etree.tostring(
            kml, pretty_print=True, encoding='utf-8', xml_declaration=True))


def polygon_requires_clipping(polygon, region_geometry):
    geom_type = polygon.geom_type
    if geom_type == 'MultiPolygon':
        polygons = polygon.coords
    elif geom_type == 'Polygon':
        polygons = [polygon.coords]
    else:
        raise Exception("Unknown geom_type {0}".format(geom_type))
    for p in polygons:
        for t in p:
            for x, y in t:
                point = Point(x, y)
                if not region_geometry.contains(point):
                    return True
    return False


if __name__ == '__main__':

    parser = argparse.ArgumentParser(
        description="Generate KML files of the Voronoi diagram of NSUL postcode coordinates")
    parser.add_argument('-s', '--startswith', metavar='PREFIX',
                        help='Only process postcodes that start with PREFIX')
    parser.add_argument('-p', '--postcode-points', action='store_true',
                        help='Also output a KML file with a Placemark per postcode')
    parser.add_argument('nsul_csv_filenames', metavar='NSUL-CSV-FILE', nargs='+')
    parser.add_argument('-r', '--regions-shapefile', metavar='REGIONS-SHAPEFILE')
    parser.add_argument('-o', '--output_directory', metavar='OUTPUT-DIRECTORY')

    args = parser.parse_args()
    required_pc_prefix = args.startswith

    # ------------------------------------------------------------------------

    # Make sure the output directory exists:

    postcodes_output_directory = join(args.output_directory, 'postcodes')
    mkdir_p(postcodes_output_directory)

    # A modified version of one of the regular expressions suggested here:
    #    http://en.wikipedia.org/wiki/Postcodes_in_the_United_Kingdom

    postcode_matcher = re.compile(
        r'^([A-PR-UWYZ]([0-9][0-9A-HJKPS-UW]?|[A-HK-Y][0-9][0-9ABEHMNPRV-Y]?)) *([0-9][ABD-HJLNP-UW-Z]{2})$'
    )

    total_postcodes = 0

    for csv_filename in args.nsul_csv_filenames:
        print("Processing", csv_filename)
        m = re.search(r'NSUL_\w+_\d+_(EE|EM|LN|NE|NW|SC|SE|SW|WA|WM|YH).csv', basename(csv_filename))
        if not m:
            raise Exception(f"Unexpected format of CSV filename: {basename(csv_filename)} - is this really from NSUL?")
        region_name = region_code_to_name[m.group(1)]

        print("Region name is:", region_name)

        # ------------------------------------------------------------------------
        # Load the corresponding boundary of that region of Great Britain, so we
        # can clip the postcode regions that cross that boundary.

        regions_ds = DataSource(args.regions_shapefile)
        if len(regions_ds) != 1:
            raise Exception("Expected the regions shapefile to only have one layer")
        regions_layer = next(iter(regions_ds))

        gb_region_geom = None
        for feature in regions_layer:
            if feature.get('NAME') == region_name:
                gb_region_geom = feature.geom.geos
        if not gb_region_geom:
            raise Exception(f"Failed to find the geometry of ‘{region_name}’ in {args.regions_shapefile}")

        # Clear the previous Voronoi diagram calculation results to help the garbage collector
        vor = None

        # Now load the centroids of (almost) all the postcodes in that region:

        positions_list = []
        postcodes_list = []
        uprn_list = []

        position_to_postcodes = defaultdict(set)
        positions_seen = set()
        wgs84_postcode_and_points = []

        with open(csv_filename) as fp:
            reader = csv.DictReader(fp)
            for i, row in enumerate(reader):
                if i > 0 and (i % 100000 == 0):
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
                if args.postcode_points:
                    osgb_point = Point(lon, lat, srid=27700)
                    wgs84_point = osgb_point.transform(4326, clone=True)
                    wgs84_postcode_and_points.append((pc, wgs84_point))
                position_tuple = (lon, lat)
                postcodes_there = position_to_postcodes[position_tuple]
                postcodes_there.add(pc)
                if position_tuple not in positions_seen:
                    positions_list.append((lon, lat))
                    postcodes_list.append(postcodes_there)
                    uprn_list.append(row[COLUMN_UPRN])
                positions_seen.add(position_tuple)

        # Now add some "points at infinity" - 200 points in a circle way
        # outside the border of the United Kingdom:

        points_at_infinity = 200

        distance_to_infinity = (UK_MAX_NORTHINGS - UK_MIN_NORTHINGS) * 1.5

        first_infinity_point_index = len(positions_list)

        for i in range(0, points_at_infinity):
            angle = (2 * math.pi * i) / float(points_at_infinity)
            new_x = CENTRE_OF_GB_E + math.cos(angle) * distance_to_infinity
            new_y = CENTRE_OF_GB_N + math.sin(angle) * distance_to_infinity
            positions_list.append((new_x, new_y))
            postcodes_list.append(None)
            if args.postcode_points:
                # Also add these points to those we might output as KML of each
                # postcode centroid to help with debugging:
                osgb_point = Point(new_x, new_y, srid=27700)
                wgs84_point = osgb_point.transform(4326, clone=True)
                wgs84_postcode_and_points.append(('infinity{0:06d}'.format(i), wgs84_point))

        if args.postcode_points:
            output_postcode_points_kml(
                join(postcodes_output_directory, 'postcode-points.kml'),
                wgs84_postcode_and_points,
            )

        points = np.array(positions_list)
        print("Calculating the Voronoi diagram...")
        vor = Voronoi(points)
        print("Finished!")

        # Now generate the KML output:

        print("Now generating KML output")

        def output_kml(point_index):
            position_tuple = positions_list[point_index]
            if not postcodes_list[point_index]:
                return
            postcodes = sorted(postcodes_list[point_index])
            voronoi_region_index = vor.point_region[point_index]
            voronoi_region = vor.regions[voronoi_region_index]
            if any(vi < 0 for vi in voronoi_region):
                # Then this region extends to infinity, so is outside our "points at infinity"
                return
            centre_x, centre_y = positions_list[point_index]

            if len(voronoi_region) < 3:
                # Skip any point with fewer than 3 triangle_indices
                return

            if position_tuple in position_to_postcodes:
                file_basename = postcodes[0]
                outcode = file_basename.split()[0]
            else:
                postcodes = []
                file_basename = 'point-{0:09d}'.format(point_index)
                outcode = 'points-at-infinity'

            mkdir_p(join(postcodes_output_directory, outcode))

            leafname = f"{file_basename}_{uprn_list[point_index]}.kml"

            if len(postcodes) > 1:
                json_leafname = file_basename + ".json"
                with open(join(postcodes_output_directory, outcode, json_leafname), "w") as fp:
                    json.dump(postcodes, fp)

            kml_filename = join(postcodes_output_directory, outcode, leafname)

            if not os.path.exists(kml_filename):

                border = [vor.vertices[i] for i in voronoi_region]
                border.append(border[0])

                # The coordinates are NumPy arrays, so convert them to tuples:
                border = [tuple(p) for p in border]

                polygon = Polygon(border, srid=27700)
                wgs_84_polygon = polygon.transform(4326, clone=True)

                requires_clipping = polygon_requires_clipping(polygon, gb_region_geom)
                if requires_clipping:
                    try:
                        if polygon.intersects(gb_region_geom):
                            clipped_polygon = polygon.intersection(gb_region_geom)
                        else:
                            clipped_polygon = polygon
                    except Exception as e:
                        tqdm.write("Got exception when generating:", kml_filename)
                        tqdm.write("The exception was:", e)
                        tqdm.write("The polygon's KML was:", wgs_84_polygon.kml)
                        clipped_polygon = polygon
                else:
                    clipped_polygon = polygon

                wgs_84_clipped_polygon = clipped_polygon.transform(4326, clone=True)

                # If the polygon isn't valid after transformation, try to
                # fix it. (There has been at least one such case with the old dataset.
                if not wgs_84_clipped_polygon.valid:
                    tqdm.write("Warning: had to fix polygon {0}".format(kml_filename))
                    wgs_84_clipped_polygon = fix_invalid_geos_geometry(wgs_84_clipped_polygon)

                output_boundary_kml(kml_filename, requires_clipping, postcodes, wgs_84_clipped_polygon)

        pool = Pool(processes=cpu_count())

        # There's some weird memory leak with this combination of tqdm and pool.imap_unordered
        # so do at most REGION_BATCH_SIZE at a time:
        total_positions = len(positions_list)
        for start_index in range(0, total_positions, REGION_BATCH_SIZE):
            n = min(REGION_BATCH_SIZE, total_positions - start_index)
            print("Processing batch from index", start_index, "to", (start_index + n - 1, "inclusive"))
            for _ in tqdm(
                    pool.imap_unordered(output_kml, range(start_index, start_index + n)),
                    total=n,
                    dynamic_ncols=True):
                pass

    if len(positions_seen) == 0 and required_pc_prefix:
        print("No postcodes we could process matched '{0}'".format(required_pc_prefix))
