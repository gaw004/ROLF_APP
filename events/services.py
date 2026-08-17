"""What happens to a signup: joining, arriving, leaving, being counted.

Permanent asset (D18). Views call these and render the result; nothing here
knows what a template or a request is, so the front end that replaces the admin
imports the same functions unchanged.
"""

import datetime
import io
import uuid
from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.db.models.functions import TruncMonth
from django.utils import timezone
from django.utils.text import slugify
from PIL import Image as PILImage
from PIL import ImageOps as PILImageOps

from contact.models import Contact, ContactQuerySet
from core.images import draft_to, stored_size, upright_size
from core.notifications.base import EMAIL, SMS, Message, get_backend
from core.timeutils import local_date_of, local_now
from org.models import Assignment, Position

from . import tokens
from .models import (
    Event,
    EventNotification,
    EventRole,
    Participation,
    ParticipationRole,
)


class ConsentRequired(ValidationError):
    """A minor (or somebody whose age we do not know) signing up without consent.

    A subclass rather than a bare ValidationError so a view can tell this apart
    from an ordinary form error and re-render with the consent section open.
    """


class TurnedUp(ValidationError):
    """Refusing to mark somebody absent when the record says they were here.

    Its own class for the same reason as ConsentRequired: the attendance page
    has to show this one next to the person it concerns rather than as a
    generic failure, because the fix is a different button.
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


def _record_method(participation, method):
    """Note how this row's attendance was established, if nothing has yet. D28.

    ⚠️ First write wins. The question the column answers is "did the volunteer
       fill this in, or did I?", so an admin correcting the hours an hour later
       must not rewrite the answer to "I did". That correction is recorded in
       the history table; this field is the origin.

    ⚠️ All three routes to attended call this, for the reason written out on
       _mark_attended(): a rule with three entrances and one guard is a rule
       with two ways round it. Here the cost of missing one is quieter — the
       column would simply be empty, which reads as "an old row" and is false.
    """
    if not participation.checked_in_method:
        participation.checked_in_method = method


def check_in(participation, *, at=None, method=Participation.CheckInMethod.ADMIN):
    """They turned up. Records the time and moves status to attended.

    Refuses a minor with no consent on the row — see _mark_attended().

    `method` says who did the recording. It defaults to ADMIN because that is
    what every existing caller means; the QR page passes SELF_QR. See D28.
    """
    participation.checked_in_at = at or local_now()
    _record_method(participation, method)
    _mark_attended(participation)
    participation.full_clean(exclude=["registered_at"])
    participation.save()
    return participation


def check_out(participation, *, at=None, method=Participation.CheckInMethod.ADMIN):
    """They left. Records the time and *writes* the elapsed hours.

    ⚠️ Self-service check-out therefore **writes an authoritative number**. A
       volunteer who taps this by mistake five minutes in leaves 0.08 in a
       column a human is answerable for, and mark_absent() will refuse that row
       from then on. Accepted in D28 with its eyes open: the bias runs the same
       direction as the one it replaces (an admin who forgets to tap check-out),
       the confirmation page names the number before writing it, and the history
       table keeps the old value.

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
        _record_method(participation, method)
        _mark_attended(participation)
    participation.full_clean(exclude=["registered_at"])
    participation.save()
    return participation


def record_hours(participation, hours, *, method=Participation.CheckInMethod.ADMIN):
    """Enter hours by hand — the paper sign-in sheet, with no timestamps.

    Goes through the same field as check_out() because there is only one
    authoritative value; what differs is where the number came from.

    ⚠️ This is a third route to attended that never passes through check_in(),
       which is why it records the method too: a row entered from paper has no
       timestamps at all, and leaving the column empty would file it under
       "predates the feature".
    """
    participation.hours = hours
    _record_method(participation, method)
    _mark_attended(participation)
    participation.full_clean(exclude=["registered_at"])
    participation.save()
    return participation


# --- D28: the QR scan, from proof-of-presence to one written row ------------

#: Where the proof of presence is kept between the scan and the confirmation.
#  A key, not a cookie name — the view puts the value in request.session.
CHECKIN_CREDENTIAL_KEY = "events.checkin_credential"

#: How long the proof survives. Long enough to type a password on a phone,
#  which is the whole reason it exists: the token's 90 seconds constrain the
#  scan and nothing else. D28「为什么 ② 和 ③ 一定要分开」.
CREDENTIAL_MAX_AGE = datetime.timedelta(minutes=10)


class CredentialExpired(ValidationError):
    """The proof of presence is gone or too old. Scan again."""


def issue_credential(event_id, mode, *, at=None):
    """A plain dict recording that somebody stood in front of the screen.

    ⚠️ Returned rather than written, because nothing in this module may know
       what a request is (see the module docstring). The view is what puts it
       into the session — and a plain dict is also what the session can hold,
       since sessions are serialised as JSON.
    """
    return {
        "event": event_id,
        "mode": mode,
        "at": (at or local_now()).timestamp(),
    }


def read_credential(data, *, at=None):
    """(event_id, mode) from a stored credential, or raise CredentialExpired."""
    try:
        event_id, mode, issued_at = data["event"], data["mode"], float(data["at"])
    except (TypeError, KeyError, ValueError) as error:
        raise CredentialExpired(_CREDENTIAL_MESSAGE) from error
    age = (at or local_now()).timestamp() - issued_at
    if age < 0 or age > CREDENTIAL_MAX_AGE.total_seconds():
        raise CredentialExpired(_CREDENTIAL_MESSAGE)
    return event_id, mode


_CREDENTIAL_MESSAGE = (
    "That check-in has timed out. Scan the code on the screen again."
)


@dataclass(frozen=True)
class ScanTargets:
    """This person's rows at this event, sorted by what the scan can do to them.

    Three lists rather than one, because the three lead to three different
    sentences on the phone, and a view that had to work them out from a flat
    queryset would be doing the deciding — which is what D18 puts here.
    """

    #: Rows this scan would change.
    pending: list
    #: Rows the scan would repeat — "you checked in at 9:03".
    done: list
    #: Rows that cannot be checked out because they were never checked in.
    needs_check_in: list

    @property
    def any_signup(self):
        """Are they signed up for this event at all? The first question asked."""
        return bool(self.pending or self.done or self.needs_check_in)


def scan_targets(contact, event, mode):
    """Sort this contact's live signups at `event` into what the scan can do.

    ⚠️ Cancelled rows are excluded, not listed as "done". Somebody who pulled
       out and then scanned should be told they are not signed up — the row
       exists only so that the notification history has something to point at.

    ⚠️ One person may legitimately hold several rows at one event (they signed
       up to lift *and* to run the welcome desk — D19 allows it), which is why
       this returns lists rather than a row. Picking one of several is the
       volunteer's call, not this function's: the other two options both invent
       a number. See D28「五、一个人报了两个工种」.
    """
    rows = list(
        Participation.objects
        .filter(contact=contact, event_role__event=event)
        .notifiable()
        .select_related("event_role__role", "event_role__event")
        .order_by("event_role__role__name")
    )
    pending, done, needs_check_in = [], [], []
    for row in rows:
        if mode == tokens.CHECK_IN:
            (done if row.checked_in_at else pending).append(row)
        elif row.checked_out_at:
            done.append(row)
        elif row.checked_in_at:
            pending.append(row)
        else:
            needs_check_in.append(row)
    return ScanTargets(pending=pending, done=done, needs_check_in=needs_check_in)


def apply_scan(participation_pk, *, contact, event_id, mode, at=None):
    """Write one scanned check-in or check-out. Atomic, and safe to repeat.

    Returns (participation, changed). `changed` is False when the row was
    already in the state the scan asks for — a second tap, a browser retry, or
    the same person scanning on two devices. That is not an error and must not
    be reported as one: the page says "you checked in at 9:03" and everybody
    involved is happy.

    ⚠️ The row is locked and then **re-read inside the lock**. Checking the
       state before taking the lock is the same as not taking it: two requests
       both read "not checked in", both proceed, and the second overwrites the
       first's timestamp. Contention is only ever between duplicate requests for
       the same person — different volunteers never touch the same row — so the
       lock costs nothing at a hundred-person event. D28「八、并发」.

    ⚠️ `of=("self",)` is not a micro-optimisation. Without it the FOR UPDATE
       covers every table pulled in by select_related, which includes the
       **event** — and locking the event row would serialise the entire queue
       behind whoever scanned first. That is the one way this feature could
       actually be made to fall over by a hundred people arriving at once.

    ⚠️ Nothing in here may do I/O. A transaction held open while waiting on a
       network is how a connection pool is exhausted, and the connection limit
       is the real ceiling on this feature (D28「真正要处理的四件事」).

    ⚠️ The scope is the belt: the lookup is narrowed to this contact and this
       event, so a primary key from somebody else's row cannot be posted into
       this function at all.
    """
    with transaction.atomic():
        participation = (
            Participation.objects
            .select_for_update(of=("self",))
            .filter(contact=contact, event_role__event_id=event_id)
            .notifiable()
            .select_related("event_role__event", "event_role__role", "contact")
            .get(pk=participation_pk)
        )
        method = Participation.CheckInMethod.SELF_QR
        if mode == tokens.CHECK_IN:
            if participation.checked_in_at:
                return participation, False
            check_in(participation, at=at, method=method)
        else:
            if participation.checked_out_at or participation.checked_in_at is None:
                return participation, False
            check_out(participation, at=at, method=method)
    return participation, True


def default_checkin_mode(event, *, at=None):
    """Which way round the iPad should start when the page is opened.

    Before the midpoint of the event, people are arriving; after it, they are
    leaving. ⚠️ Read **once**, at page load, and never again — the display does
    not follow this as the afternoon goes on. An iPad that switched itself would
    turn the queue standing in front of it into check-outs, with nothing on the
    screen having changed to say so.
    """
    midpoint = event.start_time + (event.end_time - event.start_time) / 2
    return tokens.CHECK_IN if (at or local_now()) < midpoint else tokens.CHECK_OUT


def checkin_result_message(participation, mode, changed):
    """The sentence that lands on My Signups after a scan.

    ⚠️ A repeat is phrased as a statement of fact, not as a failure. The most
       common way to arrive here twice is a slow page and an impatient thumb,
       and an error would send that person off to find an admin over something
       that already worked.
    """
    if mode == tokens.CHECK_IN:
        when = timezone.localtime(participation.checked_in_at)
        if changed:
            return f"Checked in at {when:%-I:%M %p}."
        return f"You already checked in at {when:%-I:%M %p}."
    when = timezone.localtime(participation.checked_out_at)
    if not changed:
        return f"You already checked out at {when:%-I:%M %p}."
    return (f"Checked out at {when:%-I:%M %p} — "
            f"{participation.hours} hours recorded.")


def undo_attendance(participation):
    """Somebody clicked check-in on the wrong row. Put it back as it was.

    Clears both timestamps and the hours, and moves the row back to registered.
    Returns True if anything actually changed, so the caller can tell a real
    undo from a second click on the same button.

    ⚠️ **This deletes hours, and that is the whole difficulty.** `check_out()`
       writes hours from the clock *only when hours is None*, so an existing
       value may have come from the clock or from a person typing it — and
       nothing in the table records which. There is therefore no version of
       this function that keeps "the ones a human typed": it cannot see them.

       What makes deleting them acceptable rather than reckless is two things,
       and both have to stay true:

         · the button asks first, **naming the number it is about to remove**
           (events/templates/events/_attendance_row.html);
         · `Participation` carries `HistoricalRecords`, so the old value and
           who removed it are recoverable from the history table.

    ⚠️ It is not optional to clear the hours either, which is worth stating so
       that nobody "improves" this by keeping them: the constraint
       `participation_hours_only_when_attended` means a row with hours **must**
       be attended. "Registered, 3 hours" cannot be stored at all.

    ⚠️ Only `attended` goes back to `registered`. A cancelled row that also
       carries a check-in is a real state (they signed in, then pulled out),
       and moving it to registered would erase the second fact to undo the
       first. Same reasoning as `cancel()` being a status change rather than a
       delete.

    ⚠️ Deliberately **not** folded into `mark_absent()`. "Undo my mistake" and
       "record that they did not come" are different facts, and one button
       doing both would make the second unreadable — the no-show rate would
       quietly count every mis-click. That was decided when mark_absent() was
       written (2026-08-05, phase-c.md's known-gaps table) and this function is
       the fix that entry names.
    """
    if (participation.checked_in_at is None
            and participation.checked_out_at is None
            and participation.hours is None):
        return False

    participation.checked_in_at = None
    participation.checked_out_at = None
    participation.hours = None
    # ⚠️ Cleared with the rest. Left behind it would say "a volunteer recorded
    #    this" about a row that now records nothing — and the next write, which
    #    is first-write-wins, would then be unable to say who really filled it
    #    in. This field describes an attendance record, so it goes when the
    #    attendance record goes.
    participation.checked_in_method = ""
    if participation.status == Participation.Status.ATTENDED:
        participation.status = Participation.Status.REGISTERED
    participation.full_clean(exclude=["registered_at"])
    participation.save()
    return True


def clear_hours(participation):
    """Remove an hours figure without touching anything else. Safe to call twice.

    ⚠️ **Empty is not zero, and that is why this exists.** Until now hours could
       be set to 0 but never back to nothing, because the form field is a
       required number. Those are different facts and the attendance page
       already draws them differently: 0 means somebody looked and recorded
       that no time was worked; empty means nobody has recorded anything yet.
       Without this, a figure typed by mistake could only ever be *corrected*
       to a wrong-but-plausible 0.

    ⚠️ The status is left alone, deliberately. "They turned up" and "they worked
       N hours" are two assertions, and a row reading attended with no hours is
       an ordinary state — somebody checked in and the hours have not been
       worked out yet. Reverting the status here would delete the first fact in
       order to undo the second. Clearing everything at once is
       `undo_attendance()`, and it is a different button on purpose.
    """
    if participation.hours is None:
        return False
    participation.hours = None
    participation.full_clean(exclude=["registered_at"])
    participation.save()
    return True


def mark_absent(participation):
    """They signed up and did not come.

    ⚠️ This status existed in `Participation.Status` from the beginning and
       **nothing ever wrote it**. That is worse than not having it: any
       "no-show rate" computed before today would have been a hard zero on
       every ministry, every period — a number that looks like good news, never
       raises, and is never right. 2026-08-05.

    ⚠️ Absence is a *recorded* fact, not the absence of a record. A row still
       sitting at `registered` after the event means nobody looked; this means
       somebody looked and they were not there. The report keeps the two apart
       and says how many events were actually marked up — see `ministry_report`.

    Refuses when the record already says they turned up, and never edits that
    record to make room for itself:

    · hours — the rule the foundation asked for (2026-08-05). "Did three hours"
      and "did not come" cannot both be true, and clearing the hours to store
      the absence would be a silent deletion of the one value in this system a
      human is answerable for having typed.
    · a check-in timestamp — the same contradiction one step earlier, and left
      in place it would print a row reading "checked in 3:44 p.m. · No-show".

    ⚠️ Undoing a check-in clicked on the wrong row is `undo_attendance()`, and
       it is deliberately **not** this function — "undo my mistake" and "record
       that they did not come" are different facts, and one button doing both
       would make the second unreadable: every mis-click would land in the
       no-show rate. That separation was the reason this gap stayed open from
       2026-08-05 until undo_attendance() closed it.

    The way back is `check_in()` (or `record_hours()`), which both go through
    `_mark_attended()` and so overwrite this status without needing to know it
    exists — somebody who turns up an hour late is signed in as normal.
    """
    if participation.hours is not None:
        raise TurnedUp({
            "hours": "There are hours on this signup, so it cannot be marked as "
                     "a no-show. Clear the hours first if they were entered by "
                     "mistake.",
        })
    if participation.checked_in_at is not None:
        raise TurnedUp({
            "checked_in_at": "This signup was checked in, so it cannot be marked "
                             "as a no-show.",
        })
    participation.status = Participation.Status.ABSENT
    participation.save(update_fields=["status", "updated_at"])
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


# --- The ministry report (2026-08-05) ------------------------------------
#
# Thirteen figures over a **set of events**, where every earlier report in this
# file answers about one. The chosen thirteen and the ones deliberately left out
# are in D27; what follows is how they are computed and where each one can lie.
#
# ⭐ The single invariant, and the reason this takes a queryset rather than a
#    ministry: **the report describes exactly the events in the list next to
#    it.** Scoping, the ministry filter and the date window are already in that
#    queryset, so a ministry admin and a foundation admin run the same code and
#    neither needs a permission branch here. A `ministry_id` argument would have
#    needed one, and a report that quietly widened past what its own page shows
#    is the failure this shape rules out rather than tests for.


@dataclass(frozen=True)
class Bar:
    """One row of a chart. `pct` is width, `caption` is the number in words."""

    label: str
    caption: str
    pct: int


@dataclass(frozen=True)
class PairedBar:
    """Wanted against got, drawn as two bars sharing one scale."""

    label: str
    needed: int
    signed: int
    needed_pct: int
    signed_pct: int
    short: bool


@dataclass(frozen=True)
class Chart:
    """Bars plus the honesty flags the template needs to decide how to draw.

    `sparse` — fewer than three bars. One bar is not a chart; it is a number
    wearing a rectangle, and it reads as less information than the sentence it
    replaced. The template falls back to a list.
    """

    title: str
    bars: list
    sparse: bool
    note: str = ""


def _bars(rows, *, formatter):
    """Turn [(label, value)] into bars scaled against the largest one.

    ⚠️ Scaled to the maximum, never to the total. A chart where the longest bar
       stops a third of the way across is read as "a third of something", and
       there is nothing on the page saying what.
    """
    rows = [(label, value) for label, value in rows if value is not None]
    largest = max((value for _, value in rows), default=0)
    return [
        Bar(
            label=label,
            caption=formatter(value),
            # int(), so a 0.4% bar is 0 rather than a hairline that reads as a
            # rendering fault. The caption still carries the real number.
            pct=int(100 * value / largest) if largest else 0,
        )
        for label, value in rows
    ]


def _chart(title, rows, *, formatter=str, note=""):
    bars = _bars(rows, formatter=formatter)
    return Chart(title=title, bars=bars, sparse=len(bars) < 3, note=note)


def _months_between(first, last):
    """Every month from first to last inclusive, gaps included.

    ⚠️ Skipping the empty months is the default of a GROUP BY and it is a lie in
       a chart: January and March drawn side by side say the ministry ran events
       in consecutive months. A quiet ministry has to look quiet.
    """
    out, cursor = [], first
    while cursor <= last:
        out.append(cursor)
        cursor = (cursor.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
    return out


def ministry_report(events):
    """Everything the report panel shows, for one already-filtered event list.

    Eight figures and five charts. Each of the three that can be misread carries
    its own caveat out to the template rather than being silently rounded off:

    ⚠️ `hours` is **hours that were recorded**, and it is biased low in a way
       that is not random — it tracks which admin remembers to check people out.
       `hours_records` and `hours_missing` go out with it so the page can say
       what the total rests on. The foundation accepted this bias knowingly
       (2026-08-05); what is not acceptable is printing the total alone.

    ⚠️ `repeat_rate` is over the people in **this window**, not their whole
       history. Somebody who came for years and once in August is a first-timer
       here. That makes the figure answer "did this period build a habit",
       which is the question worth asking of a period.

    ⚠️ `fully_staffed_rate` counts only events that opened at least one role
       with a number on it. An event that opened no roles, or only unlimited
       ones, cannot be full — including it in the denominator would drag the
       rate down for being unmeasurable, which reads as a staffing problem.
    """
    # Cancelled signups are out of every figure below: that person said they
    # were not coming, and counting them as a volunteer inflates every number
    # on this page. A no-show stays in — they signed up, which is the fact the
    # signup figures are about.
    parts = Participation.objects.filter(event_role__event__in=events).notifiable()

    event_count = events.count()
    totals = parts.aggregate(
        signups=Count("pk"),
        volunteers=Count("contact", distinct=True),
        # ⚠️ `hours_total`, not `hours`. An alias that repeats a field name wins
        #    over the field for every later argument in the same aggregate(),
        #    so `hours=Sum("hours")` next to `Count("hours")` makes the second
        #    one a count of the first — which Django refuses outright. It would
        #    have been worse if it had not.
        hours_total=Sum("hours"),
        # Count over a nullable column counts the rows that have one: how many
        # records the hours total is built from, not how many signups there are.
        hours_records=Count("hours"),
    )
    hours = totals["hours_total"] or Decimal("0")
    volunteers = totals["volunteers"]

    repeat = (
        parts.values("contact_id").annotate(n=Count("pk")).filter(n__gte=2).count()
    )

    # D3. Two id sets rather than one clever query: the "no role is short"
    # condition is a NOT EXISTS over an annotated subquery, and the version of
    # it that reads clearly is the one that is wrong. Sets of primary keys over
    # a ministry's events are small — hundreds, not millions.
    staffable = set(
        events.filter(roles__needed_count__isnull=False).values_list("pk", flat=True)
    )
    short_events = set(
        EventRole.objects.understaffed()
        .filter(event__in=events)
        .values_list("event_id", flat=True)
    )
    fully_staffed = len(staffable - short_events)

    # G2. The same rule as consent_required_for(), asked of a queryset: the
    # event demanded consent, the person is a minor **or has no date on file**,
    # and no consent was ever recorded. Asking it differently here than the two
    # gates ask it is how a report reassures somebody about a rule it is not
    # actually checking.
    minors_without_consent = parts.filter(
        event_role__event__requires_guardian_consent=True,
        consent_at__isnull=True,
    ).filter(
        Q(contact__birth_date__gt=ContactQuerySet.majority_threshold())
        | Q(contact__birth_date__isnull=True)
    ).count()

    absence = _absence(events, parts)

    figures = {
        "events": event_count,
        "signups": totals["signups"],
        "volunteers": volunteers,
        "hours": hours,
        "hours_records": totals["hours_records"],
        "hours_missing": totals["signups"] - totals["hours_records"],
        "hours_per_volunteer": (hours / volunteers) if volunteers else None,
        "repeat_volunteers": repeat,
        "repeat_rate": _percent(repeat, volunteers),
        "fully_staffed": fully_staffed,
        "staffable_events": len(staffable),
        "fully_staffed_rate": _percent(fully_staffed, len(staffable)),
        "minors_without_consent": minors_without_consent,
        **absence,
    }
    return {"figures": figures, "charts": _report_charts(events, parts)}


def _absence(events, parts):
    """How often people signed up and did not come — over the events somebody
    actually went through.

    ⚠️ The whole difficulty is the denominator, and the obvious version of it is
       wrong in the direction of bad news. "Events somebody marked up" cannot be
       asked directly: the only positive evidence of marking is an absence
       existing, so a denominator of "events with at least one no-show" contains
       nothing **but** events with absences, and the rate it produces can never
       be low. That is as dishonest as printing no caveat at all, just pointed
       the other way.

    So the question asked here is the one the data can answer:
    **is any signup on this event still sitting at `registered`?** A row left
    there after the event means nobody went through the list. Ten people all
    checked in, nobody absent, qualifies — and contributes an honest 0%.

    ⚠️ Events with no signups at all are out of the denominator. They cannot
       have an absence, and counting them would make a diligent ministry look
       careless — the same reason `fully_staffed` excludes events that opened no
       numbered role.
    """
    with_signups = set(parts.values_list("event_role__event_id", flat=True))
    still_registered = set(
        parts.filter(status=Participation.Status.REGISTERED)
        .values_list("event_role__event_id", flat=True)
    )
    marked_up = with_signups - still_registered

    counted = parts.filter(event_role__event_id__in=marked_up).aggregate(
        signups=Count("pk"),
        absences=Count("pk", filter=Q(status=Participation.Status.ABSENT)),
    )
    return {
        "absences": counted["absences"],
        "absence_signups": counted["signups"],
        "absence_rate": _percent(counted["absences"], counted["signups"]),
        # The two halves of the caption. Without them the rate is a number with
        # no idea how much of the period it speaks for.
        "marked_up_events": len(marked_up),
        "events_with_signups": len(with_signups),
    }


def _percent(part, whole):
    """An integer percentage, or None when the question does not apply.

    None rather than 0: "none of the twelve" and "there were none to count" are
    different answers, and 0% for the second reads as a failure.
    """
    return int(100 * part / whole) if whole else None


def _report_charts(events, parts):
    """The five charts, in the order the panel draws them.

    ⚠️ All five are horizontal. Twelve vertical bars in a panel that is half a
       screen wide are 20px each with "Aug 2026" underneath, and on a phone the
       panel is the full width but the labels still are not. Horizontal puts
       every label on its own line at its natural size, and the same component
       draws all five — so no chart on this page can end up styled unlike its
       neighbour. Months therefore read top to bottom, oldest first.
    """
    monthly = _monthly_series(events, parts)
    return {
        "events_by_month": _chart(
            "Events by month",
            [(label, count) for label, count, _ in monthly],
            formatter=lambda n: f"{n} event{'' if n == 1 else 's'}",
        ),
        "hours_by_month": _chart(
            "Recorded hours by month",
            [(label, total) for label, _, total in monthly],
            formatter=lambda h: f"{h:.2f} h",
            note="Only hours somebody entered. Months where nobody was checked "
                 "out read as zero.",
        ),
        "role_gap": _role_gap(events, parts),
        "top_volunteers": _top_volunteers(parts),
        "hours_by_role": _chart(
            "Recorded hours by role",
            list(
                parts.exclude(hours__isnull=True)
                .order_by()
                .values_list("event_role__role__name")
                .annotate(total=Sum("hours"))
                .order_by("-total")
            ),
            formatter=lambda h: f"{h:.2f} h",
        ),
    }


def _monthly_series(events, parts):
    """[(label, event count, recorded hours)] with no month left out.

    Two queries, merged here rather than joined in SQL. Joining Event to
    Participation and asking for Count(events) in the same statement multiplies
    the count by the number of signups — the classic multi-table aggregation
    trap, and it produces a plausible number rather than an error.
    """
    # ⚠️ `.order_by()` with nothing in it, on both, and it is not tidying up.
    #    An **explicit** ordering on the incoming queryset is added to the
    #    GROUP BY — and the list this report describes arrives ordered by
    #    start_time, so grouping by month also grouped by the exact timestamp:
    #    one row per event, every count 1. Django only ignores `Meta.ordering`
    #    here, never an order_by() somebody wrote.
    #
    #    Caught in a screenshot on 2026-08-05, not by a test: the list beside
    #    the chart said eleven events in August and the chart said one. Twenty
    #    passing tests had built their own unordered queryset, so none of them
    #    ever saw it. There is a regression test now that orders it first.
    by_month = {
        row["month"].date(): row["n"]
        for row in events.order_by().annotate(month=TruncMonth("start_time"))
        .values("month").annotate(n=Count("pk"))
    }
    hours_by_month = {
        row["month"].date(): row["total"] or Decimal("0")
        for row in parts.order_by()
        .annotate(month=TruncMonth("event_role__event__start_time"))
        .values("month").annotate(total=Sum("hours"))
    }
    if not by_month:
        return []
    return [
        (
            month.strftime("%b %Y"),
            by_month.get(month, 0),
            hours_by_month.get(month, Decimal("0")),
        )
        for month in _months_between(min(by_month), max(by_month))
    ]


def _role_gap(events, parts):
    """D1: how many each job asked for against how many signed up.

    ⚠️ Two queries on purpose. `Sum("needed_count")` and a count of
       participations in one statement join EventRole to Participation, and the
       Sum is then repeated once per signup — a role wanting 5 with 3 people
       reports wanting 15. Nothing raises; the number is simply wrong, and
       wrong in the direction that makes the ministry look short-staffed.

    ⚠️ Roles nobody signed up for have to be here — they have no Participation
       row to be found through, and they are the ones worth looking at. That is
       D19's line, arriving in a fourth place.
    """
    # `.order_by()` for the same reason as _monthly_series(): any ordering
    # still on these querysets joins the GROUP BY and splits each role into one
    # group per row.
    needed = {
        row["role__name"]: row["needed"] or 0
        for row in EventRole.objects.filter(event__in=events)
        .exclude(needed_count__isnull=True)
        .order_by()
        .values("role__name").annotate(needed=Sum("needed_count"))
    }
    signed = {
        row["event_role__role__name"]: row["n"]
        for row in parts.order_by()
        .values("event_role__role__name").annotate(n=Count("pk"))
    }
    rows = sorted(needed.items(), key=lambda item: item[1], reverse=True)
    largest = max((count for _, count in rows), default=0)
    largest = max([largest] + [signed.get(name, 0) for name, _ in rows])
    bars = [
        PairedBar(
            label=name,
            needed=count,
            signed=signed.get(name, 0),
            needed_pct=int(100 * count / largest) if largest else 0,
            signed_pct=int(100 * signed.get(name, 0) / largest) if largest else 0,
            short=signed.get(name, 0) < count,
        )
        for name, count in rows
    ]
    return Chart(
        title="Wanted against signed up, by role",
        bars=bars,
        sparse=len(bars) < 3,
        note="Roles opened without a number are not here — they can never be short.",
    )


def _top_volunteers(parts, limit=10):
    """C3: who did the most, by recorded hours then by number of events.

    ⚠️ `nulls_last`. Postgres sorts NULL first on a descending order, so the
       plain "-hours" puts everybody with no hours recorded at the top of a
       chart titled "most hours" — the exact inversion of what it claims.
    """
    rows = list(
        parts.order_by()
        .values("contact_id")
        .annotate(total=Sum("hours"), n=Count("pk"))
        .order_by(F("total").desc(nulls_last=True), "-n")[:limit]
    )
    people = Contact.objects.in_bulk([row["contact_id"] for row in rows])
    return _chart(
        "Most hours",
        [
            # ⚠️ short_label, not str(). Contact.__str__ appends an email or a
            #    phone number to tell two people of the same name apart, which
            #    a dropdown needs and a leaderboard does not — nothing is
            #    chosen from this chart, so the disclosure buys nothing.
            (people[row["contact_id"]].short_label, row["total"])
            for row in rows
            if row["total"] is not None
        ],
        formatter=lambda h: f"{h:.2f} h",
    )


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


def scheduled_hours(event) -> Decimal:
    """How long this event is supposed to run, in hours.

    ⚠️ Lives here rather than in a view or a template because it is date
       arithmetic, and there is a grep guard on exactly that: a view that
       computes gets rewritten along with the templates (D18), and "how long"
       is a question the reports already answer from this same function.

    ⚠️ This is the **scheduled** length, never what anybody actually did.
       check_out() writes real elapsed time into Participation.hours, and the
       two must not be confused: an event scheduled for six hours that somebody
       left after two has one answer here and a different one there.
    """
    return duration_hours(event.end_time - event.start_time)


# --- Event pictures --------------------------------------------------------
# Storage is the whole design constraint here: the picture has to be small, it
# has to disappear when the event is over, and it must never end up in a
# backup. Everything below serves one of those three.

#: Longest edge kept. Sized from the display, not from the source: the thumbnail
#: is at most ~140 CSS px, so this leaves headroom for a 4x display and for the
#: picture being shown larger later, while still turning a 4 MB phone photo into
#: something under 100 KB.
EVENT_IMAGE_MAX_EDGE = 900
EVENT_IMAGE_QUALITY = 82


def normalise_event_image(uploaded):
    """Re-encode an upload into a small, safe WebP. Returns a Django File.

    ⚠️ Everything here is a decision about **bytes**, never about how the
       picture looks. Tone, saturation and opacity are styling and live in
       app.css — see design-system.md. Baking a look into the stored file would
       put a design decision somewhere no stylesheet can reach and make
       changing it a re-processing job over every image ever uploaded.

    Four things happen, and three of them are not optional:

      1. ⚠️ `exif_transpose` **before** anything else. A phone photo records
         "this is sideways" in EXIF rather than rotating the pixels; strip the
         EXIF first and every portrait photo comes out on its side. Silently —
         the file is valid, it is just wrong.
      2. ⚠️ EXIF is then dropped, and the reason is privacy rather than size:
         phone photos carry GPS coordinates. A picture taken at a volunteer's
         house would publish their address to everybody who can see the event.
      3. Resized down (never up) and re-encoded as WebP.
      4. ⚠️ Opening it with Pillow **is** the validation. A file that is not a
         raster image cannot survive this, which is what keeps SVG out — an SVG
         can carry script, and it would be served from this site's own origin.

    Raises ValidationError for anything that is not a usable image, so the form
    can put the complaint next to the field.
    """
    from django.core.files.base import ContentFile

    try:
        with PILImage.open(uploaded) as source:
            # ⚠️ The memory fix. Full note over `core.images.draft_to`; short
            #    version: a photograph's decode costs its **pixel count, not
            #    its file size** (49 MP is 146 MB out of a 6 MB file), and JPEG
            #    can be decoded at 1/2, 1/4 or 1/8 scale and still give the
            #    whole frame. Nothing stored here is larger than
            #    EVENT_IMAGE_MAX_EDGE, so the rest was always waste.
            #
            # ⚠️ `target` is worked out **now**, from the size the photograph
            #    arrived at, and used for both the decode and the resize below.
            #    Recomputing it afterwards from the decoded size is what put an
            #    event card at 900×515 where the arithmetic says 900×514 — the
            #    reduced-scale decode shifts the aspect ratio by a hair. See
            #    `core.images.stored_size`.
            #
            # ⚠️ The geometry is shared with gallery, the **policy is not** —
            #    see this function's docstring on why the two normalise
            #    functions stay separate. The first attempt kept a copy of the
            #    arithmetic here too, and the copies were a pixel apart within
            #    the day, with a behaviour guard in each app staying green
            #    throughout: both were asked "was this decoded smaller?", and
            #    on both the answer was yes.
            target = stored_size(upright_size(source), EVENT_IMAGE_MAX_EDGE)
            draft_to(source, target)
            # ⚠️ **`in_place=True`, and the reasoning is gallery's** (2026-08-13)
            #    — the full note lives over the same call in
            #    gallery/services.py::normalise_gallery_image, because that is
            #    where it was measured. Short version: without it
            #    `exif_transpose` returns a *second* full-size picture and the
            #    first stays alive under `source`, so a phone photograph is
            #    decoded twice over to produce one small WebP.
            #
            # ⚠️ Allowed only because the stored bytes are unchanged — the same
            #    check was run here, over real photographs and every
            #    orientation tag. This path has no digest riding on it today,
            #    but the two functions are deliberately not shared (see the
            #    docstring), so each one has to be able to state its own
            #    guarantee.
            PILImageOps.exif_transpose(source, in_place=True)
            # ⚠️ Forces the decode before `thumbnail()` can ask for a
            #    reduced-scale one of its own. Same line, same reason, same
            #    guard as gallery's — see there.
            source.load()
            # RGBA is kept where it exists — WebP carries alpha, and flattening
            # a logo onto white would put a white box on a dark page.
            #
            # ⚠️ Converted only when the mode is not already the one wanted.
            #    `convert()` into the mode an image is already in still returns
            #    a **full-size copy** — 72 MB for a phone photograph, spent to
            #    produce pixels identical to the ones it was handed. Measured
            #    2026-08-13: 198 MB of peak memory for one upload, down to
            #    113 MB without it. The front page's version of this mistake
            #    restarted production the day before.
            #
            # ⚠️ A second name rather than rebinding `source`: the `with` keeps
            #    its own reference to whatever `open()` returned, so rebinding
            #    would not release the original — it would only read as if it
            #    had.
            picture = source
            wanted = "RGBA" if "A" in picture.getbands() else "RGB"
            if picture.mode != wanted:
                picture = picture.convert(wanted)
            # ⚠️ Resized to the target worked out above, not `thumbnail()`,
            #    which would work its own out from the decoded size. `target`
            #    is None for a picture already inside the limit — those are
            #    stored as they arrived, because enlarging adds bytes and no
            #    detail.
            if target is not None and picture.size != target:
                picture = picture.resize(target, PILImage.LANCZOS)
            buffer = io.BytesIO()
            # No exif= argument, so none is written. Stated rather than assumed,
            # because "Pillow does not copy it by default" is the kind of
            # default that changes.
            picture.save(buffer, "WEBP", quality=EVENT_IMAGE_QUALITY, method=6)
    except ValidationError:
        raise
    except Exception as error:
        raise ValidationError(
            "That file could not be read as an image. JPEG, PNG and WebP work; "
            "SVG and PDF do not."
        ) from error

    return ContentFile(buffer.getvalue(), name=f"{uuid.uuid4().hex}.webp")


def events_with_images_to_purge(now=None):
    """Finished events still holding a picture.

    ⚠️ Judged on `end_time`, not on `status`. Marking an event completed is a
       human action somebody forgets, and a picture that only disappears when
       somebody remembers is a picture that stays.
    """
    return Event.objects.filter(end_time__lt=now or local_now()).exclude(image="")


def purge_event_image(event):
    """Delete one event's picture and forget it. Safe to call twice.

    ⚠️ The field is cleared as well as the file. Leaving the name behind gives a
       row that points at nothing, and the page then renders a broken image
       rather than the default — worse than either.
    """
    if not event.image:
        return False
    event.image.delete(save=False)
    event.image = ""
    event.save(update_fields=["image"])
    return True


# --- Opening a job that does not exist in the vocabulary yet ---------------
# 2026-08-04: ministry admins are not staff, so the admin site is closed to
# them and a job nobody had thought of before used to be a dead end.


def matching_participation_role(name):
    """An existing role whose name is the same once case and padding are gone.

    ⚠️ **Only exact-after-normalising matches.** "lifting" finds "Lifting", and
       " Lifting " finds it too — but "Heavy lifting" is a different string and
       this will not catch it. That limit is real and is the price of letting
       ministry admins add to a shared vocabulary at all: the check stops the
       accidental duplicate, not the synonym.

       It matters because ParticipationRole is the grouping dimension for R5
       and R7. Two rows meaning one job do not raise anything; they just split
       one column of the report into two, and both halves look plausible.
    """
    cleaned = (name or "").strip()
    if not cleaned:
        return None
    return ParticipationRole.objects.filter(name__iexact=cleaned).first()


def create_participation_role(name):
    """Add a job to the shared vocabulary. Returns the new ParticipationRole.

    ⚠️ `code` is generated from the name and is **immutable afterwards**
       (ImmutableCodeMixin) — the rest of the codebase matches on it, so
       renaming one silently stops lookups returning rows. The display name
       stays editable in the admin; the code does not.

    ⚠️ Uniqueness of the code is enforced by a database constraint, not by the
       suffix loop below. The loop is there to produce a *usable* code on the
       ordinary path; two admins submitting the same new name at the same
       instant is settled by the constraint, which is the only thing bulk paths
       also have to obey (D9).
    """
    cleaned = (name or "").strip()
    base = slugify(cleaned)[:40] or "role"
    code, suffix = base, 2
    while ParticipationRole.objects.filter(code__iexact=code).exists():
        code = f"{base}-{suffix}"
        suffix += 1
    role = ParticipationRole(code=code, name=cleaned)
    role.full_clean()
    role.save()
    return role


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
                # The emergency contact is the fallback. Its own reachable_at
                # decides the channel — email first, then phone — so a guardian
                # reached about a change of time and a guardian reached about a
                # confirmation get the same treatment.
                emergency = next(iter(contact.emergency_contacts.all()), None)
                guardian = emergency.reachable_at if emergency else None
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
            guardian = emergency.reachable_at if emergency else None
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


def notify_event_change(event, *, reason, message, sent_by, backend=None):
    """Resolve, deliver, and leave a record. Returns the EventNotification.

    ⚠️ All three M2Ms are written once, from what was true at this moment. Never
       turn any of them into a property that recalculates: somebody unreachable
       in March may have a number today, and recomputing would rewrite this
       record into "everybody was told", which is a lie. Same rule as hours.

    ⚠️ **The sending happens before the transaction opens, and must never move
       inside it.** It used to be inside, which cost two things. A batch of a
       hundred held a database transaction open for as long as the mail server
       took; and anything raising partway through rolled the record back, so
       the forty-six people who already had the email in their inbox were
       recorded as never having been told. Rolling back a network call is not a
       thing anybody can do, so the only honest order is: send, then write down
       what happened. The other half of that fix is that the adapters do not
       raise — see core/notifications/base.py.

    ⚠️ A message the backend did not accept lands in `failed`, never in
       `recipients`. The backends have always answered per message (see
       DeliveryResult) — it was this function that threw the answer away and
       recorded everyone it *tried* as everyone it *told*.
    """
    recipients, unreachable = resolve_recipients(event)
    backend = backend or get_backend()

    subject = f"[{event.ministry.name}] {event.name}"
    results = backend.send([
        Message(to=r.to, channel=r.channel, subject=subject, body=message)
        for r in recipients
    ])

    # One verdict per recipient, in order — that is the backend contract. A
    # backend answering with fewer results than it was handed is broken, and the
    # safe direction is to call the leftovers failed: "check on these" is
    # recoverable, "they were told" is not.
    accepted = [result.accepted for result in results]
    accepted += [False] * (len(recipients) - len(accepted))

    with transaction.atomic():
        notification = EventNotification.objects.create(
            event=event,
            reason=reason,
            message=message,      # a snapshot: editing the event never changes it
            sent_at=local_now(),
            sent_by=sent_by,
            provider_ref=next((r.provider_ref for r in results if r.provider_ref), ""),
        )
        notification.recipients.set(
            [r.participation for r, ok in zip(recipients, accepted) if ok])
        notification.unreachable.set([u.participation for u in unreachable])
        notification.failed.set(
            [r.participation for r, ok in zip(recipients, accepted) if not ok])
    return notification
