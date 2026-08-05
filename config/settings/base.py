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
    # events depends on both: Event -> Ministry, Participation -> Contact.
    'events',
]

# Set before the first migrate, while no user table exists yet — swapping this
# out later means hand-written data migrations and rebuilt foreign keys.
AUTH_USER_MODEL = "accounts.User"

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    # ⚠️ Directly after SecurityMiddleware and before everything else, as
    # whitenoise documents. It serves the built CSS and JS itself so that
    # Render needs no separate web server or CDN in front of gunicorn — the
    # whole reason the deployment in C3.5 is one service rather than two.
    #
    # Order is not cosmetic here: a static file answered from this position
    # never runs the session, auth or history middleware below, which is both
    # faster and the reason a missing asset cannot take a database query with
    # it.
    'whitenoise.middleware.WhiteNoiseMiddleware',
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
    # After AuthenticationMiddleware, because it reads request.user: a signed-in
    # volunteer asking for /admin/ is refused outright rather than redirected to
    # a login form that would tell them to sign in again (D21's first
    # requirement says 403, not "no link").
    'core.middleware.StaffOnlyAdminMiddleware',
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
                # Which management entrances the shared nav should draw. Asks
                # org.permissions, never MinistryRole directly (D20).
                'core.context_processors.navigation',
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


# --- Notifications ----------------------------------------------------------
# Which adapter puts a message on the wire. Who should be told, and at what
# address, is never decided here — that is events/services.py, because "a minor
# is notified through their guardian" is a rule about this foundation and no
# provider has heard of it. See goal.md D22.
#
# Console in development: the whole flow can be walked on a laptop with no
# domain and no provider account. prod.py points at Novu; the tests override
# this to the locmem backend, so business-rule tests never touch a network and
# never go red because somebody changed provider.
NOTIFICATION_BACKEND = env(
    "NOTIFICATION_BACKEND", "core.notifications.console.ConsoleBackend")
NOVU_API_KEY = env("NOVU_API_KEY", "")
NOVU_WORKFLOW = env("NOVU_WORKFLOW", "event-change")


# --- Authentication ---------------------------------------------------------

# Django's default is "/accounts/login/", and accounts/urls.py is mounted at the
# root prefix, so the real path is "/login/". Left at the default, every
# @login_required page redirected an anonymous visitor to a 404 — which is what
# the nav's first link did to anyone arriving at this site for the first time.
# Nothing failed loudly: the redirect was correct, the target simply did not
# exist, and no test followed the redirect far enough to notice.
#
# ⚠️ Only LOGIN_URL. LOGIN_REDIRECT_URL / LOGOUT_REDIRECT_URL stay unset on
#    purpose: VolunteerLoginView.get_success_url() and VolunteerLogoutView.
#    next_page already answer "where to afterwards", and a setting saying the
#    same thing is a second copy that nothing reads until the day it disagrees.
LOGIN_URL = "/login/"

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

# The built CSS and JS. `static/` holds **build products only** — the sources
# they are built from live in `assets/`, outside anything collectstatic looks
# at.
#
# ⚠️ 04-roadmap.md 的 C1.1 原文把源文件放在 `static/src/`。**故意偏离**，理由是
#    一个会在部署那天才炸的东西：`assets/app.css` 的第一行是
#    `@import "tailwindcss"`，而 ManifestStaticFilesStorage 会去解析 CSS 里的
#    @import 并把它当成一个静态文件来找。找不到 → collectstatic 直接失败。
#    经过和实测记在 04-roadmap.md 的计划外记录里。
STATICFILES_DIRS = [BASE_DIR / 'static']


# --- Uploaded files ---------------------------------------------------------
# Event images, and nothing else so far.
#
# ⚠️ These deliberately do **not** live in the database. The backup is a
#    pg_dump (C3.6), so anything in a column is in every backup forever — and
#    the requirement for these is the opposite: they go away when the event
#    ends and are in no backup at all. A BinaryField would have made that
#    impossible to honour.
#
# ⚠️ The local filesystem is right for development and **wrong for Render**,
#    whose disk is wiped on every deploy. Production points STORAGES["default"]
#    at an object store (C3.5/C3.6). Until that is configured, "not in any
#    backup" is a promise rather than a verified fact — said plainly because
#    the local setup cannot demonstrate it either way.
#
# ⚠️ The image bucket must be a **different** bucket from the backup one. The
#    backup bucket is private and full of minors' data; these have to be
#    readable by every signed-in volunteer. One bucket cannot be both.
MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

# The largest upload accepted before it is resized. Anything past this is
# refused by the form rather than streamed to disk first.
EVENT_IMAGE_MAX_UPLOAD_BYTES = 10 * 1024 * 1024


# --- Models -----------------------------------------------------------------
# Set explicitly rather than left to Django's AutoField default: changing the
# primary key type once tables hold data means altering every table's PK and
# every foreign key column pointing at it. Free to do now, expensive later.
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
