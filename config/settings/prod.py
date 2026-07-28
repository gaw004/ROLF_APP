"""Production settings.

Deliberately thin for now. The deployment hardening — SECURE_SSL_REDIRECT,
HSTS, SESSION_COOKIE_SECURE, and getting `manage.py check --deploy` to run
clean — belongs to Phase D and is not being guessed at here.

Everything that matters in production (SECRET_KEY, DEBUG, ALLOWED_HOSTS,
DATABASE_URL) already comes from the environment via base.py.
"""

from .base import *  # noqa: F403
