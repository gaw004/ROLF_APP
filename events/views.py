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

from org.permissions import (
    SCOPED_DENIAL,
    can_manage_event,
    can_publish_event,
    can_view_registrations,
    ministry_ids_administered_by,
)

from .forms import EventForm, EventRoleForm, HoursForm, SignUpForm
from .models import Event, EventRole, Participation
from .services import (
    ConsentRequired,
    cancel,
    check_in,
    check_out,
    event_summary,
    ministry_staff_participation,
    record_hours,
    sign_up,
)


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
    events = (
        Event.objects.open_for_signup()
        .upcoming()
        .select_related("ministry", "event_type")
        .order_by("start_time")
    )
    return render(request, "events/event_list.html", {"events": events})


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
    return render(request, "events/event_detail.html", {
        "event": event,
        "roles": event.roles.with_signup_counts().select_related("role"),
        "mine": mine,
        "can_sign_up": event.status in Event.OPEN_FOR_SIGNUP,
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
            sign_up(
                contact=contact,
                event_role=form.cleaned_data["event_role"],
                consent=form.consent(),
            )
        except (ConsentRequired, ValidationError) as error:
            form.add_error(None, error)
        else:
            messages.success(request, "报名成功。")
            return redirect("events:event_detail", pk=event.pk)

    return render(request, "events/event_signup.html", {
        "event": event, "form": form, "needs_consent": form.needs_consent,
    })


@login_required
def my_participations(request):
    """Mine means mine — narrowed in the query, not in the template."""
    contact = _my_contact(request)
    rows = Participation.objects.none()
    if contact is not None:
        rows = (
            Participation.objects.filter(contact=contact)
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
        messages.success(request, "已取消报名。")
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
        messages.success(request, "活动已建立，接下来开工种。")
        return redirect("events:event_roles", pk=event.pk)

    return render(request, "events/event_form.html", {"form": form})


@login_required
def event_roles(request, pk):
    """P2's second half: which jobs, and how many people each one wants."""
    event = _managed_event(request, pk)
    form = EventRoleForm(request.POST or None, event=event)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "工种已添加。")
        return redirect("events:event_roles", pk=event.pk)

    return render(request, "events/event_roles.html", {
        "event": event,
        "form": form,
        "roles": event.roles.with_signup_counts().select_related("role"),
    })


@login_required
def role_delete(request, pk):
    """Remove a job opened by mistake."""
    role = get_object_or_404(EventRole.objects.select_related("event__ministry"), pk=pk)
    if not can_manage_event(request.user, role.event):
        raise PermissionDenied(SCOPED_DENIAL)
    if request.method == "POST":
        role.delete()
        messages.success(request, "工种已删除。")
    return redirect("events:event_roles", pk=role.event_id)


@login_required
def event_registrations(request, pk):
    """P4's first half: who signed up, by role. Read-only, so the read check."""
    event = get_object_or_404(Event.objects.select_related("ministry"), pk=pk)
    if not can_view_registrations(request.user, event):
        raise PermissionDenied(SCOPED_DENIAL)

    roles = event.roles.with_signup_counts().select_related("role").prefetch_related(
        Prefetch(
            "participations",
            queryset=Participation.objects.select_related("contact").order_by("contact"),
        )
    )
    return render(request, "events/event_registrations.html", {
        "event": event, "roles": roles,
    })


@login_required
def event_attendance(request, pk):
    """P4's second half: sign people in and out, or enter hours from paper.

    can_manage_event(), not the read check — this one writes.

    The minors and their emergency numbers are shown here because this is the
    page somebody has open when an ankle gets twisted. That is dialling a
    number on the spot; it is not the same thing as reaching a guardian before
    the event, which goes through consent_email / consent_phone (B11).
    """
    event = _managed_event(request, pk)

    if request.method == "POST":
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
    })


@login_required
def event_report(request, pk):
    """R3–R8 for one event. Every number arrives from services.py."""
    event = _managed_event(request, pk)
    return render(request, "events/event_report.html", {
        "event": event,
        "summary": event_summary(event),
        "staff": ministry_staff_participation(event),
    })
