# Import MapIt's settings (first time to quiet flake8)
from mapit_settings import INSTALLED_APPS
from mapit_settings import *  # noqa

# Update a couple of things to suit our changes

# Insert our project app before mapit so that the templates take precedence
INSTALLED_APPS.insert(INSTALLED_APPS.index('mapit'), 'mapit_gb')
INSTALLED_APPS.insert(INSTALLED_APPS.index('mapit_gb'), 'mapit_postcodes')
ROOT_URLCONF = 'mapit_postcodes.urls'
WSGI_APPLICATION = 'mapit_postcodes.wsgi.application'

# New settings

TIME_ZONE = 'Europe/London'
LANGUAGE_CODE = 'en'
POSTCODES_AVAILABLE = PARTIAL_POSTCODES_AVAILABLE = False

STATICFILES_STORAGE = 'django.contrib.staticfiles.storage.ManifestStaticFilesStorage'

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
