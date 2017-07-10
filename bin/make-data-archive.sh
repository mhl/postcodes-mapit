#!/bin/sh

if [ "$#" != 1 ]
then
    echo "Usage: $0 POSTCODE-KML-DIRECTORY"
    exit 1
fi

D=$1

if ! [ -d "$D" ]
then
    echo "$D was not a directory"
    exit 1
fi

OUTPUT_FILENAME="$(readlink -f "$D/../voronoi-of-onspd-kml.tar.bz2")"

if [ -e "$OUTPUT_FILENAME" ]
then
    echo "The output filename $OUTPUT_FILENAME already existed - not overwriting"
    exit 1
fi

echo Generating "$OUTPUT_FILENAME"

# Create a README and license file in that directoory:

cat <<EOF > "$D/README"
This archive contains very approximate boundaries for postcodes in the
UK. For more information about how these were generated, see the blog
post here:

  https://longair.net/blog/2017/07/10/approximate-postcode-boundaries/

This archive contains KML files for postcode areas, districts, sectors
and units. Under the postcodes subdirectory, which contains the
postcode unit KML files, there are also a few .json files that list
the other postcodes which had the same coordinates in the source data
(ONSPD) in the case that there was more than one.

The LICENSE file in this directory explains the conditions under which
you can use this data.
EOF

cat <<EOF > "$D/LICENSE"
The data in this archive is derived from the ONSPD, which is licensed
under the Open Government License and the UK Government Licensing
Framework. It contains National Statistics data © Crown copyright and
database right 2017 and OS data © Crown copyright and database right
2017. It is also partially derived from data from OpenStreetMap which
is licensed under the Open Data Commons Open Database License by the
OpenStreetMap Foundation (OSMF), created by OpenStreetMap and its
contributors. You must abide by the terms of these licenses. These
boundaries are known to be approximate and not accurate - they are
provided in case they are of interest and use, but are only rough
representations of the postcode boundaries due to the lack of open
address data for the UK.
EOF

# Now tar it up:
D_WITHOUT_SLASH="${D%/}"


tar  --transform "s|$D|voronoi-of-onspd-kml|" \
    --sort=name \
    -cjvf "$OUTPUT_FILENAME" \
    "$D"
