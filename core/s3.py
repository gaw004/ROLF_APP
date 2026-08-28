"""One boto3 session for all three R2 buckets, instead of one per bucket per thread.

🔴 **The single largest thing this deployment spends memory on**, and it was
   invisible for as long as the only signal was the platform's chart. The
   working, the measurements and the three misdiagnoses are in revisions.md
   五十一; the short version:

   django-storages keeps `S3Storage.connection` in a `threading.local()` and
   `_create_session()` builds a **brand new `boto3.Session`** every time it is
   called. gthread's threads live as long as the worker, so the first time a
   given thread serves a page touching a given bucket it builds a session and
   keeps it for ever: `4 threads × 3 buckets = 12 sessions per worker`, each
   carrying its own parsed copy of botocore's S3 service model.

   Measured on the real production settings, per worker, fully warmed:
   **200.3 MB as it stands, 94.1 MB sharing one session.** Across two workers
   that is the difference between a service sitting at 93% of a 512MB limit and
   one sitting at just over half.

   ⚠️ Not a leak — it stops at twelve. That is exactly why nothing caught it:
      it has no runaway to alarm on, it simply settles 35MB under the ceiling.

⚠️ **The three buckets differ only by name.** Same endpoint, same key, same
   secret (see `_r2()` in config/settings/prod.py). Twelve sessions to one
   endpoint holding one credential is not a tuning parameter, it is an
   accident; one session is the ordinary shape of this.

🔴 **Its own module, and not core/storages.py, because of what imports what.**
   That file is reached from `FileField(storage=...)` callables, so it loads
   with the models — on every management command, every test run and every
   development server. Putting `import boto3` there cost **+17.6 MB on
   `django.setup()` in development**, measured, for a class development never
   instantiates. Here nothing imports it until `prod.py`'s dotted BACKEND string
   is resolved, which happens only when the storage is first built.
   `core.tests.SharedS3SessionTests.test_an_ordinary_boot_does_not_import_boto3`
   pins that, because the regression is silent.

⚠️ **This is a stopgap over an upstream defect**, and should be sent there:
   `S3Storage._create_session` building a session per instance per thread costs
   every django-storages user this, not just us. The subclass stays until that
   lands.
"""

import threading

import boto3
from storages.backends.s3 import S3Storage

#: Guards **resource creation**, not just session creation — which is the whole
#: point. boto3's documented hazard with a shared Session is concurrent
#: `client`/`resource` construction, because that mutates the session's own
#: component and loader caches. Building a session under a lock and then
#: creating resources from it outside one would leave the real race untouched.
#:
#: 🔴 **RLock, and a plain Lock here is a deadlock** — caught the moment the
#:    multi-threaded guard below was first run, which is what that guard is for.
#:    `connection` takes it and then calls upstream's builder, which calls
#:    `_create_session()`, which takes it again on the same thread. With a plain
#:    Lock the first request to touch a bucket hangs for ever, and the symptom
#:    is a worker that stops answering rather than an error.
_session_lock = threading.RLock()

#: Keyed by credential, so a second set of keys (the backup bucket, if it ever
#: came through Django) would get its own session rather than silently reusing
#: somebody else's.
_sessions = {}


class SharedSessionS3Storage(S3Storage):
    """S3Storage that builds one boto3 Session per credential, not per thread.

    ⚠️ Named and pointed at from `prod.py`, rather than monkey-patching
       S3Storage. A patch would apply to storages this file has never heard of,
       and would not be greppable from the settings that chose it.

    ⚠️ Both hooks below are upstream internals, so the failure mode when
       django-storages changes them is **silence**: the subclass stops taking
       effect and memory climbs back to where it was, with nothing red.
       `core.tests.SharedS3SessionTests` is what turns that into a failing test
       — it asserts the *property* (N threads, three buckets, one session), not
       a byte count, because the count moves with the platform and the property
       does not.
    """

    def _create_session(self):
        """The shared one. Upstream's two branches, cached instead of rebuilt.

        ⚠️ **The `session_profile` branch is upstream's and is kept**, which the
           first version of this dropped. Upstream refuses `session_profile`
           together with explicit keys (`ImproperlyConfigured`, s3.py:325), so
           profile-only is the supported shape — and in it `access_key` and
           `secret_key` are None. Building a keyless `boto3.Session` there does
           not fail: it falls through to botocore's default credential chain
           (environment, instance metadata) and authenticates as **somebody
           else**, or fails with an error that never names the profile.

        ⚠️ And the profile is part of the cache key for the same reason. Keyed
           on the credentials alone, every profile collapses to
           `(None, None, None)` — two storages configured with two different
           profiles would share one session, which is the same silent
           wrong-credentials failure arriving by a second route.

        `_r2()` sets keys and no profile, so neither of these is reachable from
        this deployment today. They are here because this class is written to
        be sent upstream, where both shapes are ordinary.
        """
        key = (self.session_profile, self.access_key, self.secret_key,
               self.security_token)
        with _session_lock:
            session = _sessions.get(key)
            if session is None:
                if self.session_profile:
                    session = boto3.Session(profile_name=self.session_profile)
                else:
                    session = boto3.Session(
                        aws_access_key_id=self.access_key,
                        aws_secret_access_key=self.secret_key,
                        aws_session_token=self.security_token,
                    )
                _sessions[key] = session
            return session

    @property
    def connection(self):
        """Upstream's, with the one-time construction serialised.

        ⚠️ Double-checked on purpose: a thread that already has its connection
           never touches the lock, so the lock costs nothing once the worker is
           warm — it exists for the first request on each thread and no other.

        ⚠️ `super()` rather than a copy of upstream's four lines. Copying them
           would be a second implementation of the thing this class exists to
           adjust, free to drift from the one it is adjusting.
        """
        if getattr(self._connections, "connection", None) is not None:
            return S3Storage.connection.fget(self)
        with _session_lock:
            return S3Storage.connection.fget(self)

    @property
    def unsigned_connection(self):
        """The same treatment for the unsigned client the public bucket uses."""
        if getattr(self._unsigned_connections, "connection", None) is not None:
            return S3Storage.unsigned_connection.fget(self)
        with _session_lock:
            return S3Storage.unsigned_connection.fget(self)
