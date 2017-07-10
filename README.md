Voronoi Postcodes MapIt
=======================

This repository is for a version of MapIt which contains
postcode boundaries calculated from the Voronoi diagram of
postcode centroids in ONSPD.

For more information see the blog post here:

* https://longair.net/blog/2017/07/10/approximate-postcode-boundaries/

If you're looking to create a MapIt in your country, work on any
of the underlying code, or re-use it as an app in another Django
project, you probably want https://github.com/mysociety/mapit
instead.

How to generate the data
------------------------

Create a virtualenv for this project, change into the cloned
repository directory and run `pip install -r requirements.txt`
to install the Python package dependencies.

### Download the ONSPD

You can get the latest postcode database from mySociety's cache
here: http://parlvid.mysociety.org/os/ (which is a bit simpler
than getting it from http://geoportal.statistics.gov.uk/). This
data is released under the
[Open Government License v3](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).

Unpack this under `data/ONSPD_MAY_2017`

### Download the OSM border for the UK

OpenStreetMap has administrative boundaries for all countries in
the world, which you can conveniently get from Global MapIt:

http://global.mapit.mysociety.org/area/958873.html

Use the KML link to download a KML version of the boundary of
the UK to: `data/UK.kml`

### Make the fine-grained postcode unit boundaries

To run the script for the entire country, you can do:

    bin/make_postcode_shapes.py \
        data/ONSPD_MAY_2017/Data/ONSPD_MAY_2017_UK.csv \
        data/UK.kml \
        data/postcode-kml

### Make the higher-level postcode boundaries

You can build polygons for the postcode areas, districts and
sectors from the postcode unit boundaries with:

    bin/make_postcode_unions.py data/postcode-kml/

### Package up the data

To build a bzip2-compressed tar archive of these KML files, you
can run:

    bin/make-data-archive.sh data/postcode-kml

... which will create the archive as
`data/voronoi-of-onspd-kml.tar.bz2`.

### Import the data into MapIt

To import the data into MapIt, you can run:

    ./manage.py mapit_UK_import_inaccurate_postcode_areas data/postcode-kml 1

... where `1` is the ID of the MapIt Generation you want to
import the areas into. (It won't try to reimport any areas that
already exist in that generation.)

You should also import the ONSPD so that you can do lookups by
postcode on the front page:

    ./manage.py mapit_UK_import_onspd \
        --crown-dependencies=include \
        --northern-ireland=include \
        data/ONSPD_MAY_2017/Data/ONSPD_MAY_2017_UK.csv
