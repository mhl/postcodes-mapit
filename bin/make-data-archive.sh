#!/bin/sh

if [ "$#" != 1 ]
then
    echo "Usage: $0 POSTCODE-GEOJSON-DIRECTORY"
    exit 1
fi

D=$1

if ! [ -d "$D" ]
then
    echo "$D was not a directory"
    exit 1
fi

OUTPUT_BASENAME="$(basename $D)"
OUTPUT_FILENAME="$(readlink -f "$D/../$OUTPUT_BASENAME.tar.bz2")"

if [ -e "$OUTPUT_FILENAME" ]
then
    echo "The output filename $OUTPUT_FILENAME already existed - not overwriting"
    exit 1
fi

echo Generating "$OUTPUT_FILENAME"

# Create a README and license file in that directoory:

cat <<EOF > "$D/README"
This archive contains approximate boundaries for postcodes in Great
Britain. For more information about how this data generated, see the blog
post here:

  https://longair.net/blog/2021/08/20/open-data-gb-postcode-unit-boundaries

This archive contains GeoJSON files for postcode areas, districts,
sectors and units. (The postcode unit boundaries are collected into
a file per district.)

The LICENSE file in this directory explains the conditions under which
you can use this data.

Contact: mark-postcodes@longair.net
EOF

cat <<EOF > "$D/LICENSE"
The data in this archive is derived from NSUL (published by the ONS)
and Boundary-Line (published by Ordnance Survey), which are both
licensed under the Open Government License version 3. You must abide
by the terms of these licenses and include the following copyright
notices:

Contains OS data © Crown copyright and database right 2020
Contains Royal Mail data © Royal Mail copyright and database right 2020
Source: Office for National Statistics licensed under the Open Government Licence v.3.0

These boundaries are known to be approximate - they are provided in case
they are of interest and use, but are only rough representations of the
postcode boundaries due to limitations of the open address data currently
available. In other words, there are no guarantees about the quality of
this data or its suitability for any use; use it at your own risk.
EOF

# Now tar it up:
D_WITHOUT_SLASH="${D%/}"


tar --sort=name \
    -cjvf "$OUTPUT_FILENAME" \
    "$D"
