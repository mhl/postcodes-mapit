#!/usr/bin/env python3

from __future__ import unicode_literals, print_function

import argparse
from collections import defaultdict, namedtuple
from multiprocessing import Pool, cpu_count
from os import listdir, makedirs
from os.path import exists, isdir, join, splitext
import re

from django.contrib.gis.gdal import DataSource
from lxml import etree
from tqdm import tqdm


def output_boundary_kml(filename, name, geometry):
    kml = etree.Element('kml', nsmap={None: 'http://earth.google.com/kml/2.1'})
    folder = etree.SubElement(kml, 'Folder')
    name_element = etree.SubElement(folder, 'name')
    name_element.text = name
    placemark = etree.SubElement(folder, 'Placemark')
    placemark.append(etree.fromstring(geometry.kml))
    with open(filename, 'wb') as f:
        f.write(etree.tostring(
            kml, pretty_print=True, encoding='utf-8', xml_declaration=True))


def union_postcode_files(union_task):
    postcode_part_filename = join(
        union_task.full_directory,
        '{0}.kml'.format(union_task.postcode_part))
    if exists(postcode_part_filename):
        return
    # Now load each file and union them all:
    polygons = [
        feature.geom
        for f in union_task.files_to_union
        for feature in next(iter(DataSource(f)))
    ]
    unioned = polygons[0]
    for polygon in polygons[1:]:
        unioned = unioned.union(polygon)
    # And write out that file:
    output_boundary_kml(
        postcode_part_filename,
        union_task.postcode_part,
        unioned)


postcode_re = r'([A-PR-UWYZ]([0-9][0-9A-HJKPS-UW]?|[A-HK-Y][0-9][0-9ABEHMNPRV-Y]?)) *([0-9][ABD-HJLNP-UW-Z]{2})'

# postcode_matcher = re.compile(r'^' + postcode_re + '$')
postcode_and_suffix_matcher = re.compile(r'^' + postcode_re + r'_([0-9a-zA-Z]+)$')

UnionTask = namedtuple(
    'UnionTask',
    ['subdirectory', 'postcode_part', 'files_to_union', 'full_directory'])


if __name__ == '__main__':

    parser = argparse.ArgumentParser(
        description="Generate KML files of postcode areas, districts and sectors")
    parser.add_argument('data_directory', metavar='POSTCODE-KML-DIRECTORY')

    args = parser.parse_args()

    postcode_units = defaultdict(set)
    postcode_areas = defaultdict(set)
    postcode_districts = defaultdict(set)
    postcode_sectors = defaultdict(set)

    source_postcode_directory = join(args.data_directory, 'postcodes')
    sectors_output_directory = join(args.data_directory, 'sectors')
    districts_output_directory = join(args.data_directory, 'districts')
    areas_output_directory = join(args.data_directory, 'areas')
    units_output_directory = join(args.data_directory, 'units')

    for e in listdir(source_postcode_directory):
        district_m = re.search(r'^([A-Z]+)(\d+)([A-Z]+)?$', e)
        if not district_m:
            continue
        area = district_m.group(1)
        district = e
        for postcode_file in listdir(join(source_postcode_directory, e)):
            basename, extension = splitext(postcode_file)
            if extension != '.kml':
                continue
            m = postcode_and_suffix_matcher.search(basename)
            if not m:
                continue
            outward_code = m.group(1)
            inward_code = m.group(3)
            unit = outward_code + " " + inward_code
            sector_letter = inward_code[0]
            sector = '{district} {sector_letter}'.format(
                district=district, sector_letter=sector_letter)
            postcode_relative_filename = join(source_postcode_directory, e, postcode_file)
            unit_relative_filename = join(units_output_directory, unit + '.kml')
            sector_relative_filename = join(sectors_output_directory, sector + '.kml')
            district_relative_filename = join(districts_output_directory, district + '.kml')
            postcode_units[unit].add(postcode_relative_filename)
            postcode_sectors[sector].add(unit_relative_filename)
            postcode_districts[district].add(sector_relative_filename)
            postcode_areas[area].add(district_relative_filename)

    for subdirectory, mapping in (
            ('units', postcode_units),
            ('sectors', postcode_sectors),
            ('districts', postcode_districts),
            ('areas', postcode_areas)):
        tasks = []
        full_directory = join(args.data_directory, subdirectory)
        if not isdir(full_directory):
            makedirs(full_directory)
        for postcode_part, files_to_union in mapping.items():
            tasks.append(UnionTask(
                subdirectory, postcode_part, files_to_union, full_directory))

        print(f"Creating KML files for {subdirectory}")
        pool = Pool(processes=cpu_count())
        for _ in tqdm(
                pool.imap_unordered(union_postcode_files, tasks),
                total=len(tasks),
                dynamic_ncols=True):
            pass
