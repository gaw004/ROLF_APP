"""Settings shared by every environment.

Anything secret or environment-specific is read from the environment (see the
`env` helper below), never hardcoded here. Local development values live in a
`.env` file that is not committed; `.env.example` documents which keys exist.

Import this from `dev.py` / `prod.py` rather than pointing
DJANGO_SETTINGS_MODULE at it directly.
"""

import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

# config/settings/base.py -> config/settings -> config -> project root.
# Three .parent calls, not two: this file sits one level deeper than the
# settings.py that django-admin generates.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

load_dotenv(BASE_DIR / ".env")


def env(key, default=None, required=False):
    """Read an environment variable, optionally refusing to start without it."""
    value = os.getenv(key, default)
    if required and not value:
        raise RuntimeError(f"Missing required environment variable: {key}")
    return value


# --- Security ---------------------------------------------------------------
# No default: a missing key must stop the process, not fall back to something
# guessable. The key that used to be hardcoded here is in the git history and
# is therefore burned — it must never be used again.
SECRET_KEY = env("DJANGO_SECRET_KEY", required=True)

# Defaults to False so a forgotten environment variable fails on the safe side
# rather than exposing the debug pages. dev.py turns it back on.
DEBUG = env("DJANGO_DEBUG", "False").lower() in {"1", "true", "yes"}

ALLOWED_HOSTS = [h.strip() for h in env("DJANGO_ALLOWED_HOSTS", "").split(",") if h.strip()]


# --- Applications -----------------------------------------------------------

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'phonenumber_field',
    'django_countries',
    'localflavor',
    'simple_history',
    'accounts',
    # Listed before the apps that import from it, so the dependency direction
    # is obvious from reading this list: core holds what everything shares.
    'core',
    'contact',
    # org depends on contact (Assignment -> Contact), contact depends on core.
    'org',
]

# Set before the first migrate, while no user table exists yet — swapping this
# out later means hand-written data migrations and rebuilt foreign keys.
AUTH_USER_MODEL = "accounts.User"

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    # After AuthenticationMiddleware, as the library documents. The position
    # turns out not to actually matter: this middleware only stashes the request
    # object, and request.user is read later, at save time, by which point
    # AuthenticationMiddleware has populated it on that same object. Verified by
    # moving it earlier — the history tests still pass. Kept here anyway because
    # following the documented order costs nothing.
    'simple_history.middleware.HistoryRequestMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# --- Database ---------------------------------------------------------------
# A single DATABASE_URL rather than five separate settings: managed platforms
# (Render, Fly.io) inject exactly this variable, so deploying in Phase D needs
# no change here. Required, so a missing URL stops the process rather than
# silently falling back to a local database.

DATABASES = {"default": dj_database_url.parse(env("DATABASE_URL", required=True))}


# --- Authentication ---------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# --- Internationalization ---------------------------------------------------

LANGUAGE_CODE = 'en-us'

# The foundation is in Santa Clara, California. USE_TZ stays True, so the
# database still stores UTC; this only affects how times are displayed and
# where the boundaries of "this month" fall in reports (Phase C).
TIME_ZONE = 'America/Los_Angeles'

USE_I18N = True

USE_TZ = True


# --- Static files -----------------------------------------------------------

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'


# --- Models -----------------------------------------------------------------
# Set explicitly rather than left to Django's AutoField default: changing the
# primary key type once tables hold data means altering every table's PK and
# every foreign key column pointing at it. Free to do now, expensive later.
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
