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
