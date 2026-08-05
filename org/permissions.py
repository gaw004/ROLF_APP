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

from django.contrib.auth.models import Group, Permission

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


#: What the global tier may do, as app_label.codename. Global permissions are
#: the right shape here precisely because none of these sentences contains "of
#: some ministry" — which is the test for whether something belongs in a Group
#: or in MinistryRole (D20).
FOUNDATION_ADMIN_PERMISSIONS = [
    # P5 itself: appointing and revoking a ministry's admins.
    "org.add_ministryrole",
    "org.change_ministryrole",
    "org.delete_ministryrole",
    "org.view_ministryrole",
    # R1–R3 are read off the event changelist, foundation-wide: how many events
    # ran in a period, whose they were, how long each took.
    "events.view_event",
    "events.view_eventrole",
    "events.view_participation",
    # Ministries themselves. A production database comes up with none, and
    # nothing else in the interface can create one — so without these the
    # foundation cannot get started at all. Django hides a model from the admin
    # index entirely when you hold no permission on it, which is why this looked
    # like a missing page rather than a missing permission.
    #
    # ⚠️ No delete_ministry, deliberately. Deleting a ministry cascades into its
    #    events, and "we are not running this any more" is is_active=False —
    #    the same "an ending is a date, not a deletion" rule the rest of this
    #    project follows.
    "org.add_ministry",
    "org.change_ministry",
    "org.view_ministry",
    # The other lookup tables: read-only for now (2026-08-04 拍板). They still
    # have to be filled in before the pilot, and that is done by a staff account
    # in the admin — this tier can see what is there without being able to
    # rename a category out from under existing rows.
    "events.view_eventtype",
    "events.view_participationrole",
    "org.view_position",
    "org.view_employmenttype",
]


def foundation_admin_group() -> Group:
    """The global group, with its permissions. Used by seed_demo and the admin.

    ⚠️ The permissions are attached here rather than clicked on in the admin.
       A group that exists but grants nothing looks right in every listing and
       fails only when somebody tries to use it — and an empty group is
       indistinguishable from a full one until then. Membership of this group
       still confers no ministry scope whatsoever: the scoped pages ask
       MinistryRole, and a foundation admin who is not also a ministry's admin
       gets 403 from them. That is deliberate, not an oversight.
    """
    group, _ = Group.objects.get_or_create(name=FOUNDATION_ADMIN_GROUP)
    wanted = []
    for label in FOUNDATION_ADMIN_PERMISSIONS:
        app_label, codename = label.split(".")
        found = Permission.objects.filter(
            content_type__app_label=app_label, codename=codename).first()
        if found:
            wanted.append(found)
    # ⚠️ Reconciled every time, not only when the group is new or empty.
    #
    #    The earlier version was `if created or not group.permissions.exists()`,
    #    and it made the list above a **lie on every database that already had
    #    this group** — adding a permission here did nothing at all, silently,
    #    and the only symptom was a page missing from the admin index. That is
    #    exactly how add_ministry went missing: the list was right, the group
    #    was stale, and nothing reported the difference.
    #
    #    Making it authoritative also means a permission ticked by hand in the
    #    admin is removed on the next call. That is intended and is what the
    #    docstring above already promised: this list is where the answer lives.
    group.permissions.set(wanted)
    return group
