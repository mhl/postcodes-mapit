#!/usr/bin/env python3

import json
from pathlib import Path
import sys


if len(sys.argv) != 2:
    print(f"Usage: {sys.argv[0]} DIRECTORY", file=sys.stderr)
    sys.exit(1)

outcodes_directory = Path(sys.argv[1])

if not (outcodes_directory.exists() and outcodes_directory.is_dir()):
    print(f"Error: {outcodes_directory} must be a directory")
    sys.exit(1)

outcode_geojson_filenames = sorted(outcodes_directory.glob("*/*.geojson"))

with open("all-individual-postcodes.geojson", "w") as fw:
    fw.write('{"type": "FeatureCollection", "features": [')
    first_feature = True
    for input_filename in outcode_geojson_filenames:
        with open(input_filename) as f:
            outcode_data = json.load(f)
        for feature in outcode_data["features"]:
            if first_feature:
                first_feature = False
            else:
                fw.write(",")
            json.dump(feature, fw, sort_keys=True)
    fw.write(']}')
