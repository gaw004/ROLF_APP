"""Publishing a notice, and taking one down. D18: the logic is here, not in a view.

Two functions, and both of them are about the same column pair — which is the
whole of what a notice's lifecycle is. There is no approval, no recipients, no
delivery: this is a board people read, not a channel that pushes at them. That
boundary is deliberate and it is `deferred.md`'s — mass email (newsletters,
appeals, "全员公告") is deferred there because it drags in unsubscribes, list
management and compliance. A board pays none of that and answers the same need.
"""

from django.db import transaction

from core.timeutils import local_now


@transaction.atomic
def publish(notice, *, now=None):
    """Put it on the board.

    ⚠️ `starts_showing` is left exactly as it is. Somebody may be publishing a
       notice written to go up on Monday, and quietly stamping it with now would
       throw that away — silently, because a notice that went up early looks
       just like one that went up on time.
    """
    from .models import Notice

    notice.status = Notice.Status.PUBLISHED
    notice.save(update_fields=["status", "updated_at"])
    return notice


@transaction.atomic
def take_down(notice, *, now=None):
    """Off the board now, ahead of its date. Two cases, because they differ.

    🔴 Never deletes the row, and there is no `withdrawn` status.

    ⚠️ **Whether anybody could have read it decides which case this is**, and
       that is not a technicality — it is the difference between a notice with
       a history and a notice that never happened:

       · already up → `stops_showing` moves to now. It stays PUBLISHED and
         lands in `past()`, because people saw it and "what did that say" is a
         question they are entitled to ask;
       · not up yet (scheduled ahead, `now < starts_showing`) → back to DRAFT.
         Nobody ever saw it, so there is nothing to have a past. DRAFT already
         means exactly "not on the board", which is why this needs no new
         status.

    ⚠️ The first draft of this pulled **both** dates to now for the second case,
       and the database would have refused it outright —
       `notice_comes_down_after_it_goes_up` wants a window with length in it, so
       a zero-length window is an IntegrityError on a button labelled "Take
       down". Worth recording: the constraint caught a design mistake, not a
       typo. Moving dates to fake a state is what it was protecting against, and
       DRAFT was the honest answer sitting there the whole time.
    """
    from .models import Notice

    now = now or local_now()
    if notice.starts_showing > now:
        notice.status = Notice.Status.DRAFT
        notice.save(update_fields=["status", "updated_at"])
        return notice
    notice.stops_showing = now
    notice.save(update_fields=["stops_showing", "updated_at"])
    return notice
