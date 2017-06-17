#!/usr/bin/env python

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
    with open(filename, 'w') as f:
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


postcode_matcher = re.compile(r'^([A-PR-UWYZ]([0-9][0-9A-HJKPS-UW]?|[A-HK-Y][0-9][0-9ABEHMNPRV-Y]?)) *([0-9][ABD-HJLNP-UW-Z]{2})$')

UnionTask = namedtuple(
    'UnionTask',
    ['subdirectory', 'postcode_part', 'files_to_union', 'full_directory'])


if __name__ == '__main__':

    parser = argparse.ArgumentParser(
        description="Generate KML files of postcode areas, districts and sectors")
    parser.add_argument('output_directory', metavar='POSTCODE-KML-DIRECTORY')

    args = parser.parse_args()

    postcode_areas = defaultdict(set)
    postcode_districts = defaultdict(set)
    postcode_sectors = defaultdict(set)

    postcodes_output_directory = join(args.output_directory, 'postcodes')
    sectors_output_directory = join(args.output_directory, 'sectors')
    districts_output_directory = join(args.output_directory, 'districts')
    areas_output_directory = join(args.output_directory, 'areas')

    for e in listdir(postcodes_output_directory):
        district_m = re.search(r'^([A-Z]+)(\d+)([A-Z]+)?$', e)
        if not district_m:
            continue
        area = district_m.group(1)
        district = e
        for postcode_file in listdir(join(postcodes_output_directory, e)):
            basename, extension = splitext(postcode_file)
            if extension != '.kml':
                continue
            m = postcode_matcher.search(basename)
            if not m:
                continue
            sector_letter = m.group(3)[0]
            sector = '{district} {sector_letter}'.format(
                district=district, sector_letter=sector_letter)
            postcode_relative_filename = join(postcodes_output_directory, e, postcode_file)
            sector_relative_filename = join(sectors_output_directory, sector + '.kml')
            district_relative_filename = join(districts_output_directory, district + '.kml')
            postcode_sectors[sector].add(postcode_relative_filename)
            postcode_districts[district].add(sector_relative_filename)
            postcode_areas[area].add(district_relative_filename)

    tasks = []
    for subdirectory, mapping in (
            ('sectors', postcode_sectors),
            ('districts', postcode_districts),
            ('areas', postcode_areas)):
        full_directory = join(args.output_directory, subdirectory)
        if not isdir(full_directory):
            makedirs(full_directory)
        for postcode_part, files_to_union in mapping.items():
            tasks.append(UnionTask(
                subdirectory, postcode_part, files_to_union, full_directory))

    pool = Pool(processes=cpu_count())
    for _ in tqdm(
            pool.imap_unordered(union_postcode_files, tasks),
            total=len(tasks),
            dynamic_ncols=True):
        pass
