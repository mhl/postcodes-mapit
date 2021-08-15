from __future__ import unicode_literals, print_function

import json
import os
from os.path import join
import re

from django.core.management.base import LabelCommand, CommandError
from django.core.management import call_command

from mapit.models import Area, CodeType, Generation, NameType, Type


def without_spaces(s):
    return re.sub(r"\s+", "", s)


class Command(LabelCommand):
    help = "Import postcode polygons"

    def add_arguments(self, parser):
        parser.add_argument(
            "postcodes_geojson_directory", metavar="POSTCODE-GEOJSON-DIRECTORY"
        )
        parser.add_argument("generation", metavar="GENERATION_ID")

    def handle(self, **options):

        generation = Generation.objects.get(pk=options["generation"])

        call_command("loaddata", "uk_voronoi_postcodes")

        name_type = NameType.objects.get(code="uk-pc-name")

        postcodes_geojson_directory = options["postcodes_geojson_directory"]

        for type_code, relative_geojson_directory, code_type_code in [
            ("APA", "areas", "uk-pc-area"),
            ("APD", "districts", "uk-pc-district"),
            ("APS", "sectors", "uk-pc-sector"),
            ("APU", "units", "uk-pc"),
        ]:
            area_type = Type.objects.get(code=type_code)
            code_type = CodeType.objects.get(code=code_type_code)

            if relative_geojson_directory == "units":
                name_field = "postcodes"
            else:
                name_field = re.sub(r"s$", "", relative_geojson_directory)

            geojson_directory = join(
                postcodes_geojson_directory, relative_geojson_directory
            )
            if not os.path.isdir(geojson_directory):
                raise CommandError("'{0}' is not a directory".format(geojson_directory))

            # In case we're restarting after a failed import, check what
            # the last postcode to be imported into this generation was:
            last_imported = None
            possible_last = Area.objects.filter(
                type=area_type,
                generation_high__gte=generation,
                generation_low__lte=generation,
            ).order_by("-name")[:1]
            if len(possible_last) > 0:
                last_imported = possible_last[0].name

            for root, dirs, filenames in os.walk(geojson_directory):
                dirs.sort()
                filenames.sort()
                for filename in filenames:
                    m = re.search(r"^(.*)\.geojson$", filename)
                    if not m:
                        continue
                    postcode = m.group(1)
                    if last_imported is not None and postcode <= last_imported:
                        continue
                    if postcode.startswith("point-"):
                        continue
                    print("doing postcode:", postcode)

                    full_filename = os.path.join(root, filename)

                    command_kwargs = {
                        "generation_id": generation.id,
                        "area_type_code": area_type.code,
                        "name_type_code": name_type.code,
                        # Postcode units sometimes overlap between countries of the UK,
                        # so just use the country field to say "United Kingdom"
                        "country_code": "U",
                        "name_field": name_field,
                        "code_field": "mapit_code",
                        "code_type": code_type.code,
                        "encoding": None,
                        "commit": True,
                        "new": False,
                        "use_code_as_id": False,
                        "fix_invalid_polygons": False,
                        "preserve": True,
                    }

                    call_command("mapit_import", full_filename, **command_kwargs)

        # Now handle the cases where there are multiple postcodes at a
        # single point. Change the main name, and add additional codes
        # and names for the other postcodes at that point.
        postcodes_directory = join(postcodes_geojson_directory, "postcodes")
        code_type = CodeType.objects.get(code="uk-pc")
        name_type = NameType.objects.get(code="uk-pc-name")
        for root, dirs, filenames in os.walk(postcodes_directory):
            dirs.sort()
            filenames.sort()
            for filename in filenames:
                m = re.search("^(.*)\.json$", filename)
                if not m:
                    continue
                primary_postcode = m.group(1)
                full_filename = join(root, filename)
                if not full_filename.endswith(".json"):
                    continue
                with open(full_filename) as f:
                    all_postcodes = json.load(f)
                assert primary_postcode == all_postcodes[0]
                # Find the area this refers to from the primary code:
                area = Area.objects.get(
                    codes__type__code="uk-pc",
                    codes__code=without_spaces(primary_postcode),
                )
                # Now change its name to include all the postcodes for
                # that area:
                joined = ", ".join(all_postcodes)
                if len(joined) > 2000:
                    joined = re.sub(r",([^,]*)$", " ...", joined[:1996])
                area.name = joined
                area.save()
