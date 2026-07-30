"""The only place in the project that answers "may they do this, here?".

Every judgement about ministry-scoped authority lives here, and views may only
call it: `if not can_manage_event(request.user, event): raise PermissionDenied`.
core/tests.py greps for MinistryRole.objects appearing anywhere else.

The reason is not tidiness. Scattered permission checks mean one of them
eventually forgets .active(), or forgets ministry__is_active — and a missed
permission check is silent. Nothing raises; somebody simply sees a page they
should not. Compare the org-tree guard, where the failure at least hangs.

⚠️ user.contact is None is a normal state, not an error. MinistryRole hangs off
   Contact while the entry point is a User, and D12/D21 require User.contact to
   stay nullable because a superuser is a technical account matching no real
   person. So:

     - every can_*() returns False for such an account, and raises nothing —
       raising would turn every protected view into a 500 for them;
     - superusers get no exemption. Exempting them would open a hole straight
       through the ministry scoping, which is the entire point of D20. A
       superuser has the admin, which can already do anything.

   The cost, stated rather than hidden: logging in as a superuser and clicking
   these pages gives 403 after 403 and looks broken. The message says so, so
   that the next person edits their account rather than the check.
"""

from django.contrib.auth.models import Group

from .models import MinistryRole

# The global tier. P5 — "who may appoint a ministry's admin" — has no "of some
# ministry" in it, so it is a plain Django Group and not a MinistryRole.
FOUNDATION_ADMIN_GROUP = "foundation_admin"

# What a 403 on these pages should say. Written once, because the explanation
# is the mitigation: without it the next person removes the check instead of
# fixing the account.
SCOPED_DENIAL = (
    "These pages are granted per ministry. A superuser has no ministry scope by "
    "design — use an account with a MinistryRole (seed_demo creates some)."
)


def _contact_of(user):
    """The Contact behind a login, or None. Never raises."""
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return getattr(user, "contact", None)


def ministry_ids_administered_by(user, on=None) -> set[int]:
    """Which ministries this person administers on `on`. The floor everything stands on.

    Named for what it returns — ids, not objects. Callers wanting objects do
    Ministry.objects.filter(id__in=...) themselves.

    ⚠️ Three filters, none of them optional:
       active(on)          — an expired grant must stop conferring anything;
       ministry__is_active — authority over a retired ministry is not authority;
       a Contact           — see the module docstring.
    """
    contact = _contact_of(user)
    if contact is None:
        return set()
    return set(
        MinistryRole.objects.active(on=on)
        .filter(
            contact=contact,
            role=MinistryRole.Role.ADMIN,
            ministry__is_active=True,
        )
        .values_list("ministry_id", flat=True)
    )


def administers(user, ministry, on=None) -> bool:
    """Does this person administer this one ministry?"""
    if ministry is None:
        return False
    ministry_id = getattr(ministry, "pk", ministry)
    return ministry_id in ministry_ids_administered_by(user, on=on)


def can_publish_event(user, ministry) -> bool:
    """P2: publish an event for this ministry, and say how many each role needs."""
    return administers(user, ministry)


def can_manage_event(user, event) -> bool:
    """Edit it, open roles on it, check people in, notify the people signed up.

    The write side. Sending a notification belongs here rather than with the
    read side: it puts a message in front of everybody who signed up, which is
    not something "may look at the list" should carry.
    """
    return event is not None and administers(user, event.ministry_id)


def can_view_registrations(user, event) -> bool:
    """P4's first half: see who has signed up. The read side."""
    return event is not None and administers(user, event.ministry_id)


def can_grant_ministry_admin(user) -> bool:
    """P5: appoint somebody as a ministry's admin.

    Reads the global Group and does not look at MinistryRole at all — a
    ministry admin must not be able to recruit their own downline. That is what
    makes this tier "higher", and it is the one thing about P5 worth testing.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    return user.groups.filter(name=FOUNDATION_ADMIN_GROUP).exists()


def foundation_admin_group() -> Group:
    """The global group, created if it is not there. Used by seed_demo and admin."""
    group, _ = Group.objects.get_or_create(name=FOUNDATION_ADMIN_GROUP)
    return group
