"""What happens to a signup: joining, arriving, leaving, being counted.

Permanent asset (D18). Views call these and render the result; nothing here
knows what a template or a request is, so the front end that replaces the admin
imports the same functions unchanged.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Sum

from core.timeutils import local_now
from org.models import Assignment, Position

from .models import Participation


class ConsentRequired(ValidationError):
    """A minor (or somebody whose age we do not know) signing up without consent.

    A subclass rather than a bare ValidationError so a view can tell this apart
    from an ordinary form error and re-render with the consent section open.
    """


def sign_up(*, contact, event_role, consent=None):
    """Sign `contact` up for `event_role`. Returns the new Participation.

    ⚠️ This is a hint layer, not enforcement, and is not dressed up as more.
       The consent rule spans two tables — the age is on Contact, the signup is
       here — so no CheckConstraint can express it and bulk_create walks
       straight past this function. D14 says record that plainly.

    Two rules, both of them P3's:

    1. A minor cannot sign up without a consent record. Somebody whose birth
       date is unknown counts as a minor: is_minor is deliberately three-state
       (B4.5), and folding "unknown" into "adult" is exactly how a minor with
       no date on file disappears from the list of people whose parents need
       telling.
    2. That consent must carry an email address or a phone number. Consent with
       only a *name* on it satisfies the paperwork and leaves P6 unable to reach
       the guardian at all — the signup would be recorded already doomed to be
       unreachable, which is the failure this project keeps convicting: nothing
       raises, the person simply never hears.
    """
    consent = dict(consent or {})
    needs_consent = contact.is_minor in (True, None)

    if needs_consent:
        if not consent.get("consent_given_by") or not consent.get("consent_method"):
            raise ConsentRequired({
                "consent_given_by": "A guardian's consent is needed before a minor "
                                    "can sign up. (An unknown birth date is treated "
                                    "as a minor.)",
            })
        if not consent.get("consent_email") and not consent.get("consent_phone"):
            raise ConsentRequired({
                "consent_email": "Give the guardian an email address or a phone "
                                 "number — without one they cannot be told if the "
                                 "event changes.",
            })
        consent.setdefault("consent_at", local_now())
    else:
        # An adult's row carries no consent fields, even if a form sent some.
        consent = {}

    participation = Participation(
        contact=contact,
        event_role=event_role,
        registered_at=local_now(),
        **consent,
    )
    # full_clean() rather than a bare save(): the uniqueness of (event_role,
    # contact) has to come back as a form error, not as an IntegrityError 500.
    participation.full_clean(exclude=["registered_at"])
    participation.save()
    return participation


def cancel(participation):
    """The volunteer is not coming after all.

    A status change, never a delete: "they signed up and pulled out" and "they
    were never here" are different facts, and the notification history points
    at these rows.
    """
    participation.status = Participation.Status.CANCELLED
    participation.save(update_fields=["status", "updated_at"])
    return participation


def check_in(participation, *, at=None):
    """They turned up. Records the time and moves status to attended."""
    participation.checked_in_at = at or local_now()
    participation.status = Participation.Status.ATTENDED
    participation.full_clean(exclude=["registered_at"])
    participation.save()
    return participation


def check_out(participation, *, at=None):
    """They left. Records the time and *writes* the elapsed hours.

    ⚠️ Writes, does not derive. Once somebody has corrected hours by hand, that
       value stands — do not recompute it from the timestamps and overwrite it.
       Three ordinary situations make the timestamps the wrong authority:
       somebody forgets to check out (their hours would be zero forever though
       they worked four), somebody is entered afterwards from a paper sheet (no
       timestamps at all, just "she did 3 hours"), and somebody leaves and comes
       back (one pair of timestamps cannot say it, but the hours can).

    ⚠️ Nor is hours a property over the timestamps. Two fields answering one
       question independently is Relationship.is_active next to end_date: two
       answers, free to disagree, and nothing to tell you they have.
    """
    at = at or local_now()
    participation.checked_out_at = at
    if participation.checked_in_at and participation.hours is None:
        elapsed = at - participation.checked_in_at
        participation.hours = Decimal(elapsed.total_seconds()) / Decimal(3600)
        participation.hours = participation.hours.quantize(Decimal("0.01"))
    if participation.hours is not None:
        participation.status = Participation.Status.ATTENDED
    participation.full_clean(exclude=["registered_at"])
    participation.save()
    return participation


def record_hours(participation, hours):
    """Enter hours by hand — the paper sign-in sheet, with no timestamps.

    Goes through the same field as check_out() because there is only one
    authoritative value; what differs is where the number came from.
    """
    participation.hours = hours
    participation.status = Participation.Status.ATTENDED
    participation.full_clean(exclude=["registered_at"])
    participation.save()
    return participation


# --- Statistics: R1–R8 --------------------------------------------------
# All of it here or on a QuerySet, none of it in a view. Changing the interface
# is not hypothetical in this project — it is scheduled — and anything a view
# computes gets rewritten with the templates (D18).


def event_summary(event):
    """R3–R7 for one event, in two queries.

    Returns duration, the number of roles opened, and per-role signup and hours
    figures. Note that the role count comes from EventRole and never from
    DISTINCT over signups: an event that opened five roles and filled three has
    five, and that is the single acceptance point of D19.
    """
    roles = list(
        event.roles.with_signup_counts()
        .select_related("role")
        .annotate(hours_total=Sum("participations__hours"))
    )
    return {
        "duration": event.duration,                     # R3
        "role_count": len(roles),                       # R4 — from the roles, not the signups
        "roles": roles,                                 # R5 + R7
        # R6. Summed from the same rows as R7 so the total and the breakdown
        # cannot drift apart; None (not yet recorded) stays out rather than
        # counting as zero.
        "total_hours": sum(
            (role.hours_total for role in roles if role.hours_total is not None),
            Decimal("0"),
        ),
    }


def ministry_staff_participation(event):
    """R8: which employees of the ministry running this event took part, doing what.

    Three traps in this one query, all of them silent:

    1. The date is the day of the *event*, not today. Asking .active() with its
       default about last year's event drops everybody who has left since, and
       reports a smaller number without a word. That is what the `on` parameter
       exists for (D16, layer 2).
    2. .active(), not .serving(). The question is "were they an employee of this
       ministry at the time", not "could they work a shift today" — somebody on
       leave who came along still counts.
    3. .distinct() is not optional. One person may hold two employee posts in
       one ministry (D11's central case), and the join would list their
       participation twice — the headcount quietly gains a person.
    """
    on = event.start_time.date()
    employed_here = Assignment.objects.active(on=on).filter(
        position__kind=Position.Kind.EMPLOYEE,
        position__ministry=event.ministry,
        position__is_active=True,
    )
    return (
        Participation.objects.filter(
            event_role__event=event,
            contact__assignments__in=employed_here,
        )
        .select_related("contact", "event_role__role")
        .distinct()
    )


def events_in_period(start, end):
    """R1 + R2: how many events ran in a window, and which ministry each is from."""
    from .models import Event

    return (
        Event.objects.in_period(start, end)
        .select_related("ministry", "event_type")
        .annotate(role_count=Count("roles", distinct=True))
    )


@transaction.atomic
def reschedule(event, *, start_time, end_time):
    """Move an event. Kept here so the notification page has one thing to call.

    Does not touch anybody's signup: a change of time is not a change of who
    said yes. Re-confirmation would be a second dimension on
    Participation.status — see D22's closing section for why there is no
    needs_reconfirmation.
    """
    event.start_time = start_time
    event.end_time = end_time
    event.full_clean(exclude=["created_at", "updated_at"])
    event.save()
    return event


def duration_hours(delta: datetime.timedelta) -> Decimal:
    """A timedelta as hours, two decimal places — for display only."""
    return (Decimal(delta.total_seconds()) / Decimal(3600)).quantize(Decimal("0.01"))
