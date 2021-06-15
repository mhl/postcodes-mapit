from collections import defaultdict
import csv
import math
from os.path import basename
import re

from django.contrib.gis.geos import Point, Polygon
from django.contrib.gis.gdal import DataSource
from django.core.management.base import BaseCommand
from django.db import connection
from lxml import etree
import numpy as np
from scipy.spatial import Voronoi
from tqdm import tqdm

from mapit_postcodes.models import VoronoiRegion, NSULRow

BATCH_SIZE = 1000

# This doesn't need to be in any sense precise - it's used for the centre
# of our ring of "points at infinity". Taken from:
# https://www.ordnancesurvey.co.uk/blog/2014/08/where-is-the-centre-of-great-britain-2/
CENTRE_OF_GB_E = 364188
CENTRE_OF_GB_N = 456541

UK_MAX_NORTHINGS = 1219109
UK_MIN_NORTHINGS = 3706


class Command(BaseCommand):
    help = "Generate Voronoi polygons from NSUL postcode coordinates"

    def add_arguments(self, parser):
        parser.add_argument(
            "-s",
            "--startswith",
            metavar="PREFIX",
            help="Only process postcodes that start with PREFIX",
        )

    def handle(self, **options):
        required_pc_prefix = options["startswith"]

        # We do a Voronoi diagram for each region separately, since doing
        # the whole of GB in one go takes way too much memory, even for a
        # 64GB machine. (This is bad for the very few postcodes that cross
        # EU region boundaries, but I can live with that for the moment.)

        for region_code in NSULRow.objects.values_list("region_code", flat=True).distinct():
            print("===== Processing region", region_code)

            positions_list = []
            position_to_row_ids = defaultdict(set)

            # Get the unique positions from the mapit_postcodes_nsulrow table
            # into a list, storing the corresponding primary key of all the rows
            # that refer to that position.

            rows_processed = 0
            for nsul_row in NSULRow.objects.filter(region_code=region_code).iterator(chunk_size=BATCH_SIZE):
                position_tuple = (int(nsul_row.point.x), int(nsul_row.point.y))
                positions_list.append(position_tuple)
                rows_processed += 1
                if (rows_processed % 100000) == 0:
                    print(f"{region_code}: Read {rows_processed} rows from the database")
                if required_pc_prefix and not nsul_row.startswith(required_pc_prefix):
                    continue
                position_to_row_ids[position_tuple].add(nsul_row.id)

            # Now add some "points at infinity" - 200 points in a circle way
            # outside the border of the United Kingdom:

            points_at_infinity = 200

            distance_to_infinity = (UK_MAX_NORTHINGS - UK_MIN_NORTHINGS) * 1.5

            for i in range(0, points_at_infinity):
                angle = (2 * math.pi * i) / float(points_at_infinity)
                new_x = CENTRE_OF_GB_E + math.cos(angle) * distance_to_infinity
                new_y = CENTRE_OF_GB_N + math.sin(angle) * distance_to_infinity
                positions_list.append((new_x, new_y))

            points = np.array(positions_list)
            print(f"{region_code}: Calculating the Voronoi diagram...")
            vor = Voronoi(points)
            print(f"{region_code}: Finished!")

            # Now put the Voronoi polygons into the database, and set up foreign keys
            # from the NSUL rows. Batch them up so that we can use bulk_create and
            # bulk_update.

            total_positions = len(positions_list)
            with tqdm(total=total_positions) as progress:
                for start_index in range(0, total_positions, BATCH_SIZE):
                    n = min(BATCH_SIZE, total_positions - start_index)
                    # print(f"{region_code}: Processing batch from index", start_index, "to", start_index + n - 1, "inclusive")

                    nr_list = []
                    vr_to_create = []
                    for i in range(start_index, start_index + n):
                        position_tuple = positions_list[i]
                        row_ids = position_to_row_ids[position_tuple]
                        if not row_ids:
                            # This is one of the "points at infinity" - ignore them
                            continue

                        voronoi_region_index = vor.point_region[i]
                        voronoi_region = vor.regions[voronoi_region_index]
                        if any(vi < 0 for vi in voronoi_region):
                            # Then this region extends to infinity, so is outside our "points at infinity"
                            continue
                        if len(voronoi_region) < 3:
                            # Skip any point with fewer than 3 triangle_indices
                            return

                        border = [vor.vertices[i] for i in voronoi_region]
                        border.append(border[0])
                        # The coordinates are NumPy arrays, so convert them to tuples:
                        border = [tuple(p) for p in border]
                        polygon = Polygon(border, srid=27700)

                        voronoi_region_object = VoronoiRegion(polygon=polygon)
                        vr_to_create.append(voronoi_region_object)

                        nr_list.append(row_ids)

                    nr_vr_ids_to_update = []
                    vr_created = VoronoiRegion.objects.bulk_create(vr_to_create)
                    for i, voronoi_region in enumerate(vr_created):
                        for nsul_row_id in nr_list[i]:
                            nr_vr_ids_to_update.append((nsul_row_id, voronoi_region.id))

                    # The update was incredibly slow, so I'm trying the technique here to see if
                    # it helps https://stackoverflow.com/a/24811058/223092
                    if len(nr_vr_ids_to_update) > 0:
                        with connection.cursor() as cursor:
                            cursor.execute("create temporary table tmp (nsul_row_id integer, voronoi_region_id integer)")
                            insert_query = "insert into tmp (nsul_row_id, voronoi_region_id) values " + \
                                ", ".join(f"({nr_id}, {vr_id})" for nr_id, vr_id in nr_vr_ids_to_update)
                            cursor.execute(insert_query)
                            cursor.execute("update mapit_postcodes_nsulrow nr set voronoi_region_id = tmp.voronoi_region_id from tmp where nr.id = tmp.nsul_row_id")
                            # Not strictly necessary since it's a temporary table, but this saves me
                            # having to figure out the database session lifetime
                            cursor.execute("drop table tmp")

                    progress.update(n)
