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
    """Gallery photos. Private bucket, versioning on, URLs signed for an hour.

    ⚠️ Not the `default` bucket, and the reason is a policy that cannot be held
       by one bucket twice: event images need versioning **off** so that
       purge_event_images really deletes, and these need it **on** because they
       are the only files in the system that no backup can bring back.
    """
    return storages["memories"]


def public_storage():
    """The front page's picture and video. Public bucket, unsigned URLs.

    ⚠️ Public on purpose. The front page is the only page that needs no login,
       so a signed URL would protect nothing while making the largest file the
       site serves uncacheable at the CDN.
    """
    return storages["public"]
