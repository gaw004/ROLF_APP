"""The access log, minus the one request nobody is ever going to read.

⚠️ **This is a fourth file that has to agree with `core.health.HEALTH_PATH`**,
   and it is the only one of the four that fails *loudly* when it disagrees —
   it simply stops silencing anything and the log fills up again. That is still
   worth a guard, because "the log filled up again" is not a thing anybody
   notices until the day they need to read it. `core.tests.HealthCheckGuardTests`
   keeps all four agreed.

The problem it solves: `render.yaml`'s start command passes
`--access-logfile -` so Render can capture request logs, and Render's own
health check hits `healthCheckPath` continuously. On a service that is mostly
idle — which a pilot deployment is — the health check *is* the log. Anything
worth reading (a 500, a slow report, who was on the site when something broke)
is buried under thousands of identical 200s.

⚠️ **Silence, not aggregation.** An earlier sketch had this counting requests
   and printing one rolled-up line a day. It does not work here and the reason
   is worth writing down rather than rediscovering: the counters would live in
   the worker process, there are two of them, and gunicorn is given no
   `--max-requests`, so the only thing that resets them is a restart — a
   deploy, or the OOM kill that 三十五 documents. A "daily" summary that is
   silently truncated at every restart is worse than no summary, because it
   reads like a complete one.

⚠️ Imports nothing from Django, on purpose. Gunicorn loads this class while
   parsing its own configuration, which is before the app — and therefore
   before the settings — exists. `core.health` is importable that early
   precisely because it imports nothing itself; that is what its module
   docstring is about.
"""

from gunicorn.glogging import Logger

from core.health import HEALTH_PATH

#: Both spellings, because both occur. `render.yaml` asks for `/healthz` while
#: the URLconf serves `healthz/`, so the platform's request is answered with
#: APPEND_SLASH's 301 (which Render accepts — it reads 2xx and 3xx alike as
#: healthy). Matching only the trailing-slash form would silence a line that is
#: never logged and leave the one that is.
_HEALTH = "/" + HEALTH_PATH.strip("/")


class QuietHealthCheckLogger(Logger):
    """Gunicorn's own logger, with the health check's access line dropped.

    ⚠️ Only `access()` is touched. The error log is untouched, so a traceback
       coming out of the health check — if it ever grew a body that could raise
       one — is still reported. Silencing a request is not the same as not
       watching it.
    """

    def access(self, resp, req, environ, request_time):
        if environ.get("PATH_INFO", "").rstrip("/") == _HEALTH.rstrip("/"):
            return
        super().access(resp, req, environ, request_time)
