"""Production settings.

Everything that matters in production (SECRET_KEY, DEBUG, ALLOWED_HOSTS,
DATABASE_URL) already comes from the environment via base.py. What this file
adds is the part that is only true behind Render's proxy, in front of a real
mail provider, and with a real object store: the C3.4 hardening, the C3.3 email
wiring, the C3.8 error reporting, and the four R2 buckets.

⚠️ Several variables here are `required=True`, which means the process does not
   start without them rather than quietly degrading. That is deliberate and it
   has a cost worth knowing about: **every service that imports these settings
   needs them**, including the purge cron, which sends no email and stores no
   files. core/tests.py::RenderBlueprintGuardTests checks that render.yaml
   declares them per service, because a missing one is not a warning — it is a
   job that dies at 2am and looks exactly like "there was nothing to do".
"""

import sentry_sdk
from botocore.config import Config

from .base import *  # noqa: F403
from core.health import HEALTH_PATH

from .base import ALLOWED_HOSTS, env

# --- C3.4 · Hardening -------------------------------------------------------
# The set `manage.py check --deploy` asks for, plus the one it cannot ask for.
#
# ⚠️ SECURE_PROXY_SSL_HEADER is that one, and leaving it out turns
#    SECURE_SSL_REDIRECT into an **infinite redirect loop**: Render terminates
#    TLS at its proxy, so the request Django sees is plain http, so it redirects
#    to https, which arrives as http again. Nothing about the error message
#    would point here. It is safe only because that proxy is the one setting the
#    header — with nothing in front of the app, any caller could claim https.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
# ⚠️ **The one path that must not be redirected**, and leaving it out cost a
#    failed deploy on 2026-08-17. Render checks the instance directly, over
#    plain HTTP, with no X-Forwarded-Proto on the request — so Django correctly
#    answers 301 to https, the platform reads "not a success code", and the
#    deploy times out. The message says "health check timed out", which points
#    at the application being slow or dead; nothing in it points at a setting.
#    The exemption is one path and never the site: the front page still
#    redirects, which is the whole point of the line above.
SECURE_REDIRECT_EXEMPT = [rf"^{HEALTH_PATH}?$"]
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
X_FRAME_OPTIONS = "DENY"

# HSTS is opened in two steps (C3.4 short, C5 long), and these read from the
# environment so the second step is a dashboard change rather than a deploy.
#
# ⚠️ **HSTS lives in the visitor's browser, and changing the server does not
#    reach it.** A long max-age with includeSubDomains, set before the domain
#    and its certificate are settled, makes every subdomain without HTTPS
#    *hard-fail* for everyone who has visited once — for the whole max-age, no
#    matter what is done server-side. Preload is worse: leaving that list takes
#    months. An hour is a real policy and passes the check just the same; the
#    check asks whether the setting has a value, not whether the value is brave.
#
# ⚠️ Deliberately absent from render.yaml, the same trick the registration rate
#    limits use: a value written there is pushed back over the dashboard on the
#    next blueprint sync — which would silently undo C5's change to a year.
SECURE_HSTS_SECONDS = int(env("SECURE_HSTS_SECONDS", "3600") or "3600")
SECURE_HSTS_INCLUDE_SUBDOMAINS = env("SECURE_HSTS_INCLUDE_SUBDOMAINS", "False").lower() in {
    "1", "true", "yes"}
SECURE_HSTS_PRELOAD = env("SECURE_HSTS_PRELOAD", "False").lower() in {"1", "true", "yes"}

# ⚠️ **C3.4's acceptance test was written on a wrong premise.** It says a short
#    HSTS value passes `check --deploy` clean "because the check only asks
#    whether the setting has a value" — true of W004, and beside the point:
#    setting *any* HSTS value turns on two further warnings, W005 and W021,
#    asking for includeSubDomains and preload. Both are the exact things the
#    two-step plan refuses to switch on before the domain is settled. So the
#    choice is between two permanent warnings and saying so out loud.
#
# ⚠️ Silenced by the short value itself, not by a list somebody has to remember
#    to edit: raise SECURE_HSTS_SECONDS towards a year at C5 and these two come
#    back on their own, asking for precisely the settings C5 is there to turn
#    on. A silence that expires with the reason for it.
_HSTS_IS_STILL_PROVISIONAL = SECURE_HSTS_SECONDS < 31536000
SILENCED_SYSTEM_CHECKS = (
    ["security.W005", "security.W021"] if _HSTS_IS_STILL_PROVISIONAL else [])

# Derived from ALLOWED_HOSTS rather than given its own variable. C5 hangs a
# custom domain on the app, and the two lists have to change together — set one
# and not the other and the site opens fine while **every POST is rejected**,
# which reads as "the form is broken", not as "a setting is missing". A second
# list is a second thing to forget; this one cannot disagree with the first.
#
# Wildcard entries are skipped: "*" and ".example.com" are host patterns, and
# CSRF_TRUSTED_ORIGINS wants origins. A wildcard host with no matching origin is
# the one case this cannot cover, and it is not one Render produces.
CSRF_TRUSTED_ORIGINS = [
    f"https://{host}" for host in ALLOWED_HOSTS
    if not host.startswith((".", "*"))
]

# --- C3.3 · Email -----------------------------------------------------------
# Brevo's SMTP relay as of 2026-08-17, Amazon SES before that, and the swap cost
# nothing because none of it is named here. Four values from the environment.
#
# ⚠️ required=True, so a deployment with no mail credentials refuses to start.
#    The alternative is worse than it sounds: with these unset Django falls back
#    to localhost:25, and *that* failure surfaces as a volunteer's password
#    reset doing nothing at all — at the moment they are already locked out.
EMAIL_HOST = env("EMAIL_HOST", required=True)
# ⚠️ `or "587"`, because Render's blueprint asks for every `sync: false`
#    variable and an empty answer is stored as an empty string — which int()
#    raises on, at import, so the whole service fails to boot over a field
#    somebody sensibly left blank to accept the default.
EMAIL_PORT = int(env("EMAIL_PORT", "587") or "587")
EMAIL_HOST_USER = env("EMAIL_HOST_USER", required=True)
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", required=True)
# ⚠️ Derived from the port rather than configured, because getting this pair
#    wrong does not raise: 465 speaks TLS from the first byte and 587 starts in
#    the clear and upgrades. Ask for the wrong one and the connection **hangs**
#    until it times out, which arrives as "the site is slow", not as a setting.
EMAIL_USE_SSL = EMAIL_PORT == 465
EMAIL_USE_TLS = not EMAIL_USE_SSL
# ⚠️ Must be an address at the domain that was authenticated with the provider
#    (the DKIM/SPF records from C3.0). A From: the provider has not been told
#    about is the case where nothing errors, nothing bounces, and the message
#    quietly lands in spam — "he says he never got it".
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", required=True)
# Django's From: for error mail. Set for completeness; see LOGGING below for why
# no error mail is actually sent.
SERVER_EMAIL = env("SERVER_EMAIL", DEFAULT_FROM_EMAIL)

# --- C3.8 · Error visibility ------------------------------------------------
# Without this, DEBUG=False means an unhandled exception is a bare 500 that
# nobody hears about: Django's own logging config only writes to the console
# when DEBUG is on, so the traceback goes nowhere at all.
#
# ⚠️ send_default_pii stays off. This database holds minors' names, dates of
#    birth and addresses, and there is no version of "it helps with debugging"
#    that justifies a second copy of that sitting in a third party's service.
if env("SENTRY_DSN", ""):
    sentry_sdk.init(
        dsn=env("SENTRY_DSN"),
        send_default_pii=False,
        # No performance tracing: the free tier's budget is for errors, and a
        # sampled trace of every request would spend it on ordinary Saturdays.
        traces_sample_rate=0,
    )

# The line that is still there when Sentry is not: an empty DSN, an outage, a
# used-up quota. Render captures stderr, so this is readable without any account
# anywhere.
#
# ⚠️ **No mail_admins handler, on purpose**, which is a departure from Django's
#    default. Error mail and the volunteers' password resets come out of the
#    same daily allowance on the mail plan, and the day something breaks is
#    exactly the day it would break repeatedly — spending the allowance that the
#    people locked out of their accounts need. Errors go to the log and to
#    Sentry; neither of those can run out and lock somebody out.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "{levelname} {asctime} {name} {message}", "style": "{"},
    },
    "handlers": {
        "stderr": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "loggers": {
        # The one that carries unhandled exceptions, with the traceback.
        "django.request": {"handlers": ["stderr"], "level": "ERROR", "propagate": False},
        # Anything else the project logs on its way past — HomePage's failed
        # object deletions land here, and those are written to be non-fatal
        # precisely because somebody is expected to read the log later.
        "": {"handlers": ["stderr"], "level": "WARNING"},
    },
}

# --- Rate limiting ----------------------------------------------------------
# ⚠️ Set here rather than left to an environment variable, because forgetting it
#    breaks the registration limit **silently and in the worse direction**. This
#    deployment runs behind a reverse proxy (Render), so REMOTE_ADDR is the
#    proxy: every visitor on earth would land in one bucket, the hour's first
#    twenty registrations would use up everybody's allowance, and the page would
#    start refusing real volunteers with nothing anywhere reporting why.
#
#    See core/ratelimit.py::client_ip for why the header is read from the end and
#    not the beginning. If this app is ever run in production with nothing in
#    front of it, this line has to come back out — trusting the header with no
#    proxy is a limit any caller can step around.
TRUST_PROXY_CLIENT_IP = True

# --- Static files -----------------------------------------------------------
# Hashed filenames plus a manifest, so the built CSS and JS can be cached
# forever and a deploy invalidates them by changing the name. Compressed, so
# whitenoise serves a pre-made .gz/.br rather than compressing per request.
#
# ⚠️ **Production only, deliberately.** Turning this on in dev means every
#    template render needs a manifest that only `collectstatic` writes, so the
#    first page opened after a fresh clone raises "Missing staticfiles manifest
#    entry for 'css/app.css'" — an error about static files, arriving while you
#    are working on something else entirely.
#
# ⚠️ The Manifest half is also the reason the Tailwind and esbuild **sources
#    live in `assets/` rather than under `static/`**, which is a deliberate
#    deviation from 04-roadmap.md's C1.1. It parses `@import` and `url()` in
#    every collected CSS file and fails on anything it cannot resolve, and the
#    source stylesheet opens with `@import "tailwindcss"` — not a file.
#    Collecting it raises `MissingFileError: The file 'src/tailwindcss' could
#    not be found` and the deploy stops there. Reproduced rather than assumed;
#    经过记在 04-roadmap.md 的计划外记录.
# --- Uploaded files: Cloudflare R2 (C3.5, 2026-08-06) ------------------------
#
# Which alias means what, and why there are three of them rather than one, is
# written out over STORAGES in base.py. This file only supplies the credentials
# and the two policies that differ between them: whether the URL is signed, and
# how long the signature lasts.
#
# ⚠️ **R2, not S3, and the differences bite:**
#
#    · `region_name = "auto"`. R2 has no regions, but botocore refuses to sign
#      without one — the failure is a signature error, not a "missing region".
#    · `default_acl = None`. R2 rejects any request carrying an ACL header, and
#      django-storages sends one whenever this is set. An upload with an ACL
#      fails; an upload without one works. There is no ACL that means "public"
#      on R2 — public access is a property of the bucket, set in the dashboard.
#    · `request_checksum_calculation = "when_required"`. botocore ≥ 1.36 adds a
#      streaming CRC32 trailer by default, which S3-compatible stores have
#      historically rejected with an opaque `XAmzContentSHA256Mismatch`. Asking
#      for checksums only when the API requires them keeps uploads on the path
#      every S3-compatible store supports.
#
# ⚠️ `file_overwrite = False` everywhere. Uploads are already named with a uuid
#    by the normalise_* functions, so a collision means something is wrong; with
#    overwrite on, that collision would silently replace somebody else's photo.
_R2_ENDPOINT = env("R2_ENDPOINT_URL", required=True)
_R2_ACCESS_KEY = env("R2_ACCESS_KEY_ID", required=True)
_R2_SECRET_KEY = env("R2_SECRET_ACCESS_KEY", required=True)


def _r2(bucket, *, public=False, expire=None):
    """One R2 bucket as a STORAGES entry. `public` drops the signature."""
    options = {
        "bucket_name": bucket,
        "endpoint_url": _R2_ENDPOINT,
        "access_key": _R2_ACCESS_KEY,
        "secret_key": _R2_SECRET_KEY,
        "region_name": "auto",
        "signature_version": "s3v4",
        "default_acl": None,
        "file_overwrite": False,
        "client_config": Config(
            signature_version="s3v4",
            request_checksum_calculation="when_required",
        ),
    }
    if public:
        # Served straight off the bucket's own domain, unsigned and cacheable.
        options["querystring_auth"] = False
        options["custom_domain"] = env("R2_PUBLIC_BASE_HOST", required=True)
    else:
        # ⚠️ This is what makes @login_required mean anything for a photo. With
        #    an unsigned URL the login gate protects the *page* and not the
        #    file: one copied link and the picture is public for good, which is
        #    not a decision the person who uploaded it ever made.
        #
        # ⚠️ An hour, not a day and not a minute. Long enough that a photo
        #    opened from a page left sitting overnight has already expired
        #    (rather than the link outliving the session by a day), short enough
        #    that nobody watches a lightbox 404 mid-browse.
        options["querystring_auth"] = True
        options["querystring_expire"] = expire or 3600
    return {"BACKEND": "storages.backends.s3.S3Storage", "OPTIONS": options}


STORAGES = {
    # Event images. ⚠️ Versioning **off** on this bucket — see base.py.
    "default": _r2(env("R2_BUCKET_EVENT_IMAGES", required=True)),
    # Memories. ⚠️ Versioning **on** on this bucket — see base.py.
    "memories": _r2(env("R2_BUCKET_MEMORIES", required=True)),
    # The front page's picture and video.
    "public": _r2(env("R2_BUCKET_PUBLIC", required=True), public=True),
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
