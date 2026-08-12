"""Production settings.

Still thin. The deployment hardening — SECURE_SSL_REDIRECT, HSTS,
SESSION_COOKIE_SECURE, and getting `manage.py check --deploy` to run clean —
is C3.4, and is not being guessed at here.

Everything that matters in production (SECRET_KEY, DEBUG, ALLOWED_HOSTS,
DATABASE_URL) already comes from the environment via base.py.
"""

from botocore.config import Config

from .base import *  # noqa: F403
from .base import env

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
