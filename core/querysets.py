"""QuerySet behaviour shared by every table that carries a start/end date."""

from django.db import models
from django.db.models import Q

from core.timeutils import local_today


class DateRangeQuerySet(models.QuerySet):
    """The "in effect on a given day" predicate, defined exactly once.

    Assignment and Relationship both use it. That Q expression ends up behind
    the ministry page, the active-headcount numbers and several admin filters —
    written out ten times, one of them would be wrong, and a wrong one does not
    raise, it just reports a number that is quietly off.

    ⚠️ This layer knows about dates only, never about status. Assignment adds
       its own serving() (= active() AND status=active) on top; do not push
       status down into here — Relationship has no status, a relationship is
       never "suspended". See goal.md「Assignment.status」.
    """

    def active(self, on=None):
        """Rows whose date range covers `on` (default: today, foundation time)."""
        # `on` has to be resolved at call time. Writing this as
        # `def active(self, on=local_today())` freezes the date at import, which
        # on a long-lived gunicorn worker drifts further off every day.
        on = on or local_today()
        return self.filter(
            # The start_date half is not optional: with only the end_date test,
            # a row starting 2027-01-01 with no end date counts as active today.
            (Q(start_date__isnull=True) | Q(start_date__lte=on))
            & (Q(end_date__isnull=True) | Q(end_date__gte=on))
        )


class DateRangeMixin:
    """The same predicate for a single row already in memory.

    ⚠️ This and DateRangeQuerySet.active() above are two implementations of one
       rule. They sit in the same file precisely so that "change one, change the
       other" is a glance rather than a promise — the alternative,
       `type(self).objects.filter(pk=self.pk).active().exists()`, costs one query
       per row, which in an admin changelist is an N+1.
    """

    @property
    def is_currently_active(self):
        on = local_today()
        return (
            (self.start_date is None or self.start_date <= on)
            and (self.end_date is None or self.end_date >= on)
        )
