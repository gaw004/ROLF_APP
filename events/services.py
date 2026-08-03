"""What happens to a signup: joining, arriving, leaving, being counted.

Permanent asset (D18). Views call these and render the result; nothing here
knows what a template or a request is, so the front end that replaces the admin
imports the same functions unchanged.
"""

import datetime
from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Sum

from contact.models import Contact
from core.notifications.base import EMAIL, SMS, Message, get_backend
from core.timeutils import local_date_of, local_now
from org.models import Assignment, Position

from .models import EventNotification, Participation


class ConsentRequired(ValidationError):
    """A minor (or somebody whose age we do not know) signing up without consent.

    A subclass rather than a bare ValidationError so a view can tell this apart
    from an ordinary form error and re-render with the consent section open.
    """


def consent_required_for(contact, event):
    """Does this person, on this event, need a guardian's consent?

    Two conditions, and both have to hold. The person: is_minor is three-state
    and the unknown case takes the cautious branch, because folding "no date on
    file" into "adult" is exactly how a minor stops having anybody notified on
    their behalf. The event: requires_guardian_consent, which a coordinator sets
    when publishing — sorting tins on a Saturday morning with parents in the
    room is not a weekend away, and one blanket answer would either burden the
    first or under-protect the second.

    ⚠️ One function, called from both gates. sign_up() refuses to create an
       unconsented minor's row and _mark_attended() refuses to turn one into
       hours; asking the question differently in those two places is how an
       event ends up refusing the signup and then accepting the attendance.
    """
    return contact.is_minor in (True, None) and event.requires_guardian_consent


# The consent columns on Participation, named once. sign_up() has to be able to
# clear all of them when an adult reuses a row a minor's signup once filled in.
CONSENT_FIELDS = (
    "consent_given_by", "consent_method", "consent_email", "consent_phone",
)


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
    needs_consent = consent_required_for(contact, event_role.event)

    if needs_consent:
        # An emergency contact first, before anything about consent is asked.
        #
        # ⚠️ Checked here rather than on the form because the form is not the
        #    only door: an admin entering somebody from a paper list reaches
        #    this function too, and a minor with nobody to call is the state
        #    this rule exists to prevent — on the day, not at signup. It also
        #    has to come before the consent questions: telling somebody their
        #    guardian's email is missing, when the real answer is "add an
        #    emergency contact first", sends them to the wrong page.
        if not contact.emergency_contacts.exists():
            raise ConsentRequired({
                "__all__": "Add an emergency contact to your profile before "
                           "signing up. Anyone under 18 — or whose date of "
                           "birth we do not have — needs one on file.",
            })
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

    # Signing up again after pulling out reuses the existing row rather than
    # adding a second one.
    #
    # ⚠️ cancel() is a status change, never a delete (the notification history
    #    points at these rows), so the cancelled row still holds the unique
    #    (event_role, contact) pair. Building a fresh Participation here made
    #    changing your mind a permanent decision: the second signup came back
    #    as "you have already signed up for this role", which is both wrong and
    #    unfixable from the volunteer's side. Found in the browser, not by the
    #    tests — every test that cancelled a signup stopped there.
    existing = Participation.objects.filter(
        contact=contact, event_role=event_role).first()
    if existing is not None:
        if existing.status != Participation.Status.CANCELLED:
            raise ValidationError({
                "event_role": "You have already signed up for this role.",
            })
        participation = existing
        participation.status = Participation.Status.REGISTERED
        participation.registered_at = local_now()
        # Yesterday's consent does not carry: the form has just collected it
        # again, and an adult's row must end up with none at all.
        for field, value in {**{name: "" for name in CONSENT_FIELDS}, **consent}.items():
            setattr(participation, field, value)
        if not consent:
            participation.consent_relationship = None
            participation.consent_at = None
    else:
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


def _mark_attended(participation):
    """Move a row to attended — the one place that transition is made.

    ⚠️ The other half of P3's rule, and the half that had been left out. sign_up()
       refuses to create a minor's row without consent, but that is a hint layer:
       bulk_create and the admin both walk straight past it, so such a row can
       exist. The gate that matters then is this one — "did they turn up" is what
       the hours, the statistics and the notifications all hang off, and marking
       an unconsented minor as attended is the state the rule exists to forbid.

    Cross-table again (the age is on Contact, the signup is here), so no
    CheckConstraint can say it and D14 says to record that plainly rather than
    dress it up. All three routes to attended come through here — check_in(),
    check_out() and the paper-sheet record_hours() — because a rule with three
    entrances and one guard is a rule with two ways round it.
    """
    if (consent_required_for(participation.contact, participation.event)
            and participation.consent_at is None):
        raise ConsentRequired({
            "consent_given_by": "There is no guardian's consent on this signup, so "
                                "it cannot be marked as attended. (An unknown birth "
                                "date is treated as a minor.)",
        })
    participation.status = Participation.Status.ATTENDED


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
    """They turned up. Records the time and moves status to attended.

    Refuses a minor with no consent on the row — see _mark_attended().
    """
    participation.checked_in_at = at or local_now()
    _mark_attended(participation)
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
       question independently is is_active sitting next to end_date: two
       answers, free to disagree, and nothing to tell you they have.
    """
    at = at or local_now()
    participation.checked_out_at = at
    if participation.checked_in_at and participation.hours is None:
        elapsed = at - participation.checked_in_at
        participation.hours = Decimal(elapsed.total_seconds()) / Decimal(3600)
        participation.hours = participation.hours.quantize(Decimal("0.01"))
    if participation.hours is not None:
        _mark_attended(participation)
    participation.full_clean(exclude=["registered_at"])
    participation.save()
    return participation


def record_hours(participation, hours):
    """Enter hours by hand — the paper sign-in sheet, with no timestamps.

    Goes through the same field as check_out() because there is only one
    authoritative value; what differs is where the number came from.
    """
    participation.hours = hours
    _mark_attended(participation)
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

    ⚠️ And the day itself comes from local_date_of(), never from asking the
       stored instant for its own day. That value comes back in UTC, so an
       event running at 6pm Pacific reports the following day and trap 1 then
       fires against a date that is off by one. This function shipped that way
       for about an hour; see 02-roadmap.md「计划外（B12）」.
    """
    on = local_date_of(event.start_time)
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


def events_in_period(start, end, ministry=None):
    """R1 + R2 + R3: what ran in a window, whose it was, and how long each took.

    The boundaries come in already resolved through core.timeutils — slicing a
    month in UTC puts the last evening of it into the next month, and the count
    is then wrong without anything saying so.

    In this phase R1–R3 are read off the admin changelist (date_hierarchy plus
    the ministry and duration columns), which is what the acceptance list walks.
    This is the same question asked in a form the front end can use, and the
    reason it exists here rather than being spelled out wherever it is needed:
    the period arithmetic and the ministry join should have one definition.
    """
    from .models import Event

    events = (
        Event.objects.in_period(start, end)
        .select_related("ministry", "event_type")
        .annotate(role_count=Count("roles", distinct=True))
    )
    return events.filter(ministry=ministry) if ministry is not None else events


def set_status(event, status):
    """Publish, close, or cancel an event — the one field the manage list edits inline.

    Separate from reschedule() because it is a different business event with a
    different consequence: moving an event obliges somebody to tell the
    volunteers, changing its status does not. Both go through full_clean so the
    end-after-start constraint is checked on either path.

    ⚠️ Not a plain save(update_fields=["status"]). draft -> open is publication:
       it is the moment volunteers can see the event at all, and it should fail
       loudly on a row that would not otherwise validate rather than quietly
       publishing a broken one.
    """
    event.status = status
    event.full_clean(exclude=["created_at", "updated_at"])
    event.save()
    return event


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


# --- P6: telling people the event changed --------------------------------
# Who to tell and at what address is business logic and stays here. Putting a
# message on the wire is an adapter (core/notifications). The dividing question
# is D18's, aimed at a different target: change notification provider — does
# this code have to move? If yes, it does not belong in the adapter.


@dataclass(frozen=True)
class Recipient:
    """One address a notice is going to, and on whose behalf."""

    participation: Participation
    to: str
    channel: str
    # Shown on the preview page: whoever is confirming the send should be able
    # to see that four of these are going to parents rather than volunteers.
    is_guardian: bool = False


@dataclass(frozen=True)
class Unreachable:
    """Somebody who should have been told and for whom we have no address.

    ⚠️ This group has to be worked out here and stored. A notification platform
       can answer "did this letter arrive"; it cannot answer "this person has no
       address at all", because it has never heard of them. A green "27 people
       notified" covering three people nobody could reach is the same failure
       this project keeps convicting: silent, and pointing the wrong way.
    """

    participation: Participation
    why: str


def _preferred_channels(contact):
    """The channels to try for an adult, best first.

    Reads Contact.preferred_communication_method and falls back to the other
    channel when the preferred one is empty. Two of the four choices are not
    deliverable by this system at all: `mail` is a postal address and `phone`
    means "ring them", neither of which a backend can do, so both fall through
    to whatever address exists.
    """
    preference = contact.preferred_communication_method
    order = [EMAIL, SMS]
    if preference == Contact.CommunicationMethod.SMS:
        order = [SMS, EMAIL]
    elif preference == Contact.CommunicationMethod.PHONE:
        # A phone preference means they would rather be reached on the phone;
        # SMS is the closest thing that can actually be sent.
        order = [SMS, EMAIL]
    return order


def _address_for(contact, channel):
    if channel == EMAIL:
        return contact.email or ""
    return str(contact.phone) if contact.phone else ""


def resolve_recipients(event):
    """Who should be told about a change to this event, and at what address.

    Returns (recipients, unreachable). Changing notification provider does not
    change a word of this function — that is the claim the split is making.

    Three rules:

    1. An adult is told directly, on their preferred channel, falling back to
       the other one when the preferred address is empty.
    2. A minor is told through their guardian. A fifteen-year-old may well have
       no phone of their own, so notifying them is the same as notifying
       nobody. Two paths, in order: the consent record taken at signup
       (consent_email / consent_phone), then their emergency contact — which
       carries a phone and no email, so that path is SMS-only, and with an
       email-only backend configured it is a real gap. A visible one: it shows
       up in this list rather than failing quietly.
    3. An unknown birth date is treated as a minor. is_minor is three-state,
       and folding "unknown" into "adult" is exactly how a minor with no date
       on file stops having anybody notified on their behalf.

    Cancelled signups are not notified: that person already said they are not
    coming.
    """
    recipients, unreachable = [], []
    rows = (
        Participation.objects.filter(event_role__event=event)
        .notifiable()
        .select_related("contact")
        .prefetch_related("contact__emergency_contacts")
    )

    for participation in rows:
        contact = participation.contact
        if contact.is_minor in (True, None):
            guardian = participation.guardian_address
            if guardian is None:
                # The emergency contact is the fallback, and it is phone-only
                # because that table has no email column at all.
                emergency = next(iter(contact.emergency_contacts.all()), None)
                guardian = (str(emergency.phone), SMS) if emergency else None
            if guardian is None:
                unreachable.append(Unreachable(
                    participation=participation,
                    why="Under 18 (or no date of birth on file), and no guardian "
                        "contact of any kind",
                ))
                continue
            address, channel = guardian
            recipients.append(Recipient(
                participation=participation, to=address, channel=channel,
                is_guardian=True,
            ))
            continue

        for channel in _preferred_channels(contact):
            address = _address_for(contact, channel)
            if address:
                recipients.append(Recipient(
                    participation=participation, to=address, channel=channel))
                break
        else:
            unreachable.append(Unreachable(
                participation=participation, why="No email address and no phone number",
            ))

    return recipients, unreachable


def confirm_signup(participation, *, backend=None):
    """Tell them the signup went through, and tell a minor's guardian too.

    Returns the DeliveryResults, which the caller is free to ignore: a
    confirmation that could not be sent must never undo a signup that was
    accepted. The row is the record; this is a courtesy on top of it.

    Who hears about it:

    · the volunteer, on their own preferred channel — including a minor, who
      may well have a phone and an email of their own. Sending only to a parent
      would leave the person who actually has to turn up unconfirmed;
    · and, for a minor, the guardian as well. Both, not one or the other:
      whoever gave consent has to know it was used, and D22's argument for
      reaching a guardian holds just as much for "your child signed up" as for
      "the time has changed".

    ⚠️ Addresses only, never a name in the body — same rule as
       default_message(). What leaves this database is an announcement and an
       address, so that a minor's name is not sitting in a third party's logs.
    """
    contact = participation.contact
    event = participation.event
    to = []

    for channel in _preferred_channels(contact):
        address = _address_for(contact, channel)
        if address:
            to.append((address, channel))
            break

    if contact.is_minor in (True, None):
        # The consent record first, then the emergency contact — the same order
        # resolve_recipients() uses, so a guardian is not reached one way for a
        # change of time and another way for a confirmation.
        guardian = participation.guardian_address
        if guardian is None:
            emergency = next(iter(contact.emergency_contacts.all()), None)
            guardian = (str(emergency.phone), SMS) if emergency else None
        if guardian is not None and guardian not in to:
            to.append(guardian)

    if not to:
        # Nobody reachable. Not an error: the signup stands, and the person can
        # see it under "My signups". Returning the empty list rather than
        # raising keeps that true.
        return []

    body = "\n".join([
        f"You are signed up for “{event.name}” ({event.ministry.name}).",
        "",
        f"When: {event.start_time:%Y-%m-%d %H:%M} — {event.end_time:%H:%M}",
        *( [f"Where: {event.location}"] if event.location else [] ),
        f"Role: {participation.event_role.role.name}",
        "",
        "(If this message is for a parent: your child has signed up.)",
        "To withdraw, open “My signups”.",
    ])
    backend = backend or get_backend()
    return backend.send([
        Message(to=address, channel=channel,
                subject=f"[{event.ministry.name}] {event.name}", body=body)
        for address, channel in to
    ])


def default_message(event, reason):
    """The body offered on the preview page, editable before it goes.

    ⚠️ No minor's name in it. D22's second cost is that notification content
       leaves our database; the mitigation is that what leaves is an address
       plus an announcement, and never "your child 小明". That holds however
       aggressive the provider turns out to be.
    """
    lines = [
        f"About “{event.name}” ({event.ministry.name}):",
        "",
        {
            EventNotification.Reason.TIME_CHANGED: "The time has changed.",
            EventNotification.Reason.LOCATION_CHANGED: "The location has changed.",
            EventNotification.Reason.CANCELLED: "This event has been cancelled.",
        }.get(reason, "This event has changed."),
        "",
        f"Now: {event.start_time:%Y-%m-%d %H:%M} — {event.end_time:%H:%M}",
    ]
    if event.location:
        lines.append(f"Where: {event.location}")
    lines += [
        "",
        "(If this message is for a parent: your child signed up for this event.)",
        "If the new time does not work, please cancel under “My signups”.",
    ]
    return "\n".join(lines)


@transaction.atomic
def notify_event_change(event, *, reason, message, sent_by, backend=None):
    """Resolve, deliver, and leave a record. Returns the EventNotification.

    ⚠️ Both M2Ms are written once, from what was true at this moment. Never
       turn either into a property that recalculates: somebody unreachable in
       March may have a number today, and recomputing would rewrite this record
       into "everybody was told", which is a lie. Same rule as hours.
    """
    recipients, unreachable = resolve_recipients(event)
    backend = backend or get_backend()

    subject = f"[{event.ministry.name}] {event.name}"
    results = backend.send([
        Message(to=r.to, channel=r.channel, subject=subject, body=message)
        for r in recipients
    ])

    notification = EventNotification.objects.create(
        event=event,
        reason=reason,
        message=message,          # a snapshot: editing the event never changes it
        sent_at=local_now(),
        sent_by=sent_by,
        provider_ref=next((r.provider_ref for r in results if r.provider_ref), ""),
    )
    notification.recipients.set([r.participation for r in recipients])
    notification.unreachable.set([u.participation for u in unreachable])
    return notification
