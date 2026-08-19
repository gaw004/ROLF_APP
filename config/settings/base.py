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
    # gallery depends on org (GalleryPhoto -> Ministry) and accounts
    # (-> uploaded_by). Nothing depends on gallery, which is why it is last.
    'gallery',
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
                # The front page's picture and the brand ramp derived from it.
                # Needed by the shared shell on every page (D25 / D26).
                'core.context_processors.site_appearance',
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

# --- Google sign-in (prefill only) -------------------------------------------
# The OAuth client id for the "Continue with Google" button on the registration
# page. It fills three boxes in and grants nothing — see accounts/google.py.
#
# ⚠️ Empty by default, and empty means **the button is not drawn**. A deployment
#    without a client id must not show a control that fails when pressed.
#
# ⚠️ The client id is not a secret (it is in the page source by design), and this
#    flow needs no client *secret* at all — there is no code exchange, only a
#    signed identity token that the server verifies.
GOOGLE_OAUTH_CLIENT_ID = env("GOOGLE_OAUTH_CLIENT_ID", "")


# --- Rate limiting ----------------------------------------------------------
# Registration is the only write an anonymous stranger can do, and it writes two
# rows each time. See core/ratelimit.py for what this buys and what it does not.
#
# ⚠️ The numbers are **generous on purpose**, and the reason is a real scenario
#    rather than caution: forty volunteers signing up on a church hall's wifi at
#    an onboarding evening are one IP address. A tight per-IP limit would refuse
#    most of them and look exactly like a broken site. A script, meanwhile, wants
#    thousands — so twenty an hour separates the two cases perfectly well, and
#    anything tighter only breaks the good one.
#
# ⚠️ Both are environment variables so that a signup drive can raise them
#    without a deploy, and core/ratelimit.py reads them per request rather than
#    at import so the change takes effect on restart alone.
REGISTRATION_RATELIMIT_PER_IP = env("REGISTRATION_RATELIMIT_PER_IP", "20/h")
REGISTRATION_RATELIMIT_SITE = env("REGISTRATION_RATELIMIT_SITE", "100/h")

# The password-reset request page, limited for a different reason: it is a form
# that makes this application send mail to any address a stranger types, and the
# allowance it spends is shared with every notification and every reset a real
# person needs. Tighter than registration because the honest case is smaller —
# forty people at one event all register, and approximately none of them all
# forget their password on the same evening.
#
# ⚠️ Tuning these means asking what the mail plan allows per day, not what feels
#    safe. The failure is not an error page: it is the day's remaining messages
#    being gone, and the people who needed them hearing nothing.
# Typing or asking for an email confirmation code. Tighter than the reset limits
# on purpose: this one is guessed at, not merely triggered — see
# core.ratelimit.verification_rate_per_ip.
VERIFICATION_RATELIMIT_PER_IP = env("VERIFICATION_RATELIMIT_PER_IP", "20/h")
PASSWORD_RESET_RATELIMIT_PER_IP = env("PASSWORD_RESET_RATELIMIT_PER_IP", "10/h")
PASSWORD_RESET_RATELIMIT_SITE = env("PASSWORD_RESET_RATELIMIT_SITE", "60/h")

# Where the client's address comes from. See core/ratelimit.py::client_ip — the
# short version is that REMOTE_ADDR is the proxy in production, and trusting the
# whole X-Forwarded-For header is worse than not limiting at all.
RATELIMIT_IP_META_KEY = "core.ratelimit.client_ip"

# ⚠️ Off here, on in prod.py. A development machine has no proxy in front of it,
#    and a machine that trusts X-Forwarded-For with nothing in front of it will
#    believe whatever a caller puts there.
TRUST_PROXY_CLIENT_IP = env("TRUST_PROXY_CLIENT_IP", "False").lower() in {"1", "true", "yes"}


# --- Caches -----------------------------------------------------------------
# ⚠️ A **database** cache, and this is the one decision here that is not
#    incidental. django-ratelimit counts in the cache, and Django's default cache
#    is per-process local memory — so under gunicorn with four workers the limit
#    is counted four times over and is quietly worth four times what it says.
#    Nothing errors; the limit simply does not hold. A database cache is shared
#    by every worker, needs no second service, and the write volume here is one
#    row per registration attempt.
#
# ⚠️ The table is created by a **migration** (core/0004), not by a deploy step
#    calling `createcachetable`. A step that can be forgotten, whose only symptom
#    is a rate limit that raises on the registration page, is a step that will be
#    forgotten. Renaming the table below without a new migration would leave the
#    same hole.
#
# Redis instead is a Phase C3.4/C3.5 question and a change of two lines; nothing
# here depends on which backend it is.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "django_cache",
    }
}


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
# Three kinds now: event images, gallery photos (Memories) and the front page's
# picture/video. They have **three different lifecycles**, which is why they end
# up in three different buckets rather than one — see STORAGES below.
#
# ⚠️ These deliberately do **not** live in the database. The backup is a
#    pg_dump (C3.6), so anything in a column is in every backup forever — and
#    the requirement for event images is the opposite: they go away when the
#    event ends and are in no backup at all. A BinaryField would have made that
#    impossible to honour.
#
# ⚠️ The local filesystem is right for development and **wrong for Render**,
#    whose disk is wiped on every deploy. prod.py points all three aliases at
#    Cloudflare R2 (C3.5, 2026-08-06).
MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'

# The largest upload accepted before it is resized. Anything past this is
# refused by the form rather than streamed to disk first.
EVENT_IMAGE_MAX_UPLOAD_BYTES = 10 * 1024 * 1024


# --- Where each kind of upload is stored -------------------------------------
# ⚠️ **Four aliases, and the split is a requirement rather than tidiness.**
#    Each one is a different answer to "who may read this" and "what is allowed
#    to delete it", and no single bucket can hold two different answers. Decided
#    2026-08-06 alongside the Memories page; the reasoning per alias:
#
#      default    Event images. Private — every signed-in volunteer may see
#                 them, nobody else. purge_event_images sweeps this bucket
#                 daily, so an object lifecycle rule ("drop anything older than
#                 N days") would be a reasonable thing to add here one day.
#                 ⚠️ It must have **no versioning**. R2 has no such feature at
#                 all, so today that holds for free — but on S3 or B2 it becomes
#                 a switch somebody has to keep off, because with versioning on
#                 the purge's delete is only a marker while the console
#                 cheerfully shows the object as deleted. "Gone after the event"
#                 would fail in a way that looks like it succeeded.
#
#      memories   Gallery photos. Private, and the reason it cannot share the
#                 bucket above is the sentence just before that ⚠️: whatever
#                 automatic deletion `default` is given must never be able to
#                 reach these. They are the only files in the system that can be
#                 neither regenerated nor restored from the pg_dump.
#
#                 ⚠️ **2026-08-12: there is no safety net under them, and that
#                    is now a decision rather than an oversight.** This comment
#                    used to say the bucket had versioning on and that this was
#                    "the whole reason" for the split. **R2 has no object
#                    versioning** — it was never true. Deliberately not replaced
#                    with a bucket lock: a retention policy also refuses the
#                    *legitimate* takedown ("please remove the photo of my
#                    child"), which the privacy page has to promise. So one
#                    mis-click on gallery/manage deletes a photograph for good,
#                    and the only thing in the way is the confirm on that
#                    button. Written down in phase-c.md's known gaps; the
#                    change of position is in revisions.md.
#
#      public     The front page's picture and video. **Public**, because that
#                 page is the one thing here that needs no login. Signing a URL
#                 for content that is public by definition buys nothing and
#                 costs the CDN cache on the largest file the site serves.
#
#      staticfiles  Built CSS/JS. Not an upload at all; whitenoise, from the repo.
#
# ⚠️ Development and the test suite keep all four on the local filesystem, so a
#    test run reaches no network and needs no credentials. Only prod.py swaps
#    them. Anything reading `storages["memories"]` therefore works in both.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "memories": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "public": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


# --- Models -----------------------------------------------------------------
# Set explicitly rather than left to Django's AutoField default: changing the
# primary key type once tables hold data means altering every table's PK and
# every foreign key column pointing at it. Free to do now, expensive later.
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
