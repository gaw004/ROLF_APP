"""The only place in the project allowed to ask what "now" is. See goal.md D16.

Three spellings of "today" exist in a USE_TZ=True project and two of them are
wrong — wrong in the way that does not raise, it just quietly reports the wrong
day around the boundary. Keeping the correct one in a single function means the
grep guard in core/tests.py can enforce it for everyone else.
"""

from django.utils import timezone


def local_today():
    """Today in the foundation's timezone (settings.TIME_ZONE = America/Los_Angeles).

    Do NOT use datetime.date.today()  — it follows the server's local timezone,
                                        which is UTC on Render and PT on this
                                        laptop, so the two disagree.
    Do NOT use timezone.now().date()  — that is the UTC date. At 5pm Pacific it
                                        has already rolled over to tomorrow, so
                                        "active today" flips a day early.
    """
    return timezone.localdate()
