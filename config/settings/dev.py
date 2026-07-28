"""Local development settings.

This is what manage.py points at by default. Production sets
DJANGO_SETTINGS_MODULE=config.settings.prod instead.
"""

from .base import *  # noqa: F403

# Overridden rather than read from the environment: on a development machine
# these should just work, without a correctly filled .env being a prerequisite.
DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]
