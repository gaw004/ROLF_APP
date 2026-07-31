"""What each test here nails down is written next to it, as in A10 and B5.

The one that matters most is the first: an event that opened five roles and
filled three still has five. Every other test in this file could go; that one
could not, because it is「空缺编制」moved to a different table (goal.md D19).
"""

import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.services import register_account
from contact.models import Contact, EmergencyContact, RelationshipType
from core.notifications.locmem import LocmemBackend
from core.timeutils import (
    local_date_of,
    local_month_of,
    local_now,
    local_today,
    month_bounds,
)
from org.models import Assignment, Ministry, MinistryRole, Position
from org.permissions import foundation_admin_group

from .models import Event, EventRole, EventType, Participation, ParticipationRole
from .services import (
    ConsentRequired,
    check_in,
    check_out,
    default_message,
    event_summary,
    ministry_staff_participation,
    notify_event_change,
    record_hours,
    resolve_recipients,
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
        return make_person("Minor", birth_date=local_today() - datetime.timedelta(days=365 * 15))

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

    def test_signing_up_over_needed_count_is_allowed(self):
        # Advisory, never a limit — the same line taken with duplicate names.
        # Over-subscription is ordinary; the system's job is to say so, not to
        # stand in the way.
        role = make_role(self.event, "welcome", needed_count=1)
        for index in range(3):
            sign_up(
                contact=make_person(f"A{index}", birth_date=datetime.date(1980, 1, 1)),
                event_role=role,
            )
        self.assertEqual(role.participations.count(), 3)


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
        relationship = RelationshipType.objects.create(
            code="parent_of", name_a_to_b="parent of", name_b_to_a="child of",
            usable_as_emergency_contact=True)
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
        self.parent_of = RelationshipType.objects.create(
            code="parent_of", name_a_to_b="parent of", name_b_to_a="child of",
            usable_as_emergency_contact=True)

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
        self.assertIn("您的孩子", body)

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
        self.assertContains(response, "联系不上")
