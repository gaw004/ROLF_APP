"""Where the platform asks "is this instance able to serve?".

One line, its own module, and no imports at all — because **config/settings/
prod.py has to read it**, and settings are evaluated before Django's app
registry exists. Importing anything that reaches a model from here would turn a
constant into "Apps aren't loaded yet" at boot.

Three files have to agree on this path and two of them fail silently when they
disagree: the URLconf (a 404 the platform reads as an unhealthy instance),
prod.py's SECURE_REDIRECT_EXEMPT (a 301 it reads the same way), and
render.yaml's healthCheckPath. core.tests.HealthCheckGuardTests keeps them
agreed, because the failure they produce — "health check timed out" — describes
none of the three.
"""

HEALTH_PATH = "healthz/"
