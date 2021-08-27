Voronoi Postcodes MapIt
=======================

This repository is for a version of MapIt which contains
postcode boundaries calculated from the Voronoi diagram of
UPRNs with postcodes in NSUL.

For more information see the blog post here:

* https://longair.net/blog/2021/08/23/open-data-gb-postcode-unit-boundaries

If you're looking to create a MapIt in your country, work on any
of the underlying code, or re-use it as an app in another Django
project, you probably want https://github.com/mysociety/mapit
instead.

How to generate the data
------------------------

If you just want the data yourself, there's no need to follow these
instructions - you can
[download it](https://postcodes-mapit-static.s3.eu-west-2.amazonaws.com/data/gb-postcodes-v5.tar.bz2).
instead. If for some reason you really want to regenerate i yourself,
read on.

Create a virtualenv for this project, change into the cloned
repository directory and run `pip install -r requirements.txt`
to install the Python package dependencies.

### Download NSUL

You can get NSUL from the ONS's [Open Geography Portal](https://geoportal.statistics.gov.uk/).
e.g. here is [the July 2021 version](https://geoportal.statistics.gov.uk/)

Unpack the archive somewhere convenient.

### Create the database and schema

Create a PostgreSQL database and install the PostGIS extensions
with `CREATE EXTENSION postgis` and `CREATE EXTENSION postgis_topology`.

Create a `~/.mapit` file with your database credentials, something like:

    {
        "DJANGO_SECRET_KEY": "some long random string",
        "MAPIT_DB_NAME": "mapit-postcodes",
        "MAPIT_DB_USER": "",
        "MAPIT_DB_PASS": "",
        "MAPIT_DB_HOST": null,
        "MAPIT_DB_PORT": "5432",
        "COUNTRY": "GB"
    }

Then run `./manage.py migrate` to create the database tables.

### Load the NSUL data into the database

You can do this with:

    ./manage.py mapit_postcodes_populate_nsul_table -r data/regions/gb_regions_multipolygons.shp ../NSUL/Data/*.csv

... adjusting the wildcarded parameter for where you've actually extracted NSUL.

### Calculate the Voronoi diagram

This command will calculate the Voronoi diagram of all those points, and store
the results in the database:

    ./manage.py mapit_postcodes_populate_voronoi_table

### Generate postcode GeoJSON files

This command will create unions of the Voronoi regions to create polygons
representing all postcode areas, districts, sectors and units and output
them to GeoJSON. (Warning: even with a fast machine with many cores, this
can take several days to run, so you definitely want to run it under tmux
or similar.)

    ./manage.py mapit_postcodes_union_postcode_regions --skip-vertical-streets \
        -r data/regions/gb_regions_multipolygons.shp \
        -o data/gb-postcodes

That will generate those GeoJSON files under `data/gb-postcodes`

### Package up the data

To build a bzip2-compressed tar archive of these GeoJSON files, you
can run:

    bin/make-data-archive.sh data/gb-postcodes

... which will create the archive as
`data/gb-postocdes.tar.bz2`.

### Import the data into MapIt

If you want to import this data into a MapIt instance, then you can
do that with:

    ./manage.py mapit_postcodes_import_postcode_areas data/gb-postcodes 1

... where `1` is the ID of the MapIt Generation you want to
import the areas into. (It won't try to reimport any areas that
already exist in that generation.)

If you want postcode lookups to work from the front page of the MapIt
instance you will also need to import postcodes from ONSPD or Code-Point:
see http://code.mapit.mysociety.org/import/uk/ for more details.

### Contact

Contact: mark-postcodes@longair.net
