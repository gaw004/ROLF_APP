"""Which bucket a given upload goes to.

One function per STORAGES alias that a model field names explicitly. Fields
that name nothing keep Django's `default` — that is the event-images bucket,
and it stays implicit because it was here first and every existing migration
already records it that way.

⚠️ **These are passed to `FileField(storage=...)` as callables, never as
   `storages["memories"]` directly.** Django serialises whatever it is given
   into the migration, so handing it a Storage *instance* writes that instance's
   configuration — bucket name, endpoint, and in the worst case the access key —
   into a migration file that is committed to git and replayed on every
   environment. A callable is serialised as a dotted path and evaluated again on
   each side, which is what lets development keep these on the local disk while
   production has them on R2 **without the migrations differing**.

⚠️ Evaluated when the model class is loaded, not per request. Nothing here may
   depend on the request, the user, or the time.
"""

from django.core.files.storage import storages


def memories_storage():
    """Gallery photos. Private bucket, URLs signed for an hour.

    ⚠️ Not the `default` bucket, and the reason is what is allowed to delete
       from each: purge_event_images sweeps the event-images bucket daily, and
       whatever automatic deletion that bucket is given must never be able to
       reach these — they are the only files in the system that no backup can
       bring back.

    ⚠️ **2026-08-12:** this used to say "versioning on", and that was the stated
       reason for the split. **R2 has no object versioning.** Nothing undoes a
       delete here now; see the note over STORAGES in config/settings/base.py.
    """
    return storages["memories"]


def public_storage():
    """The front page's picture and video. Public bucket, unsigned URLs.

    ⚠️ Public on purpose. The front page is the only page that needs no login,
       so a signed URL would protect nothing while making the largest file the
       site serves uncacheable at the CDN.
    """
    return storages["public"]
