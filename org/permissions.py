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


def administers_one_of(ministry, administered) -> bool:
    """`administers()` for a page of rows: the same rule, asked with no query.

    ⚠️ **The second implementation of one rule, and it lives here beside the
       first for that reason** — the same arrangement core/querysets.py uses for
       active()/is_currently_active and events/models.py for
       recording_hours()/records_hours. Change one, change the other.

    It was inlined in events/views.py until 2026-09-08 (`event.ministry_id in
    administered`), which is this function's body written somewhere the grep
    guard cannot see it: PermissionGuardTests looks for MinistryRole.objects,
    and a set membership test names nothing it recognises. The rule that views
    make exactly one call into this module was being broken by the only spelling
    that could not be caught.

    ⚠️ `administered` is the caller's already-fetched set of ids — one query for
       a page rather than one per row, which is why the pair exists at all. It
       decides **what to draw**; every write still goes through the real check.
    """
    ministry_id = getattr(ministry, "pk", ministry)
    return ministry_id in administered


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


def can_publish_notice(user, ministry) -> bool:
    """Put a notice on the board in this ministry's name.

    Two ways in, and unlike an event's records they are **not** two different
    authorities — both may write:

      · the ministry's own admin, in their own ministry's name;
      · the foundation tier, in any ministry's name.

    ⚠️ The foundation half arrived 2026-09-03, on the foundation's word, and it
       is the restart condition D41's last table wrote down verbatim — "给
       ministry admin 之外的人发布权 / 基金会说了算". That entry also promised
       "改动只在 org.permissions.can_publish_notice 一个函数里", and that turned
       out to be **half true**: the check is one line here, but a check is not a
       door. `NoticeForm` builds its ministry dropdown from
       ministry_ids_administered_by(), so a foundation admin holding no
       MinistryRole would have passed this function and still had an empty
       dropdown — permitted to publish, with nothing to publish for. The other
       half of the change is there, and it is written on that form.

    ⚠️ What did **not** change is the audience axis. A notice is louder than an
       event — it lands on the home page of everybody it is ticked for — and
       there was a real argument (2026-08-31) for reserving the wider ticks to
       this tier. It was not taken then and is not taken now: the same three
       ticks meaning something stricter on a second table is how two checks come
       to disagree about one question. Every audience is still recorded with a
       name against it.
    """
    return administers(user, ministry) or in_foundation_tier(user)


def can_manage_notice(user, notice) -> bool:
    """Edit it, publish it, take it down.

    ⚠️ **Deliberately still its own function, even though it and
       can_publish_notice now admit exactly the same two tiers** (2026-09-03).
       They coincide today for two unrelated reasons: this one has always
       included the foundation tier because taking down a wrong or harmful
       notice cannot wait for the admin who wrote it to answer the phone, and
       that one includes it as of today because the foundation asked to be able
       to write one. Folding either into the other would tie the two together,
       and the next move is likelier to separate them again — if publishing is
       ever narrowed back, removing must stay wide.
    """
    if notice is None:
        return False
    return administers(user, notice.ministry_id) or in_foundation_tier(user)


def can_reach_notice_manage(user) -> bool:
    """May this account open the notice manage page at all, with nothing on it yet?

    ⚠️ Its own question rather than "do they own any notices", because
       "you have not written one yet" and "this page is not for you" must not
       look the same (D27). A ministry admin on their first day gets an empty
       page and a button, not a 403.
    """
    return bool(ministry_ids_administered_by(user)) or in_foundation_tier(user)


def in_foundation_tier(user) -> bool:
    """Is this account in the foundation-wide group?

    The primitive the global tier is built on. Named for what it reads rather
    than for one thing it permits, because it now answers two questions —
    "may they appoint ministry admins" and "may they read any event's records"
    — and giving each of those its own copy of `groups.filter(...)` is how two
    checks end up disagreeing about who is in the tier.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    return user.groups.filter(name=FOUNDATION_ADMIN_GROUP).exists()


def can_grant_ministry_admin(user) -> bool:
    """P5: appoint somebody as a ministry's admin.

    Reads the global Group and does not look at MinistryRole at all — a
    ministry admin must not be able to recruit their own downline. That is what
    makes this tier "higher", and it is the one thing about P5 worth testing.
    """
    return in_foundation_tier(user)


def can_view_event_records(user, event) -> bool:
    """Read one event's signups, attendance and report. **Read only.**

    Two ways in, and they are not the same authority (2026-08-05):

      · the ministry's own admin, who may also change these things;
      · the foundation tier, who may only look.

    ⚠️ Nothing that writes may be gated on this. The attendance page in
       particular is a write page — check-in, check-out, hours — so it asks
       this for GET and `can_manage_event` for POST. Hiding the buttons is
       interface; refusing the POST is the permission, and only the second one
       is a boundary.

    ⚠️ This widens who can see a minor's emergency contact number, because the
       attendance page shows it (that is the number somebody dials when an
       ankle gets twisted). Deliberate, decided 2026-08-05, and written into
       phase-c.md's "who can see a minor's data" table — which is what C3.7
       checks against, so it cannot be widened quietly.
    """
    if event is None:
        return False
    return administers(user, event.ministry_id) or in_foundation_tier(user)


def event_access(user, event) -> tuple[bool, bool]:
    """(may manage it, may read its records) — both answers, one look at the grants.

    ⚠️ Composed here rather than in the caller, and that is the whole reason it
       exists. The two questions share a term (`administers`), and a page that
       needs both was asking each separately — reading the grant table twice
       per render. Spelling `administers(...) or in_foundation_tier(...)` out at
       the call site would fix the query and break the rule this module is for:
       the two ways into an event's records are one policy, judged in one place.

    ⚠️ Order matters for the second element: managing implies reading, so the
       foundation-tier check is only reached by somebody who does not manage
       this ministry. That is the containment can_view_event_records() states,
       not a shortcut on top of it.
    """
    manages = can_manage_event(user, event)
    return manages, manages or (event is not None and in_foundation_tier(user))


def can_upload_gallery_photo(user, ministry) -> bool:
    """Put a photo on the Memories wall, attributed to `ministry`.

    `ministry` None means foundation-wide, and **only the foundation tier may
    pass it**. That is D20's test applied literally: "a photo that speaks for
    the whole foundation" contains no "of some ministry", so it belongs to the
    global tier. A ministry admin publishes their ministry's memories; they do
    not publish the foundation's.
    """
    if ministry is None:
        return in_foundation_tier(user)
    return administers(user, ministry) or in_foundation_tier(user)


def can_delete_gallery_photo(user, photo) -> bool:
    """Take a photo back off the wall.

    ⚠️ Deliberately wider than uploading: the foundation tier may delete
       anything, including a ministry's own. Somebody has to be able to take
       down a photo of a child whose family has asked for it to go, at an hour
       when that ministry's admin is not reachable — and that request does not
       wait for a duty roster.

    ⚠️ And deliberately **not** a read check. There is no "may they see it"
       function here because the wall is one page behind @login_required with
       no per-photo visibility; adding a per-photo read rule would be inventing
       a boundary the interface does not have.
    """
    if photo is None:
        return False
    if in_foundation_tier(user):
        return True
    return photo.ministry_id is not None and administers(user, photo.ministry_id)


def can_reach_gallery_manage(user) -> bool:
    """Open the page photos are put up and taken down from at all.

    ⚠️ Coarser than the two above on purpose, and it does not replace either.
       This answers "is this person any kind of photo admin" — the question the
       *page* asks — while `can_upload_gallery_photo` and
       `can_delete_gallery_photo` answer it per photo, which is what the page's
       contents and its buttons are decided by. A gate this wide is the right
       shape for a door and the wrong shape for anything behind it.

    ⚠️ It exists because the batch removal added a **second** URL that has to
       ask it (2026-08-14). One copy of `in_foundation_tier(...) or
       ministry_ids_administered_by(...)` in a view was fine; two copies is a
       permission rule living in views.py, and the second copy is the one that
       gets forgotten when the rule changes.
    """
    return in_foundation_tier(user) or bool(ministry_ids_administered_by(user))


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
    # L5.2's two tables, on the same footing and for the same reason: a run's
    # meetings and its register are part of "what did this ministry run", which
    # is what R1–R3 are read off.
    #
    # 🔴 Registering a model in admin.py is **not** what makes it reachable.
    #    Django hides a model from the admin index entirely when you hold no
    #    permission on it, so a table that is registered but not named here is
    #    invisible to every account except a superuser — and it looks exactly
    #    like a page that was never built. That is how add_ministry went
    #    missing, and it is written a few lines above; L5.2 walked into the same
    #    hole on 2026-09-08 and this is the second half of the fix.
    #
    # ⚠️ View only, deliberately — no add/change, matching the three lines
    #    above. Scheduling a course's meetings is an act on **one ministry's**
    #    event, and by D20's test ("does the sentence contain 'of some
    #    ministry'?") that belongs to the ministry tier, not to a
    #    foundation-wide grant. Its door is the Programmes pages (06-roadmap
    #    L5.8), scoped by MinistryRole, and until those exist the only writer is
    #    a superuser — the same footing event creation is on.
    "events.view_session",
    "events.view_sessionattendance",
    # L5.4's two, on the same footing and for the same reason as the pair above.
    # A repeat rule and the roles it opens are part of "what did this ministry
    # run", which is what R1–R3 are read off — and a batch of twelve events with
    # no visible rule behind them is twelve events nobody can explain.
    #
    # ⚠️ View only, deliberately. Building a batch is an act on **one ministry's**
    #    events, so by D20's test it belongs to the ministry tier rather than to
    #    a foundation-wide grant; its door is the series pages in L5.8, and until
    #    those exist the only writer is a superuser. Same footing as Session.
    #
    # 🔴 And they are here at all because registering a model in admin.py is not
    #    what makes it reachable: Django hides a model from the admin index
    #    entirely when you hold no permission on it, so a registered-but-ungranted
    #    table is invisible to every account except a superuser and looks exactly
    #    like a page nobody built. That is how add_ministry went missing, and how
    #    L5.2 lost two days on 2026-09-08.
    "events.view_eventseries",
    "events.view_eventseriesrole",
    # Ministries themselves. A production database comes up with none, and
    # nothing else in the interface can create one — so without these the
    # foundation cannot get started at all. Django hides a model from the admin
    # index entirely when you hold no permission on it, which is why this looked
    # like a missing page rather than a missing permission.
    #
    # ⚠️ No delete_ministry, deliberately, and "we are not running this any
    #    more" is is_active=False — the same "an ending is a date, not a
    #    deletion" rule the rest of this project follows.
    #
    # 🔴 The reason written here until 2026-09-08 was **wrong**: it said
    #    deleting a ministry cascades into its events. It does not —
    #    `Event.ministry` is PROTECT, so a ministry that owns events cannot be
    #    deleted at all. What does cascade is the one nobody had written down:
    #    the audience many-to-many. A ministry that owns nothing but is *ticked
    #    into* other ministries' events takes those ticks with it, and an event
    #    left with an empty audience disappears for everybody — the state
    #    refuse_empty_audience() exists to prevent, arriving by a path it does
    #    not watch. Deleting still needs a superuser, so the withholding above
    #    is the protection; what changed here is that it now gives the reason
    #    that is true.
    "org.add_ministry",
    "org.change_ministry",
    "org.view_ministry",
    # The public front page: its picture, its video and the verse over them.
    # ⚠️ Foundation-wide by construction — the sentence "change the face of the
    #    foundation" contains no "of some ministry", which is D20's test for
    #    which tier a permission belongs to. A ministry admin publishes events;
    #    they do not speak for the whole foundation.
    "core.change_homepage",
    "core.view_homepage",
    # The other lookup tables: read-only for now (2026-08-04 拍板). They still
    # have to be filled in before the pilot, and that is done by a staff account
    # in the admin — this tier can see what is there without being able to
    # rename a category out from under existing rows.
    # ⚠️ `events.view_eventtype` sat here until 2026-09-08, three days after
    #    that table was deleted (commit d600bc9, "一处不留"). Nothing reported
    #    it: the loop below skipped the label it could not resolve, and the test
    #    on this list asserts a **subset**, so a name that resolves to nothing
    #    can never fail. That is the third time this file has met "the list is
    #    right, reality is not, and nothing says so" — see the note under
    #    unresolved() for what now says so.
    "events.view_participationrole",
    "org.view_position",
    "org.view_employmenttype",
]


def _named_permissions():
    """Every label in FOUNDATION_ADMIN_PERMISSIONS, resolved — None where it is not.

    One resolution, two readers: the group builder below takes what resolved,
    and unresolved() reports what did not. Written as one function because the
    two used to be one loop with the failures thrown away.
    """
    for label in FOUNDATION_ADMIN_PERMISSIONS:
        app_label, codename = label.split(".")
        yield Permission.objects.filter(
            content_type__app_label=app_label, codename=codename).first()


def unresolved_permissions():
    """The labels naming a permission this database does not have.

    ⚠️ Skipping them is still right — an app that is not installed yet should
       not stop the group being built. What was wrong was skipping them
       **silently**: `events.view_eventtype` outlived its model by three days
       and the only thing that would ever have noticed was somebody reading this
       file. A typo in a codename fails exactly the same way, and grants nothing
       while looking correct in every listing.

    Read by core.management.commands.check_deployment, so a stale name is
    reported where the rest of "is this database ready" is reported.
    """
    return [label for label, found
            in zip(FOUNDATION_ADMIN_PERMISSIONS, _named_permissions())
            if found is None]


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
    wanted = [p for p in _named_permissions() if p is not None]
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
