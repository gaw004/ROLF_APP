"""Local development settings.

This is what manage.py points at by default. Production sets
DJANGO_SETTINGS_MODULE=config.settings.prod instead.
"""

from .base import *  # noqa: F403

# Overridden rather than read from the environment: on a development machine
# these should just work, without a correctly filled .env being a prerequisite.
DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

# Password reset mail is sent by Django itself (its own views, its own form),
# so it goes out over EMAIL_BACKEND and not through core/notifications — that
# layer exists for "who should be told about this event", a question with no
# equivalent here. Django's default is SMTP, which on a laptop means the reset
# page raising a connection error; printing it keeps the whole flow walkable
# with no provider account, the same choice base.py makes for notifications.
#
# ⚠️ The link in the printed message is what a browser walk needs — copy it out
#    of the console. It is a real, working, single-use link.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = "rolf-dev@localhost"
