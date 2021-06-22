#!/usr/bin/env python3

from __future__ import unicode_literals, print_function

import argparse
import json
from pathlib import Path
import re

from django.contrib.gis.geos import GEOSGeometry, GeometryCollection

postcode_re = re.compile(
    r"(?P<outcode>[A-PR-UWYZ]([0-9][0-9A-HJKPS-UW]?|[A-HK-Y][0-9][0-9ABEHMNPRV-Y]?)) *"
    + r"((?P<sector>[0-9])(?P<unit>[ABD-HJLNP-UW-Z]{2}))"
)


def postcode_to_sector(pc):
    m = postcode_re.search(pc)
    return m.group("outcode") + " " + m.group("sector")


def postcode_to_district(pc):
    m = postcode_re.search(pc)
    return m.group("outcode")


if __name__ == "__main__":

    parser = argparse.ArgumentParser(
        description="Generate KML files of postcode areas, districts and sectors"
    )
    parser.add_argument("outcodes_directory", metavar="OUTCODES-DIRECTORY")

    args = parser.parse_args()

    outcodes_directory = Path(args.outcodes_directory)
    outcode_filenames = outcodes_directory.glob("*/*.geojson")

    for outcode_filename in sorted(outcode_filenames):
        outcode = outcode_filename.with_suffix("").name
        print("===", outcode, "(sectors and districts)")
        with open(outcode_filename) as f:
            outcode_data = json.load(f)
        features = [
            {
                "postcode": feature["properties"]["postcodes"],
                "geos_geometry": GEOSGeometry(json.dumps(feature["geometry"])),
            }
            for feature in outcode_data["features"]
        ]
        for area_type, area_type_singular, extract_fn in [
            ("sectors", "sector", postcode_to_sector),
            ("districts", "district", postcode_to_district),
        ]:
            areas_directory = outcodes_directory.parent / area_type
            distinct_areas = sorted(set(extract_fn(f["postcode"]) for f in features))
            for area in distinct_areas:
                gc = GeometryCollection(
                    *[
                        f["geos_geometry"]
                        for f in features
                        if f["postcode"].startswith(area)
                    ]
                )
                unioned = gc.unary_union
                areas_outcode_directory = areas_directory / outcode
                areas_outcode_directory.mkdir(parents=True, exist_ok=True)
                area_output_filename = areas_outcode_directory / f"{area}.geojson"
                with open(area_output_filename, "w") as fw:
                    fw.write('{"type": "FeatureCollection", "features": [')
                    feature = {
                        "geometry": json.loads(unioned.json),
                        "properties": {area_type.rstrip("s"): area},
                    }
                    fw.write(json.dumps(feature))
                    fw.write("]}")

    outcode_filenames = outcodes_directory.glob("*/*.geojson")
    outcodes = sorted(
        outcode_filename.with_suffix("").name for outcode_filename in outcode_filenames
    )
    areas = sorted(set(re.sub(r"\d.*", "", outcode) for outcode in outcodes))
    areas_directory = outcodes_directory.parent / "areas"
    areas_directory.mkdir(parents=True, exist_ok=True)
    for area in areas:
        print("===", area, "(area)")
        districts_directory = outcodes_directory.parent / "districts"
        filenames = districts_directory.glob(area + "[0-9]*/*.geojson")
        geos_geometries = []
        for filename in filenames:
            with open(filename) as f:
                district_data = json.load(f)
            geos_geometries.append(
                GEOSGeometry(json.dumps(district_data["features"][0]["geometry"]))
            )
        gc = GeometryCollection(*geos_geometries)
        unioned = gc.unary_union
        with open(areas_directory / f"{area}.geojson", "w") as fw:
            fw.write('{"type": "FeatureCollection", "features": [')
            feature = {
                "geometry": json.loads(unioned.json),
                "properties": {"area": area},
            }
            fw.write(json.dumps(feature))
            fw.write("]}")
