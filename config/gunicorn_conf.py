"""Gunicorn's config file. Today it holds one thing: the memory probe.

⚠️ **A `--config` file rather than more flags on the start command**, because a
   `post_fork` hook has no command-line spelling — and `post_fork` is exactly
   the semantics wanted here: it runs in each **worker**, after the fork, and
   only under gunicorn. The alternatives all run somewhere they should not:
   `AppConfig.ready()` fires in every management command and every test run,
   and a thread started as an import side effect in `wsgi.py` fires wherever
   anything imports it.

⚠️ It does not set `logger_class`. That stays on the start command in
   render.yaml next to `--access-logfile`, which is the flag it exists to make
   useful — `core.tests.HealthCheckGuardTests` reads it there, and moving it
   here would put the two halves of one decision in two files.

Why the probe exists at all is written over `core/memory.py`: 2026-08-28 took
four rounds of investigation because the only signal was a single number on a
dashboard, and three different readings of the same system disagreed by a
factor of 2.5. This turns the next one into a grep.
"""

import os
import threading

from core.memory import format_snapshot

#: How often each worker prints a line. ⚠️ **Deliberately not in render.yaml**,
#: for the reason written over REGISTRATION_RATELIMIT_* in that file: anything
#: named there is rewritten on every blueprint sync, so a value meant to be
#: turned up while chasing something — or turned off — has to be a variable the
#: blueprint does not mention.
#:
#: ⚠️ Five minutes, and the number follows from the thing being watched: the
#: ratchet found on 2026-08-28 climbed over hours, so five minutes draws its
#: shape with room to spare, at 288 lines a day per worker. That is a readable
#: quantity in a log that no longer carries the health check, and would not have
#: been in the one that did.
#:
#: 0 turns it off, and that is a supported value rather than a way to break it.
DEFAULT_PROBE_SECONDS = 300


def _probe_seconds(raw):
    """The interval, or the default when the variable is unusable.

    🔴 **Never raises, and that is the entire reason this is a function.**
       Gunicorn executes this file inside `Application.get_config_from_filename`,
       which catches *every* exception, prints "Failed to read config file" and
       calls `sys.exit(1)` — so an exception on this line is not a bad probe
       interval, it is **the web service failing to boot**, reported by an error
       that names a config file rather than the variable that caused it.

       And the way in is the knob being used exactly as intended: an operator
       turning the probe off from the dashboard clears the value, Render passes
       a declared-but-empty variable as `""`, and `int("")` raises ValueError.
       `5m` and `600s` do the same. An observability probe that can take the
       site down has inverted its whole purpose — this one may only ever
       degrade to silence.

    ⚠️ A bad value falls back to the default rather than to 0. "Off" has to be
       something somebody *asked* for; a typo silently disabling the thing you
       are mid-investigation with is the failure this project keeps convicting.
    """
    try:
        return int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PROBE_SECONDS


PROBE_SECONDS = _probe_seconds(os.environ.get("MEMORY_PROBE_SECONDS", DEFAULT_PROBE_SECONDS))


def _probe(log, seconds):
    """Print one reading, every `seconds`, until the process goes away.

    ⚠️ The whole body is guarded. A probe is an observer: if reading /proc ever
       raises something core.memory does not expect, the thread dies quietly and
       the site keeps serving — the opposite trade from the code it watches.
       It says so once rather than every five minutes, because a probe that
       floods the log has destroyed the thing it was added to protect.
    """
    while True:
        try:
            log.info(format_snapshot())
        except Exception as error:                       # noqa: BLE001
            log.warning("[memory] probe stopped: %r", error)
            return
        # ⚠️ Event.wait rather than time.sleep so a future shutdown hook can
        #    cut it short; today nothing sets it, and daemon=True is what
        #    actually stops it from holding the worker open.
        if _stop.wait(seconds):
            return


_stop = threading.Event()


def post_fork(server, worker):
    """One probe per worker.

    ⚠️ Per worker, not per process. The master does not serve requests, and on
       2026-08-28 its figure was the one thing that did not move all afternoon —
       which is what proved the growth belonged to request handling.

    ⚠️ `worker.log`, not `logging.getLogger(...)`. This is operational output
       about the process, not about the application, and Django's LOGGING keeps
       the root logger at WARNING in production — an INFO line through that
       route would be configured out and nobody would know.
    """
    if PROBE_SECONDS <= 0:
        return
    threading.Thread(
        target=_probe, args=(worker.log, PROBE_SECONDS),
        name="memory-probe", daemon=True,
    ).start()
