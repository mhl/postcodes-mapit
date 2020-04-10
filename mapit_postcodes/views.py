from django.views.generic.base import RedirectView

class RedirectToRawDataArchive(RedirectView):
    permanent = False
    url = "https://postcodes-mapit-static.s3.eu-west-2.amazonaws.com/data/voronoi-of-onspd-kml.tar.bz2"
