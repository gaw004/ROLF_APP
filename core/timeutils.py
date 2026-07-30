"""The only place in the project allowed to ask what "now" is. See goal.md D16.

Three spellings of "today" exist in a USE_TZ=True project and two of them are
wrong — wrong in the way that does not raise, it just quietly reports the wrong
day around the boundary. Keeping the correct one in a single function means the
grep guard in core/tests.py can enforce it for everyone else.
"""

import datetime

from django.utils import timezone


def local_now():
    """The current instant, timezone-aware.

    A thin wrapper on purpose. Having it here means a report that needs both a
    date and an instant takes both from one module, and tests freeze one clock
    rather than hunting for every caller of timezone.now().
    """
    return timezone.now()


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


def day_start(day):
    """Midnight at the start of `day`, in the foundation's timezone.

    Report boundaries have to be built this way rather than from a naive
    datetime: slicing "this month" in UTC puts the last evening of the month
    into the next one, and R1/R2 would be off by however many events ran after
    5pm on the 31st. Nothing raises — the count is just wrong.
    """
    return timezone.make_aware(datetime.datetime.combine(day, datetime.time.min))


def month_bounds(year, month):
    """[start, end) covering one calendar month, in the foundation's timezone.

    Half-open on purpose: an event starting at 23:59:59 on the last day belongs
    to this month, and `<= end_of_month` written with a date would drop it.
    """
    start = datetime.date(year, month, 1)
    end = datetime.date(year + (month == 12), month % 12 + 1, 1)
    return day_start(start), day_start(end)
