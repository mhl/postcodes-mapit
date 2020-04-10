from django.conf import settings
from django.conf.urls import include, url
from django.contrib import admin

from .views import RedirectToRawDataArchive

urlpatterns = [
    url(r'^data/voronoi-of-onspd-kml.tar.bz2', RedirectToRawDataArchive.as_view(), name='data-archive-redirect'),
    url(r'^admin/', admin.site.urls),
    url(r'^', include('mapit.urls')),
]

if settings.DEBUG:
    import debug_toolbar
    urlpatterns = [
        url(r'^__debug__/', include(debug_toolbar.urls)),
    ] + urlpatterns
