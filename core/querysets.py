"""QuerySet behaviour shared by every table that carries a start/end date."""

from django.db import models
from django.db.models import Q

from core.timeutils import local_today


def in_effect_on(on=None, prefix=""):
    """The "covers this day" predicate as a Q, so related tables can reuse it.

    `prefix` walks a relation: in_effect_on(prefix="assignments__") is what
    Position uses to count the people currently in a post, and what its
    vacancy query is defined against. Without the prefix the expression would
    have to be spelled out a second time inside the aggregate — and the second
    copy is the one that quietly disagrees with the first.

    `on` is resolved here, at call time. A default argument holding a date
    would freeze at import and drift further off every day a worker stays up.

    ⚠️ `on` may also be a **database expression** rather than a date — an
       OuterRef onto a day the outer query annotated, which is how a correlated
       subquery asks this of a set of events that each have their own day
       (events.services.on_the_books_exists). The `or` below is safe with one:
       an expression object is truthy, so it is never silently swapped for
       today. Checked rather than assumed.
    """
    on = on or local_today()
    start, end = f"{prefix}start_date", f"{prefix}end_date"
    return (
        # The start half is not optional: with only the end test, a row
        # starting 2027-01-01 with no end date counts as in effect today.
        (Q(**{f"{start}__isnull": True}) | Q(**{f"{start}__lte": on}))
        & (Q(**{f"{end}__isnull": True}) | Q(**{f"{end}__gte": on}))
    )


class DateRangeQuerySet(models.QuerySet):
    """The "in effect on a given day" predicate, defined exactly once.

    Assignment and MinistryRole both use it. That Q expression ends up behind
    the ministry page, the active-headcount numbers and several admin filters —
    written out ten times, one of them would be wrong, and a wrong one does not
    raise, it just reports a number that is quietly off.

    ⚠️ This layer knows about dates only, never about status. Assignment adds
       its own serving() (= active() AND status=active) on top; do not push
       status down into here — MinistryRole has no status, a grant of authority
       is never "suspended". See goal.md「Assignment.status」.
    """

    def active(self, on=None):
        """Rows whose date range covers `on` (default: today, foundation time)."""
        # The predicate itself lives in in_effect_on() above, because Position
        # needs the same one applied across a relation. One definition, two
        # callers — not two definitions that agree today.
        return self.filter(in_effect_on(on))


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
