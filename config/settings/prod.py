"""Production settings.

Still thin. The deployment hardening — SECURE_SSL_REDIRECT, HSTS,
SESSION_COOKIE_SECURE, and getting `manage.py check --deploy` to run clean —
is C3.4, and is not being guessed at here.

Everything that matters in production (SECRET_KEY, DEBUG, ALLOWED_HOSTS,
DATABASE_URL) already comes from the environment via base.py.
"""

from .base import *  # noqa: F403

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
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
