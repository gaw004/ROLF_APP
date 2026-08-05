"""The self-service pages and the ministry-admin pages.

Thin shells, every one of them. Three rules hold across the whole file:

1. Visibility is decided in the query, never in the template. Hiding a draft
   with {% if %} still sent it to the browser; filtering it out means it was
   never fetched. Same for "mine": the queryset is narrowed to the logged-in
   contact, so somebody else's id in the URL can only 404.
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
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

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
from .services import (
    ConsentRequired,
    cancel,
    check_in,
    check_out,
    confirm_signup,
    default_message,
    event_summary,
    ministry_staff_participation,
    notify_event_change,
    record_hours,
    reschedule,
    scheduled_hours,
    resolve_recipients,
    set_status,
    sign_up,
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
    if marker == "past":
        return reverse("events:past_events"), "Past events"
    if marker == "manage":
        administers_any = bool(ministry_ids_administered_by(request.user))
        if administers_any or in_foundation_tier(request.user):
            # Same label the navigation uses for this account, so the two do
            # not name one page two different things.
            label = "Events I manage" if administers_any else "All events"
            return reverse("events:event_manage_list"), label
    return reverse("events:event_list"), "Events"


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
    return render(request, _template(
        request, "events/event_list.html", "events/_event_list_results.html"), {
        "events": events,
        "period": period,
        # R1, in the plainest possible form: how many, in the window they asked
        # for. Counted here rather than with {{ events|length }} so the template
        # cannot quietly count a different set than the one it lists.
        "total": events.count(),
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
    return render(request, _template(
        request, "events/past_events.html", "events/_past_events_results.html"), {
        "events": events,
        "period": period,
        "total": events.count(),
    })


@login_required
def event_detail(request, pk):
    """visible_to_volunteers(), so a confirmed or finished event still opens.

    ⚠️ Written as status == OPEN this page would 404 the moment an event filled
       up — for exactly the people who had signed up, and for P6's "can't make
       the new time? cancel here" link, which is sent to precisely them.
    """
    event = get_object_or_404(
        Event.objects.visible_to_volunteers().select_related("ministry", "event_type"),
        pk=pk,
    )
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


@login_required
def event_manage_list(request):
    """Everything this account administers — including drafts and finished ones.

    The entrance the ministry-admin side never had. event_roles links onward to
    registrations, attendance, the report and the notice page, but the only way
    to reach event_roles was the redirect after creating an event: come back
    tomorrow and there was no route to any of it. event_list is no help, since
    it shows only what is open and upcoming — which excludes drafts, and
    excludes every event whose report anybody would actually want to read.

    No ministry filter beyond the scope itself: somebody who administers two
    ministries wants one list, and the ministry is a column.
    """
    administered = ministry_ids_administered_by(request.user)
    # The foundation tier gets the same page over every ministry — read only.
    # ⚠️ Without this it had no entrance at all: it holds no MinistryRole, so
    #    nothing anywhere listed events for it to open. That is the same gap
    #    C0.2 closed five times over — the pages existed and nothing pointed at
    #    them.
    foundation = in_foundation_tier(request.user)
    if not administered and not foundation:
        raise PermissionDenied(SCOPED_DENIAL)

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

    events = Event.objects.all() if not administered else Event.objects.filter(
        ministry_id__in=administered)
    # ⚠️ Somebody who is both — a foundation admin who also runs a ministry —
    #    keeps the managing view of their own ministries rather than the
    #    read-only view of everything. Losing the ability to publish an event
    #    because you were also promoted would be a strange way to be rewarded.
    events = events.select_related("ministry", "event_type").order_by("-start_time")
    return render(request, "events/event_manage_list.html", {
        "events": events,
        "can_manage": bool(administered),
        # One unbound form, reused to draw every row's dropdown: the choices are
        # identical, and building one per event would be a form per row for no
        # gain.
        "status_form": EventStatusForm(),
    })


@login_required
def event_create(request):
    """P2: publish an event, for a ministry this person actually runs."""
    if not ministry_ids_administered_by(request.user):
        raise PermissionDenied(SCOPED_DENIAL)

    form = EventForm(request.POST or None, user=request.user)
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
    form = EventForm(request.POST or None, instance=event, user=request.user)
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
        elif action == "hours":
            hours_form = HoursForm(request.POST)
            if hours_form.is_valid():
                # The paper-sheet path: no timestamps, just a number. Same
                # field, because there is only one authoritative value.
                record_hours(participation, hours_form.cleaned_data["hours"])
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
