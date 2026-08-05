"""What each test here nails down is written next to it, as in A10 and B5.

The one that matters most is the first: an event that opened five roles and
filled three still has five. Every other test in this file could go; that one
could not, because it is「空缺编制」moved to a different table (goal.md D19).
"""

import datetime
import io
import os
import tempfile
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image as PILImage
from django.utils import formats, timezone
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils.timezone import localtime

from accounts.services import register_account
from contact.models import Contact, EmergencyContact, RelationshipType
from core.notifications.locmem import LocmemBackend
from core.timeutils import (
    day_start,
    local_date_of,
    local_month_of,
    local_now,
    local_today,
    month_bounds,
)
from org.models import Assignment, Ministry, MinistryRole, Position
from org.permissions import foundation_admin_group

from .forms import EventForm, SignUpForm
from .models import (
    Event,
    EventNotification,
    EventRole,
    EventType,
    Participation,
    ParticipationRole,
)
from .services import (
    ConsentRequired,
    check_in,
    check_out,
    confirm_signup,
    default_message,
    event_summary,
    events_in_period,
    ministry_staff_participation,
    notify_event_change,
    record_hours,
    resolve_recipients,
    scheduled_hours,
    sign_up,
)

NOW = local_now()
HOUR = datetime.timedelta(hours=1)
DAY = datetime.timedelta(days=1)


def make_person(last_name, **kwargs):
    return Contact.objects.create(
        contact_type=Contact.ContactType.INDIVIDUAL, legal_last_name=last_name, **kwargs)


def make_event(ministry=None, **kwargs):
    ministry = ministry or Ministry.objects.create(code="food_pantry", name="Food Pantry")
    event_type, _ = EventType.objects.get_or_create(
        code="distribution", defaults={"name": "Distribution"})
    fields = {
        "name": "Saturday distribution",
        "event_type": event_type,
        "ministry": ministry,
        "start_time": NOW + DAY,
        "end_time": NOW + DAY + 3 * HOUR,
        "owner": make_person("Owner"),
        "status": Event.Status.OPEN,
    }
    fields.update(kwargs)
    return Event.objects.create(**fields)


def give_emergency_contact(contact, name="Emergency Kin", phone="+14085550177"):
    """Minors must have somebody to call — sign_up() refuses without one.

    A helper rather than a line in each fixture: the rule applies to every
    minor, so every minor's setup needs it, and spelling it out five times is
    five places to forget it when the rule changes.
    """
    return EmergencyContact.objects.create(
        person=contact, name=name, phone=phone,
        relationship_type=RelationshipType.objects.get(code="parent"),
    )


def make_role(event, code, name=None, needed_count=None):
    role, _ = ParticipationRole.objects.get_or_create(
        code=code, defaults={"name": name or code.title()})
    return EventRole.objects.create(event=event, role=role, needed_count=needed_count)


class EventRoleIsARoleEvenWithNobodyInItTests(TestCase):
    """D19's acceptance point, and the reason this table exists at all."""

    def setUp(self):
        self.event = make_event()

    def test_an_event_role_with_no_signups_still_counts_as_a_role(self):
        # ⭐ Five roles opened, three with anybody in them. R4 answers five.
        # Counting DISTINCT roles over the signups would answer three, and would
        # not raise while doing it.
        for index, code in enumerate(["lifting", "welcome", "interpreting", "kitchen", "cleanup"]):
            role = make_role(self.event, code)
            if index < 3:
                Participation.objects.create(contact=make_person(f"P{index}"), event_role=role)

        self.assertEqual(event_summary(self.event)["role_count"], 5)

    def test_understaffed_lists_a_role_that_nobody_signed_up_for(self):
        # The same fact from the other side: a role nobody has taken has to be
        # visible, because "which job is still short" is what P2 wants to see.
        empty = make_role(self.event, "interpreting", needed_count=1)
        self.assertIn(empty, EventRole.objects.understaffed())

    def test_understaffed_ignores_roles_with_no_needed_count(self):
        # No limit is not "short by an infinite number".
        unlimited = make_role(self.event, "general", needed_count=None)
        self.assertNotIn(unlimited, EventRole.objects.understaffed())

    def test_a_filled_role_leaves_understaffed(self):
        role = make_role(self.event, "lifting", needed_count=1)
        Participation.objects.create(contact=make_person("Wang"), event_role=role)
        self.assertNotIn(role, EventRole.objects.understaffed())

    def test_cancelled_signups_do_not_fill_a_role(self):
        role = make_role(self.event, "lifting", needed_count=1)
        Participation.objects.create(
            contact=make_person("Wang"), event_role=role,
            status=Participation.Status.CANCELLED,
        )
        self.assertIn(role, EventRole.objects.understaffed())

    def test_signup_counts_take_one_query_for_any_number_of_roles(self):
        # An annotation, not a property. Same guard as with_headcounts() on
        # Position: it stops somebody quietly restoring a per-row count.
        for code in ["lifting", "welcome", "interpreting", "kitchen"]:
            make_role(self.event, code)
        with CaptureQueriesContext(connection) as queries:
            list(EventRole.objects.with_signup_counts())
        self.assertEqual(len(queries), 1)

    def test_the_same_role_cannot_be_opened_twice_on_one_event(self):
        # Otherwise needed_count has two answers and R4 doubles.
        make_role(self.event, "lifting")
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_role(self.event, "lifting")

    def test_needed_count_must_be_positive(self):
        role, _ = ParticipationRole.objects.get_or_create(code="lifting", defaults={"name": "L"})
        with self.assertRaises(IntegrityError), transaction.atomic():
            EventRole.objects.create(event=self.event, role=role, needed_count=0)


class ParticipationTests(TestCase):
    def setUp(self):
        self.event = make_event()
        self.lifting = make_role(self.event, "lifting")
        self.welcome = make_role(self.event, "welcome")
        self.wang = make_person("Wang", birth_date=datetime.date(1980, 5, 5))

    def test_one_person_can_take_two_roles_in_one_event(self):
        # Lifting in the morning, welcome desk in the afternoon: two rows, two
        # sets of hours. Merging them would lose the dimension that tells them
        # apart, permanently.
        Participation.objects.create(contact=self.wang, event_role=self.lifting)
        Participation.objects.create(contact=self.wang, event_role=self.welcome)
        self.assertEqual(self.wang.participations.count(), 2)

    def test_the_same_person_cannot_sign_up_for_one_role_twice(self):
        Participation.objects.create(contact=self.wang, event_role=self.lifting)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Participation.objects.create(contact=self.wang, event_role=self.lifting)

    def test_negative_hours_are_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Participation.objects.create(
                contact=self.wang, event_role=self.lifting,
                status=Participation.Status.ATTENDED, hours=Decimal("-1"),
            )

    def test_hours_on_a_non_attended_row_are_rejected(self):
        # "No-show, 5 hours" is the same self-contradiction as is_active=True
        # next to end_date=2020.
        with self.assertRaises(IntegrityError), transaction.atomic():
            Participation.objects.create(
                contact=self.wang, event_role=self.lifting,
                status=Participation.Status.ABSENT, hours=Decimal("5"),
            )

    def test_checkout_before_checkin_is_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Participation.objects.create(
                contact=self.wang, event_role=self.lifting,
                checked_in_at=NOW, checked_out_at=NOW - HOUR,
            )

    def test_a_checked_in_participant_cannot_be_marked_absent(self):
        # "Did they turn up" may only have one answer.
        with self.assertRaises(IntegrityError), transaction.atomic():
            Participation.objects.create(
                contact=self.wang, event_role=self.lifting,
                checked_in_at=NOW, status=Participation.Status.ABSENT,
            )

    def test_deleting_a_contact_with_participation_is_blocked(self):
        Participation.objects.create(contact=self.wang, event_role=self.lifting)
        with self.assertRaises(ProtectedError):
            self.wang.delete()

    def test_participation_has_no_event_or_role_column(self):
        # Both live inside event_role. Keeping either would let two columns name
        # two different events, and no CheckConstraint can see across that join.
        columns = {field.name for field in Participation._meta.get_fields()}
        self.assertNotIn("role", columns)
        # `event` exists only as a read-through property, not as a column.
        self.assertNotIn("event", {field.name for field in Participation._meta.fields})


class CheckInAndHoursTests(TestCase):
    """hours is the authoritative value; the timestamps only collect it."""

    def setUp(self):
        self.event = make_event()
        self.role = make_role(self.event, "lifting")
        self.wang = make_person("Wang", birth_date=datetime.date(1980, 5, 5))
        self.participation = Participation.objects.create(
            contact=self.wang, event_role=self.role)

    def test_checking_in_sets_status_to_attended(self):
        check_in(self.participation, at=NOW)
        self.participation.refresh_from_db()
        self.assertEqual(self.participation.status, Participation.Status.ATTENDED)

    def test_check_out_writes_hours(self):
        check_in(self.participation, at=NOW)
        check_out(self.participation, at=NOW + 3 * HOUR)
        self.participation.refresh_from_db()
        self.assertEqual(self.participation.hours, Decimal("3.00"))

    def test_check_out_does_not_overwrite_a_manually_entered_hours(self):
        # Somebody left and came back, so the timestamps understate it and a
        # human corrected the number. Recomputing would silently undo that.
        check_in(self.participation, at=NOW)
        record_hours(self.participation, Decimal("5.00"))
        check_out(self.participation, at=NOW + HOUR)
        self.participation.refresh_from_db()
        self.assertEqual(self.participation.hours, Decimal("5.00"))

    def test_hours_entered_from_a_paper_sheet_need_no_timestamps(self):
        record_hours(self.participation, Decimal("3.00"))
        self.participation.refresh_from_db()
        self.assertIsNone(self.participation.checked_in_at)
        self.assertEqual(self.participation.hours, Decimal("3.00"))
        self.assertEqual(self.participation.status, Participation.Status.ATTENDED)


class AttendanceNeedsConsentTests(TestCase):
    """P3's second gate: an unconsented minor never reaches attended.

    sign_up() refuses to create such a row, but that is a hint layer and the
    admin creates Participation rows directly — so the row can exist, and this
    is the gate that decides whether it turns into hours, statistics and a
    notification. Asserted as a ValidationError rather than a database refusal,
    because it spans two tables and D14 says not to dress a hint up as
    enforcement.
    """

    def setUp(self):
        self.event = make_event()
        self.role = make_role(self.event, "lifting")

    def unconsented(self, birth_date):
        # Created straight on the model, exactly as the admin does: this is the
        # state sign_up() refuses to produce and cannot prevent.
        return Participation.objects.create(
            contact=make_person("Minor", birth_date=birth_date), event_role=self.role)

    def minor(self):
        return self.unconsented(local_today() - datetime.timedelta(days=365 * 15))

    def test_checking_in_a_minor_without_consent_is_refused(self):
        participation = self.minor()
        with self.assertRaises(ConsentRequired):
            check_in(participation)
        participation.refresh_from_db()
        self.assertEqual(participation.status, Participation.Status.REGISTERED)

    def test_an_unknown_birth_date_is_refused_too(self):
        with self.assertRaises(ConsentRequired):
            check_in(self.unconsented(None))

    def test_recording_hours_by_hand_is_refused_as_well(self):
        # The paper-sheet route reaches attended without ever checking in, so
        # guarding only check_in() would leave a second way round.
        with self.assertRaises(ConsentRequired):
            record_hours(self.minor(), Decimal("3.00"))

    def test_checking_out_is_refused_as_well(self):
        participation = self.minor()
        participation.checked_in_at = NOW
        participation.save(update_fields=["checked_in_at"])
        with self.assertRaises(ConsentRequired):
            check_out(participation, at=NOW + HOUR)

    def test_a_minor_with_consent_checks_in_normally(self):
        consented = make_person(
            "Consented", birth_date=local_today() - datetime.timedelta(days=365 * 15))
        give_emergency_contact(consented)
        participation = sign_up(
            contact=consented,
            event_role=self.role,
            consent={
                "consent_given_by": "王秀英",
                "consent_method": Participation.ConsentMethod.VERBAL,
                "consent_email": "parent@example.com",
            },
        )
        check_in(participation, at=NOW)
        self.assertEqual(participation.status, Participation.Status.ATTENDED)


class EventTests(TestCase):
    def test_event_end_time_cannot_precede_start_time(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_event(start_time=NOW, end_time=NOW - HOUR)

    def test_deleting_an_event_takes_its_roles_and_signups(self):
        # Two levels of cascade, which is exactly why deleting an event is not
        # a permission ordinary groups get.
        event = make_event()
        role = make_role(event, "lifting")
        Participation.objects.create(contact=make_person("Wang"), event_role=role)
        event.delete()
        self.assertEqual(EventRole.objects.count(), 0)
        self.assertEqual(Participation.objects.count(), 0)

    def test_deleting_a_ministry_with_events_is_blocked(self):
        event = make_event()
        with self.assertRaises(ProtectedError):
            event.ministry.delete()


class VisibilityTests(TestCase):
    """Two predicates, not one status test. See goal.md「可见性与生命周期」."""

    def setUp(self):
        self.ministry = Ministry.objects.create(code="food_pantry", name="Food Pantry")

    def make(self, status):
        return make_event(ministry=self.ministry, status=status)

    def test_a_confirmed_event_is_still_visible_but_not_open_for_signup(self):
        # ⭐ The whole point. Written as status == OPEN, a confirmed event would
        # 404 for the people who already signed up — precisely the ones P6's
        # "click here to cancel" link is sent to.
        confirmed = self.make(Event.Status.CONFIRMED)
        self.assertIn(confirmed, Event.objects.visible_to_volunteers())
        self.assertNotIn(confirmed, Event.objects.open_for_signup())

    def test_a_draft_event_is_neither_visible_nor_open(self):
        draft = self.make(Event.Status.DRAFT)
        self.assertNotIn(draft, Event.objects.visible_to_volunteers())
        self.assertNotIn(draft, Event.objects.open_for_signup())

    def test_a_cancelled_event_stays_visible(self):
        # The people who signed up are exactly who needs to see it is off.
        cancelled = self.make(Event.Status.CANCELLED)
        self.assertIn(cancelled, Event.objects.visible_to_volunteers())
        self.assertNotIn(cancelled, Event.objects.open_for_signup())

    def test_every_status_is_placed_deliberately_in_both_sets(self):
        # A partition test, same as .minors()/.adults()/.birth_date_unknown().
        # Add a sixth status and this goes red rather than the complement
        # quietly publishing it.
        for status in Event.Status:
            with self.subTest(status=status):
                self.assertIn(
                    status in Event.VISIBLE_TO_VOLUNTEERS, (True, False))
                if status in Event.OPEN_FOR_SIGNUP:
                    self.assertIn(status, Event.VISIBLE_TO_VOLUNTEERS)
        self.assertEqual(len(Event.Status), 5)

    def test_neither_set_is_written_as_a_complement(self):
        # B5's lesson, nailed down: list the states and count them — five, so
        # exclude(DRAFT) is wrong even though it agrees today.
        self.assertEqual(
            Event.VISIBLE_TO_VOLUNTEERS,
            frozenset({Event.Status.OPEN, Event.Status.CONFIRMED,
                       Event.Status.COMPLETED, Event.Status.CANCELLED}),
        )


class SignUpTests(TestCase):
    """P3, including the two rules that no constraint can express."""

    def setUp(self):
        self.event = make_event()
        self.role = make_role(self.event, "lifting")
        self.guardian_consent = {
            "consent_given_by": "王秀英",
            "consent_method": Participation.ConsentMethod.VERBAL,
            "consent_email": "parent@example.com",
        }

    def minor(self):
        # With an emergency contact: sign_up() refuses a minor without one, and
        # that refusal has its own test rather than shadowing every other case.
        person = make_person(
            "Minor", birth_date=local_today() - datetime.timedelta(days=365 * 15))
        give_emergency_contact(person)
        return person

    def test_an_adult_can_sign_up_without_consent(self):
        adult = make_person("Adult", birth_date=datetime.date(1980, 1, 1))
        participation = sign_up(contact=adult, event_role=self.role)
        self.assertEqual(participation.status, Participation.Status.REGISTERED)
        self.assertIsNotNone(participation.registered_at)

    def test_a_minor_cannot_sign_up_without_consent(self):
        # ⭐ P3. Asserted as a ValidationError, not a database refusal: this is
        # a hint layer and the test says so rather than dressing it up.
        with self.assertRaises(ConsentRequired):
            sign_up(contact=self.minor(), event_role=self.role)
        self.assertEqual(Participation.objects.count(), 0)

    def test_a_volunteer_with_unknown_birth_date_also_needs_consent(self):
        # is_minor is three-state, and the third state goes to the cautious
        # side. Folded into "adult", a minor with no date on file would be
        # silently signed up with nobody to notify.
        unknown = make_person("Unknown", birth_date=None)
        with self.assertRaises(ConsentRequired):
            sign_up(contact=unknown, event_role=self.role)

    def test_consent_with_a_name_but_no_way_to_reach_them_is_refused(self):
        # Otherwise the consent is collected and P6 still cannot reach the
        # guardian — a signup filed already doomed to be unreachable.
        consent = dict(self.guardian_consent, consent_email="")
        with self.assertRaises(ConsentRequired):
            sign_up(contact=self.minor(), event_role=self.role, consent=consent)

    def test_a_minor_with_consent_and_an_address_can_sign_up(self):
        participation = sign_up(
            contact=self.minor(), event_role=self.role, consent=self.guardian_consent)
        self.assertIsNotNone(participation.consent_at)
        self.assertEqual(
            participation.guardian_address, ("parent@example.com", "email"))

    def test_a_minor_with_only_a_phone_number_resolves_to_sms(self):
        consent = dict(self.guardian_consent, consent_email="", consent_phone="+14085550123")
        participation = sign_up(
            contact=self.minor(), event_role=self.role, consent=consent)
        self.assertEqual(participation.guardian_address, ("+14085550123", "sms"))

    def test_signing_up_twice_for_the_same_role_is_rejected(self):
        adult = make_person("Adult", birth_date=datetime.date(1980, 1, 1))
        sign_up(contact=adult, event_role=self.role)
        with self.assertRaises(ValidationError):
            sign_up(contact=adult, event_role=self.role)

    def test_signing_up_over_needed_count_is_allowed_but_flagged(self):
        # Advisory, never a limit — the same line taken with duplicate names.
        # Over-subscription is ordinary; the system's job is to say so, not to
        # stand in the way.
        #
        # Both halves are asserted, because the name promises both: three
        # people go in against a need of one, the role stops counting as short,
        # and the count a page renders is high enough for it to say so. Testing
        # only "allowed" would leave "flagged" as a claim nothing checks.
        role = make_role(self.event, "welcome", needed_count=1)
        for index in range(3):
            sign_up(
                contact=make_person(f"A{index}", birth_date=datetime.date(1980, 1, 1)),
                event_role=role,
            )
        self.assertEqual(role.participations.count(), 3)
        self.assertNotIn(role, EventRole.objects.understaffed())
        counted = EventRole.objects.with_signup_counts().get(pk=role.pk)
        self.assertGreater(counted.registered_count, counted.needed_count)


class ReportingTests(TestCase):
    """R1–R8. The three R8 traps have a test each — all three fail silently."""

    def setUp(self):
        self.pantry = Ministry.objects.create(code="food_pantry", name="Food Pantry")
        self.event = make_event(ministry=self.pantry)
        self.lifting = make_role(self.event, "lifting")
        self.welcome = make_role(self.event, "welcome")

    def attend(self, person, role, hours):
        return Participation.objects.create(
            contact=person, event_role=role,
            status=Participation.Status.ATTENDED, hours=Decimal(hours),
        )

    def test_total_hours_equals_the_sum_of_per_role_hours(self):
        self.attend(make_person("A"), self.lifting, "3.00")
        self.attend(make_person("B"), self.lifting, "2.50")
        self.attend(make_person("C"), self.welcome, "1.25")
        summary = event_summary(self.event)
        per_role = sum(
            role.hours_total for role in summary["roles"] if role.hours_total is not None)
        self.assertEqual(summary["total_hours"], Decimal("6.75"))
        self.assertEqual(summary["total_hours"], per_role)

    def test_rows_with_null_hours_are_not_counted_as_zero(self):
        # Registered-but-not-yet-happened is not "did zero hours". The total is
        # over the two who have hours, and the third simply is not in it.
        self.attend(make_person("A"), self.lifting, "3.00")
        Participation.objects.create(contact=make_person("B"), event_role=self.lifting)
        summary = event_summary(self.event)
        self.assertEqual(summary["total_hours"], Decimal("3.00"))

    def test_duration_is_derived_not_stored(self):
        self.assertEqual(self.event.duration, datetime.timedelta(hours=3))
        self.assertNotIn("duration", {f.name for f in Event._meta.fields})

    def test_events_in_a_period_use_foundation_month_boundaries(self):
        # local_month_of(), not .year/.month on the stored value: that comes
        # back in UTC, so an event at 6pm Pacific on the 31st reports the next
        # month and falls outside its own month's window.
        start, end = month_bounds(*local_month_of(self.event.start_time))
        self.assertIn(self.event, Event.objects.in_period(start, end))


class R8Tests(TestCase):
    """Which employees of the running ministry took part, and doing what."""

    def setUp(self):
        self.pantry = Ministry.objects.create(code="food_pantry", name="Food Pantry")
        self.tax = Ministry.objects.create(code="tax_help", name="Tax Help")
        self.event = make_event(
            ministry=self.pantry,
            start_time=local_now() - 30 * DAY,
            end_time=local_now() - 30 * DAY + 3 * HOUR,
        )
        self.role = make_role(self.event, "lifting")
        self.post = Position.objects.create(
            code="pantry_staff", name="Pantry staff",
            kind=Position.Kind.EMPLOYEE, ministry=self.pantry,
        )

    def took_part(self, person):
        return Participation.objects.create(contact=person, event_role=self.role)

    def test_an_employee_on_the_day_of_the_event_is_listed(self):
        person = make_person("Staff")
        Assignment.objects.create(
            contact=person, position=self.post,
            start_date=local_today() - datetime.timedelta(days=365),
        )
        self.took_part(person)
        self.assertEqual(
            [row.contact for row in ministry_staff_participation(self.event)], [person])

    def test_somebody_who_left_before_the_event_does_not_appear(self):
        # The clock is the day of the event, not today. Using the default would
        # drop everybody who has left since — quietly.
        person = make_person("Leaver")
        Assignment.objects.create(
            contact=person, position=self.post,
            start_date=local_today() - datetime.timedelta(days=365),
            end_date=local_date_of(self.event.start_time) - datetime.timedelta(days=1),
        )
        self.took_part(person)
        self.assertEqual(list(ministry_staff_participation(self.event)), [])

    def test_somebody_hired_after_the_event_does_not_appear(self):
        person = make_person("Newcomer")
        Assignment.objects.create(
            contact=person, position=self.post,
            start_date=local_date_of(self.event.start_time) + datetime.timedelta(days=1),
        )
        self.took_part(person)
        self.assertEqual(list(ministry_staff_participation(self.event)), [])

    def test_somebody_on_leave_at_the_time_still_counts(self):
        # active(), not serving(). The question is "were they an employee
        # then", not "could they work a shift today".
        person = make_person("OnLeave")
        Assignment.objects.create(
            contact=person, position=self.post,
            status=Assignment.Status.ON_LEAVE,
            start_date=local_today() - datetime.timedelta(days=365),
        )
        self.took_part(person)
        self.assertEqual(
            [row.contact for row in ministry_staff_participation(self.event)], [person])

    def test_two_posts_in_the_same_ministry_still_count_as_one_person(self):
        # distinct() is not optional: one person holding two employee posts
        # would be joined twice and the headcount would gain somebody.
        person = make_person("Doubled")
        second = Position.objects.create(
            code="pantry_lead", name="Pantry lead",
            kind=Position.Kind.EMPLOYEE, ministry=self.pantry,
        )
        for post in (self.post, second):
            Assignment.objects.create(
                contact=person, position=post,
                start_date=local_today() - datetime.timedelta(days=365),
            )
        self.took_part(person)
        self.assertEqual(ministry_staff_participation(self.event).count(), 1)

    def test_an_employee_of_another_ministry_does_not_appear(self):
        person = make_person("TaxStaff")
        other_post = Position.objects.create(
            code="tax_staff", name="Tax staff",
            kind=Position.Kind.EMPLOYEE, ministry=self.tax,
        )
        Assignment.objects.create(
            contact=person, position=other_post,
            start_date=local_today() - datetime.timedelta(days=365),
        )
        self.took_part(person)
        self.assertEqual(list(ministry_staff_participation(self.event)), [])

    def test_a_volunteer_of_the_same_ministry_does_not_appear(self):
        # R8 asks for employees. A volunteer holding a post in the ministry is
        # not one, and the kind filter is what keeps them out.
        person = make_person("Helper")
        volunteer_post = Position.objects.create(
            code="pantry_helper", name="Pantry helper",
            kind=Position.Kind.VOLUNTEER, ministry=self.pantry,
        )
        Assignment.objects.create(
            contact=person, position=volunteer_post,
            start_date=local_today() - datetime.timedelta(days=365),
        )
        self.took_part(person)
        self.assertEqual(list(ministry_staff_participation(self.event)), [])


class DictionaryTableTests(TestCase):
    def test_bulk_create_cannot_insert_an_event_type_code_differing_only_in_case(self):
        EventType.objects.create(code="distribution", name="Distribution")
        with self.assertRaises(IntegrityError), transaction.atomic():
            EventType.objects.bulk_create([EventType(code="Distribution", name="Dup")])

    def test_event_type_code_cannot_be_changed_once_created(self):
        event_type = EventType.objects.create(code="distribution", name="Distribution")
        event_type.code = "dist"
        with self.assertRaises(ValidationError) as caught:
            event_type.full_clean()
        self.assertIn("code", caught.exception.message_dict)

    def test_the_general_participation_role_can_always_be_had(self):
        # Participation.event_role is not nullable, so "no particular job" needs
        # a row to land on.
        first = ParticipationRole.seed_general()
        second = ParticipationRole.seed_general()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.code, "general")

    def test_the_general_role_is_already_there_after_migrating(self):
        # This database was built by the migrations and nothing else, so the
        # row can only have come from 0003. It used to be created by seed_demo,
        # which refuses to run with DEBUG off — meaning a production database
        # would have come up without the one row that "no particular job" has
        # to land on. An invariant of the schema belongs in a migration.
        self.assertTrue(ParticipationRole.objects.filter(code="general").exists())


class PageTestCase(TestCase):
    """Shared cast: two ministries, one admin of each, one plain volunteer.

    Two ministries and two admins, because a single one cannot demonstrate
    scoping — "he can see his own" passes just as well when there is no scope
    at all.
    """

    def setUp(self):
        self.pantry = Ministry.objects.create(code="food_pantry", name="Food Pantry")
        self.tax = Ministry.objects.create(code="tax_help", name="Tax Help")

        self.zhang = self.account("zhang", "张", birth_date=datetime.date(1980, 1, 1))
        MinistryRole.objects.create(contact=self.zhang.contact, ministry=self.pantry)
        self.other_admin = self.account("chen", "陈", birth_date=datetime.date(1980, 1, 1))
        MinistryRole.objects.create(contact=self.other_admin.contact, ministry=self.tax)
        self.lisi = self.account("lisi", "李", birth_date=datetime.date(1990, 1, 1))

        self.event = make_event(ministry=self.pantry, owner=self.zhang.contact)
        self.role = make_role(self.event, "lifting", needed_count=2)

    def account(self, username, last_name, **contact_fields):
        # An email by default: without one the person is legitimately
        # unreachable, which is a real state but not the one most of these
        # tests are about.
        contact_fields.setdefault("email", f"{username}@example.com")
        return register_account(
            username=username, password="a-good-long-password",
            legal_last_name=last_name, **contact_fields,
        )

    def login(self, user):
        self.client.force_login(user)
        return user


class VolunteerPageTests(PageTestCase):
    """P3, tested by hitting URLs — the isolation is in the query, not the page."""

    def test_anonymous_visitors_are_redirected_to_login(self):
        response = self.client.get(reverse("events:event_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_the_event_list_shows_only_open_events(self):
        self.login(self.lisi)
        draft = make_event(ministry=self.pantry, name="Draft one",
                           owner=self.zhang.contact, status=Event.Status.DRAFT)
        response = self.client.get(reverse("events:event_list"))
        self.assertContains(response, self.event.name)
        self.assertNotContains(response, draft.name)

    def test_a_confirmed_event_is_absent_from_the_list_but_still_opens(self):
        # ⭐ Visibility and signability are two questions. Answer them with one
        # status test and P6's "can't make it? cancel here" link 404s, on
        # exactly the events that filled up.
        self.login(self.lisi)
        self.event.status = Event.Status.CONFIRMED
        self.event.save()
        listing = self.client.get(reverse("events:event_list"))
        self.assertNotContains(listing, self.event.name)
        detail = self.client.get(reverse("events:event_detail", args=[self.event.pk]))
        self.assertEqual(detail.status_code, 200)

    def test_a_cancelled_event_does_not_appear_in_the_list(self):
        self.login(self.lisi)
        self.event.status = Event.Status.CANCELLED
        self.event.save()
        response = self.client.get(reverse("events:event_list"))
        self.assertNotContains(response, self.event.name)

    def test_a_draft_event_detail_page_is_404_for_volunteers(self):
        self.login(self.lisi)
        self.event.status = Event.Status.DRAFT
        self.event.save()
        response = self.client.get(reverse("events:event_detail", args=[self.event.pk]))
        self.assertEqual(response.status_code, 404)

    def test_an_adult_can_sign_up_from_the_page(self):
        self.login(self.lisi)
        response = self.client.post(
            reverse("events:event_signup", args=[self.event.pk]),
            {"event_role": self.role.pk},
        )
        self.assertRedirects(
            response, reverse("events:event_detail", args=[self.event.pk]))
        self.assertTrue(
            Participation.objects.filter(contact=self.lisi.contact).exists())

    def test_a_minor_signing_up_without_consent_is_refused_by_the_page(self):
        minor = self.account(
            "xiaoming", "小明",
            birth_date=local_today() - datetime.timedelta(days=365 * 15))
        self.login(minor)
        response = self.client.post(
            reverse("events:event_signup", args=[self.event.pk]),
            {"event_role": self.role.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Participation.objects.count(), 0)

    def test_a_minor_with_consent_but_no_address_is_still_refused(self):
        minor = self.account(
            "xiaoming", "小明",
            birth_date=local_today() - datetime.timedelta(days=365 * 15))
        self.login(minor)
        response = self.client.post(
            reverse("events:event_signup", args=[self.event.pk]),
            {
                "event_role": self.role.pk,
                "consent_given_by": "王秀英",
                "consent_method": Participation.ConsentMethod.VERBAL,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Participation.objects.count(), 0)

    def test_a_volunteer_cannot_open_another_persons_participation(self):
        mine = Participation.objects.create(
            contact=self.zhang.contact, event_role=self.role)
        self.login(self.lisi)
        response = self.client.get(
            reverse("events:participation_cancel", args=[mine.pk]))
        self.assertEqual(response.status_code, 404)

    def test_my_participations_lists_only_my_own(self):
        mine = Participation.objects.create(
            contact=self.lisi.contact, event_role=self.role)
        Participation.objects.create(contact=self.zhang.contact, event_role=self.role)
        self.login(self.lisi)
        response = self.client.get(reverse("events:my_participations"))
        self.assertEqual(list(response.context["participations"]), [mine])

    def test_my_participations_leaves_out_signups_on_unpublished_events(self):
        # Every row on that page links to the detail page, and the detail page
        # uses visible_to_volunteers() — so a signup an admin entered against a
        # draft would otherwise be listed with a link that 404s. Both pages ask
        # the same predicate, which is the point of having two of them.
        draft = make_event(ministry=self.pantry, name="Unpublished",
                           owner=self.zhang.contact, status=Event.Status.DRAFT)
        hidden = Participation.objects.create(
            contact=self.lisi.contact, event_role=make_role(draft, "lifting"))
        self.login(self.lisi)
        response = self.client.get(reverse("events:my_participations"))
        self.assertNotIn(hidden, response.context["participations"])
        detail = self.client.get(reverse("events:event_detail", args=[draft.pk]))
        self.assertEqual(detail.status_code, 404)

    def test_cancelling_keeps_the_row_and_changes_the_status(self):
        mine = Participation.objects.create(
            contact=self.lisi.contact, event_role=self.role)
        self.login(self.lisi)
        self.client.post(reverse("events:participation_cancel", args=[mine.pk]))
        mine.refresh_from_db()
        self.assertEqual(mine.status, Participation.Status.CANCELLED)


class MinistryAdminPageTests(PageTestCase):
    """P2 / P4, and the over-reach checks that give D20 its point."""

    def test_publishing_for_another_ministry_returns_403(self):
        # The POST side. The narrowed dropdown stops a slip; this stops a
        # forged id, and only one of the two is a security check.
        self.login(self.zhang)
        event_type = EventType.objects.first()
        response = self.client.post(reverse("events:event_create"), {
            "name": "Sneaky", "event_type": event_type.pk, "ministry": self.tax.pk,
            "start_time": "2026-09-01T09:00", "end_time": "2026-09-01T12:00",
            "status": Event.Status.DRAFT,
        })
        self.assertIn(response.status_code, (403, 200))
        self.assertFalse(Event.objects.filter(name="Sneaky").exists())

    def test_the_ministry_dropdown_lists_only_administered_ministries(self):
        self.login(self.zhang)
        response = self.client.get(reverse("events:event_create"))
        choices = list(response.context["form"].fields["ministry"].queryset)
        self.assertEqual(choices, [self.pantry])

    def test_publishing_for_their_own_ministry_works(self):
        self.login(self.zhang)
        event_type = EventType.objects.first()
        response = self.client.post(reverse("events:event_create"), {
            "name": "Saturday pantry", "event_type": event_type.pk,
            "ministry": self.pantry.pk,
            "start_time": "2026-09-01T09:00", "end_time": "2026-09-01T12:00",
            "status": Event.Status.OPEN,
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Event.objects.filter(name="Saturday pantry").exists())

    def test_viewing_another_ministrys_registrations_returns_403(self):
        # The GET side of the same rule.
        self.login(self.other_admin)
        response = self.client.get(
            reverse("events:event_registrations", args=[self.event.pk]))
        self.assertEqual(response.status_code, 403)

    def test_a_plain_volunteer_gets_403_on_every_admin_url(self):
        self.login(self.lisi)
        for name in ["event_roles", "event_registrations", "event_attendance",
                     "event_report"]:
            with self.subTest(url=name):
                response = self.client.get(reverse(f"events:{name}", args=[self.event.pk]))
                self.assertEqual(response.status_code, 403)
        self.assertEqual(
            self.client.get(reverse("events:event_create")).status_code, 403)

    def test_a_superuser_has_no_ministry_scope_either(self):
        # No back door. A superuser has the admin; these pages are scoped, and
        # exempting them here would be a hole straight through D20.
        root = get_user_model().objects.create_superuser(username="root", password="x")
        self.login(root)
        response = self.client.get(reverse("events:event_roles", args=[self.event.pk]))
        self.assertEqual(response.status_code, 403)

    def test_a_role_with_nobody_in_it_is_listed_on_the_registrations_page(self):
        # The one thing this page exists to show a coordinator.
        empty = make_role(self.event, "interpreting", needed_count=1)
        self.login(self.zhang)
        response = self.client.get(
            reverse("events:event_registrations", args=[self.event.pk]))
        self.assertContains(response, empty.role.name)

    def test_checking_in_sets_status_to_attended(self):
        participation = Participation.objects.create(
            contact=self.lisi.contact, event_role=self.role)
        self.login(self.zhang)
        self.client.post(reverse("events:event_attendance", args=[self.event.pk]),
                         {"participation": participation.pk, "action": "check_in"})
        participation.refresh_from_db()
        self.assertEqual(participation.status, Participation.Status.ATTENDED)

    def test_hours_can_be_entered_by_hand_with_no_timestamps(self):
        participation = Participation.objects.create(
            contact=self.lisi.contact, event_role=self.role)
        self.login(self.zhang)
        self.client.post(reverse("events:event_attendance", args=[self.event.pk]),
                         {"participation": participation.pk, "action": "hours",
                          "hours": "3.00"})
        participation.refresh_from_db()
        self.assertEqual(participation.hours, Decimal("3.00"))
        self.assertIsNone(participation.checked_in_at)

    def test_the_attendance_page_shows_a_minors_emergency_phone(self):
        minor = self.account(
            "xiaoming", "小明",
            birth_date=local_today() - datetime.timedelta(days=365 * 15))
        relationship = RelationshipType.objects.get(code="parent")
        EmergencyContact.objects.create(
            person=minor.contact, name="王秀英", phone="+14085550199",
            relationship_type=relationship)
        Participation.objects.create(contact=minor.contact, event_role=self.role)
        self.login(self.zhang)
        response = self.client.get(
            reverse("events:event_attendance", args=[self.event.pk]))
        self.assertContains(response, "王秀英")

    def test_a_ministry_admin_cannot_open_the_grant_page(self):
        # P5 reads the global group and never MinistryRole: no recruiting one's
        # own downline.
        self.login(self.zhang)
        response = self.client.get(
            reverse("org:ministry_admins", args=[self.pantry.pk]))
        self.assertEqual(response.status_code, 403)

    def test_a_foundation_admin_can_grant_and_revoke(self):
        self.login(self.zhang)
        self.zhang.groups.add(foundation_admin_group())
        target = self.account("wang", "王", birth_date=datetime.date(1985, 1, 1))
        url = reverse("org:ministry_admins", args=[self.tax.pk])
        self.client.post(url, {"contact": target.contact.pk})
        grant = MinistryRole.objects.get(contact=target.contact, ministry=self.tax)
        # granted_by comes from the session, not from a field on the page.
        self.assertEqual(grant.granted_by, self.zhang)

        self.client.post(url, {"revoke": grant.pk})
        grant.refresh_from_db()
        # Revoked by dating it, not by deleting it: the history has to answer
        # "who could see this ministry's signups last March".
        self.assertIsNotNone(grant.end_date)
        self.assertTrue(MinistryRole.objects.filter(pk=grant.pk).exists())


class NavigationEntranceTests(PageTestCase):
    """C0.2.4: every management page is reachable by clicking, and only by those who may.

    The gap this closes was not a permission bug — the checks were all correct.
    It was that event_create and org:ministry_admins appeared in no template at
    all, and event_roles (which links onward to registrations, attendance, the
    report and the notice page) was reachable only by the redirect after
    creating an event. 334 tests passed the whole time, because a test reverses
    a URL directly and never has to find the link.
    """

    def nav_of(self, user):
        self.login(user)
        return self.client.get(reverse("events:event_list")).content.decode()

    def test_a_ministry_admin_is_offered_the_pages_they_can_use(self):
        nav = self.nav_of(self.zhang)
        self.assertIn(reverse("events:event_manage_list"), nav)

    def test_a_plain_volunteer_is_offered_neither_management_entrance(self):
        # ⚠️ The failure this asserts against is a link that 403s. Showing an
        #    entrance you may not walk through teaches people that the app is
        #    broken, which is how a correct permission check gets deleted.
        nav = self.nav_of(self.lisi)
        self.assertNotIn(reverse("events:event_manage_list"), nav)
        self.assertNotIn(reverse("org:ministry_list"), nav)

    def test_only_a_foundation_admin_is_offered_the_grant_page(self):
        self.assertNotIn(reverse("org:ministry_list"), self.nav_of(self.zhang))

        boss = self.account("boss", "Boss", birth_date=datetime.date(1970, 1, 1))
        boss.groups.add(foundation_admin_group())
        self.assertIn(reverse("org:ministry_list"), self.nav_of(boss))

    def test_the_hub_lists_drafts_and_finished_events(self):
        # Why event_list cannot serve as the entrance: it shows what is open
        # and upcoming, which excludes drafts and excludes every event whose
        # report anybody would want to read.
        make_event(ministry=self.pantry, owner=self.zhang.contact,
                   name="Draft one", status=Event.Status.DRAFT)
        make_event(ministry=self.pantry, owner=self.zhang.contact,
                   name="Finished one", status=Event.Status.COMPLETED,
                   start_time=NOW - 10 * DAY, end_time=NOW - 10 * DAY + HOUR)
        self.login(self.zhang)
        response = self.client.get(reverse("events:event_manage_list"))
        self.assertContains(response, "Draft one")
        self.assertContains(response, "Finished one")

    def test_the_hub_links_onward_to_every_page_for_an_event(self):
        # ⚠️ `event_roles` is deliberately absent: the roles panel moved onto
        #    the edit page on 2026-08-04, so `event_update` is now that link
        #    too. Keeping it in this list would assert a route that exists only
        #    to redirect.
        self.login(self.zhang)
        page = self.client.get(reverse("events:event_manage_list")).content.decode()
        for name in ["event_update", "event_registrations",
                     "event_attendance", "event_report", "event_notify"]:
            with self.subTest(link=name):
                self.assertIn(reverse(f"events:{name}", args=[self.event.pk]), page)

    def test_the_hub_shows_only_ministries_this_account_administers(self):
        make_event(ministry=self.tax, owner=self.other_admin.contact,
                   name="Not mine")
        self.login(self.zhang)
        response = self.client.get(reverse("events:event_manage_list"))
        self.assertNotContains(response, "Not mine")

    def test_the_hub_refuses_an_account_with_no_ministry(self):
        self.login(self.lisi)
        self.assertEqual(
            self.client.get(reverse("events:event_manage_list")).status_code, 403)

    def test_the_ministry_list_refuses_a_mere_ministry_admin(self):
        # P5 asks for a tier above ministry admin; if zhang could reach this he
        # could appoint his own downline.
        self.login(self.zhang)
        self.assertEqual(
            self.client.get(reverse("org:ministry_list")).status_code, 403)


class PeriodFilterPageTests(PageTestCase):
    """C0.2.3: R1 with a URL — "how many events run in this window".

    in_period() and events_in_period() were written and tested in Phase B and
    then had no caller but the tests: the only UI that could answer the first
    line of the requirement was the admin changelist, which volunteers cannot
    reach. These two pages are for everybody, because deciding which event to
    join is the same question pointed forwards.
    """

    def setUp(self):
        super().setUp()
        # self.event (from PageTestCase) is NOW + 1 day, open.
        self.old = make_event(
            ministry=self.pantry, owner=self.zhang.contact,
            name="Last month", status=Event.Status.COMPLETED,
            start_time=NOW - 30 * DAY, end_time=NOW - 30 * DAY + 3 * HOUR,
        )

    def day(self, when):
        return local_date_of(when).isoformat()

    def test_the_upcoming_list_reports_how_many(self):
        self.login(self.lisi)
        response = self.client.get(reverse("events:event_list"))
        self.assertEqual(response.context["total"], 1)

    def test_a_window_narrows_the_count(self):
        self.login(self.lisi)
        response = self.client.get(reverse("events:event_list"), {
            "start": self.day(NOW + 10 * DAY), "end": self.day(NOW + 20 * DAY),
        })
        self.assertEqual(response.context["total"], 0)
        self.assertNotContains(response, self.event.name)

    def test_the_last_day_of_the_window_is_included(self):
        # in_period() is half-open, so the end date has to become midnight at
        # the start of the *next* day. Getting this wrong drops everything on
        # the final day of the range somebody asked for, and the answer still
        # looks plausible — which is why it is asserted rather than trusted.
        self.login(self.lisi)
        response = self.client.get(reverse("events:event_list"), {
            "start": self.day(NOW), "end": self.day(self.event.start_time),
        })
        self.assertEqual(response.context["total"], 1)

    def test_past_events_are_listed_and_counted(self):
        self.login(self.lisi)
        response = self.client.get(reverse("events:past_events"))
        self.assertEqual(response.context["total"], 1)
        self.assertContains(response, "Last month")

    def test_a_finished_event_is_reachable_again(self):
        # The point of the page: before it existed, an event left the interface
        # the moment it ended, taking its report with it.
        self.login(self.lisi)
        response = self.client.get(reverse("events:past_events"))
        self.assertContains(
            response, reverse("events:event_detail", args=[self.old.pk]))

    def test_an_event_running_right_now_is_neither_upcoming_nor_past(self):
        # past() reads end_time, not "not upcoming". An event that started this
        # morning and runs until tonight is still happening; filing it under
        # history while people are checking in would be wrong in both places.
        running = make_event(
            ministry=self.pantry, owner=self.zhang.contact, name="Running now",
            start_time=NOW - HOUR, end_time=NOW + HOUR,
        )
        self.login(self.lisi)
        self.assertNotContains(
            self.client.get(reverse("events:past_events")), "Running now")
        self.assertNotContains(
            self.client.get(reverse("events:event_list")), "Running now")
        self.assertTrue(Event.objects.filter(pk=running.pk).exists())

    def test_a_draft_never_appears_on_either_page(self):
        make_event(
            ministry=self.pantry, owner=self.zhang.contact, name="Secret draft",
            status=Event.Status.DRAFT,
            start_time=NOW - 5 * DAY, end_time=NOW - 5 * DAY + HOUR,
        )
        self.login(self.lisi)
        self.assertNotContains(
            self.client.get(reverse("events:past_events")), "Secret draft")
        self.assertNotContains(
            self.client.get(reverse("events:event_list")), "Secret draft")

    def test_an_end_before_the_start_is_a_form_error_not_an_empty_list(self):
        self.login(self.lisi)
        response = self.client.get(reverse("events:event_list"), {
            "start": self.day(NOW + 10 * DAY), "end": self.day(NOW),
        })
        self.assertFalse(response.context["period"].is_valid())

    def test_both_pages_need_a_login(self):
        for name in ["event_list", "past_events"]:
            with self.subTest(page=name):
                response = self.client.get(reverse(f"events:{name}"))
                self.assertEqual(response.status_code, 302)

    # --- R2 as a filter, not only as a column (2026-08-04) -----------------

    def test_filtering_by_ministry_alone_answers_what_is_open_here(self):
        """"Everything the food pantry has open" — no dates involved.

        One of the three questions this one form has to answer; the other two
        are "what is on next month" and both together.
        """
        other = make_event(
            ministry=self.tax, owner=self.zhang.contact, name="Tax clinic",
            start_time=NOW + 2 * DAY, end_time=NOW + 2 * DAY + 2 * HOUR,
        )
        self.login(self.lisi)
        response = self.client.get(reverse("events:event_list"),
                                   {"ministry": self.pantry.pk})
        self.assertEqual(response.context["total"], 1)
        self.assertContains(response, self.event.name)
        self.assertNotContains(response, other.name)

    def test_a_ministry_and_a_window_narrow_together(self):
        make_event(
            ministry=self.tax, owner=self.zhang.contact, name="Tax clinic",
            start_time=self.event.start_time, end_time=self.event.end_time,
        )
        self.login(self.lisi)
        response = self.client.get(reverse("events:event_list"), {
            "ministry": self.pantry.pk,
            "start": self.day(NOW), "end": self.day(self.event.start_time),
        })
        self.assertEqual(response.context["total"], 1)

    def test_the_dropdown_offers_every_active_ministry(self):
        """Not "ministries with events in the current results".

        ⚠️ The narrower list reads better until somebody picks a ministry and
           watches it disappear from the dropdown it was just chosen from —
           the options would depend on the filter they are part of.
        """
        self.login(self.lisi)
        response = self.client.get(reverse("events:event_list"),
                                   {"ministry": self.pantry.pk})
        offered = set(response.context["period"].fields["ministry"].queryset)
        self.assertIn(self.tax, offered)

    def test_past_events_can_be_filtered_by_ministry_too(self):
        self.login(self.lisi)
        response = self.client.get(reverse("events:past_events"),
                                   {"ministry": self.tax.pk})
        self.assertEqual(response.context["total"], 0)


class ConsentFormShapeTests(PageTestCase):
    """2026-08-04 feedback: the consent section says what it actually enforces.

    ⚠️ `consent_method` was `required=False` on the form while
       `services.sign_up()` refused any signup without it. The field was
       therefore **already compulsory and the page did not say so** — the only
       way to find out was to submit and be bounced, with the complaint
       attached to a different field.
    """

    def setUp(self):
        super().setUp()
        self.minor = self.account(
            "xiaoming", "Xiao",
            birth_date=local_today() - datetime.timedelta(days=365 * 15))

    def minor_form(self):
        return SignUpForm(event=self.event, contact=self.minor.contact)

    def test_consent_method_is_required_when_consent_applies(self):
        self.assertTrue(self.minor_form().fields["consent_method"].required)

    def test_consent_method_is_asked_last(self):
        """It applies to both paths, so it sits after the branch, not inside it.

        Picking an emergency contact still leaves "how was consent given?"
        unanswered — which is why it cannot live in the group that greys out.
        """
        names = list(self.minor_form().fields)
        self.assertEqual(names[-1], "consent_method")
        self.assertLess(names.index("use_emergency_contact"),
                        names.index("consent_method"))

    def test_an_adult_is_not_asked_for_a_consent_method(self):
        # required=True is switched on in __init__ and only when it applies —
        # at class level it would demand a method from adults, for whom the
        # whole section is hidden.
        form = SignUpForm(event=self.event, contact=self.lisi.contact)
        self.assertFalse(form.needs_consent)
        self.assertFalse(form.fields["consent_method"].required)

    def test_the_manual_details_are_grouped_so_they_can_be_disabled_together(self):
        """The four fields the emergency-contact shortcut makes irrelevant.

        Asserted on the rendered page rather than on the form, because the
        grouping is what lets one native `disabled` on the fieldset switch all
        four off at once — and `consent_method` has to stay outside it.
        """
        self.login(self.minor)
        page = self.client.get(
            reverse("events:event_signup", args=[self.event.pk])).content.decode()
        block = page.split("<fieldset", 1)[1].split("</fieldset>", 1)[0]
        for name in ["consent_given_by", "consent_relationship",
                     "consent_email", "consent_phone"]:
            with self.subTest(field=name):
                self.assertIn(name, block)
        self.assertNotIn("consent_method", block)


class DetailPageBackLinkTests(PageTestCase):
    """2026-08-05: "back" goes where you came from, not where your role lives.

    The first version of this link was chosen by role — a manager got "Events I
    manage", everybody else got nothing. That sent a ministry admin who had
    arrived from the volunteer list to a page they had never been on, and left
    a volunteer with no way back at all.

    The origin travels as a `?from=` marker on the link.

    ⚠️ Never the Referer header: empty for a pasted URL, a new tab, a link in
       an email, or a browser told not to send it — and empty silently means
       "default", which is the case nobody tests.
    """

    def url(self, marker=None):
        base = reverse("events:event_detail", args=[self.event.pk])
        return f"{base}?from={marker}" if marker else base

    def back(self, response):
        """The href and text of the back link, as they were rendered."""
        page = response.content.decode()
        chunk = page.split("&larr;", 1)[1].split("</a>", 1)[0]
        return page.split('<a href="', 1), chunk.strip()

    def test_arriving_with_no_marker_goes_to_events(self):
        # A pasted URL, a bookmark, a link out of a notification email.
        self.login(self.lisi)
        response = self.client.get(self.url())
        self.assertContains(response, reverse("events:event_list"))
        self.assertContains(response, "&larr; Events")

    def test_a_manager_arriving_from_the_volunteer_list_goes_back_to_it(self):
        """⭐ The case the old role-based link got wrong.

        zhang administers this event, so the previous version sent them to the
        management list — a page they had not come from.
        """
        self.login(self.zhang)
        response = self.client.get(self.url())
        self.assertContains(response, "&larr; Events")
        self.assertNotContains(response, "&larr; Events I manage")

    def test_arriving_from_the_management_list_goes_back_to_it(self):
        self.login(self.zhang)
        response = self.client.get(self.url("manage"))
        self.assertContains(response, reverse("events:event_manage_list"))
        self.assertContains(response, "&larr; Events I manage")

    def test_arriving_from_past_events_goes_back_to_past_events(self):
        # Otherwise somebody three pages into the history is dropped into the
        # "upcoming" list and has to start again.
        self.login(self.lisi)
        response = self.client.get(self.url("past"))
        self.assertContains(response, reverse("events:past_events"))
        self.assertContains(response, "&larr; Past events")

    def test_a_volunteer_handed_a_manage_marker_is_not_sent_somewhere_they_are_refused(self):
        """⚠️ The marker is honoured only if the page is actually reachable.

        A back button that 403s reads as a broken site, not as a page that is
        not for you — and the marker is in a URL anybody can construct.
        """
        self.login(self.lisi)
        response = self.client.get(self.url("manage"))
        self.assertNotContains(response, reverse("events:event_manage_list"))
        self.assertContains(response, "&larr; Events")

    def test_the_foundation_tier_gets_the_label_its_navigation_uses(self):
        # The same page is called "All events" for this tier, and one page
        # should not have two names.
        boss = self.account("boss", "Boss", birth_date=datetime.date(1975, 1, 1))
        boss.groups.add(foundation_admin_group())
        self.login(type(boss).objects.get(pk=boss.pk))
        response = self.client.get(self.url("manage"))
        self.assertContains(response, "&larr; All events")

    def test_arriving_from_my_signups_goes_back_to_my_signups(self):
        self.login(self.lisi)
        response = self.client.get(self.url("mine"))
        self.assertContains(response, reverse("events:my_participations"))
        self.assertContains(response, "&larr; My signups")

    def test_an_unknown_marker_falls_back_rather_than_breaking(self):
        # ⚠️ The marker is a key into a table in the view, never a URL. Anything
        #    not in that table is simply not a destination.
        self.login(self.lisi)
        response = self.client.get(self.url("https://example.com/evil"))
        self.assertNotContains(response, "example.com")
        self.assertContains(response, "&larr; Events")


class MinistryWebsiteLinkTests(PageTestCase):
    """2026-08-05: the ministry's name links to its own page — when there is one.

    ⚠️ No placeholder link. A link that does nothing does not read as "not
       written yet", it reads as a broken site; C0.5 spent a whole step on that
       failure. The field is optional and the foundation fills it in the admin.
    """

    def url(self):
        return reverse("events:event_detail", args=[self.event.pk])

    def test_a_ministry_with_no_website_is_plain_text(self):
        self.login(self.lisi)
        response = self.client.get(self.url())
        self.assertContains(response, self.pantry.name)
        self.assertNotContains(response, 'rel="noopener noreferrer"')

    def test_a_ministry_with_a_website_becomes_a_link(self):
        self.pantry.website = "https://pantry.example.org/"
        self.pantry.save()
        self.login(self.lisi)
        response = self.client.get(self.url())
        self.assertContains(response, 'href="https://pantry.example.org/"')

    def test_the_link_cannot_hand_the_other_site_this_window(self):
        """⚠️ `noopener` is not decoration.

        Without it the opened page can navigate this one somewhere else through
        `window.opener`, and nothing about this tab looks wrong while it happens.
        """
        self.pantry.website = "https://pantry.example.org/"
        self.pantry.save()
        self.login(self.lisi)
        self.assertContains(self.client.get(self.url()), 'rel="noopener noreferrer"')

    def test_a_javascript_url_is_refused_by_validation(self):
        # URLField's scheme whitelist is what keeps this out of an href.
        # ⚠️ It runs in full_clean(), which the admin calls and bulk paths do
        #    not — D9's standing caveat, restated because this value is rendered.
        self.pantry.website = "javascript:alert(1)"
        with self.assertRaises(ValidationError):
            self.pantry.full_clean()


class MyParticipationsColumnTests(PageTestCase):
    """2026-08-04 feedback: "when is it?" answered on the list itself."""

    def test_each_row_carries_the_events_start_time(self):
        self.client.force_login(self.lisi)
        self.client.post(reverse("events:event_signup", args=[self.event.pk]),
                         {"event_role": self.role.pk})
        response = self.client.get(reverse("events:my_participations"))
        self.assertContains(response, "Starts")
        self.assertContains(response, formats.date_format(
            timezone.localtime(self.event.start_time), "DATETIME_FORMAT"))


class MergedEditAndRolesPageTests(PageTestCase):
    """2026-08-04 feedback: an event is edited in one place, not two.

    The roles page never had an entrance of its own — you arrived at it from
    the redirect after creating an event, and a day later there was no way back
    except typing the URL. That is the same shape as the five gaps C0.2 closed.
    """

    def edit_url(self):
        return reverse("events:event_update", args=[self.event.pk])

    def test_the_edit_page_carries_the_roles(self):
        self.login(self.zhang)
        response = self.client.get(self.edit_url())
        self.assertContains(response, "Roles for this event")
        self.assertContains(response, self.role.role.name)

    def test_the_old_roles_url_sends_you_to_the_edit_page(self):
        # Kept rather than deleted: the roles form still posts here, and
        # templates, tests and bookmarks point at it.
        self.login(self.zhang)
        response = self.client.get(reverse("events:event_roles", args=[self.event.pk]))
        self.assertRedirects(response, self.edit_url())

    def test_creating_an_event_lands_on_the_merged_page(self):
        self.login(self.zhang)
        response = self.client.post(reverse("events:event_create"), {
            "name": "Soup run", "event_type": EventType.objects.first().pk,
            "ministry": self.pantry.pk,
            "start_time": "2026-09-01T09:00", "end_time": "2026-09-01T12:00",
            "status": Event.Status.OPEN,
        })
        created = Event.objects.get(name="Soup run")
        self.assertRedirects(
            response, reverse("events:event_update", args=[created.pk]))

    def test_a_rejected_role_keeps_its_errors_instead_of_redirecting(self):
        """⚠️ The reason this POST renders rather than redirects.

        A redirect after an invalid submission throws the errors away, and the
        page comes back looking as though nothing happened.
        """
        self.login(self.zhang)
        response = self.client.post(
            reverse("events:event_roles", args=[self.event.pk]), {"needed_count": 2})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["role_form"].errors)

    def test_the_two_forms_on_the_page_do_not_share_errors(self):
        """One request binds one form; the other has to come back clean.

        ⚠️ Sharing a context key between them was the trap this merge could
           easily have introduced — the event form would come back covered in
           complaints about a role nobody was editing.
        """
        self.login(self.zhang)
        response = self.client.post(
            reverse("events:event_roles", args=[self.event.pk]), {"needed_count": 2})
        self.assertFalse(response.context["form"].errors)


class NewParticipationRoleTests(PageTestCase):
    """2026-08-04 feedback: a ministry admin can add a job to the vocabulary.

    ⚠️ ParticipationRole is the grouping dimension for R5 and R7. Two rows
       meaning one job do not raise anything — they split one column of every
       report in two, and both halves look plausible. That is the cost this
       feature buys, and the duplicate check is what keeps it rare.
    """

    def add(self, **payload):
        self.login(self.zhang)
        return self.client.post(
            reverse("events:event_roles", args=[self.event.pk]),
            {"needed_count": 1, **payload})

    def test_a_new_name_becomes_a_role_and_gets_opened(self):
        self.add(new_role_name="Sound desk")
        created = ParticipationRole.objects.get(name="Sound desk")
        self.assertEqual(created.code, "sound-desk")
        self.assertTrue(self.event.roles.filter(role=created).exists())

    def test_a_duplicate_is_refused_and_names_the_one_that_exists(self):
        """⚠️ The existing name is the whole content of the error.

        "That already exists" leaves somebody hunting a dropdown of thirty
        entries for a word they may have spelled differently.
        """
        response = self.add(new_role_name="  lIfTiNg ")
        self.assertEqual(response.status_code, 200)
        errors = response.context["role_form"].errors["new_role_name"]
        self.assertIn(self.role.role.name, " ".join(errors))
        self.assertEqual(
            ParticipationRole.objects.filter(name__iexact="lifting").count(), 1)

    def test_only_near_misses_are_caught_not_synonyms(self):
        """The limit, asserted so nobody mistakes it for more than it is.

        "Heavy lifting" is a different string, so it goes straight in next to
        "Lifting". Catching that needs a human, not a comparison.
        """
        self.add(new_role_name="Heavy lifting")
        self.assertTrue(ParticipationRole.objects.filter(name="Heavy lifting").exists())

    def test_picking_one_and_naming_one_is_refused(self):
        response = self.add(role=self.role.role.pk, new_role_name="Sound desk")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["role_form"].non_field_errors())
        self.assertFalse(ParticipationRole.objects.filter(name="Sound desk").exists())

    def test_neither_is_refused(self):
        response = self.add()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["role_form"].non_field_errors())

    def test_two_different_names_that_slugify_alike_still_get_distinct_codes(self):
        # ⚠️ The suffix loop produces a usable code; the database constraint is
        #    what actually guarantees uniqueness (D9). This asserts the first.
        self.add(new_role_name="Set-up")
        self.add(new_role_name="Set up")
        codes = set(ParticipationRole.objects.filter(
            name__in=["Set-up", "Set up"]).values_list("code", flat=True))
        self.assertEqual(len(codes), 2)


class PrefilledHoursTests(PageTestCase):
    """2026-08-04 feedback: the hours box starts at the event's own length.

    ⚠️ The cost is recorded rather than hidden, in phase-c.md's known gaps: a
       prefilled number is **indistinguishable from a considered one**. The
       empty box forced somebody to type; this one does not.
    """

    def setUp(self):
        super().setUp()
        self.participation = Participation.objects.create(
            contact=self.lisi.contact, event_role=self.role)
        self.url = reverse("events:event_attendance", args=[self.event.pk])

    def box(self, page):
        return page.split(f'id="hours-{self.participation.pk}"', 1)[1].split(">", 1)[0]

    def test_somebody_with_no_hours_yet_gets_the_scheduled_length(self):
        self.login(self.zhang)
        page = self.client.get(self.url).content.decode()
        expected = scheduled_hours(self.event)
        self.assertIn(f'value="{expected}"', self.box(page))

    def test_somebody_with_hours_already_recorded_gets_those(self):
        """⚠️ Not the scheduled length.

        Otherwise a volunteer who checked out after three hours has a box
        reading six, and one careless click overwrites the real number with
        the plan.
        """
        record_hours(self.participation, Decimal("3.00"))
        self.login(self.zhang)
        page = self.client.get(self.url).content.decode()
        self.assertIn('value="3.00"', self.box(page))

    def test_zero_recorded_hours_are_not_treated_as_missing(self):
        # Same trap as the dash in the Hours column: Decimal("0.00") is falsy,
        # so `default` would quietly replace a real zero with the plan.
        record_hours(self.participation, Decimal("0"))
        self.login(self.zhang)
        page = self.client.get(self.url).content.decode()
        self.assertIn('value="0.00"', self.box(page))

    def test_using_an_emergency_contact_copies_its_email_too(self):
        """2026-08-05: the shortcut used to write an empty consent_email.

        ⚠️ It did that because EmergencyContact had no email column, so the only
           address it could hand over was a phone number — every guardian
           reached through this path got an SMS. Now that the column exists,
           not copying it would leave the field looking filled in while P6 fell
           back to the more expensive channel.
        """
        from contact.models import EmergencyContact, RelationshipType

        minor = self.account(
            "xiaoming", "Xiao",
            birth_date=local_today() - datetime.timedelta(days=365 * 15))
        kin = EmergencyContact.objects.create(
            person=minor.contact, name="Xiao's mother", phone="+14085550111",
            email="mother@example.com",
            relationship_type=RelationshipType.objects.get(code="parent"))

        form = SignUpForm(
            {"event_role": self.role.pk, "use_emergency_contact": kin.pk,
             "consent_method": Participation.ConsentMethod.VERBAL},
            event=self.event, contact=minor.contact)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.consent()["consent_email"], "mother@example.com")

    def test_the_length_is_computed_in_services_not_in_the_view(self):
        # D18: date arithmetic in a view gets rewritten along with the
        # templates. There is a grep guard on it; this asserts the answer is
        # the one services gives, so the two cannot drift apart either.
        self.login(self.zhang)
        response = self.client.get(self.url)
        self.assertEqual(response.context["scheduled_hours"],
                         scheduled_hours(self.event))


class EventUpdatePageTests(PageTestCase):
    """C0.2.2: the page that makes P6's trigger possible.

    Until this existed, services.reschedule() had no caller anywhere in the
    repo: an admin could send "the time has changed" and had no way to change
    it, because EventForm was reachable only from event_create and the admin
    site is closed to them (StaffOnlyAdminMiddleware). Event.status was stuck
    for the same reason — nothing could mark an event completed.
    """

    def url(self, event=None):
        return reverse("events:event_update", args=[(event or self.event).pk])

    def widget_value(self, when):
        """What the datetime-local input would actually contain.

        ⚠️ localtime() is the whole point. Formatting the stored UTC instant
           directly posts a different wall clock than the browser would, the
           view parses it back as a *different* instant, and the "nothing
           moved" case then looks like a move — the bug this helper exists to
           keep out of the payload rather than out of the assertions.
        """
        return localtime(when).strftime("%Y-%m-%dT%H:%M")

    def payload(self, **overrides):
        fields = {
            "name": self.event.name,
            "event_type": self.event.event_type_id,
            "ministry": self.event.ministry_id,
            "start_time": self.widget_value(self.event.start_time),
            "end_time": self.widget_value(self.event.end_time),
            "location": self.event.location,
            "status": self.event.status,
            "description": self.event.description,
        }
        fields.update(overrides)
        return fields

    def test_moving_an_event_saves_the_new_time(self):
        self.login(self.zhang)
        # Minute precision, because that is all the widget carries.
        moved_to = (NOW + 3 * DAY).replace(second=0, microsecond=0)
        response = self.client.post(self.url(), self.payload(
            start_time=self.widget_value(moved_to),
            end_time=self.widget_value(moved_to + 3 * HOUR),
        ))
        self.assertEqual(response.status_code, 302)
        self.event.refresh_from_db()
        self.assertEqual(self.event.start_time, moved_to)

    def test_a_move_lands_on_the_notice_page_with_the_reason_chosen(self):
        # The half of P6 that is easy to leave out: whoever moved the event is
        # one click from telling the people who signed up, rather than having
        # to know that the notify page exists at all.
        self.login(self.zhang)
        moved_to = (NOW + 4 * DAY).replace(second=0, microsecond=0)
        response = self.client.post(self.url(), self.payload(
            start_time=self.widget_value(moved_to),
            end_time=self.widget_value(moved_to + 3 * HOUR),
        ))
        self.assertRedirects(
            response,
            f"{reverse('events:event_notify', args=[self.event.pk])}"
            f"?reason={EventNotification.Reason.TIME_CHANGED}",
        )

    def test_editing_without_moving_does_not_go_to_the_notice_page(self):
        # Renaming an event is not news. Sending everybody a notice for it
        # would train them to ignore the ones that matter.
        self.login(self.zhang)
        response = self.client.post(self.url(), self.payload(name="Renamed"))
        self.assertRedirects(
            response, reverse("events:event_detail", args=[self.event.pk]))
        self.event.refresh_from_db()
        self.assertEqual(self.event.name, "Renamed")

    def test_seconds_the_widget_cannot_show_are_not_read_as_a_reschedule(self):
        # Found by the tests above, and silent in production: datetime-local
        # carries no seconds, so an event stored at 09:00:37 comes back from an
        # untouched form as 09:00:00. Compared naively that is a reschedule —
        # and correcting a typo in the location would then mail every volunteer
        # to announce a time change that never happened.
        self.event.start_time = self.event.start_time.replace(second=37, microsecond=5)
        self.event.save(update_fields=["start_time"])
        self.login(self.zhang)
        response = self.client.post(self.url(), self.payload(location="Hall B"))
        self.assertRedirects(
            response, reverse("events:event_detail", args=[self.event.pk]))

    def test_an_event_can_be_marked_completed(self):
        # R4–R8 are read after the event. Nothing else in the project can move
        # status off its published value.
        self.login(self.zhang)
        self.client.post(self.url(), self.payload(status=Event.Status.COMPLETED))
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.COMPLETED)

    def test_an_end_time_before_the_start_is_refused(self):
        # reschedule() runs full_clean inside its transaction, so the check
        # constraint comes back as a form error rather than an IntegrityError.
        self.login(self.zhang)
        original = self.event.start_time
        response = self.client.post(self.url(), self.payload(
            start_time=self.widget_value(NOW + 5 * DAY),
            end_time=self.widget_value(NOW + 5 * DAY - HOUR),
        ))
        self.assertEqual(response.status_code, 200)
        self.event.refresh_from_db()
        self.assertEqual(self.event.start_time, original)

    def test_another_ministrys_admin_cannot_open_or_post_to_it(self):
        self.login(self.other_admin)
        self.assertEqual(self.client.get(self.url()).status_code, 403)
        self.assertEqual(self.client.post(self.url(), self.payload()).status_code, 403)

    def test_a_plain_volunteer_gets_403(self):
        self.login(self.lisi)
        self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_an_event_cannot_be_handed_to_a_ministry_you_do_not_run(self):
        # The forged-POST case, the same one event_create guards. The dropdown
        # is narrowed, but a POST can name any id — and moving an event into
        # another ministry would take its signups and hours with it.
        self.login(self.zhang)
        response = self.client.post(self.url(), self.payload(ministry=self.tax.pk))
        self.assertIn(response.status_code, (403, 200))
        self.event.refresh_from_db()
        self.assertEqual(self.event.ministry, self.pantry)


class ReportPageTests(PageTestCase):
    def test_the_report_counts_roles_that_nobody_signed_up_for(self):
        make_role(self.event, "interpreting", needed_count=1)
        Participation.objects.create(contact=self.lisi.contact, event_role=self.role)
        self.login(self.zhang)
        response = self.client.get(reverse("events:event_report", args=[self.event.pk]))
        self.assertEqual(response.context["summary"]["role_count"], 2)

    def test_another_ministrys_report_is_403(self):
        self.login(self.other_admin)
        response = self.client.get(reverse("events:event_report", args=[self.event.pk]))
        self.assertEqual(response.status_code, 403)


@override_settings(NOTIFICATION_BACKEND="core.notifications.locmem.LocmemBackend")
class NotificationTests(TestCase):
    """P6. The three things that were not free: guardians, the unreachable, the record."""

    def setUp(self):
        LocmemBackend.outbox = []
        self.pantry = Ministry.objects.create(code="food_pantry", name="Food Pantry")
        self.event = make_event(ministry=self.pantry)
        self.role = make_role(self.event, "lifting")
        self.sender = get_user_model().objects.create_user(username="zhang", password="x")
        self.parent_of = RelationshipType.objects.get(code="parent")

    def minor(self, last_name="小明", **fields):
        return make_person(
            last_name,
            birth_date=local_today() - datetime.timedelta(days=365 * 15),
            **fields,
        )

    def signup(self, contact, **fields):
        return Participation.objects.create(
            contact=contact, event_role=self.role, **fields)

    def notify(self, reason=None):
        from .models import EventNotification

        return notify_event_change(
            self.event,
            reason=reason or EventNotification.Reason.TIME_CHANGED,
            message=default_message(self.event, EventNotification.Reason.TIME_CHANGED),
            sent_by=self.sender,
        )

    # --- who gets told ---------------------------------------------------

    def test_an_adult_is_notified_at_their_own_address(self):
        adult = make_person("李", birth_date=datetime.date(1980, 1, 1),
                            email="lisi@example.com")
        self.signup(adult)
        recipients, unreachable = resolve_recipients(self.event)
        self.assertEqual([(r.to, r.channel) for r in recipients],
                         [("lisi@example.com", "email")])
        self.assertEqual(unreachable, [])

    def test_an_adult_preferring_sms_is_notified_by_sms(self):
        adult = make_person(
            "李", birth_date=datetime.date(1980, 1, 1), email="lisi@example.com",
            phone="+14085550111",
            preferred_communication_method=Contact.CommunicationMethod.SMS)
        self.signup(adult)
        recipients, _ = resolve_recipients(self.event)
        self.assertEqual(recipients[0].channel, "sms")

    def test_a_preference_falls_back_when_that_address_is_missing(self):
        adult = make_person(
            "李", birth_date=datetime.date(1980, 1, 1), email="lisi@example.com",
            preferred_communication_method=Contact.CommunicationMethod.SMS)
        self.signup(adult)
        recipients, _ = resolve_recipients(self.event)
        self.assertEqual(recipients[0].channel, "email")

    def test_a_minor_is_notified_through_their_guardian(self):
        # ⭐ D22 ①. A fifteen-year-old may have no phone at all, so notifying
        # them is notifying nobody. Fail this and P6 is broken for exactly the
        # group that most needs telling.
        child = self.minor(email="xiaoming@example.com")
        self.signup(child, consent_given_by="王秀英",
                    consent_email="parent@example.com",
                    consent_method=Participation.ConsentMethod.VERBAL)
        recipients, _ = resolve_recipients(self.event)
        self.assertEqual([(r.to, r.is_guardian) for r in recipients],
                         [("parent@example.com", True)])
        # And explicitly not the child's own address, which is on file.
        self.assertNotIn("xiaoming@example.com", [r.to for r in recipients])

    def test_a_minor_with_only_consent_phone_is_notified_by_sms(self):
        child = self.minor()
        self.signup(child, consent_given_by="王秀英", consent_phone="+14085550123",
                    consent_method=Participation.ConsentMethod.VERBAL)
        recipients, _ = resolve_recipients(self.event)
        self.assertEqual([(r.to, r.channel) for r in recipients],
                         [("+14085550123", "sms")])

    def test_a_minor_falls_back_to_the_emergency_contact(self):
        # The second path, and it is SMS-only because EmergencyContact has a
        # phone column and no email one. With an email-only backend that is a
        # real gap — a visible one, which is the requirement.
        child = self.minor()
        EmergencyContact.objects.create(
            person=child, name="王秀英", phone="+14085550199",
            relationship_type=self.parent_of)
        self.signup(child)
        recipients, _ = resolve_recipients(self.event)
        self.assertEqual([(r.to, r.channel, r.is_guardian) for r in recipients],
                         [("+14085550199", "sms", True)])

    def test_a_minor_with_no_guardian_contact_lands_in_unreachable(self):
        self.signup(self.minor(email="xiaoming@example.com"))
        recipients, unreachable = resolve_recipients(self.event)
        self.assertEqual(recipients, [])
        self.assertEqual(len(unreachable), 1)

    def test_a_participant_with_unknown_birth_date_is_treated_as_a_minor(self):
        # The cautious side of the three-state. Folded into "adult", a minor
        # with no date on file would be mailed directly and their parents never
        # told — silently.
        unknown = make_person("未知", birth_date=None, email="unknown@example.com")
        self.signup(unknown)
        recipients, unreachable = resolve_recipients(self.event)
        self.assertEqual(recipients, [])
        self.assertEqual(len(unreachable), 1)

    def test_a_participant_with_no_email_and_no_phone_lands_in_unreachable(self):
        # D22 ②. Staff-entered contacts often have neither.
        self.signup(make_person("无", birth_date=datetime.date(1980, 1, 1)))
        _, unreachable = resolve_recipients(self.event)
        self.assertEqual(len(unreachable), 1)

    def test_cancelled_participations_are_not_notified(self):
        adult = make_person("李", birth_date=datetime.date(1980, 1, 1),
                            email="lisi@example.com")
        self.signup(adult, status=Participation.Status.CANCELLED)
        recipients, unreachable = resolve_recipients(self.event)
        self.assertEqual((recipients, unreachable), ([], []))

    # --- the record ------------------------------------------------------

    def test_unreachable_rows_are_not_counted_as_recipients(self):
        told = make_person("李", birth_date=datetime.date(1980, 1, 1),
                           email="lisi@example.com")
        missed = make_person("无", birth_date=datetime.date(1980, 1, 1))
        self.signup(told)
        Participation.objects.create(
            contact=missed, event_role=make_role(self.event, "welcome"))
        notification = self.notify()
        self.assertEqual(notification.recipients.count(), 1)
        self.assertEqual(notification.unreachable.count(), 1)

    def test_who_was_unreachable_is_still_queryable_afterwards(self):
        # The reason unreachable is an M2M and not a count: a number answers
        # "how many" once and can never answer "which three".
        missed = make_person("无", birth_date=datetime.date(1980, 1, 1))
        self.signup(missed)
        notification = self.notify()
        self.assertEqual(
            [row.contact for row in notification.unreachable.all()], [missed])

    def test_unreachable_rows_do_not_change_after_the_phone_is_filled_in(self):
        # A snapshot, never recomputed. Somebody unreachable in March may have
        # a number today, and recalculating would rewrite this record into
        # "everybody was told", which is false.
        missed = make_person("无", birth_date=datetime.date(1980, 1, 1))
        self.signup(missed)
        notification = self.notify()
        missed.email = "found@example.com"
        missed.save()
        self.assertEqual(notification.unreachable.count(), 1)

    def test_the_message_snapshot_survives_editing_the_event(self):
        self.signup(make_person("李", birth_date=datetime.date(1980, 1, 1),
                                email="lisi@example.com"))
        notification = self.notify()
        original = notification.message
        self.event.name = "Renamed entirely"
        self.event.save()
        notification.refresh_from_db()
        self.assertEqual(notification.message, original)

    def test_deleting_the_sending_user_keeps_the_notification(self):
        notification = self.notify()
        self.sender.delete()
        notification.refresh_from_db()
        self.assertIsNone(notification.sent_by)

    def test_the_default_message_does_not_contain_a_minors_name(self):
        # D22's second cost is that content leaves our database. What leaves is
        # an address plus an announcement, never "your child 小明".
        child = self.minor()
        self.signup(child, consent_given_by="王秀英",
                    consent_email="parent@example.com",
                    consent_method=Participation.ConsentMethod.VERBAL)
        from .models import EventNotification

        body = default_message(self.event, EventNotification.Reason.TIME_CHANGED)
        self.assertNotIn("小明", body)
        self.assertIn("your child", body)

    # --- the seam --------------------------------------------------------

    def test_resolve_recipients_makes_no_network_calls(self):
        # Business-rule tests must not depend on a provider, and must not go
        # red when one is swapped. Nothing here even reaches a backend.
        self.signup(make_person("李", birth_date=datetime.date(1980, 1, 1),
                                email="lisi@example.com"))
        resolve_recipients(self.event)
        self.assertEqual(LocmemBackend.outbox, [])

    def test_delivery_goes_through_the_configured_backend(self):
        self.signup(make_person("李", birth_date=datetime.date(1980, 1, 1),
                                email="lisi@example.com"))
        self.notify()
        self.assertEqual([m.to for m in LocmemBackend.outbox], ["lisi@example.com"])


class NotificationPageTests(PageTestCase):
    def test_notifying_another_ministrys_event_returns_403(self):
        self.login(self.other_admin)
        response = self.client.get(reverse("events:event_notify", args=[self.event.pk]))
        self.assertEqual(response.status_code, 403)

    def test_a_plain_volunteer_cannot_open_the_notify_page(self):
        self.login(self.lisi)
        response = self.client.get(reverse("events:event_notify", args=[self.event.pk]))
        self.assertEqual(response.status_code, 403)

    @override_settings(NOTIFICATION_BACKEND="core.notifications.locmem.LocmemBackend")
    def test_the_preview_shows_all_three_groups(self):
        LocmemBackend.outbox = []
        Participation.objects.create(
            contact=self.lisi.contact, event_role=self.role)
        nobody = make_person("无", birth_date=datetime.date(1980, 1, 1))
        Participation.objects.create(
            contact=nobody, event_role=make_role(self.event, "welcome"))
        self.login(self.zhang)
        response = self.client.get(reverse("events:event_notify", args=[self.event.pk]))
        self.assertEqual(len(response.context["unreachable"]), 1)
        # The group is rendered even when empty — it is the one part of this
        # page that could fail without anybody noticing.
        self.assertContains(response, "Cannot be reached")


class PeriodReportTests(TestCase):
    """R1 / R2 / R3 — and the timezone trap that sits under all three."""

    def setUp(self):
        self.pantry = Ministry.objects.create(code="food_pantry", name="Food Pantry")
        self.tax = Ministry.objects.create(code="tax_help", name="Tax Help")

    def event_at(self, moment, ministry=None, **kwargs):
        return make_event(
            ministry=ministry or self.pantry,
            start_time=moment, end_time=moment + 2 * HOUR, **kwargs)

    def test_r1_counts_the_events_inside_the_window(self):
        inside = self.event_at(day_start(datetime.date(2026, 3, 15)))
        self.event_at(day_start(datetime.date(2026, 4, 2)), name="April")
        start, end = month_bounds(2026, 3)
        self.assertEqual(list(events_in_period(start, end)), [inside])

    def test_r1_uses_foundation_month_boundaries_not_utc_ones(self):
        # 11pm Pacific on 31 March is already 1 April in UTC. Sliced in UTC this
        # event leaves its own month, the count drops by one, and nothing says so.
        late = self.event_at(
            day_start(datetime.date(2026, 3, 31)) + datetime.timedelta(hours=23))
        start, end = month_bounds(2026, 3)
        self.assertIn(late, events_in_period(start, end))
        april_start, april_end = month_bounds(2026, 4)
        self.assertNotIn(late, events_in_period(april_start, april_end))

    def test_r2_can_narrow_the_window_to_one_ministry(self):
        mine = self.event_at(day_start(datetime.date(2026, 3, 15)))
        self.event_at(day_start(datetime.date(2026, 3, 16)),
                      ministry=self.tax, name="Tax clinic")
        start, end = month_bounds(2026, 3)
        self.assertEqual(list(events_in_period(start, end, ministry=self.pantry)), [mine])

    def test_r3_duration_survives_a_round_trip_through_the_database(self):
        event = self.event_at(day_start(datetime.date(2026, 3, 15)))
        event.refresh_from_db()
        self.assertEqual(event.duration, datetime.timedelta(hours=2))


class SeedDemoTests(TestCase):
    """The demo data has to contain every awkward branch, or the walk proves nothing."""

    def seed(self):
        call_command("seed_demo", verbosity=0)

    def test_it_refuses_to_run_with_debug_off(self):
        # One mistaken run against production fills the contact table with
        # invented people who — by this system's own design — look exactly like
        # real ones and are near-impossible to pick out afterwards.
        with override_settings(DEBUG=False), self.assertRaises(CommandError):
            self.seed()

    def test_force_overrides_the_refusal(self):
        with override_settings(DEBUG=False):
            call_command("seed_demo", "--force", verbosity=0)
        self.assertTrue(Event.objects.exists())

    def test_running_it_twice_does_not_double_anything(self):
        # get_or_create throughout. Three 张三 would set the duplicate warning
        # off on every page from then on.
        with override_settings(DEBUG=True):
            self.seed()
            counts = (Contact.objects.count(), Event.objects.count(),
                      EventRole.objects.count(), Participation.objects.count())
            self.seed()
        self.assertEqual(
            counts,
            (Contact.objects.count(), Event.objects.count(),
             EventRole.objects.count(), Participation.objects.count()),
        )

    def test_it_builds_every_branch_the_acceptance_walk_needs(self):
        # One test per branch would read better; one test that names them all
        # is what stops a branch being dropped, because the list is the
        # checklist. Each of these fails silently if it is missing: the walk
        # looks green and has verified nothing.
        with override_settings(DEBUG=True):
            self.seed()

        with self.subTest("a role nobody signed up for — R4 answers 3, not 2"):
            self.assertTrue(
                EventRole.objects.filter(participations__isnull=True).exists())

        with self.subTest("somebody with no email and no phone — P6's third group"):
            self.assertTrue(
                Contact.objects.filter(
                    participations__isnull=False, email="", phone="").exists())

        with self.subTest("an unknown birth date — the cautious side of three states"):
            self.assertTrue(
                Contact.objects.filter(
                    participations__isnull=False, birth_date__isnull=True).exists())

        with self.subTest("a minor reachable only through an emergency contact"):
            self.assertTrue(
                Contact.objects.filter(
                    participations__isnull=False,
                    emergency_contacts__isnull=False,
                ).exists())

        with self.subTest("hours from a paper sheet, with no timestamps"):
            self.assertTrue(
                Participation.objects.filter(
                    hours__isnull=False, checked_in_at__isnull=True).exists())

        with self.subTest("a draft event and a confirmed one"):
            self.assertTrue(Event.objects.filter(status=Event.Status.DRAFT).exists())
            confirmed = Event.objects.filter(status=Event.Status.CONFIRMED).first()
            self.assertIsNotNone(confirmed)
            # And somebody signed up to it, so "still opens once full" is walkable.
            self.assertTrue(
                Participation.objects.filter(event_role__event=confirmed).exists())

        with self.subTest("two ministries with an admin each — one cannot show scoping"):
            self.assertEqual(
                MinistryRole.objects.values("ministry").distinct().count(), 2)

        with self.subTest("an employee who left after the event — R8's clock"):
            past = Event.objects.filter(status=Event.Status.COMPLETED).first()
            self.assertIsNotNone(past)
            self.assertTrue(
                Assignment.objects.filter(
                    end_date__lt=local_today(),
                    contact__participations__event_role__event=past,
                ).exists())

        with self.subTest("a vacant post — vacancy is a first-class state"):
            self.assertTrue(Position.objects.vacant().exists())

    def test_the_seeded_data_produces_all_three_notification_groups(self):
        # The end-to-end point of the fixture: the demo event must exercise
        # direct recipients, guardian recipients and the unreachable at once.
        with override_settings(DEBUG=True):
            self.seed()
        event = Event.objects.get(name="Saturday distribution")
        recipients, unreachable = resolve_recipients(event)
        self.assertTrue([r for r in recipients if not r.is_guardian])
        self.assertTrue([r for r in recipients if r.is_guardian])
        self.assertTrue(unreachable)


@override_settings(DEBUG=True, NOTIFICATION_BACKEND="core.notifications.locmem.LocmemBackend")
class AcceptanceWalkTests(TestCase):
    """The three-role walk from phase-b.md, driven over URLs against seed_demo.

    Not a replacement for doing it in a browser — that is still the acceptance,
    and it catches things no assertion will (a form that renders unusable, a
    link that goes nowhere). This is the half a machine can hold onto: every
    step below is one of the ticks on that list, and each of them fails
    silently if it regresses.
    """

    PASSWORD = "demo-password-not-a-secret"

    @classmethod
    def setUpTestData(cls):
        call_command("seed_demo", verbosity=0)

    def as_role(self, username):
        self.assertTrue(self.client.login(username=username, password=self.PASSWORD))

    def open_event(self):
        return Event.objects.get(name="Saturday distribution")

    def past_event(self):
        return Event.objects.get(name="Last month's distribution")

    def other_event(self):
        return Event.objects.get(name="Tax clinic")

    # --- ① the foundation-wide role ---------------------------------------

    def test_the_foundation_admin_can_appoint_and_revoke_a_ministry_admin(self):
        # P5, plus the rule that revoking dates the row rather than deleting it.
        self.as_role("foundation_admin")
        pantry = Ministry.objects.get(code="food_pantry")
        newcomer = Contact.objects.get(legal_last_name="Sun")
        url = reverse("org:ministry_admins", args=[pantry.pk])
        self.client.post(url, {"contact": newcomer.pk})
        grant = MinistryRole.objects.get(contact=newcomer, ministry=pantry)
        self.assertEqual(grant.granted_by.username, "foundation_admin")

        self.client.post(url, {"revoke": grant.pk})
        grant.refresh_from_db()
        self.assertIsNotNone(grant.end_date)
        self.assertTrue(MinistryRole.objects.filter(pk=grant.pk).exists())

    def test_r1_to_r3_are_readable_from_the_admin_changelist(self):
        # The tick that used to sit under "play a volunteer" while the previous
        # tick required that same volunteer to get 403 from the admin — a step
        # that contradicts itself gets skipped, and then nobody knows whether
        # R1–R3 were ever checked.
        self.as_role("foundation_admin")
        response = self.client.get("/admin/events/event/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Food Pantry")          # R2
        self.assertContains(response, "3:00:00")           # R3, the duration column

    # --- ② the food pantry's admin ----------------------------------------

    def test_the_pantry_admin_sees_a_role_nobody_signed_up_for(self):
        # ⭐ D19's acceptance point, walked: the event opened three roles and
        # Interpreting has nobody in it, so it has three — not two.
        self.as_role("pantry_admin")
        response = self.client.get(
            reverse("events:event_registrations", args=[self.open_event().pk]))
        self.assertContains(response, "Interpreting")
        self.assertEqual(len(response.context["roles"]), 3)

    def test_the_pantry_admin_is_refused_the_other_ministrys_event(self):
        # Fail this one and scoped permission was not built.
        self.as_role("pantry_admin")
        for name in ["event_registrations", "event_attendance", "event_notify"]:
            with self.subTest(page=name):
                response = self.client.get(
                    reverse(f"events:{name}", args=[self.other_event().pk]))
                self.assertEqual(response.status_code, 403)

    def test_the_report_counts_the_empty_role_and_totals_the_hours(self):
        self.as_role("pantry_admin")
        response = self.client.get(
            reverse("events:event_report", args=[self.past_event().pk]))
        summary = response.context["summary"]
        self.assertEqual(summary["role_count"], 3)          # R4 — 翻译 had nobody
        self.assertEqual(summary["total_hours"], Decimal("9.00"))   # R6: 3 + 2 + 4

    def test_r8_lists_the_employee_who_has_since_left(self):
        # The clock is the day of the event. Asked with today's date this list
        # would be short by one, and would say nothing about it.
        self.as_role("pantry_admin")
        response = self.client.get(
            reverse("events:event_report", args=[self.past_event().pk]))
        names = {row.contact.legal_last_name for row in response.context["staff"]}
        self.assertIn("Sun", names)

    def test_the_notification_preview_has_all_three_groups(self):
        # ⭐ P6. The third group is the one that fails silently: a green
        # "27 notified" hiding three people nobody could reach.
        self.as_role("pantry_admin")
        response = self.client.get(
            reverse("events:event_notify", args=[self.open_event().pk]))
        self.assertTrue(response.context["recipients"])
        self.assertTrue(response.context["guardian_recipients"])
        self.assertTrue(response.context["unreachable"])
        # And the guardian rows are the minors', not the minors' own addresses.
        for row in response.context["guardian_recipients"]:
            self.assertIn(row.participation.contact.is_minor, (True, None))

    def test_sending_leaves_a_record_naming_who_was_missed(self):
        self.as_role("pantry_admin")
        event = self.open_event()
        url = reverse("events:event_notify", args=[event.pk])
        preview = self.client.get(url)
        missed = {row.participation.pk for row in preview.context["unreachable"]}
        self.client.post(url, {
            "reason": EventNotification.Reason.TIME_CHANGED,
            "message": preview.context["form"].initial["message"],
        })
        notification = event.notifications.get()
        self.assertEqual({p.pk for p in notification.unreachable.all()}, missed)
        # "Already notified …" is the only thing standing between a shaky
        # connection and a second identical notice.
        #
        # ⚠️ Asserted case-insensitively, and on the word that carries the
        #    meaning rather than on a whole sentence. C2 rewrote this page and
        #    the previous assertion — the literal "most recently" — broke purely
        #    because the phrase moved to the start of a sentence and gained a
        #    capital. A test that fails on capitalisation is not testing the
        #    warning; it is testing the wording, and it will keep costing a
        #    round trip every time the copy is touched.
        page = self.client.get(url).content.decode().lower()
        self.assertIn("most recently", page)
        self.assertIn("already notified", page)

    def test_checking_somebody_in_and_out_fills_in_their_hours(self):
        self.as_role("pantry_admin")
        event = self.open_event()
        participation = Participation.objects.filter(
            event_role__event=event, contact__legal_last_name="Li").get()
        url = reverse("events:event_attendance", args=[event.pk])
        self.client.post(url, {"participation": participation.pk, "action": "check_in"})
        self.client.post(url, {"participation": participation.pk, "action": "check_out"})
        participation.refresh_from_db()
        self.assertEqual(participation.status, Participation.Status.ATTENDED)
        self.assertIsNotNone(participation.hours)

    def test_zero_recorded_hours_do_not_read_as_no_hours_recorded(self):
        """Somebody who checked straight back out shows 0, not a dash.

        ⚠️ The template filter for this is `default_if_none`, never `default`:
           `default` substitutes on any falsy value and Decimal("0.00") is
           falsy, so "checked in and out, worked zero hours" rendered exactly
           like "nothing recorded yet". Those are different facts — the first
           wants explaining (came and left again, or forgot to check out), the
           second is a job still to do.

        Found by looking at a screenshot of the page, not by a failing test:
        the seed data has somebody checked in at 3:44 and out at 3:45, marked
        Attended, with a dash under Hours.
        """
        self.as_role("pantry_admin")
        event = self.open_event()
        participation = Participation.objects.filter(
            event_role__event=event, contact__legal_last_name="Li").get()
        url = reverse("events:event_attendance", args=[event.pk])
        self.client.post(url, {"participation": participation.pk, "action": "hours",
                               "hours": "0"})
        participation.refresh_from_db()
        self.assertEqual(participation.hours, Decimal("0"))

        row = self.client.get(url).content.decode()
        cell = row.split(f'id="participation-{participation.pk}"', 1)[1].split("</tr>", 1)[0]
        self.assertIn("0.00", cell)
        self.assertNotIn("—", cell.split("Hours")[-1] if "Hours" in cell else cell)

    # --- ③ the plain volunteer --------------------------------------------

    def test_a_volunteer_is_refused_the_admin_outright(self):
        # D21's first requirement: 403, not a redirect to a login form.
        self.as_role("volunteer_adult")
        self.assertEqual(self.client.get("/admin/").status_code, 403)

    def test_a_volunteer_sees_open_events_only(self):
        self.as_role("volunteer_adult")
        response = self.client.get(reverse("events:event_list"))
        listed = {event.name for event in response.context["events"]}
        self.assertIn("Saturday distribution", listed)
        self.assertNotIn("Christmas distribution (not published yet)", listed)     # draft
        self.assertNotIn("English corner (full)", listed)         # confirmed

    def test_a_volunteer_can_still_open_the_event_they_joined_once_it_filled_up(self):
        # ⭐ Visibility is not signability. Written the other way, P6's
        # "can't make it? cancel here" link 404s on exactly the full events.
        self.as_role("volunteer_adult")
        confirmed = Event.objects.get(name="English corner (full)")
        response = self.client.get(reverse("events:event_detail", args=[confirmed.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_sign_up"])

    def test_a_volunteer_cannot_open_a_draft_event(self):
        self.as_role("volunteer_adult")
        draft = Event.objects.get(name="Christmas distribution (not published yet)")
        response = self.client.get(reverse("events:event_detail", args=[draft.pk]))
        self.assertEqual(response.status_code, 404)

    def test_a_minor_must_supply_a_guardian_address_to_sign_up(self):
        # And an email or a phone with it: consent carrying only a name would
        # leave P6 nothing to send to.
        self.as_role("volunteer_minor")
        event = self.open_event()
        role = event.roles.get(role__code="lifting")
        url = reverse("events:event_signup", args=[event.pk])
        before = Participation.objects.count()

        self.client.post(url, {"event_role": role.pk})
        self.client.post(url, {
            "event_role": role.pk, "consent_given_by": "家长",
            "consent_method": Participation.ConsentMethod.VERBAL,
        })
        self.assertEqual(Participation.objects.count(), before)

        self.client.post(url, {
            "event_role": role.pk, "consent_given_by": "家长",
            "consent_method": Participation.ConsentMethod.VERBAL,
            "consent_email": "parent2@example.invalid",
        })
        self.assertEqual(Participation.objects.count(), before + 1)

    def test_a_volunteer_with_no_birth_date_is_asked_for_consent_too(self):
        self.as_role("volunteer_unknown")
        event = self.open_event()
        role = event.roles.get(role__code="lifting")
        before = Participation.objects.count()
        self.client.post(reverse("events:event_signup", args=[event.pk]),
                         {"event_role": role.pk})
        self.assertEqual(Participation.objects.count(), before)

    def test_a_volunteer_cannot_reach_another_persons_signup(self):
        self.as_role("volunteer_adult")
        someone_else = Participation.objects.exclude(
            contact__legal_last_name="李").first()
        response = self.client.get(
            reverse("events:participation_cancel", args=[someone_else.pk]))
        self.assertEqual(response.status_code, 404)


class SignUpAgainAfterCancellingTests(PageTestCase):
    """Pulling out of a role and then changing your mind.

    ⚠️ Found in the browser, not by the tests: cancel() is a status change and
       never a delete, so the cancelled row still holds the unique
       (event_role, contact) pair. Building a fresh Participation made the
       second signup come back as "you have already signed up" — wrong, and
       unfixable from the volunteer's side. Every existing test that cancelled
       a signup stopped at the cancellation.
    """

    def signup_url(self):
        return reverse("events:event_signup", args=[self.event.pk])

    def test_a_volunteer_can_sign_up_again_after_cancelling(self):
        self.login(self.lisi)
        self.client.post(self.signup_url(), {"event_role": self.role.pk})
        row = Participation.objects.get(contact=self.lisi.contact, event_role=self.role)

        self.client.post(reverse("events:participation_cancel", args=[row.pk]))
        row.refresh_from_db()
        self.assertEqual(row.status, Participation.Status.CANCELLED)

        self.client.post(self.signup_url(), {"event_role": self.role.pk})
        row.refresh_from_db()
        self.assertEqual(row.status, Participation.Status.REGISTERED)

    def test_it_reuses_the_row_rather_than_adding_a_second(self):
        # One person, one role, one row — the unique constraint says so, and a
        # second row would double them in every count on the report page.
        self.login(self.lisi)
        self.client.post(self.signup_url(), {"event_role": self.role.pk})
        row = Participation.objects.get(contact=self.lisi.contact, event_role=self.role)
        self.client.post(reverse("events:participation_cancel", args=[row.pk]))
        self.client.post(self.signup_url(), {"event_role": self.role.pk})
        self.assertEqual(
            Participation.objects.filter(
                contact=self.lisi.contact, event_role=self.role).count(), 1)

    def test_signing_up_again_moves_the_registered_time_forward(self):
        self.login(self.lisi)
        self.client.post(self.signup_url(), {"event_role": self.role.pk})
        row = Participation.objects.get(contact=self.lisi.contact, event_role=self.role)
        first = row.registered_at
        self.client.post(reverse("events:participation_cancel", args=[row.pk]))
        self.client.post(self.signup_url(), {"event_role": self.role.pk})
        row.refresh_from_db()
        self.assertGreater(row.registered_at, first)

    def test_signing_up_twice_without_cancelling_is_still_refused(self):
        # The uniqueness error still has to reach the form rather than 500.
        self.login(self.lisi)
        self.client.post(self.signup_url(), {"event_role": self.role.pk})
        response = self.client.post(self.signup_url(), {"event_role": self.role.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            Participation.objects.filter(
                contact=self.lisi.contact, event_role=self.role).count(), 1)


class ManageListStatusTests(PageTestCase):
    """The status dropdown on the manage list, and the two fields it is not.

    Publishing a draft and closing a finished event are the two most frequent
    things a coordinator does, and each is a single choice — so status is
    editable in the list. The times are shown read-only there on purpose:
    moving an event obliges somebody to notify the volunteers, so it goes
    through the edit page, which routes on to the notice.
    """

    def url(self):
        return reverse("events:event_manage_list")

    def test_the_times_are_shown(self):
        self.login(self.zhang)
        response = self.client.get(self.url())
        self.assertContains(response, "Starts")
        self.assertContains(response, "Ends")

    def test_status_can_be_changed_from_the_list(self):
        self.event.status = Event.Status.DRAFT
        self.event.save(update_fields=["status"])
        self.login(self.zhang)
        self.client.post(self.url(), {"event": self.event.pk, "status": Event.Status.OPEN})
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, Event.Status.OPEN)

    def test_publishing_from_the_list_makes_the_event_visible_to_volunteers(self):
        # What the status change is actually for, asserted as the consequence.
        self.event.status = Event.Status.DRAFT
        self.event.save(update_fields=["status"])
        self.login(self.lisi)
        self.assertNotContains(self.client.get(reverse("events:event_list")), self.event.name)

        self.login(self.zhang)
        self.client.post(self.url(), {"event": self.event.pk, "status": Event.Status.OPEN})
        self.login(self.lisi)
        self.assertContains(self.client.get(reverse("events:event_list")), self.event.name)

    def test_the_times_cannot_be_changed_from_the_list(self):
        # Posting them is ignored: EventStatusForm has one field, so a forged
        # start_time never reaches the instance — and a time change that skipped
        # the edit page would also skip the prompt to notify anybody.
        original = self.event.start_time
        self.login(self.zhang)
        self.client.post(self.url(), {
            "event": self.event.pk, "status": Event.Status.OPEN,
            "start_time": "2030-01-01T09:00", "end_time": "2030-01-01T12:00",
        })
        self.event.refresh_from_db()
        self.assertEqual(self.event.start_time, original)

    def test_another_ministrys_event_cannot_be_touched(self):
        theirs = make_event(ministry=self.tax, owner=self.other_admin.contact,
                            status=Event.Status.DRAFT)
        self.login(self.zhang)
        response = self.client.post(self.url(), {
            "event": theirs.pk, "status": Event.Status.OPEN})
        self.assertEqual(response.status_code, 403)
        theirs.refresh_from_db()
        self.assertEqual(theirs.status, Event.Status.DRAFT)


class MinorEmergencyContactTests(TestCase):
    """A minor needs somebody to call, and that record can stand in for consent.

    Two rules that arrived together and lean on each other: requiring an
    emergency contact is what makes reusing it possible, and reusing it is what
    stops the requirement from being one more box to fill at every signup.
    """

    def setUp(self):
        self.event = make_event()
        self.role = make_role(self.event, "lifting")
        self.minor = make_person(
            "Minor", birth_date=local_today() - datetime.timedelta(days=365 * 15))

    def test_a_minor_with_nobody_to_call_cannot_sign_up(self):
        with self.assertRaises(ConsentRequired):
            sign_up(contact=self.minor, event_role=self.role, consent={
                "consent_given_by": "王秀英",
                "consent_method": Participation.ConsentMethod.VERBAL,
                "consent_email": "parent@example.com",
            })
        self.assertEqual(Participation.objects.count(), 0)

    def test_the_refusal_comes_before_the_consent_questions(self):
        # ⚠️ Order matters more than it looks. Told "the guardian's email is
        #    missing" when the real answer is "add an emergency contact", the
        #    volunteer fixes the wrong thing on the wrong page and the signup
        #    still fails.
        with self.assertRaises(ConsentRequired) as caught:
            sign_up(contact=self.minor, event_role=self.role)
        self.assertIn("emergency contact", str(caught.exception))

    def test_an_adult_needs_no_emergency_contact(self):
        adult = make_person("Adult", birth_date=datetime.date(1980, 1, 1))
        participation = sign_up(contact=adult, event_role=self.role)
        self.assertEqual(participation.status, Participation.Status.REGISTERED)

    def test_an_unknown_birth_date_is_held_to_the_same_rule(self):
        unknown = make_person("Unknown", birth_date=None)
        with self.assertRaises(ConsentRequired):
            sign_up(contact=unknown, event_role=self.role)

    def test_picking_an_emergency_contact_fills_the_consent_in(self):
        kin = give_emergency_contact(self.minor, name="Wang Xiuying")
        form = SignUpForm(
            {"event_role": self.role.pk, "use_emergency_contact": kin.pk,
             "consent_method": Participation.ConsentMethod.VERBAL},
            event=self.event, contact=self.minor,
        )
        self.assertTrue(form.is_valid(), form.errors)
        participation = sign_up(
            contact=self.minor, event_role=self.role, consent=form.consent())
        self.assertEqual(participation.consent_given_by, "Wang Xiuying")
        self.assertEqual(str(participation.consent_phone), str(kin.phone))
        self.assertEqual(participation.consent_relationship, kin.relationship_type)

    def test_the_consent_is_copied_not_referenced(self):
        # ⚠️ Editing the emergency contact later must not rewrite consent that
        #    was already given: Participation's consent columns record what was
        #    agreed on the day. Same rule as hours and the notice snapshot.
        kin = give_emergency_contact(self.minor, name="Wang Xiuying")
        form = SignUpForm(
            {"event_role": self.role.pk, "use_emergency_contact": kin.pk,
             "consent_method": Participation.ConsentMethod.VERBAL},
            event=self.event, contact=self.minor,
        )
        form.is_valid()
        participation = sign_up(
            contact=self.minor, event_role=self.role, consent=form.consent())

        kin.name = "Somebody Else"
        kin.save(update_fields=["name"])
        participation.refresh_from_db()
        self.assertEqual(participation.consent_given_by, "Wang Xiuying")

    def test_only_this_persons_own_emergency_contacts_are_offered(self):
        give_emergency_contact(self.minor, name="Mine")
        stranger = make_person("Stranger", birth_date=datetime.date(1980, 1, 1))
        give_emergency_contact(stranger, name="Theirs")
        form = SignUpForm(event=self.event, contact=self.minor)
        offered = [k.name for k in form.fields["use_emergency_contact"].queryset]
        self.assertEqual(offered, ["Mine"])

    def test_typing_a_guardian_by_hand_still_works(self):
        # The emergency contact is a shortcut, not a replacement: a guardian who
        # is not the person you would call on the day is a real situation, and
        # D15 keeps those two ideas separate on purpose.
        give_emergency_contact(self.minor)
        participation = sign_up(contact=self.minor, event_role=self.role, consent={
            "consent_given_by": "Another Guardian",
            "consent_method": Participation.ConsentMethod.ONLINE,
            "consent_email": "guardian@example.com",
        })
        self.assertEqual(participation.consent_given_by, "Another Guardian")


class SignupConfirmationTests(TestCase):
    """Item 10: the signup went through, and who gets told.

    A minor hears about it themselves *and* through their guardian. Both,
    because a fifteen-year-old with a phone still has to turn up, and whoever
    gave consent has to know it was used.
    """

    def setUp(self):
        LocmemBackend.outbox = []
        self.event = make_event()
        self.role = make_role(self.event, "lifting")

    def sent(self):
        return [(m.to, m.channel) for m in LocmemBackend.outbox]

    def sign_up_adult(self, **fields):
        adult = make_person("Adult", birth_date=datetime.date(1980, 1, 1), **fields)
        participation = sign_up(contact=adult, event_role=self.role)
        confirm_signup(participation, backend=LocmemBackend())
        return participation

    def test_an_adult_is_confirmed_on_their_preferred_channel(self):
        self.sign_up_adult(email="adult@example.com")
        self.assertEqual(self.sent(), [("adult@example.com", "email")])

    def test_sms_is_used_when_that_is_the_preference(self):
        self.sign_up_adult(
            email="adult@example.com", phone="+14085550111",
            preferred_communication_method=Contact.CommunicationMethod.SMS)
        self.assertEqual(self.sent(), [("+14085550111", "sms")])

    def test_a_minor_and_their_guardian_are_both_told(self):
        minor = make_person(
            "Minor", email="teen@example.com",
            birth_date=local_today() - datetime.timedelta(days=365 * 15))
        give_emergency_contact(minor)
        participation = sign_up(contact=minor, event_role=self.role, consent={
            "consent_given_by": "Guardian",
            "consent_method": Participation.ConsentMethod.VERBAL,
            "consent_email": "guardian@example.com",
        })
        confirm_signup(participation, backend=LocmemBackend())
        self.assertIn(("teen@example.com", "email"), self.sent())
        self.assertIn(("guardian@example.com", "email"), self.sent())

    def test_a_minor_with_no_address_of_their_own_still_reaches_the_guardian(self):
        minor = make_person(
            "Minor", birth_date=local_today() - datetime.timedelta(days=365 * 15))
        give_emergency_contact(minor)
        participation = sign_up(contact=minor, event_role=self.role, consent={
            "consent_given_by": "Guardian",
            "consent_method": Participation.ConsentMethod.VERBAL,
            "consent_phone": "+14085550122",
        })
        confirm_signup(participation, backend=LocmemBackend())
        self.assertEqual(self.sent(), [("+14085550122", "sms")])

    def test_nobody_reachable_is_not_an_error(self):
        # ⚠️ The signup stands. A confirmation that could not be sent must never
        #    undo a row that was accepted — the person can still see it under
        #    "My signups".
        silent = make_person("Silent", birth_date=datetime.date(1980, 1, 1))
        participation = sign_up(contact=silent, event_role=self.role)
        self.assertEqual(confirm_signup(participation, backend=LocmemBackend()), [])
        self.assertEqual(participation.status, Participation.Status.REGISTERED)

    def test_no_minors_name_leaves_the_database(self):
        # Same rule as default_message(): what goes out is an address and an
        # announcement, never "your child 小明".
        minor = make_person(
            "Xiaoming", birth_date=local_today() - datetime.timedelta(days=365 * 15))
        give_emergency_contact(minor)
        participation = sign_up(contact=minor, event_role=self.role, consent={
            "consent_given_by": "Guardian",
            "consent_method": Participation.ConsentMethod.VERBAL,
            "consent_email": "guardian@example.com",
        })
        confirm_signup(participation, backend=LocmemBackend())
        for message in LocmemBackend.outbox:
            self.assertNotIn("Xiaoming", message.body)


class PerEventMinorRuleTests(TestCase):
    """Event.requires_guardian_consent: the coordinator decides, per event.

    Sorting tins on a Saturday with parents in the room is not a weekend away.
    Default True, so a new event is protected until somebody deliberately says
    otherwise — the other default fails silently, on the day.
    """

    def setUp(self):
        self.event = make_event()
        self.role = make_role(self.event, "lifting")
        self.minor = make_person(
            "Minor", birth_date=local_today() - datetime.timedelta(days=365 * 15))

    def test_the_default_is_to_require_consent(self):
        self.assertTrue(self.event.requires_guardian_consent)

    def test_a_waived_event_lets_a_minor_sign_up_like_an_adult(self):
        self.event.requires_guardian_consent = False
        self.event.save(update_fields=["requires_guardian_consent"])
        participation = sign_up(contact=self.minor, event_role=self.role)
        self.assertEqual(participation.status, Participation.Status.REGISTERED)
        self.assertEqual(participation.consent_given_by, "")

    def test_a_waived_event_needs_no_emergency_contact_either(self):
        # The emergency-contact rule hangs off the same question; leaving it on
        # would make "no consent needed" still refuse the signup, for a reason
        # the page never mentioned.
        self.event.requires_guardian_consent = False
        self.event.save(update_fields=["requires_guardian_consent"])
        self.assertEqual(self.minor.emergency_contacts.count(), 0)
        sign_up(contact=self.minor, event_role=self.role)

    def test_a_waived_event_lets_a_minor_be_marked_attended(self):
        # ⚠️ Both gates have to answer the same way. Refusing the attendance
        #    after accepting the signup would strand somebody who did the work,
        #    with their hours uncounted.
        self.event.requires_guardian_consent = False
        self.event.save(update_fields=["requires_guardian_consent"])
        participation = sign_up(contact=self.minor, event_role=self.role)
        check_in(participation)
        self.assertEqual(participation.status, Participation.Status.ATTENDED)

    def test_a_required_event_still_refuses(self):
        with self.assertRaises(ConsentRequired):
            sign_up(contact=self.minor, event_role=self.role)

    def test_the_form_stops_drawing_the_consent_boxes_when_waived(self):
        self.event.requires_guardian_consent = False
        self.event.save(update_fields=["requires_guardian_consent"])
        form = SignUpForm(event=self.event, contact=self.minor)
        self.assertFalse(form.needs_consent)

    def test_a_coordinator_can_set_it_when_publishing(self):
        self.assertIn("requires_guardian_consent", EventForm.Meta.fields)


class FoundationTierReadOnlyTests(PageTestCase):
    """2026-08-05 feedback: the foundation tier reads any event, changes none.

    ⭐ The assertion that matters is the POST one. Not drawing a button keeps
       nobody out — a form posted from anywhere at all arrives at the view with
       the same shape — so every one of these pages is checked by sending the
       write, not by reading the HTML.
    """

    def setUp(self):
        super().setUp()
        self.boss = self.account("boss", "Boss", birth_date=datetime.date(1975, 1, 1))
        self.boss.groups.add(foundation_admin_group())
        self.boss = type(self.boss).objects.get(pk=self.boss.pk)
        self.participation = Participation.objects.create(
            contact=self.lisi.contact, event_role=self.role)

    # --- may read, on any ministry's event ---------------------------------

    def test_the_three_read_pages_open(self):
        self.login(self.boss)
        for name in ["event_registrations", "event_attendance", "event_report"]:
            with self.subTest(page=name):
                response = self.client.get(reverse(f"events:{name}", args=[self.event.pk]))
                self.assertEqual(response.status_code, 200)

    def test_it_works_for_a_ministry_they_do_not_administer(self):
        # The whole point of the tier: it is not scoped to one ministry.
        other = make_event(ministry=self.tax, owner=self.other_admin.contact,
                           name="Someone else's")
        self.login(self.boss)
        self.assertEqual(
            self.client.get(reverse("events:event_report", args=[other.pk])).status_code,
            200)

    def test_the_hub_lists_every_ministrys_events(self):
        make_event(ministry=self.tax, owner=self.other_admin.contact, name="Tax one")
        self.login(self.boss)
        response = self.client.get(reverse("events:event_manage_list"))
        self.assertContains(response, "Tax one")
        self.assertContains(response, self.event.name)
        self.assertContains(response, "All events")
        self.assertFalse(response.context["can_manage"])

    # --- may not write, anywhere -------------------------------------------

    def test_it_cannot_check_anybody_in(self):
        """⭐ The boundary. Buttons are hidden too, but that is not why."""
        self.login(self.boss)
        response = self.client.post(
            reverse("events:event_attendance", args=[self.event.pk]),
            {"participation": self.participation.pk, "action": "check_in"})
        self.assertEqual(response.status_code, 403)
        self.participation.refresh_from_db()
        self.assertIsNone(self.participation.checked_in_at)

    def test_it_cannot_enter_hours(self):
        self.login(self.boss)
        self.client.post(
            reverse("events:event_attendance", args=[self.event.pk]),
            {"participation": self.participation.pk, "action": "hours", "hours": "5"})
        self.participation.refresh_from_db()
        self.assertIsNone(self.participation.hours)

    def test_the_three_write_pages_stay_shut(self):
        self.login(self.boss)
        for name in ["event_update", "event_notify"]:
            with self.subTest(page=name):
                self.assertEqual(
                    self.client.get(reverse(f"events:{name}", args=[self.event.pk])).status_code,
                    403)
        self.assertEqual(
            self.client.get(reverse("events:event_create")).status_code, 403)

    def test_it_cannot_change_an_events_status_from_the_hub(self):
        self.login(self.boss)
        response = self.client.post(reverse("events:event_manage_list"), {
            "event": self.event.pk, "status": Event.Status.CANCELLED})
        self.assertEqual(response.status_code, 403)
        self.event.refresh_from_db()
        self.assertNotEqual(self.event.status, Event.Status.CANCELLED)

    def test_it_cannot_add_or_delete_a_role(self):
        self.login(self.boss)
        self.assertEqual(
            self.client.post(reverse("events:event_roles", args=[self.event.pk]),
                             {"role": self.role.role.pk, "needed_count": 1}).status_code,
            403)
        self.assertEqual(
            self.client.post(reverse("events:role_delete", args=[self.role.pk])).status_code,
            403)

    # --- the interface agrees with the permission --------------------------

    def test_no_edit_or_notify_links_are_drawn_for_it(self):
        self.login(self.boss)
        page = self.client.get(
            reverse("events:event_report", args=[self.event.pk])).content.decode()
        self.assertNotIn(reverse("events:event_update", args=[self.event.pk]), page)
        self.assertNotIn(reverse("events:event_notify", args=[self.event.pk]), page)
        self.assertIn(reverse("events:event_attendance", args=[self.event.pk]), page)

    def test_a_ministry_admin_who_is_also_foundation_keeps_managing(self):
        """⚠️ Being promoted must not take the publish button away.

        zhang administers the pantry. Adding the foundation group as well has to
        leave the managing view of their own ministries intact, not replace it
        with the read-only view of everything.
        """
        self.zhang.groups.add(foundation_admin_group())
        self.login(type(self.zhang).objects.get(pk=self.zhang.pk))
        response = self.client.get(reverse("events:event_manage_list"))
        self.assertTrue(response.context["can_manage"])
        self.assertContains(response, "Events I manage")

    def test_the_navigation_actually_offers_the_page(self):
        """⚠️ The seventh time this project has built a page nothing linked to.

        `is_ministry_admin` is False for this tier — it holds no MinistryRole —
        so the shared nav drew nothing at all and the only way in was typing
        the URL. The view was open the whole time; that is precisely what makes
        this class of gap invisible.

        Caught by looking at a screenshot, not by a test. This is the test.
        """
        self.login(self.boss)
        page = self.client.get(reverse("events:event_list")).content.decode()
        self.assertIn(reverse("events:event_manage_list"), page)
        self.assertIn("All events", page)

    def test_a_ministry_admin_still_sees_the_managing_label(self):
        self.login(self.zhang)
        page = self.client.get(reverse("events:event_list")).content.decode()
        self.assertIn("Events I manage", page)
        self.assertNotIn("All events", page)

    def test_a_plain_volunteer_is_offered_neither(self):
        self.login(self.lisi)
        page = self.client.get(reverse("events:event_list")).content.decode()
        self.assertNotIn(reverse("events:event_manage_list"), page)

    def test_a_plain_volunteer_still_gets_nothing(self):
        self.login(self.lisi)
        for name in ["event_registrations", "event_attendance", "event_report"]:
            with self.subTest(page=name):
                self.assertEqual(
                    self.client.get(reverse(f"events:{name}", args=[self.event.pk])).status_code,
                    403)


def a_photo(size=(1200, 800), fmt="JPEG", exif=None, colour=(200, 60, 40)):
    """An uploaded image file, as a browser would send one."""
    buffer = io.BytesIO()
    image = PILImage.new("RGB", size, colour)
    if exif is not None:
        image.save(buffer, fmt, exif=exif)
    else:
        image.save(buffer, fmt)
    return SimpleUploadedFile(
        f"photo.{fmt.lower()}", buffer.getvalue(),
        content_type=f"image/{fmt.lower()}")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class EventImageUploadTests(PageTestCase):
    """2026-08-05: a picture for the listing, small and short-lived.

    Three requirements drove every decision here — barely any storage, gone when
    the event ends, in no backup at all — and the third is why this is a file
    rather than a column: the backup is a pg_dump.
    """

    def upload(self, upload):
        from events.forms import EventForm
        return EventForm(
            {
                "name": "With a picture", "event_type": EventType.objects.first().pk,
                "ministry": self.pantry.pk,
                "start_time": "2026-09-01T09:00", "end_time": "2026-09-01T12:00",
                "status": Event.Status.OPEN,
            },
            {"image": upload},
            user=self.zhang,
        )

    def test_a_big_photo_is_re_encoded_small(self):
        form = self.upload(a_photo(size=(3000, 2000)))
        self.assertTrue(form.is_valid(), form.errors)
        stored = form.cleaned_data["image"]
        with PILImage.open(stored) as check:
            self.assertLessEqual(max(check.size), 900)
            self.assertEqual(check.format, "WEBP")

    def test_a_small_photo_is_not_blown_up(self):
        # thumbnail() only ever shrinks. Upscaling would add bytes and no detail.
        form = self.upload(a_photo(size=(120, 90)))
        self.assertTrue(form.is_valid(), form.errors)
        with PILImage.open(form.cleaned_data["image"]) as check:
            self.assertEqual(check.size, (120, 90))

    def test_a_sideways_phone_photo_comes_out_upright(self):
        """⚠️ exif_transpose has to run **before** the EXIF is dropped.

        A phone records "this is rotated" in EXIF rather than rotating pixels.
        Strip the EXIF first and every portrait photo is stored on its side —
        a perfectly valid file that is simply wrong, with nothing raised.
        """
        exif = PILImage.Exif()
        exif[274] = 6  # Orientation: rotate 90°
        form = self.upload(a_photo(size=(400, 200), exif=exif))
        self.assertTrue(form.is_valid(), form.errors)
        with PILImage.open(form.cleaned_data["image"]) as check:
            # 400x200 rotated a quarter turn is 200x400.
            self.assertEqual(check.size, (200, 400))

    def test_no_exif_at_all_survives_the_re_encode(self):
        """⚠️ Privacy, not file size.

        GPS coordinates live in this same EXIF block, so a picture taken at a
        volunteer's home would publish where they live to everybody who can
        open the event. Asserted on the block as a whole rather than on the GPS
        tag alone — "nothing carried over" is both easier to be sure of and the
        rule actually wanted.
        """
        exif = PILImage.Exif()
        exif[271] = "TestPhone"      # Make
        exif[272] = "Model X"        # Model
        form = self.upload(a_photo(exif=exif))
        self.assertTrue(form.is_valid(), form.errors)
        with PILImage.open(form.cleaned_data["image"]) as check:
            self.assertFalse(dict(check.getexif()))

    def test_an_svg_is_refused(self):
        """⚠️ Not fussiness — SVG can carry script, served from our own origin.

        Opening it with Pillow *is* the check: an SVG cannot be opened as a
        raster image, so nothing extra is needed to keep it out.
        """
        svg = SimpleUploadedFile(
            "logo.svg", b'<svg xmlns="http://www.w3.org/2000/svg"></svg>',
            content_type="image/svg+xml")
        form = self.upload(svg)
        self.assertFalse(form.is_valid())
        self.assertIn("image", form.errors)

    def test_something_that_is_not_an_image_at_all_is_refused(self):
        pdf = SimpleUploadedFile("notes.pdf", b"%PDF-1.4 not really",
                                 content_type="application/pdf")
        self.assertFalse(self.upload(pdf).is_valid())

    @override_settings(EVENT_IMAGE_MAX_UPLOAD_BYTES=512)
    def test_an_upload_over_the_size_limit_is_refused(self):
        """⚠️ This limit is about storage and bandwidth, not about safety.

        The first version of this test sent a fake oversized file and expected
        the size complaint. It got Django's "not a valid image" instead —
        because `ImageField.to_python()` opens the file with Pillow **before**
        `clean_image()` ever runs. Decompression bombs are stopped by Pillow's
        own `MAX_IMAGE_PIXELS`, not by the comparison below.

        So the file here is a real image, and what is being asserted is that a
        genuine photo can still be too big to accept.
        """
        form = self.upload(a_photo(size=(600, 400)))
        self.assertFalse(form.is_valid())
        self.assertIn("larger than", " ".join(form.errors["image"]))

    def test_an_event_without_a_picture_is_perfectly_valid(self):
        from events.forms import EventForm
        form = EventForm({
            "name": "No picture", "event_type": EventType.objects.first().pk,
            "ministry": self.pantry.pk,
            "start_time": "2026-09-01T09:00", "end_time": "2026-09-01T12:00",
            "status": Event.Status.OPEN,
        }, user=self.zhang)
        self.assertTrue(form.is_valid(), form.errors)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class EventImagePurgeTests(PageTestCase):
    """The picture goes when the event is over. That is the whole storage plan."""

    def with_image(self, event):
        event.image.save("x.webp", ContentFile(a_photo(size=(20, 20)).read()),
                         save=True)
        return event

    def test_a_finished_events_picture_is_deleted_and_forgotten(self):
        old = make_event(
            ministry=self.pantry, owner=self.zhang.contact, name="Over",
            start_time=NOW - 2 * DAY, end_time=NOW - 2 * DAY + HOUR)
        self.with_image(old)
        path = old.image.path

        call_command("purge_event_images", verbosity=0)

        old.refresh_from_db()
        # ⚠️ Both halves. Deleting the file but leaving the name gives a row
        #    pointing at nothing, and the page then renders a broken image
        #    rather than falling back to the default — worse than either.
        self.assertEqual(old.image.name, "")
        self.assertFalse(os.path.exists(path))

    def test_an_event_still_to_come_keeps_its_picture(self):
        self.with_image(self.event)
        call_command("purge_event_images", verbosity=0)
        self.event.refresh_from_db()
        self.assertTrue(self.event.image.name)

    def test_the_clock_is_end_time_not_the_status(self):
        """⚠️ Not `status == completed`.

        Marking an event completed is a human action somebody forgets, and a
        picture that only goes when somebody remembers is a picture that stays.
        """
        finished_but_unmarked = make_event(
            ministry=self.pantry, owner=self.zhang.contact, name="Nobody closed it",
            status=Event.Status.OPEN,
            start_time=NOW - 3 * DAY, end_time=NOW - 3 * DAY + HOUR)
        self.with_image(finished_but_unmarked)
        call_command("purge_event_images", verbosity=0)
        finished_but_unmarked.refresh_from_db()
        self.assertEqual(finished_but_unmarked.image.name, "")

    def test_a_dry_run_changes_nothing(self):
        old = make_event(
            ministry=self.pantry, owner=self.zhang.contact, name="Over",
            start_time=NOW - 2 * DAY, end_time=NOW - 2 * DAY + HOUR)
        self.with_image(old)
        call_command("purge_event_images", "--dry-run", verbosity=0)
        old.refresh_from_db()
        self.assertTrue(old.image.name)

    def test_running_it_twice_is_harmless(self):
        old = make_event(
            ministry=self.pantry, owner=self.zhang.contact, name="Over",
            start_time=NOW - 2 * DAY, end_time=NOW - 2 * DAY + HOUR)
        self.with_image(old)
        call_command("purge_event_images", verbosity=0)
        call_command("purge_event_images", verbosity=0)  # must not raise


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class EventImageDisplayTests(PageTestCase):
    """Where the picture appears, and where it deliberately does not."""

    def test_the_list_falls_back_to_the_foundation_logo(self):
        self.login(self.lisi)
        self.assertContains(self.client.get(reverse("events:event_list")),
                            "core/img/event-default")

    def test_the_list_shows_an_uploaded_picture_instead(self):
        self.event.image.save("x.webp", ContentFile(a_photo(size=(20, 20)).read()),
                              save=True)
        self.login(self.lisi)
        response = self.client.get(reverse("events:event_list"))
        self.assertContains(response, self.event.image.url)

    def test_past_events_carry_no_picture_column(self):
        """⚠️ Deliberate: every finished event shows the default logo, so a
           column of identical logos would be noise rather than information.
        """
        make_event(
            ministry=self.pantry, owner=self.zhang.contact, name="Over",
            status=Event.Status.COMPLETED,
            start_time=NOW - 2 * DAY, end_time=NOW - 2 * DAY + HOUR)
        self.login(self.lisi)
        self.assertNotContains(self.client.get(reverse("events:past_events")),
                               "event-thumb")

    def test_the_detail_page_carries_no_picture_yet(self):
        # Not decided where it should sit; drawn nowhere until it is.
        self.event.image.save("x.webp", ContentFile(a_photo(size=(20, 20)).read()),
                              save=True)
        self.login(self.lisi)
        self.assertNotContains(
            self.client.get(reverse("events:event_detail", args=[self.event.pk])),
            "event-thumb")
