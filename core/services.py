"""Work that is about the site itself rather than about any one app.

Today that is one subject: **the objects in the public bucket that the front
page puts there**, and the two questions worth asking about them — "which ones
did that save just stop pointing at" (answered by the caller, `HomePage.
_superseded_media`, and acted on here) and "which ones is nothing pointing at
any more" (`orphaned_home_media`, for the sweep).

⚠️ The two halves are deliberately different in temperament. `discard_media`
   deletes what the model has just told it to delete and is allowed to fail
   quietly; `orphaned_home_media` **only ever reports**, and something with a
   human in it decides whether to act. Nothing in this module deletes a file
   because it worked out for itself that the file was unwanted.
"""

import logging

logger = logging.getLogger(__name__)


def discard_media(items):
    """Delete `[(storage, name)]`. Returns what went. **Never raises.**

    ⚠️ Swallowing the error is the point, not an oversight. This runs after a
       save has already committed, so there is nothing left to roll back and
       nothing useful to tell the person who pressed the button: they changed
       the front page and the front page changed. Letting a slow R2 turn that
       into a 500 would mean the picture *is* live and the page says it failed
       — after which they press save again, which is exactly the sequence that
       produces two of everything.

    ⚠️ It is logged with `exception()`, so the traceback survives even though
       the request does not see it. A leaked object is cheap and invisible;
       a leaked object that leaves no trace is how you end up unable to say
       whether the sweep has anything to do.
    """
    gone = []
    for storage, name in items:
        try:
            storage.delete(name)
        except Exception:
            # ⚠️ Not `raise`. See the docstring — and note this catches the
            #    "already deleted" case for free, which is what makes calling
            #    this twice harmless.
            logger.exception("Could not delete superseded media %r", name)
            continue
        gone.append(name)
    return gone


def orphaned_home_media():
    """Object keys under the front page's prefix that no row points at.

    ⚠️ **Reports, never deletes.** The caller is a management command that
       prints this and stops unless it is told twice; see
       core/management/commands/purge_orphaned_home_media.py for why deleting
       on sight would be the wrong shape for this particular list.

    ⚠️ The live set is read from `HomePage.MEDIA_FIELDS` rather than named here.
       A third media field added to the front page and not added there would
       make this function call it rubbish and offer to delete it — the one bug
       in this file that costs something irreversible.

    ⚠️ Names are returned **prefixed** (`home/x.jpg`), the way the columns store
       them, so that what is printed can be compared to what is in the database
       without anybody having to reassemble a path by hand.
    """
    from .models import HomePage

    storage = HomePage._meta.get_field("hero_image").storage
    prefix = HomePage.MEDIA_DIR
    try:
        _, files = storage.listdir(prefix)
    except FileNotFoundError:
        # Nothing has ever been uploaded — the prefix does not exist yet. Not
        # an error: it is the state a fresh install is in.
        return []

    page = HomePage.current()
    live = {getattr(page, field).name for field in HomePage.MEDIA_FIELDS}
    return sorted(
        name for name in (f"{prefix}/{f}" for f in files) if name not in live
    )


def restamp_home_media():
    """Rewrite the headers on the front page's live objects. Returns the keys.

    ⚠️ **This exists because `object_parameters` is an upload-time setting.**
       django-storages sends it as ExtraArgs from `_save`, so giving the public
       bucket a `Cache-Control` (config/settings/prod.py) changes every object
       written *after* the deploy and none of the ones already there. The
       picture on the front page today is one of the ones already there, and it
       is also the only one anybody downloads — so without this the fix ships,
       the setting is right, and the symptom does not move.

    ⚠️ A server-side copy onto itself with `MetadataDirective="REPLACE"`, not a
       re-upload. The bytes never leave R2, so this costs no bandwidth and —
       more to the point — **the key does not change**. A re-upload would take a
       collision suffix and hand the front page a new URL, which is a database
       write dressed up as a cache fix.

    ⚠️ `ContentType` is restated because REPLACE means *replace*: every header
       not named in the call is dropped, and an image served as
       `application/octet-stream` downloads instead of displaying. It is read
       back off the object rather than guessed from the extension.

    ⚠️ The value comes from `get_object_parameters`, never from a constant
       here. A second copy of the header is a second thing to change, and the
       two disagreeing fails silently: new uploads cached one way, the
       backfilled ones another, and nothing anywhere saying so.

    ⚠️ Only what the row points at. Orphans under the same prefix are
       `purge_orphaned_home_media`'s business, and stamping a file nobody serves
       so that it caches for a month is work in the wrong direction.

    ⚠️ **`MEDIA_FIELDS`, so it covers whatever that tuple grows to** — as of
       2026-08-31 that is the picture, the video and the three responsive rungs.
       The rungs already carry the header (they are written after the setting
       landed, by `FieldFile.save()`), so for them this is a no-op that costs
       two API calls each. Walking the tuple rather than naming two fields is
       what keeps a sixth object from being the one nobody remembers.

    ⚠️ The `connection` lookup is **outside** the try, so running this against
       development storage raises rather than reporting nothing to do. That is
       the honest answer: the local disk serves no headers, and a run that
       quietly said "0 objects" would look exactly like a run against a bucket
       that is already correct.
    """
    from .models import HomePage

    page = HomePage.current()
    storage = HomePage._meta.get_field("hero_image").storage
    client = storage.connection.meta.client
    bucket = storage.bucket_name

    stamped = []
    for field in HomePage.MEDIA_FIELDS:
        name = getattr(page, field).name
        if not name:
            continue
        try:
            head = client.head_object(Bucket=bucket, Key=name)
            # ⚠️ Merged rather than passed alongside as keywords, so that the
            #    configured parameters win and a second `ContentType` appearing
            #    in them one day is an override instead of a TypeError.
            params = {"ContentType": head.get("ContentType",
                                              "application/octet-stream")}
            params.update(storage.get_object_parameters(name))
            client.copy_object(
                Bucket=bucket, Key=name,
                CopySource={"Bucket": bucket, "Key": name},
                MetadataDirective="REPLACE", **params,
            )
        except Exception:
            # ⚠️ Logged and carried, like discard_media: hero_video failing is
            #    no reason to leave hero_image — the big one, and the one this
            #    was written for — unstamped.
            logger.exception("Could not restamp %r", name)
            continue
        stamped.append(name)
    return stamped
