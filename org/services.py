"""Reading the org chart. Permanent asset — see goal.md D18.

⚠️ This module is the only place in the project that walks the reporting
   chain. core/tests.py greps for a second one and fails if it finds it. The
   reason is not tidiness: a loop that spans two rows (A reports to B, B
   reports to A) is two individually legal inserts, and no CHECK constraint can
   see it. Any recursion that meets one hangs. Putting the protection in one
   function beats "every traversal remembers to carry a visited set", which is
   discipline — the kind this project has already thrown out twice.
"""

import logging
from collections import defaultdict

from .models import Position

logger = logging.getLogger(__name__)

# A chain deeper than this is not an org chart, it is data damage. The visited
# set below already guarantees termination; this is a second floor under it, so
# that a pathological row costs a bounded number of queries rather than a hang.
MAX_CHAIN_DEPTH = 20


def creates_a_reporting_cycle(position):
    """Would `position.reports_to` close a loop? Used by Position.clean().

    Climbs from the proposed manager upward, one query per level. That is fine
    here and only here: it runs once per form submit on a chain that is a
    handful deep, not once per row of a changelist. build_org_tree() is the one
    that has to be cheap, and it takes the whole table in a single query.
    """
    seen = {position.pk} if position.pk else set()
    current = position.reports_to
    for _ in range(MAX_CHAIN_DEPTH):
        if current is None:
            return False
        if current.pk in seen:
            return True
        seen.add(current.pk)
        current = current.reports_to
    # Ran out of depth without reaching the top. With `seen` in hand this can
    # only mean the chain is absurdly long, so refuse it the same way.
    return True


def build_org_tree(positions=None):
    """The whole chart as a list of roots, each with a `children` list attached.

    Two things it guarantees, both of which have a test nailing them down:

    1. **One query.** Position is a table of a few dozen rows; fetching all of
       it and assembling the tree in memory beats any per-level query, and
       climbing `position.reports_to` row by row would be an N+1.
    2. **Bad data does not hang it.** clean() refuses to save a loop, but
       bulk_create never calls clean() — so a loop can exist. Anything caught
       in one is hung at the root and logged, rather than recursed into.

    Every position appears exactly once, so callers can render the result
    without ever knowing a loop was possible.
    """
    if positions is None:
        positions = list(Position.objects.select_related("ministry"))

    by_id = {position.pk: position for position in positions}
    children = defaultdict(list)
    roots = []
    looping = set()

    for position in positions:
        # .reports_to_id, never .reports_to: the attribute version would fire a
        # query per row and turn the promised single query into an N+1.
        manager_id = position.reports_to_id
        if manager_id is None or manager_id not in by_id:
            # No manager, or a manager outside the set we were handed: a root
            # as far as this chart is concerned.
            roots.append(position)
            continue
        if _climbs_into_a_loop(position, by_id):
            looping.add(position.pk)
            roots.append(position)
            continue
        children[manager_id].append(position)

    for position in positions:
        position.children = children[position.pk]

    if looping:
        logger.warning(
            "Reporting lines contain a loop; positions %s were hung at the root. "
            "Fix reports_to on one of them.",
            sorted(looping),
        )
    return roots


def _climbs_into_a_loop(position, by_id):
    """True if climbing from `position` revisits somebody it has already seen."""
    seen = set()
    current = position
    while current is not None and current.reports_to_id is not None:
        if current.pk in seen:
            return True
        seen.add(current.pk)
        current = by_id.get(current.reports_to_id)
    return False
