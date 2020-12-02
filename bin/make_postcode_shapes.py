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
import sys

from django.contrib.gis.geos import Point, Polygon
from django.contrib.gis.gdal import DataSource
from lxml import etree
from matplotlib.delaunay import delaunay
import numpy as np
from tqdm import tqdm

from mapit.management.command_utils import fix_invalid_geos_geometry

COLUMN_POSTCODE = "pcds"
COLUMN_E = "gridgb1e"
COLUMN_N = "gridgb1n"


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


if __name__ == '__main__':

    parser = argparse.ArgumentParser(
        description="Generate KML files of the Voronoi diagram of ONSPD postcode centroids")
    parser.add_argument('-s', '--startswith', metavar='PREFIX',
                        help='Only process postcodes that start with PREFIX')
    parser.add_argument('-p', '--postcode-points', action='store_true',
                        help='Also output a KML file with a Placemark per postcode')
    parser.add_argument('onspd_csv_filename', metavar='ONSPD-CSV-FILE')
    parser.add_argument('uk_boundary_filename', metavar='UK-BOUNDARY-KML-FILE')
    parser.add_argument('output_directory', metavar='OUTPUT-DIRECTORY')

    args = parser.parse_args()
    required_pc_prefix = args.startswith

    # ------------------------------------------------------------------------

    # Load the boundary of the UK, so that we can restrict the regions at
    # the edges of the diagram.

    uk_ds = DataSource(args.uk_boundary_filename)

    if len(uk_ds) != 1:
        raise Exception("Expected the UK border to only have one layer")

    uk_layer = next(iter(uk_ds))
    uk_geometries = uk_layer.get_geoms(geos=True)

    if len(uk_geometries) != 1:
        raise Exception("Expected the UK layer to only have one MultiPolygon")

    uk_multipolygon = uk_geometries[0]

    def polygon_requires_clipping(wgs84_polygon):
        geom_type = wgs84_polygon.geom_type
        if geom_type == 'MultiPolygon':
            polygons = wgs84_polygon.coords
        elif geom_type == 'Polygon':
            polygons = [wgs84_polygon.coords]
        else:
            raise Exception("Unknown geom_type {0}".format(geom_type))
        for polygon in polygons:
            for t in polygon:
                for x, y in t:
                    point = Point(x, y)
                    if not uk_multipolygon.contains(point):
                        return True
        return False

    # ------------------------------------------------------------------------

    # Make sure the output directory exists:

    postcodes_output_directory = join(args.output_directory, 'postcodes')
    mkdir_p(postcodes_output_directory)

    # A modified version of one of the regular expressions suggested here:
    #    http://en.wikipedia.org/wiki/Postcodes_in_the_United_Kingdom

    postcode_matcher = re.compile(r'^([A-PR-UWYZ]([0-9][0-9A-HJKPS-UW]?|[A-HK-Y][0-9][0-9ABEHMNPRV-Y]?)) *([0-9][ABD-HJLNP-UW-Z]{2})$')

    # Now load the centroids of (almost) all the postcodes:

    lon_sum = 0
    lat_sum = 0

    lon_min = sys.maxsize
    lat_min = sys.maxsize

    lon_max = -sys.maxsize - 1
    lat_max = -sys.maxsize - 1

    x = []
    y = []


    lon_max_row = None
    lat_min_row = None

    position_to_postcodes = defaultdict(set)
    wgs84_postcode_and_points = []

    total_postcodes = 0

    with open(args.onspd_csv_filename) as fp:
        reader = csv.DictReader(fp)
        for i, row in enumerate(reader):
            if i > 0 and (i % 1000 == 0):
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
            lon_min = min(lon_min, lon)
            lat_min = min(lat_min, lat)
            lon_max = max(lon_max, lon)
            lat_max = max(lat_max, lat)
            position_tuple = (lon, lat)
            position_to_postcodes[position_tuple].add(pc)
            if len(position_to_postcodes[position_tuple]) == 1:
                x.append(lon)
                y.append(lat)
                lon_sum += lon
                lat_sum += lat
            total_postcodes += 1

    if total_postcodes == 0 and required_pc_prefix:
        print("No postcodes we could process matched '{0}'".format(
            required_pc_prefix))
        sys.exit(1)

    centroid_x = lon_sum / float(len(x))
    centroid_y = lat_sum / float(len(y))

    # Now add some "points at infinity" - 200 points in a circle way
    # outside the border of the United Kingdom:

    points_at_infinity = 200

    distance_to_infinity = (lat_max - lat_min) * 2

    first_infinity_point_index = len(x)

    for i in range(0, points_at_infinity):
        angle = (2 * math.pi * i) / float(points_at_infinity)
        new_x = centroid_x + math.cos(angle) * distance_to_infinity
        new_y = centroid_y + math.sin(angle) * distance_to_infinity
        x.append(new_x)
        y.append(new_y)
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

    x = np.array(x)
    y = np.array(y)

    print("Calculating the Delaunay Triangulation...")

    ccs, edges, triangles, neighbours = delaunay(x, y)

    point_to_triangles = [[] for _ in x]

    for i, triangle in enumerate(triangles):
        for point_index in triangle:
            point_to_triangles[point_index].append(i)

    polygon_count_per_postcode = defaultdict(int)

    # Now generate the KML output:

    print("Now generating KML output")

    def output_kml(point_index_and_triangle_indices):
        point_index, triangle_indices = point_index_and_triangle_indices

        centre_x = x[point_index]
        centre_y = y[point_index]
        position_tuple = centre_x, centre_y

        if len(triangle_indices) < 3:
            # Skip any point with fewer than 3 triangle_indices
            return

        if position_tuple in position_to_postcodes:
            postcodes = sorted(position_to_postcodes[position_tuple])
            file_basename = postcodes[0]
            outcode = file_basename.split()[0]
        else:
            postcodes = []
            file_basename = 'point-{0:09d}'.format(point_index)
            outcode = 'points-at-infinity'

        mkdir_p(join(postcodes_output_directory, outcode))

        previous_polygons_found = polygon_count_per_postcode[file_basename]
        leafname = f"{file_basename}_{previous_polygons_found}.kml"
        polygon_count_per_postcode[file_basename] += 1

        if len(postcodes) > 1:
            json_leafname = file_basename + ".json"
            with open(join(postcodes_output_directory, outcode, json_leafname), "w") as fp:
                json.dump(postcodes, fp)

        kml_filename = join(postcodes_output_directory, outcode, leafname)

        if not os.path.exists(kml_filename):

            circumcentres = [ccs[i] for i in triangle_indices]

            def angle_from_centre(p):
                dx = p[0] - centre_x
                dy = p[1] - centre_y
                return math.atan2(dy, dx)

            sccs = np.array(sorted(circumcentres, key=angle_from_centre))
            xs = [cc[0] for cc in sccs]
            ys = [cc[1] for cc in sccs]

            border = []
            for i in range(0, len(sccs) + 1):
                index_to_use = i
                if i == len(sccs):
                    index_to_use = 0
                cc = (float(xs[index_to_use]),
                      float(ys[index_to_use]))
                border.append(cc)

            polygon = Polygon(border, srid=27700)
            wgs_84_polygon = polygon.transform(4326, clone=True)

            # If the polygon isn't valid after transformation, try to
            # fix it. (There is one such case.)
            if not wgs_84_polygon.valid:
                tqdm.write("Warning: had to fix polygon {0}".format(kml_filename))
                wgs_84_polygon = fix_invalid_geos_geometry(wgs_84_polygon)

            requires_clipping = polygon_requires_clipping(wgs_84_polygon)
            if requires_clipping:
                try:
                    if wgs_84_polygon.intersects(uk_multipolygon):
                        clipped_polygon = wgs_84_polygon.intersection(uk_multipolygon)
                    else:
                        clipped_polygon = wgs_84_polygon
                except Exception as e:
                    tqdm.write("Got exception when generating:", kml_filename)
                    tqdm.write("The exception was:", e)
                    tqdm.write("The polygon's KML was:", wgs_84_polygon.kml)
                    clipped_polygon = wgs_84_polygon
            else:
                clipped_polygon = wgs_84_polygon

            output_boundary_kml(kml_filename, requires_clipping, postcodes, clipped_polygon)

    pool = Pool(processes=cpu_count())
    index_and_point_tuples = enumerate(point_to_triangles)
    for _ in tqdm(
            pool.imap_unordered(output_kml, index_and_point_tuples),
            total=len(point_to_triangles),
            dynamic_ncols=True):
        pass
