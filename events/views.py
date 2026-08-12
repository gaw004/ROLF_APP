"""The self-service pages and the ministry-admin pages.

Thin shells, every one of them. Three rules hold across the whole file:

1. Visibility is decided in the query, never in the template. Hiding a draft
   with {% if %} still sent it to the browser; filtering it out means it was
   never fetched. Same for "mine": the queryset is narrowed to the logged-in
   contact, so somebody else's id in the URL can only 404.

   ⚠️ `event_detail` is the one deliberate exception (2026-08-06), and it is
      worth stating precisely so the next reader does not "fix" it: that page
      **must** serve a row the volunteer predicate excludes, because a draft's
      preview is the whole point. What the exception does not move is *where*
      the decision is made — it is still the view, before render, and the
      refusal is still 404. The rule this docstring is protecting is "no
      {% if %} in a template stands between a viewer and data the response
      already carries", and that still holds.
2. The permission check is the first thing each protected view does, and the
   check itself is one call into org.permissions — there is a grep guard on
   that.
3. No arithmetic here. Counts and totals come from QuerySet methods and
   services.py, because anything computed in a view gets rewritten along with
   the templates (D18) — and there is a guard for that too.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Prefetch
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django_ratelimit.decorators import ratelimit

from org.models import Ministry
from org.permissions import (
    SCOPED_DENIAL,
    in_foundation_tier,
    can_manage_event,
    can_publish_event,
    can_view_event_records,
    ministry_ids_administered_by,
)

from .forms import (
    EventForm,
    EventPeriodForm,
    EventRoleForm,
    EventStatusForm,
    HoursForm,
    NotifyForm,
    SignUpForm,
)
from .models import Event, EventNotification, EventRole, Participation
from .tokens import (
    CHECK_IN,
    MODES,
    InvalidCheckInToken,
    issue_with_expiry,
    verify as verify_checkin_token,
    window_is_open,
    window_message,
)
from .services import (
    CHECKIN_CREDENTIAL_KEY,
    ConsentRequired,
    CredentialExpired,
    TurnedUp,
    apply_scan,
    cancel,
    checkin_result_message,
    default_checkin_mode,
    issue_credential,
    read_credential,
    scan_targets,
    check_in,
    check_out,
    clear_hours,
    confirm_signup,
    default_message,
    event_summary,
    mark_absent,
    ministry_report,
    ministry_staff_participation,
    notify_event_change,
    record_hours,
    reschedule,
    scheduled_hours,
    resolve_recipients,
    set_status,
    sign_up,
    undo_attendance,
)


def _template(request, full, fragment):
    """Whole page, or just the part HTMX asked to swap.

    ⚠️ Both branches are handed **the same context** by the caller. Building a
       smaller one for the fragment is the obvious optimisation and the thing
       that eventually makes the count on the fragment disagree with the count
       on the full page — and nothing would report that, because both renders
       succeed.

    ⭐ HTMX is only ever the fast path here. Every one of these views still
       answers a plain GET or POST with a complete page, which is what keeps the
       end-to-end tests (they never send HX-Request) testing the real thing —
       see D24's progressive-enhancement rule.
    """
    return fragment if request.headers.get("HX-Request") else full


def _back_link(request):
    """Where "back" goes from an event's page, and what it should be called.

    Decided by **where they came from**, not by who they are: a ministry admin
    who reached an event from the volunteer list wants the volunteer list back,
    and the same person arriving from the management list wants that one.
    Before this, the link was chosen by role alone and sent half the arrivals
    somewhere they had not been.

    ⚠️ Read from a `?from=` marker on the link, never from the Referer header.
       Referer is empty for a pasted URL, a new tab, a link in an email, or a
       browser configured not to send it — and when it is empty this function
       silently returns the default, which is exactly the case nobody tests.
       It is also attacker-controlled, and this value ends up in an href.

    ⚠️ The marker is a **key into a table written here**, never a URL. Taking a
       URL from the query string and rendering it into a link is how a page
       ends up with a "back" button pointing at somebody else's site.

    ⚠️ `manage` is honoured only if this account can actually open that page.
       A volunteer handed a `?from=manage` link would otherwise get a back
       button that 403s — a link that refuses the person who clicked it reads
       as a broken site rather than as a page not meant for them.
    """
    marker = request.GET.get("from")
    if marker == "mine":
        return reverse("events:my_participations"), "My Signups"
    if marker == "past":
        return reverse("events:past_events"), "Past Events"
    if marker == "manage":
        administers_any = bool(ministry_ids_administered_by(request.user))
        if administers_any or in_foundation_tier(request.user):
            # Same label the navigation uses for this account, so the two do
            # not name one page two different things.
            label = "Events I Manage" if administers_any else "All Events"
            return reverse("events:event_manage_list"), label
    return reverse("events:event_list"), "Events"


#: How many events one page of each list holds (2026-08-05).
#:
#: Two different numbers because the rows are two different heights: the
#: volunteer lists are cards with a thumbnail, the management list is a table
#: row. Fifty cards is a very long page; fifty table rows is one screen and a
#: bit, and the person reading that page is scanning across everything.
EVENTS_PER_PAGE = 20
MANAGED_EVENTS_PER_PAGE = 50


def _page(request, events, per_page):
    """One page of a filtered list, ordered so that paging cannot lie.

    ⚠️ The ordering **must** end in a unique column. `-start_time` alone is not
       unique — two events starting at the same minute have no defined order
       between them, so Postgres may return them in a different order for
       page 1 and page 2. The visible result is a row appearing twice, or one
       vanishing entirely, and nothing anywhere reports it.

    ⚠️ The caller keeps the unpaginated queryset. The report is computed from
       **that**, not from this page: a figure that changed when you turned the
       page would mean nothing at all (D27).
    """
    ordered = events.order_by(*events.query.order_by, "-pk")
    return Paginator(ordered, per_page).get_page(request.GET.get("page"))


def _my_contact(request):
    """The Contact behind the logged-in account, or None.

    None is a normal state — a superuser has no Contact by design — so pages
    cope with it rather than raising, the same rule permissions.py follows.
    """
    return getattr(request.user, "contact", None)


# --- B9: the volunteer's own pages --------------------------------------


@login_required
def event_list(request):
    """P3: what a volunteer can sign up for.

    open_for_signup(), not visible_to_volunteers(): a list of things to join
    should not offer events that are full or already over. The detail page uses
    the other predicate — being able to see something and being able to join it
    are different questions.
    """
    period = EventPeriodForm(request.GET or None)
    events = (
        Event.objects.open_for_signup()
        .upcoming()
        .select_related("ministry", "event_type")
        .order_by("start_time")
    )
    events = period.narrow(events)
    page = _page(request, events, EVENTS_PER_PAGE)
    return render(request, _template(
        request, "events/event_list.html", "events/_event_list_results.html"), {
        "events": page,
        "page": page,
        "period": period,
        # R1, in the plainest possible form: how many, in the window they asked
        # for. ⚠️ The whole filtered set, not this page — "20 events" under a
        # filter that matched 180 would answer a question nobody asked.
        "total": page.paginator.count,
    })


@login_required
def past_events(request):
    """R1's other half: everything already over.

    Not an admin report. The requirement's first line is "how many events run
    in a given period", and a volunteer answering "which of these do I want to
    join" needs the same query pointed the other way — event_list only ever
    shows what is open and upcoming, so an event vanished from the interface
    the moment it ended, taking its report page with it.

    visible_to_volunteers(), so a cancelled or completed event still appears:
    the people who were there have hours on it.
    """
    period = EventPeriodForm(request.GET or None)
    events = (
        Event.objects.visible_to_volunteers()
        .past()
        .select_related("ministry", "event_type")
        .order_by("-start_time")
    )
    events = period.narrow(events)
    page = _page(request, events, EVENTS_PER_PAGE)
    return render(request, _template(
        request, "events/past_events.html", "events/_past_events_results.html"), {
        "events": page,
        "page": page,
        "period": period,
        "total": page.paginator.count,
    })


@login_required
def event_detail(request, pk):
    """visible_to_volunteers(), so a confirmed or finished event still opens.

    ⚠️ Written as status == OPEN this page would 404 the moment an event filled
       up — for exactly the people who had signed up, and for P6's "can't make
       the new time? cancel here" link, which is sent to precisely them.

    2026-08-06 — and one way past that predicate: **preview**. An event nobody
    has published yet 404'd for everybody, its own ministry's admin included,
    which made every draft's name on the management list a link that refuses the
    person who clicked it. It was also the odd one out: registrations,
    attendance, the report, the edit page and the notice page have all opened on
    a draft the whole time. Only this page did not, and nothing said why.

    ⭐ Same page, not a second one. The point of a preview is to read what the
       volunteers will read, so a preview that had its own template would be
       answering a different question by the second time somebody edited one of
       them. What `preview` adds is a banner and an inert signup button; nothing
       else on the page is drawn from it.

    ⚠️ The refusal stays **404, not 403**. To somebody with no business here a
       draft must not exist — a 403 says "there is an event at this id and it is
       not for you", which is exactly the sentence a draft is supposed to
       withhold. So the outcome for a volunteer is byte-for-byte what it was.

    ⚠️ `can_view_event_records`, the read check — not `can_manage_event`. The
       foundation tier can already open this event's signups, attendance and
       report; letting the *event page* be the one thing it cannot see would
       make the narrower rule the confusing one, and it would be a rule no
       reader could guess from the other five pages.

    ⚠️ Keyed on membership of VISIBLE_TO_VOLUNTEERS, never on `== DRAFT`. The
       set is the model's answer to "who may a volunteer see", listed in full
       for the reason written above it (events/models.py) — and the day somebody
       adds `postponed` to it, a branch spelled `== DRAFT` would quietly publish
       that event to everybody while this comment still claimed otherwise.
    """
    event = get_object_or_404(
        Event.objects.select_related("ministry", "event_type"), pk=pk)
    preview = event.status not in Event.VISIBLE_TO_VOLUNTEERS
    if preview and not can_view_event_records(request.user, event):
        # Deliberately indistinguishable from "no such event" — see above.
        raise Http404("No event matches the given query.")
    contact = _my_contact(request)
    mine = Participation.objects.none()
    if contact is not None:
        mine = Participation.objects.filter(
            event_role__event=event, contact=contact,
        ).select_related("event_role__role")
    back_url, back_label = _back_link(request)
    return render(request, "events/event_detail.html", {
        "event": event,
        "roles": event.roles.with_signup_counts().select_related("role"),
        "mine": mine,
        "can_sign_up": event.status in Event.OPEN_FOR_SIGNUP,
        "can_manage": can_manage_event(request.user, event),
        # ⚠️ False on every published event, so the banner is not something a
        #    template has to remember to switch off. It is only ever true for a
        #    viewer who already passed the check above.
        "preview": preview,
        "back_url": back_url,
        "back_label": back_label,
    })


@login_required
def event_signup(request, pk):
    """P3: join a role. Minors — and unknown birth dates — go through consent."""
    event = get_object_or_404(Event.objects.open_for_signup(), pk=pk)
    contact = _my_contact(request)
    if contact is None:
        raise PermissionDenied(SCOPED_DENIAL)

    form = SignUpForm(request.POST or None, event=event, contact=contact)
    if request.method == "POST" and form.is_valid():
        try:
            # The rule lives in sign_up(), not here: an admin registering
            # somebody from a paper list has to meet the same one.
            participation = sign_up(
                contact=contact,
                event_role=form.cleaned_data["event_role"],
                consent=form.consent(),
            )
        except (ConsentRequired, ValidationError) as error:
            form.add_error(None, error)
        else:
            # The confirmation is a courtesy on top of the row, not part of it:
            # a signup that was accepted must not be undone because a message
            # could not go out. confirm_signup() returns rather than raises.
            confirm_signup(participation)
            messages.success(request, "You are signed up. We have sent a confirmation.")
            return redirect("events:event_detail", pk=event.pk)

    return render(request, "events/event_signup.html", {
        "event": event, "form": form, "needs_consent": form.needs_consent,
    })


@login_required
def my_participations(request):
    """Mine means mine — narrowed in the query, not in the template.

    visible_to_volunteers() as well, which is not belt and braces: every row
    here links to the detail page, and that page uses the same predicate. A
    signup an admin entered against an unpublished event would otherwise appear
    with a link that 404s — the failure this pair of predicates was written to
    prevent, arriving from the other end.
    """
    contact = _my_contact(request)
    rows = Participation.objects.none()
    if contact is not None:
        rows = (
            Participation.objects.filter(
                contact=contact,
                event_role__event__in=Event.objects.visible_to_volunteers(),
            )
            .select_related("event_role__event__ministry", "event_role__role")
            .order_by("-event_role__event__start_time")
        )
    return render(request, "events/my_participations.html", {"participations": rows})


@login_required
def participation_cancel(request, pk):
    """Withdraw. Looked up inside "mine", so somebody else's row 404s."""
    contact = _my_contact(request)
    owned = (
        Participation.objects.filter(contact=contact)
        if contact is not None
        else Participation.objects.none()
    )
    participation = get_object_or_404(
        owned.select_related("event_role__event"), pk=pk)
    if request.method == "POST":
        cancel(participation)
        messages.success(request, "Your signup has been cancelled.")
        return redirect("events:my_participations")
    return render(request, "events/participation_cancel.html", {
        "participation": participation,
    })


# --- B10: the ministry admin's pages -------------------------------------


def _managed_event(request, pk):
    """An event this account may manage, or a refusal.

    The lookup is not narrowed to their ministries: for an event that exists,
    "not yours" is the honest answer, and the message explains the scoping so
    that the next person fixes their account instead of deleting the check.
    """
    event = get_object_or_404(Event.objects.select_related("ministry"), pk=pk)
    if not can_manage_event(request.user, event):
        raise PermissionDenied(SCOPED_DENIAL)
    return event


def _scoped_events(request):
    """The events this account may see on the management side, or a refusal.

    Extracted 2026-08-05 because the full report page needs the same answer.
    ⚠️ A second copy of "which events may this account see" is the one thing on
       a report that could go wrong quietly — it is read once and believed, and
       nobody checks a total against a list they are not allowed to see.

    ⚠️ Somebody who is both — a foundation admin who also runs a ministry —
       keeps the managing view of their own ministries rather than the read-only
       view of everything. Losing the ability to publish an event because you
       were also promoted would be a strange way to be rewarded.

    ⚠️ `?scope=all` (2026-08-06) is how that person reaches the other view, and it
       **does not widen anybody's authority**. It is available only to the
       foundation tier, and it answers by handing back an empty `administered` —
       so `can_manage` is False, the page draws itself read-only, and every write
       on it still goes through `_managed_event()`, which asks
       permissions.py about the actual account and not about this parameter.
       A ministry admin who adds it to the URL gets nothing: they are not in the
       tier, so the branch is not taken.

       The 2026-08-05 decision above is not reversed by it. That decision was
       about which view somebody gets **by default**, and the default is
       unchanged; what was missing was any way at all to ask for the other one,
       which left the read-only authority the foundation tier already holds with
       no entrance for half the people who hold it (revisions.md).
    """
    if request.GET.get("scope") == "all" and in_foundation_tier(request.user):
        return (
            Event.objects.all()
            .select_related("ministry", "event_type").order_by("-start_time"),
            set(),
            True,
        )

    administered = ministry_ids_administered_by(request.user)
    # The foundation tier gets the same page over every ministry — read only.
    # ⚠️ Without this it had no entrance at all: it holds no MinistryRole, so
    #    nothing anywhere listed events for it to open. That is the same gap
    #    C0.2 closed five times over — the pages existed and nothing pointed at
    #    them.
    foundation = in_foundation_tier(request.user)
    if not administered and not foundation:
        raise PermissionDenied(SCOPED_DENIAL)

    events = Event.objects.all() if not administered else Event.objects.filter(
        ministry_id__in=administered)
    return (
        events.select_related("ministry", "event_type").order_by("-start_time"),
        administered,
        foundation,
    )


def _looking_foundation_wide(request, administered):
    """Did `_scoped_events()` hand back the whole foundation, read only?

    Asked of the **outcome** (`administered` came back empty) rather than of the
    query string alone, so a ministry admin who types `?scope=all` does not get a
    page that behaves as if the parameter had been honoured. It was not.
    """
    return request.GET.get("scope") == "all" and not administered


def _offered_ministries(administered):
    """What the filter's dropdown may offer this account.

    ⚠️ Interface, not a permission — the queryset is already narrowed. What this
       prevents is a ministry admin being offered every ministry in the
       foundation, picking one, and getting an empty list with nothing saying why.
    """
    if not administered:
        return None
    return Ministry.objects.filter(
        pk__in=administered, is_active=True).order_by("name")


@login_required
def event_manage_list(request):
    """Everything this account administers — including drafts and finished ones.

    The entrance the ministry-admin side never had. event_roles links onward to
    registrations, attendance, the report and the notice page, but the only way
    to reach event_roles was the redirect after creating an event: come back
    tomorrow and there was no route to any of it. event_list is no help, since
    it shows only what is open and upcoming — which excludes drafts, and
    excludes every event whose report anybody would actually want to read.

    2026-08-05 — the same period filter the volunteer lists have, plus a report
    over whatever it selected.

    ⭐ The report describes **exactly the events in the list**: it is handed the
       filtered queryset, not a ministry id. So both tiers run one code path,
       and the panel cannot widen past the page it is drawn on.

    ⚠️ It is computed only when asked for (`?report=1`), not on every filter.
       Thirteen figures are a dozen aggregate queries, and most of the time
       somebody changing a date is only reading the list.
    """
    events, administered, foundation = _scoped_events(request)

    if request.method == "POST":
        # Status is editable straight from the list: publishing an event and
        # closing a finished one are frequent, and both are a single choice.
        # The times are not, and are shown read-only — moving an event obliges
        # somebody to notify the volunteers, so it goes through the edit page,
        # which routes to the notice. Two fields, two different consequences.
        event = _managed_event(request, request.POST.get("event"))
        form = EventStatusForm(request.POST, instance=event)
        if form.is_valid():
            set_status(event, form.cleaned_data["status"])
            messages.success(request, f"“{event.name}” is now {event.get_status_display()}.")
        return redirect("events:event_manage_list")

    period = EventPeriodForm(
        request.GET or None, ministries=_offered_ministries(administered))
    events = period.narrow(events)
    page = _page(request, events, MANAGED_EVENTS_PER_PAGE)
    return render(request, _template(
        request, "events/event_manage_list.html",
        "events/_event_manage_results.html"), {
        "events": page,
        "page": page,
        "total": page.paginator.count,
        "period": period,
        "can_manage": bool(administered),
        # ⚠️ Passed to the filter so it survives a Filter or a Clear. A
        #    method="get" form replaces the whole query string with its own
        #    fields, so without it the foundation-wide page silently narrows back
        #    to the person's own ministries on the first click — and the list
        #    still looks perfectly normal, just shorter.
        "scope_param": "all" if _looking_foundation_wide(request, administered) else None,
        # ⚠️ Present only when asked for, and the template keys the whole panel
        #    off "is it there". `report=1` without it would draw an empty panel
        #    full of zeros, which is a different claim from "not run yet".
        #
        # ⚠️ Built from `events`, never from `page`. The report answers about
        #    the filter, not about which page you happen to be on — a figure
        #    that moved when you clicked Next would mean nothing (D27).
        "report": ministry_report(events) if request.GET.get("report") else None,
        # One unbound form, reused to draw every row's dropdown: the choices are
        # identical, and building one per event would be a form per row for no
        # gain.
        "status_form": EventStatusForm(),
    })


@login_required
def ministry_report_page(request):
    """The whole report, full width, with the event list printed under it.

    The panel beside the management list is capped to the height of that list
    and scrolls (2026-08-05 拍板) — so it needs somewhere to send you rather
    than an expander for every chart. This is that place.

    ⚠️ It shares `_scoped_events()` with the list page rather than repeating the
       scoping. A second copy of "which events may this account see" is the one
       thing on this page that could go wrong quietly: a report is read once and
       believed, and nobody double-checks a total against a list they cannot see.

    ⚠️ No pagination here, on purpose. This is the artefact somebody prints and
       hands to a board — half of it is not an artefact. The print stylesheet
       breaks the list onto its own page.

    "Save as PDF" is the browser's own print dialog (D27). The costs are stated
    there: it is two clicks rather than one, and it is worth it because the PDF
    and the page are then the **same rendering** — there is no second layout to
    keep in step, and swapping in a server-side renderer later changes only who
    rasterises this HTML.
    """
    events, administered, foundation = _scoped_events(request)
    period = EventPeriodForm(
        request.GET or None, ministries=_offered_ministries(administered))
    events = period.narrow(events)
    return render(request, "events/ministry_report.html", {
        "events": events.order_by("start_time", "pk"),
        "total": events.count(),
        "period": period,
        "can_manage": bool(administered),
        "scope": "Every ministry" if not administered else None,
        # Arrived from the panel's "Save as PDF": open the print dialog on load,
        # so that path is one click rather than two.
        "autoprint": bool(request.GET.get("print")),
        "report": ministry_report(events),
    })


@login_required
def event_create(request):
    """P2: publish an event, for a ministry this person actually runs."""
    if not ministry_ids_administered_by(request.user):
        raise PermissionDenied(SCOPED_DENIAL)

    # ⚠️ request.FILES is not optional. Without it the upload is silently
    #    dropped: the form validates, the event saves, and no image arrives.
    form = EventForm(request.POST or None, request.FILES or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        # Checked again, on the submitted value. The narrowed dropdown stops a
        # slip; this stops a forged POST. Two different jobs, both needed.
        if not can_publish_event(request.user, form.cleaned_data["ministry"]):
            raise PermissionDenied(SCOPED_DENIAL)
        event = form.save(commit=False)
        event.owner = _my_contact(request)
        event.save()
        messages.success(request, "Event created. Next, open the roles it needs.")
        return redirect("events:event_update", pk=event.pk)

    return render(request, "events/event_form.html", {"form": form, "event": None})


@login_required
def event_update(request, pk):
    """Change an event — including its time, which is what makes P6 usable.

    Without this page a ministry admin could send "the time has changed" and
    have no way to change it: EventForm was only ever reachable from
    event_create, and the admin site is closed to them by StaffOnlyAdminMiddleware.
    Event.status has the same problem — nothing else can mark an event completed.

    A move goes through services.reschedule() rather than form.save(), so that
    every path that shifts an event runs the same full_clean inside the same
    transaction. Everything else is an ordinary save.
    """
    event = _managed_event(request, pk)
    form = EventForm(request.POST or None, request.FILES or None,
                     instance=event, user=request.user)
    if request.method == "POST" and form.is_valid():
        # Re-checked on the submitted value, exactly as event_create does: the
        # dropdown is narrowed, but a POST can still name any ministry id — and
        # handing an event to a ministry you do not run is the same over-reach
        # as publishing into one.
        if not can_publish_event(request.user, form.cleaned_data["ministry"]):
            raise PermissionDenied(SCOPED_DENIAL)

        # The form answers this, not the view: it depends on what its widgets
        # can express. See EventForm.time_changed for the seconds trap.
        moved = form.time_changed()
        event = form.save(commit=False)
        if moved:
            reschedule(
                event,
                start_time=form.cleaned_data["start_time"],
                end_time=form.cleaned_data["end_time"],
            )
            # Straight to the notice, with the reason already chosen. Whoever
            # moved an event that people signed up for is one click from telling
            # them, instead of having to know that the page exists.
            messages.success(request, "Time changed. Tell the volunteers who signed up.")
            return redirect(
                f"{reverse('events:event_notify', args=[event.pk])}"
                f"?reason={EventNotification.Reason.TIME_CHANGED}"
            )
        event.save()
        messages.success(request, "Event updated.")
        return redirect("events:event_detail", pk=event.pk)

    return render(request, "events/event_form.html",
                  _edit_page_context(event, form=form))


def _edit_page_context(event, *, form=None, role_form=None, user=None):
    """Everything the merged edit page needs, from whichever view got the POST.

    2026-08-04: the event's own form and its list of roles are one page. They
    were two, and the second one had no entrance of its own — you reached
    "Roles" from a redirect after creating the event, and a day later there was
    no way back to it except the URL.

    ⚠️ Built once, here, so `event_update` and `event_roles` cannot render the
       same page from two different contexts. They both post to it, and only
       one of the two forms is bound on any given request — the other has to be
       a fresh one or the page comes back with somebody else's errors on it.
    """
    return {
        "event": event,
        # Always true here: every path into this page goes through
        # _managed_event() first. Passed explicitly rather than left out, so the
        # shared nav does not have to treat "missing" as "false".
        "can_manage": True,
        "form": form if form is not None else EventForm(instance=event, user=user),
        "role_form": role_form if role_form is not None else EventRoleForm(event=event),
        "roles": event.roles.with_signup_counts().select_related("role"),
    }


@login_required
def event_roles(request, pk):
    """P2's second half: which jobs, and how many people each one wants.

    ⚠️ No page of its own any more — a GET here goes to the edit page, which
       renders the same panel. The URL stays because templates, tests and
       anybody's bookmarks point at it, and because the roles form still posts
       here: deleting the route would have been a bigger change than merging
       the pages was.
    """
    event = _managed_event(request, pk)
    if request.method != "POST":
        return redirect("events:event_update", pk=event.pk)

    form = EventRoleForm(request.POST, event=event)
    if form.is_valid():
        form.save()
        messages.success(request, "Role added.")
        # ⭐ The plain-form path is the one that must always work: redirect, so a
        #    refresh cannot post twice. HTMX gets the list back instead — same
        #    write, same message, one fewer full page.
        if not request.headers.get("HX-Request"):
            return redirect("events:event_update", pk=event.pk)
        form = EventRoleForm(event=event)
    elif not request.headers.get("HX-Request"):
        # Errors have to survive, so this one renders rather than redirects —
        # and it renders the merged page, because that is the only page these
        # fields now live on.
        return render(request, "events/event_form.html",
                      _edit_page_context(event, role_form=form, user=request.user))

    return render(request, _template(
        request, "events/event_form.html", "events/_event_roles_swap.html"),
        _edit_page_context(event, role_form=form, user=request.user))


@login_required
def role_delete(request, pk):
    """Remove a job opened by mistake."""
    role = get_object_or_404(EventRole.objects.select_related("event__ministry"), pk=pk)
    if not can_manage_event(request.user, role.event):
        raise PermissionDenied(SCOPED_DENIAL)
    if request.method == "POST":
        role.delete()
        messages.success(request, "Role removed.")
        if request.headers.get("HX-Request"):
            # Same panel event_roles renders, so the two can never disagree
            # about what is in the list.
            return render(request, "events/_event_roles_swap.html",
                          _edit_page_context(role.event, user=request.user))
    return redirect("events:event_update", pk=role.event_id)


@login_required
def event_registrations(request, pk):
    """P4's first half: who signed up, by role. Read-only, so the read check."""
    event = get_object_or_404(Event.objects.select_related("ministry"), pk=pk)
    if not can_view_event_records(request.user, event):
        raise PermissionDenied(SCOPED_DENIAL)

    roles = event.roles.with_signup_counts().select_related("role").prefetch_related(
        Prefetch(
            "participations",
            queryset=Participation.objects.select_related("contact").order_by("contact"),
        )
    )
    return render(request, "events/event_registrations.html", {
        "event": event,
        # Drives the shared event nav: Edit and Notify are drawn only for
        # somebody who can actually open them.
        "can_manage": can_manage_event(request.user, event), "roles": roles,
    })


@login_required
def event_attendance(request, pk):
    """P4's second half: sign people in and out, or enter hours from paper.

    The minors and their emergency numbers are shown here because this is the
    page somebody has open when an ankle gets twisted. That is dialling a
    number on the spot; it is not the same thing as reaching a guardian before
    the event, which goes through consent_email / consent_phone (B11).
    """
    # ⭐ Two different questions, asked separately (2026-08-05). The foundation
    #    tier may read this page for any event; only the ministry's own admin
    #    may change anything on it.
    #
    # ⚠️ The POST check is the boundary. Not drawing the buttons is interface,
    #    and interface keeps nobody out — a form posted from anywhere at all
    #    arrives at this view with the same shape.
    event = get_object_or_404(Event.objects.select_related("ministry"), pk=pk)
    if not can_view_event_records(request.user, event):
        raise PermissionDenied(SCOPED_DENIAL)
    can_manage = can_manage_event(request.user, event)

    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied(SCOPED_DENIAL)
        participation = get_object_or_404(
            Participation.objects.filter(event_role__event=event),
            pk=request.POST.get("participation"),
        )
        action = request.POST.get("action")
        if action == "check_in":
            check_in(participation)
        elif action == "check_out":
            check_out(participation)
        elif action == "absent":
            # ⚠️ The refusal is shown, not swallowed. mark_absent() declines when
            #    the row already carries hours or a check-in, and a button that
            #    quietly does nothing reads as a broken page — the person clicks
            #    it again, then goes looking for the row somewhere else.
            try:
                mark_absent(participation)
            except TurnedUp as error:
                messages.error(request, "; ".join(
                    message for messages_ in error.message_dict.values()
                    for message in messages_
                ))
        elif action == "hours":
            hours_form = HoursForm(request.POST)
            if hours_form.is_valid():
                # The paper-sheet path: no timestamps, just a number. Same
                # field, because there is only one authoritative value.
                record_hours(participation, hours_form.cleaned_data["hours"])
        elif action == "undo":
            # ⚠️ Its own action, never folded into "absent". "I clicked the
            #    wrong row" and "they did not come" are different facts, and
            #    one button doing both would put every mis-click into the
            #    no-show rate. See services.undo_attendance.
            undo_attendance(participation)
        elif action == "clear_hours":
            # ⚠️ Empty is not zero. Until this existed, an hours figure typed by
            #    mistake could only be corrected to a wrong-but-plausible 0.
            clear_hours(participation)
        # ⭐ HTMX swaps just this person's row; the plain form path redirects, as
        #    it always did. This is the page the rule was written for — checking
        #    forty people in one at a time reloads the whole table forty times.
        if request.headers.get("HX-Request"):
            participation.refresh_from_db()
            return render(request, "events/_attendance_row_swap.html", {
                "row": participation,
                "hours_form": HoursForm(),
                "scheduled_hours": scheduled_hours(event),
                "can_manage": True,
            })
        return redirect("events:event_attendance", pk=event.pk)

    rows = (
        Participation.objects.filter(event_role__event=event)
        .notifiable()
        .select_related("contact", "event_role__role")
        .prefetch_related("contact__emergency_contacts")
        .order_by("event_role__role__name", "contact")
    )
    return render(request, "events/event_attendance.html", {
        "event": event,
        "participations": rows,
        "hours_form": HoursForm(),
        "can_manage": can_manage,
        # What the box starts at for somebody with no hours yet. Computed in
        # services, never here — this is date arithmetic, and there is a grep
        # guard on views doing any (D18).
        "scheduled_hours": scheduled_hours(event),
    })


@login_required
def event_report(request, pk):
    """R3–R8 for one event. Every number arrives from services.py.

    ⚠️ Reads, so it asks the read check — not `_managed_event()`, which is the
       write gate this page used to go through. The foundation tier may read
       any event's report without being able to touch the event.
    """
    event = get_object_or_404(Event.objects.select_related("ministry"), pk=pk)
    if not can_view_event_records(request.user, event):
        raise PermissionDenied(SCOPED_DENIAL)
    return render(request, "events/event_report.html", {
        "event": event,
        "can_manage": can_manage_event(request.user, event),
        "summary": event_summary(event),
        "staff": ministry_staff_participation(event),
    })


@login_required
def event_notify(request, pk):
    """P6: tell everybody signed up that the event changed.

    can_manage_event(), the same check as the attendance page — putting a
    message in front of everybody who signed up is a write, not a read.

    GET is the preview, and the three groups on it are the point: who is being
    told directly, whose *guardian* is being told instead, and who cannot be
    reached at all. That third group is the one thing about this page that
    could fail silently, so it is shown even when it is empty.
    """
    event = _managed_event(request, pk)
    recipients, unreachable = resolve_recipients(event)

    if request.method == "POST":
        form = NotifyForm(request.POST)
        if form.is_valid():
            notify_event_change(
                event,
                reason=form.cleaned_data["reason"],
                message=form.cleaned_data["message"],
                sent_by=request.user,
            )
            messages.success(
                request,
                f"Notified {len(recipients)}; {len(unreachable)} could not be reached.",
            )
            return redirect("events:event_notify", pk=event.pk)
    else:
        reason = request.GET.get("reason") or EventNotification.Reason.TIME_CHANGED
        form = NotifyForm(initial={
            "reason": reason,
            "message": default_message(event, reason),
        })

    return render(request, "events/event_notify.html", {
        "event": event,
        # Always true: this view is gated on can_manage_event above.
        "can_manage": True,
        "form": form,
        "recipients": [r for r in recipients if not r.is_guardian],
        "guardian_recipients": [r for r in recipients if r.is_guardian],
        "unreachable": unreachable,
        # "Last notified 5 minutes ago" is the only thing standing between a
        # shaky connection and two identical notices (D22, cost 3).
        "previous": event.notifications.all()[:5],
    })


# --- D28: the QR check-in ------------------------------------------------
#
# ⭐ The whole point of this pair of views is that "you were standing in front
#    of the screen" and "you are logged in as Maria" are two separate questions.
#    The scan answers the first inside 90 seconds and hands the answer to the
#    session; the login answers the second at whatever speed a phone keyboard
#    allows. Merging them back into one view — the obvious simplification —
#    reintroduces the failure D28 was written to remove: every volunteer's first
#    ever check-in ends with an expired token and a walk back to the iPad.


def checkin_scan(request, token):
    """The URL inside the QR code. Verifies presence, then gets out of the way.

    ⚠️ Deliberately **not** @login_required, and that is not a hole: it writes
       nothing. It turns a token into a session credential and redirects, and
       the credential names an event and a direction, not a person. Who that
       person is gets decided by the login the redirect leads to.

    ⚠️ A GET, and it must therefore stay free of writes to the database. Link
       previewers in messaging apps, browser prefetch and corporate URL scanners
       all fetch this address without a human touching it — the original design
       recorded attendance here, which means forwarding the link into any chat
       window would check somebody in.
    """
    try:
        event_id, mode = verify_checkin_token(token)
    except InvalidCheckInToken as error:
        return render(request, "events/checkin_refused.html", {
            "reason": "; ".join(error.messages),
        }, status=400)

    request.session[CHECKIN_CREDENTIAL_KEY] = issue_credential(event_id, mode)
    return redirect("events:checkin_confirm")


@login_required
@ratelimit(key="user", rate="30/m", method="POST", block=False)
def checkin_confirm(request):
    """What the volunteer actually taps. One row, one write, safe to repeat.

    ⚠️ The credential is read from the session, never from the form. Putting the
       token in a hidden field is the tempting shortcut, and it quietly undoes
       the split above: the 90-second window would then have to survive the
       login, which is the thing it was moved out of the way of.
    """
    contact = _my_contact(request)
    try:
        event_id, mode = read_credential(request.session.get(CHECKIN_CREDENTIAL_KEY))
    except CredentialExpired as error:
        return render(request, "events/checkin_refused.html", {
            "reason": "; ".join(error.messages),
        }, status=400)

    event = get_object_or_404(
        Event.objects.visible_to_volunteers().select_related("ministry"), pk=event_id)
    targets = scan_targets(contact, event, mode) if contact else None

    if targets is None or not targets.any_signup:
        return render(request, "events/checkin_refused.html", {
            "event": event,
            "reason": "You are not signed up for this event.",
            # ⚠️ A link, never a signup. Creating the row here would walk past
            #    sign_up()'s two gates, and the state on the other side of them
            #    is a minor recorded as present with nobody to call.
            "action_url": reverse("events:event_signup", args=[event.pk]),
            "action_label": "Sign up for this event",
        }, status=400)

    if request.method == "POST":
        if getattr(request, "limited", False):
            return render(request, "events/checkin_refused.html", {
                "event": event,
                "reason": "That was a lot of taps. Wait a moment and try again.",
            }, status=429)
        chosen = request.POST.get("participation")
        # Membership of these lists is the authorisation: both are already
        # narrowed to this contact and this event.
        #
        # ⚠️ `done` is in the set on purpose, and leaving it out was a real bug
        #    caught by its test. The commonest second POST comes from a page
        #    that was already open when the first one succeeded — a slow network
        #    and an impatient thumb — and by then the row has moved from pending
        #    to done. Accepting only pending answers that person with a 404,
        #    which reads as "the site is broken" at the exact moment their
        #    check-in has in fact worked. apply_scan() reports it as changed=False
        #    and the page says "you already checked in at 9:03".
        #
        # ⚠️ `needs_check_in` stays out. Those rows would also be a no-op, but
        #    the honest answer for them is "check in first", not "you already
        #    checked out".
        allowed = {str(row.pk): row for row in targets.pending + targets.done}
        if chosen not in allowed:
            raise Http404
        try:
            participation, changed = apply_scan(
                chosen, contact=contact, event_id=event.pk, mode=mode)
        except ConsentRequired as error:
            # ⚠️ Shown, not swallowed, and not a 500. The person this refusal
            #    concerns is standing in a hall holding a phone, and the fix is
            #    on their profile page — so say which fix.
            return render(request, "events/checkin_refused.html", {
                "event": event,
                "reason": "; ".join(
                    message for group in error.message_dict.values() for message in group
                ),
                "action_url": reverse("accounts:profile"),
                "action_label": "Go to my profile",
            }, status=400)
        del request.session[CHECKIN_CREDENTIAL_KEY]
        messages.success(request, checkin_result_message(participation, mode, changed))
        return redirect("events:my_participations")

    return render(request, "events/checkin_confirm.html", {
        "event": event,
        "mode": mode,
        "checking_in": mode == CHECK_IN,
        "targets": targets,
        # One row is the ordinary case and gets no question; several is rare and
        # is the only case where anybody is asked anything (D28 五).
        "only": targets.pending[0] if len(targets.pending) == 1 else None,
    })


@login_required
def checkin_display(request, pk):
    """The page that lives on the iPad. Draws nothing itself — the JS does.

    Read-only as far as the database is concerned, so the permission is the
    manage one purely because of what it grants access to: whoever can open this
    page can mint check-in codes for everybody at the event.
    """
    event = _managed_event(request, pk)
    return render(request, "events/checkin_display.html", {
        "event": event,
        "can_manage": True,
        # ⚠️ Computed here, once, at page load — never re-derived in the
        #    browser. An iPad that flipped from Check in to Check out on its own
        #    halfway through would turn the queue in front of it into check-outs
        #    with nothing on screen saying so. From here on the admin decides.
        "default_mode": default_checkin_mode(event),
        "closed_message": window_message(event),
        "token_url": reverse("events:checkin_token", args=[event.pk]),
    })


@login_required
@ratelimit(key="user", rate="60/m", block=False)
def checkin_token(request, pk):
    """A fresh code for the display. JSON, and the one endpoint that must not leak.

    ⚠️ This permission check **is** the scheme. Without it any signed-in
       volunteer fetches a live token from their sofa and books themselves in;
       every rotating-code measure above it becomes decoration. It is the first
       statement in the function for that reason.
    """
    event = _managed_event(request, pk)
    if getattr(request, "limited", False):
        return JsonResponse({"error": "Too many requests."}, status=429)
    mode = request.GET.get("mode")
    if mode not in MODES:
        raise Http404
    if not window_is_open(event):
        return JsonResponse({"error": window_message(event)}, status=409)
    token, expires_at = issue_with_expiry(event.pk, mode)
    return JsonResponse({
        # ⚠️ The whole URL is assembled here and the browser only draws it.
        #    A script that built this address from parts would be a second
        #    definition of the route, and the QR is the one place where being
        #    subtly wrong produces a code that scans perfectly and goes nowhere.
        "url": request.build_absolute_uri(
            reverse("events:checkin_scan", args=[token])),
        "expires_at": expires_at,
    })
