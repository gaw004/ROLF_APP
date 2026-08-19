"""What each test here nails down is written next to it, as in A10 and B5.

The one that matters most is the first: an event that opened five roles and
filled three still has five. Every other test in this file could go; that one
could not, because it is「空缺编制」moved to a different table (goal.md D19).
"""

import base64
import datetime
import io
import json
import os
import re
import tempfile
from decimal import Decimal
from pathlib import Path

from django.conf import settings

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from PIL import Image as PILImage
from django.utils import formats, timezone
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils.timezone import localtime

from accounts.services import register_account
from contact.models import Contact, EmergencyContact, RelationshipType
from core.limits import LONG_TEXT, SEARCH
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

from .management.commands.seed_demo import demo_login

from . import schedule, tokens
from .forms import EventForm, EventPeriodForm, SignUpForm
from .views import EVENTS_PER_PAGE
from .models import (
    Event,
    EventNotification,
    EventRole,
    EventType,
    Participation,
    ParticipationRole,
)
from .services import (
    CHECKIN_CREDENTIAL_KEY,
    CREDENTIAL_MAX_AGE,
    ConsentRequired,
    CredentialExpired,
    apply_scan,
    TurnedUp,
    cancel,
    check_in,
    check_out,
    clear_hours,
    confirm_signup,
    default_checkin_mode,
    default_message,
    event_summary,
    events_in_period,
    mark_absent,
    ministry_report,
    ministry_staff_participation,
    notify_event_change,
    record_hours,
    issue_credential,
    read_credential,
    resolve_recipients,
    scan_targets,
    scheduled_hours,
    sign_up,
    undo_attendance,
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


class RefusingEmailBackend:
    """A provider that accepts `allowance` messages and refuses the rest.

    What a free tier's daily limit looks like from in here: not an outage, but
    the same call working and then not, halfway down a list. Module level
    because NOTIFICATION_BACKEND is a dotted path and gets no arguments.
    """

    allowance = 0

    def __init__(self, allowance=None):
        if allowance is not None:
            self.allowance = allowance

    def send(self, messages):
        from core.notifications.base import DeliveryResult

        return [
            DeliveryResult(message=message, accepted=number < self.allowance,
                           detail="" if number < self.allowance else "quota exceeded")
            for number, message in enumerate(messages)
        ]


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


class NoShowTests(TestCase):
    """The status that existed for months with nothing able to write it.

    ⚠️ The bug this closes is not a crash. Every "no-show rate" the reports
       could have produced would have been a hard zero, on every ministry, in
       every period — right-looking, never raising, never right. 2026-08-05.
    """

    def setUp(self):
        self.event = make_event()
        self.role = make_role(self.event, "lifting")
        self.wang = make_person("Wang", birth_date=datetime.date(1980, 5, 5))
        self.participation = Participation.objects.create(
            contact=self.wang, event_role=self.role)

    def test_marking_absent_records_the_status(self):
        mark_absent(self.participation)
        self.participation.refresh_from_db()
        self.assertEqual(self.participation.status, Participation.Status.ABSENT)

    def test_refused_when_hours_are_already_recorded(self):
        # "Did three hours" and "did not come" cannot both be true, and the
        # hours are the value a human is answerable for having typed.
        record_hours(self.participation, Decimal("3.00"))
        with self.assertRaises(TurnedUp):
            mark_absent(self.participation)
        self.participation.refresh_from_db()
        self.assertEqual(self.participation.status, Participation.Status.ATTENDED)
        self.assertEqual(self.participation.hours, Decimal("3.00"))

    def test_refused_when_they_were_checked_in(self):
        # Left in place this would print a row reading "checked in 3:44 p.m. ·
        # No-show". Refusing is the only answer that does not either lie or
        # delete something.
        check_in(self.participation, at=NOW)
        with self.assertRaises(TurnedUp):
            mark_absent(self.participation)
        self.participation.refresh_from_db()
        self.assertEqual(self.participation.status, Participation.Status.ATTENDED)

    def test_checking_in_afterwards_puts_them_back_to_attended(self):
        # Somebody marked up the sheet, then the volunteer walked in an hour
        # late. Requiring the absence to be undone first is how people stop
        # marking absences at all.
        mark_absent(self.participation)
        check_in(self.participation, at=NOW)
        self.participation.refresh_from_db()
        self.assertEqual(self.participation.status, Participation.Status.ATTENDED)

    def test_paper_hours_afterwards_also_put_them_back(self):
        mark_absent(self.participation)
        record_hours(self.participation, Decimal("2.00"))
        self.participation.refresh_from_db()
        self.assertEqual(self.participation.status, Participation.Status.ATTENDED)

    def test_a_no_show_still_appears_on_the_attendance_page_query(self):
        # notifiable() drops cancelled rows. An absence must survive it, or the
        # only way back — checking them in — would have no row to click.
        mark_absent(self.participation)
        rows = Participation.objects.filter(event_role__event=self.event).notifiable()
        self.assertIn(self.participation, rows)


class MinistryReportTests(TestCase):
    """D27's thirteen figures, and the four places they could quietly lie."""

    def setUp(self):
        self.pantry = Ministry.objects.create(code="food_pantry", name="Food Pantry")
        self.tax = Ministry.objects.create(code="tax_help", name="Tax Help")
        self.event = make_event(ministry=self.pantry, start_time=NOW - 30 * DAY,
                                end_time=NOW - 30 * DAY + 3 * HOUR)
        self.lifting = make_role(self.event, "lifting", needed_count=5)
        self.wang = make_person("Wang", birth_date=datetime.date(1980, 5, 5))
        self.li = make_person("Li", birth_date=datetime.date(1985, 5, 5))

    def signup(self, contact, role=None, **fields):
        # Hours imply attendance — there is a database constraint saying so
        # (participation_hours_only_when_attended), and every production path
        # to hours goes through _mark_attended(). Fixtures that skip it are
        # testing a row the system cannot produce.
        if fields.get("hours") is not None:
            fields.setdefault("status", Participation.Status.ATTENDED)
        return Participation.objects.create(
            contact=contact, event_role=role or self.lifting, **fields)

    def report(self, events=None):
        return ministry_report(
            events if events is not None else Event.objects.filter(ministry=self.pantry))

    def test_it_describes_only_the_events_it_was_given(self):
        # ⭐ The whole scoping design rests on this: the report takes a queryset,
        #    so a ministry admin's page and a foundation admin's page run one
        #    code path and neither can widen past its own list.
        other = make_event(ministry=self.tax)
        self.signup(self.wang)
        self.signup(self.li, role=make_role(other, "greeting"))
        self.assertEqual(self.report()["figures"]["events"], 1)
        self.assertEqual(self.report()["figures"]["signups"], 1)

    def test_cancelled_signups_are_out_and_no_shows_are_in(self):
        # They signed up either way, but one of them said they were not coming.
        # Counting a withdrawal as a volunteer inflates every figure here.
        self.signup(self.wang, status=Participation.Status.CANCELLED)
        absent = self.signup(self.li)
        mark_absent(absent)
        figures = self.report()["figures"]
        self.assertEqual(figures["signups"], 1)
        self.assertEqual(figures["volunteers"], 1)

    def test_hours_carry_what_they_were_counted_from(self):
        # The total is biased low by whoever forgot to check people out, and
        # the bias is not random. The two companion figures are what let a
        # reader see that rather than trust a number.
        self.signup(self.wang, hours=Decimal("3.00"))
        self.signup(self.li)
        figures = self.report()["figures"]
        self.assertEqual(figures["hours"], Decimal("3.00"))
        self.assertEqual(figures["hours_records"], 1)
        self.assertEqual(figures["hours_missing"], 1)

    def test_repeat_rate_counts_people_not_signups(self):
        second = make_event(ministry=self.pantry, start_time=NOW - 20 * DAY,
                            end_time=NOW - 20 * DAY + 2 * HOUR)
        self.signup(self.wang)
        self.signup(self.wang, role=make_role(second, "lifting"))
        self.signup(self.li)
        figures = self.report()["figures"]
        self.assertEqual(figures["volunteers"], 2)
        self.assertEqual(figures["repeat_volunteers"], 1)
        self.assertEqual(figures["repeat_rate"], 50)

    def test_rates_are_none_rather_than_zero_when_there_is_nothing_to_count(self):
        # "None of the twelve" and "there were none" are different answers, and
        # printing 0% for the second reads as a failure.
        self.assertIsNone(self.report()["figures"]["repeat_rate"])

    def test_fully_staffed_ignores_events_that_could_not_be_full(self):
        # An event with no numbered role cannot be full. In the denominator it
        # would drag the rate down for being unmeasurable, which reads on the
        # page as a staffing problem.
        make_event(ministry=self.pantry, name="No roles at all")
        unlimited = make_event(ministry=self.pantry, name="Unlimited role")
        make_role(unlimited, "greeting")
        for _ in range(5):
            self.signup(make_person("Filler"))
        figures = self.report()["figures"]
        self.assertEqual(figures["staffable_events"], 1)
        self.assertEqual(figures["fully_staffed"], 1)
        self.assertEqual(figures["fully_staffed_rate"], 100)

    def test_an_understaffed_event_is_not_counted_as_full(self):
        self.signup(self.wang)
        self.assertEqual(self.report()["figures"]["fully_staffed"], 0)

    def test_minors_without_consent_follows_the_same_rule_as_the_gates(self):
        # consent_required_for() needs both halves: a minor AND an event that
        # asked for consent. Asking it differently here would reassure somebody
        # about a rule the report is not actually checking.
        child = make_person("Chen", birth_date=local_now().date())
        unknown = make_person("Zhou")
        self.signup(child)
        self.signup(unknown)
        # Both of them: an unknown birth date takes the cautious branch, the
        # same way Contact.is_minor's three states do.
        self.assertEqual(self.report()["figures"]["minors_without_consent"], 2)

        # The other half of the rule. An event where under-18s may sign up on
        # their own has nothing missing, so nothing is flagged.
        self.event.requires_guardian_consent = False
        self.event.save()
        self.assertEqual(self.report()["figures"]["minors_without_consent"], 0)

    def test_an_adult_with_no_consent_is_not_flagged(self):
        self.signup(self.wang)
        self.assertEqual(self.report()["figures"]["minors_without_consent"], 0)

    def test_a_consented_minor_is_not_flagged(self):
        self.event.requires_guardian_consent = True
        self.event.save()
        child = make_person("Chen", birth_date=local_now().date())
        self.signup(child, consent_at=local_now(), consent_given_by="A parent")
        self.assertEqual(self.report()["figures"]["minors_without_consent"], 0)

    def test_role_gap_does_not_multiply_the_wanted_count_by_the_signups(self):
        # ⚠️ The trap this whole function is shaped around. Summing
        #    needed_count in the same query that joins Participation repeats the
        #    5 once per signup — a role wanting 5 with 3 people reports 15.
        #    Nothing raises, and the ministry looks desperately short-staffed.
        for surname in ("A", "B", "C"):
            self.signup(make_person(surname))
        bar = self.report()["charts"]["role_gap"].bars[0]
        self.assertEqual(bar.needed, 5)
        self.assertEqual(bar.signed, 3)
        self.assertTrue(bar.short)

    def test_role_gap_shows_a_role_nobody_signed_up_for(self):
        # D19, arriving in a fourth place: that role has no Participation row to
        # be found through, and it is the one worth looking at.
        labels = [bar.label for bar in self.report()["charts"]["role_gap"].bars]
        self.assertIn("Lifting", labels)

    def test_paired_bars_share_one_scale(self):
        # Scaled separately, both bars fill their row and every role looks
        # exactly staffed.
        self.signup(self.wang)
        bar = self.report()["charts"]["role_gap"].bars[0]
        self.assertEqual(bar.needed_pct, 100)
        self.assertEqual(bar.signed_pct, 20)

    def test_months_with_nothing_in_them_are_still_drawn(self):
        # Skipping them is what a GROUP BY does, and in a chart it is a lie:
        # January beside March says the ministry ran events in consecutive
        # months. A quiet ministry has to look quiet.
        make_event(ministry=self.pantry, start_time=NOW - 90 * DAY,
                   end_time=NOW - 90 * DAY + HOUR)
        bars = self.report()["charts"]["events_by_month"].bars
        # Two events three months apart, so at least one month between them has
        # nothing in it — and it has to appear, at zero.
        self.assertGreaterEqual(len(bars), 3)
        self.assertIn("0 events", [bar.caption for bar in bars])

    def test_most_hours_does_not_put_the_unrecorded_people_first(self):
        # ⚠️ Postgres sorts NULL first on a descending order, so the obvious
        #    "-hours" tops a chart titled "Most hours" with everybody who has
        #    none — the exact inversion of the claim.
        self.signup(self.wang, hours=Decimal("2.00"))
        self.signup(self.li)
        bars = self.report()["charts"]["top_volunteers"].bars
        self.assertEqual([bar.label for bar in bars], [self.wang.short_label])

    def test_the_leaderboard_does_not_publish_contact_details(self):
        # ⚠️ Contact.__str__ appends an email or phone to tell two people of the
        #    same name apart — needed in a dropdown, where picking the wrong one
        #    is a silent data error. Nothing is chosen from a chart, so on this
        #    panel the address is simply published to whoever screenshots it.
        wang = make_person("Wang", email="wang@example.invalid",
                           birth_date=datetime.date(1980, 5, 5))
        self.signup(wang, hours=Decimal("2.00"))
        labels = [bar.label for bar in self.report()["charts"]["top_volunteers"].bars]
        self.assertNotIn("wang@example.invalid", " ".join(labels))
        self.assertIn("Wang", " ".join(labels))

    def test_a_chart_with_fewer_than_three_bars_says_so(self):
        # One bar is a number wearing a rectangle. The template falls back to a
        # list when this is set.
        self.signup(self.wang, hours=Decimal("2.00"))
        self.assertTrue(self.report()["charts"]["top_volunteers"].sparse)

    def test_absence_rate_counts_only_events_somebody_went_through(self):
        """⚠️ The denominator is the whole difficulty, and the obvious version
        of it is wrong in the direction of bad news.

        "Events with a no-show on them" contains nothing **but** events with
        absences, so the rate it produces can never be low. The question asked
        instead is one the data can answer: is any signup still sitting at
        `registered`?
        """
        # Event one: gone through. Two attended, one absent → 33%.
        for surname in ("A", "B"):
            self.signup(make_person(surname), hours=Decimal("2.00"))
        mark_absent(self.signup(make_person("C")))

        # Event two: nobody touched it. Must not dilute the rate.
        untouched = make_event(ministry=self.pantry, name="Nobody marked this up")
        role = make_role(untouched, "greeting")
        for surname in ("D", "E", "F", "G"):
            self.signup(make_person(surname), role=role)

        figures = self.report()["figures"]
        self.assertEqual(figures["absence_rate"], 33)
        self.assertEqual(figures["marked_up_events"], 1)
        self.assertEqual(figures["events_with_signups"], 2)

    def test_an_event_where_everybody_turned_up_counts_as_gone_through(self):
        # And contributes an honest 0%. This is the case the naive denominator
        # ("events with an absence") would throw away — which is exactly why
        # that version can never report a low rate.
        for surname in ("A", "B"):
            self.signup(make_person(surname), hours=Decimal("2.00"))
        figures = self.report()["figures"]
        self.assertEqual(figures["absence_rate"], 0)
        self.assertEqual(figures["marked_up_events"], 1)

    def test_events_with_no_signups_are_not_in_the_denominator(self):
        # They cannot have an absence. Counting them would make a diligent
        # ministry look careless — same reason fully_staffed excludes events
        # that opened no numbered role.
        make_event(ministry=self.pantry, name="Nobody signed up")
        self.signup(self.wang, hours=Decimal("2.00"))
        self.assertEqual(self.report()["figures"]["events_with_signups"], 1)

    def test_absence_rate_is_none_when_nothing_was_marked_up(self):
        self.signup(self.wang)
        self.assertIsNone(self.report()["figures"]["absence_rate"])

    def test_an_ordered_queryset_does_not_split_every_group(self):
        """⚠️ The one this suite missed, found in a screenshot instead.

        The management list hands over `…order_by("-start_time")`, and an
        explicit ordering **joins the GROUP BY** — so grouping by month also
        grouped by the exact timestamp, one row per event, every count 1. The
        page said eleven events in August beside a chart saying one.

        Django drops `Meta.ordering` for aggregates but never an order_by()
        somebody wrote, and every other test here builds its own unordered
        queryset — which is precisely why they all passed.
        """
        for day in (2, 3, 4):
            make_event(ministry=self.pantry, name=f"Same month {day}",
                       start_time=NOW - day * DAY,
                       end_time=NOW - day * DAY + HOUR)
        ordered = Event.objects.filter(ministry=self.pantry).order_by("-start_time")
        bars = ministry_report(ordered)["charts"]["events_by_month"].bars
        self.assertIn("3 events", [bar.caption for bar in bars])
        # The other four groupings ride on the same queryset.
        report = ministry_report(ordered)
        self.assertEqual(report["charts"]["role_gap"].bars[0].needed, 5)

    def test_bars_are_scaled_against_the_largest_not_the_total(self):
        for surname, hours in (("A", "1.00"), ("B", "3.00"), ("C", "4.00")):
            self.signup(make_person(surname), hours=Decimal(hours))
        bars = self.report()["charts"]["top_volunteers"].bars
        self.assertEqual([bar.pct for bar in bars], [100, 75, 25])


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


class FromTodayTests(TestCase):
    """from_today(): still to come or still going, cut at midnight.

    2026-08-18: **reads end_time** (it read start_time for one day). The row
    that changed is the overnight one below, and it changed because the
    schedule drew that event on today's column while the list beside it did
    not have it — clicking the card led nowhere.
    """

    def setUp(self):
        self.ministry = Ministry.objects.create(code="food_pantry", name="Food Pantry")
        self.midnight = day_start(local_today())

    def make(self, name, start, hours=1, **fields):
        return make_event(
            ministry=self.ministry, name=name, start_time=start,
            end_time=start + hours * HOUR, **fields)

    def test_an_event_that_finished_this_morning_is_in(self):
        event = self.make("This morning", self.midnight + HOUR)
        self.assertIn(event, Event.objects.from_today())

    def test_an_event_still_running_is_in(self):
        event = self.make("Running", self.midnight + HOUR, hours=23)
        self.assertIn(event, Event.objects.from_today())

    def test_the_first_minute_of_today_is_in(self):
        # Half-open at the bottom: midnight itself belongs to today.
        event = self.make("Midnight", self.midnight)
        self.assertIn(event, Event.objects.from_today())

    def test_yesterday_is_out(self):
        event = self.make("Yesterday", self.midnight - HOUR)
        self.assertNotIn(event, Event.objects.from_today())

    def test_an_event_that_began_last_night_and_is_still_running_is_in(self):
        """🔴 The row that 2026-08-18 changed.

        Started 22:00 yesterday, ends 02:00 today. Under the start_time reading
        it dropped off the page at midnight **while it was still running** —
        and the schedule beside the list went on drawing it on today's column,
        so clicking that card asked the list for a row it did not have.
        """
        event = self.make("Overnight", self.midnight - 2 * HOUR, hours=4)
        self.assertIn(event, Event.objects.from_today())

    def test_a_multi_day_event_stays_until_it_actually_ends(self):
        """The stated cost: it is on the list every day it runs.

        ⚠️ And it sorts to the **top**, because the page orders by start_time.
           Deliberate — it is the one in progress.
        """
        event = self.make("Retreat", self.midnight - 2 * DAY, hours=96)
        self.assertIn(event, Event.objects.from_today())

    def test_a_multi_day_event_that_is_over_is_out(self):
        """⚠️ The thing the old comment feared, checked rather than assumed:
           reading end_time does **not** keep last month's trip on the page.
           A finished event has end_time in the past, so it is out.
        """
        event = self.make("Old retreat", self.midnight - 30 * DAY, hours=72)
        self.assertNotIn(event, Event.objects.from_today())

    def test_the_day_can_be_handed_in(self):
        """⚠️ The parameter is not decoration: it is how a caller — or a test —
           says which day it means instead of racing the clock. The default is
           the foundation's today, built through core.timeutils, which is the
           only spelling of "midnight" D16 allows.
        """
        yesterday = local_today() - datetime.timedelta(days=1)
        event = self.make("Yesterday", self.midnight - HOUR)
        self.assertIn(event, Event.objects.from_today(today=yesterday))
        self.assertNotIn(event, Event.objects.from_today())


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

    def account(self, handle, last_name, **contact_fields):
        # `handle` only makes up an address; the login name **is** the address
        # (2026-08-06). An email by default because without one the person is
        # legitimately unreachable, which is a real state but not the one most of
        # these tests are about.
        contact_fields.setdefault("email", f"{handle}@example.com")
        return register_account(
            password="a-good-long-password",
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

    def test_a_confirmed_event_is_listed_saying_it_is_confirmed(self):
        """⭐ Visibility and signability are two questions, and 2026-08-17 moved
           where the *list* stands on the first one.

           It used to drop a full event, which put the page in the position of
           telling the people most likely to look it up — the ones who got in —
           that it did not exist. Now the row is there wearing its status, and
           the thing that refuses a signup is still event_signup's
           open_for_signup(), one layer down where it cannot be styled away.
        """
        self.login(self.lisi)
        self.event.status = Event.Status.CONFIRMED
        self.event.save()
        listing = self.client.get(reverse("events:event_list"))
        self.assertContains(listing, self.event.name)
        self.assertContains(listing, "Confirmed")
        detail = self.client.get(reverse("events:event_detail", args=[self.event.pk]))
        self.assertEqual(detail.status_code, 200)
        signup = self.client.get(reverse("events:event_signup", args=[self.event.pk]))
        self.assertEqual(signup.status_code, 404)

    def test_a_cancelled_event_is_listed_saying_it_is_cancelled(self):
        # The people who need to know it is off are exactly the ones who signed
        # up — and this is the page they were sent to from the notification.
        self.login(self.lisi)
        self.event.status = Event.Status.CANCELLED
        self.event.save()
        response = self.client.get(reverse("events:event_list"))
        self.assertContains(response, self.event.name)
        self.assertContains(response, "Cancelled")

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
        root = get_user_model().objects.create_superuser(email="root@example.com", password="x")
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
    """The period filter on /events/ — "how much is on in this window".

    in_period() and events_in_period() were written and tested in Phase B and
    then had no caller but the tests: the only UI that could answer that
    question was the admin changelist, which volunteers cannot reach.

    ⚠️ This was **two** pages until 2026-08-17, /events/ and /events/past/, and
       the class still reads as if it were about both in places. Past Events is
       gone; /events/ now starts at midnight today rather than at "not started
       yet", so the backwards half of R1 is answered on All Events by the tier
       that has it (goal.md's R1 row).
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

    # --- the search box (2026-08-06) ---------------------------------------

    def search(self, term, url_name="events:event_list"):
        self.login(self.lisi)
        return self.client.get(reverse(url_name), {"q": term})

    def test_it_matches_the_event_name(self):
        self.assertEqual(self.search("saturday").context["total"], 1)
        self.assertEqual(self.search("nothing like it").context["total"], 0)

    def test_it_matches_the_location_too(self):
        """⭐ The half of this that is not obvious, and the reason it exists.

        Somebody looking for "the one in the kitchen" remembers where it was, not
        what it was called.
        """
        self.event.location = "Church kitchen"
        self.event.save(update_fields=["location"])
        self.assertEqual(self.search("kitchen").context["total"], 1)

    def test_it_ignores_case(self):
        self.assertEqual(self.search("SATURDAY").context["total"], 1)

    def test_it_ignores_surrounding_space(self):
        # ⚠️ A trailing space off a phone's autocorrect would make `icontains`
        #    match nothing, and the page would come back empty with a perfectly
        #    good word sitting in the box.
        self.assertEqual(self.search("  saturday  ").context["total"], 1)

    def test_an_empty_box_narrows_nothing(self):
        self.assertEqual(self.search("").context["total"], 1)

    def test_it_does_not_match_the_ministry_name(self):
        """⚠️ Deliberate. There is a ministry dropdown two boxes along, and a
           search that also matched it would empty the whole of Food Pantry onto
           the page for the word "food" — which reads as the search having been
           ignored rather than as a wide match.
        """
        self.assertEqual(self.search("pantry").context["total"], 0)

    def test_the_search_term_is_named_in_the_filter_description(self):
        """⚠️ That line is printed under the heading of the full report, and on
           the paper it becomes. A report that does not say what it covers gets
           read as "everything", and a search is the easiest of the three filters
           to leave on by accident.
        """
        form = EventPeriodForm({"q": "kitchen"})
        self.assertTrue(form.is_valid())
        self.assertIn("kitchen", form.description())

    def test_it_survives_into_the_full_report(self):
        self.login(self.zhang)
        response = self.client.get(reverse("events:ministry_report"), {"q": "saturday"})
        self.assertContains(response, "saturday")

    def test_a_search_longer_than_the_cap_is_a_form_error_not_a_crash(self):
        form = EventPeriodForm({"q": "x" * (SEARCH + 1)})
        self.assertFalse(form.is_valid())
        self.assertIn("q", form.errors)

    def test_the_box_is_on_the_page_and_comes_from_the_form(self):
        # Hand-written search inputs are refused by a guard in core/tests.py —
        # the cap only exists at the form layer.
        self.login(self.lisi)
        response = self.client.get(reverse("events:event_list"))
        self.assertContains(response, 'name="q"')
        self.assertContains(response, "Search by name or location")

    def test_last_month_is_not_on_the_page(self):
        """The window starts at midnight today, so a finished month is gone.

        ⚠️ Not "gone from the system" — self.old still opens by URL, because
           event_detail runs visible_to_volunteers() and the people who were
           there have hours on it. What this asserts is that the *list* is a
           list of what is on, not a history.
        """
        self.login(self.lisi)
        response = self.client.get(reverse("events:event_list"))
        self.assertNotContains(response, "Last month")
        self.assertEqual(
            self.client.get(
                reverse("events:event_detail", args=[self.old.pk])
            ).status_code, 200)

    def test_an_event_that_finished_this_morning_stays_until_midnight(self):
        """⭐ The reason from_today() cuts at midnight rather than at now.

        Somebody opening the page at lunchtime is looking at today. An event
        that ran at nine is part of today — it says so with its status, rather
        than by having disappeared while people were still checking in.
        """
        make_event(
            ministry=self.pantry, owner=self.zhang.contact, name="This morning",
            status=Event.Status.COMPLETED,
            start_time=day_start(local_today()) + HOUR,
            end_time=day_start(local_today()) + 2 * HOUR,
        )
        self.login(self.lisi)
        self.assertContains(
            self.client.get(reverse("events:event_list")), "This morning")

    def test_an_event_running_right_now_is_on_the_page(self):
        """It started this morning and runs until tonight.

        ⚠️ This used to assert the opposite on both pages, and that was the
           hole: upcoming() was start_time-based and past() was end_time-based,
           so a running event fell between the two and appeared on neither.
           from_today() asks a third question — which day is it on — and a
           running event is unambiguously on today's.
        """
        running = make_event(
            ministry=self.pantry, owner=self.zhang.contact, name="Running now",
            start_time=day_start(local_today()) + HOUR, end_time=NOW + HOUR,
        )
        self.login(self.lisi)
        self.assertContains(
            self.client.get(reverse("events:event_list")), "Running now")
        self.assertTrue(Event.objects.filter(pk=running.pk).exists())

    def test_a_draft_never_appears(self):
        """Today's drafts too, not only last month's.

        ⚠️ Worth its own case now that the page reaches back to midnight: the
           predicate that keeps drafts out is visible_to_volunteers(), and it
           is the *status* half of the query, untouched by the date half.
        """
        make_event(
            ministry=self.pantry, owner=self.zhang.contact, name="Secret draft",
            status=Event.Status.DRAFT,
            start_time=day_start(local_today()) + HOUR,
            end_time=day_start(local_today()) + 2 * HOUR,
        )
        self.login(self.lisi)
        self.assertNotContains(
            self.client.get(reverse("events:event_list")), "Secret draft")

    def test_an_end_before_the_start_is_a_form_error_not_an_empty_list(self):
        self.login(self.lisi)
        response = self.client.get(reverse("events:event_list"), {
            "start": self.day(NOW + 10 * DAY), "end": self.day(NOW),
        })
        self.assertFalse(response.context["period"].is_valid())

    def test_the_page_needs_a_login(self):
        response = self.client.get(reverse("events:event_list"))
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

    def test_a_window_entirely_in_the_past_comes_back_empty(self):
        """⚠️ And that is now the honest answer, not a bug.

        The date fields still accept a backwards window — the same form serves
        the management list, where the whole history is in scope. On this page
        the query starts at midnight today, so an old window intersects nothing
        and the empty state says to widen the range. What it must *not* do is
        quietly show today's events while the boxes say last month.
        """
        self.login(self.lisi)
        response = self.client.get(reverse("events:event_list"), {
            "start": self.day(NOW - 40 * DAY), "end": self.day(NOW - 20 * DAY),
        })
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
        self.assertNotContains(response, "&larr; Events I Manage")

    def test_arriving_from_the_management_list_goes_back_to_it(self):
        self.login(self.zhang)
        response = self.client.get(self.url("manage"))
        self.assertContains(response, reverse("events:event_manage_list"))
        self.assertContains(response, "&larr; Events I Manage")

    def test_an_old_past_marker_falls_through_to_events(self):
        """⭐ Why the marker is a whitelist and not a URL.

        `?from=past` was a valid marker until 2026-08-17, and links carrying it
        are in sent emails and in people's history. The page it named is gone,
        so the only two options were "send them to a 404" or "fall through to
        the default" — and falling through is what a whitelist does for free.
        A version that read a URL out of the query string would have had to
        grow a special case here instead.
        """
        self.login(self.lisi)
        response = self.client.get(self.url("past"))
        self.assertContains(response, reverse("events:event_list"))
        self.assertContains(response, "&larr; Events")

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
        # The same page is called "All Events" for this tier, and one page
        # should not have two names.
        boss = self.account("boss", "Boss", birth_date=datetime.date(1975, 1, 1))
        boss.groups.add(foundation_admin_group())
        self.login(type(boss).objects.get(pk=boss.pk))
        response = self.client.get(self.url("manage"))
        self.assertContains(response, "&larr; All Events")

    def test_arriving_from_my_signups_goes_back_to_my_signups(self):
        self.login(self.lisi)
        response = self.client.get(self.url("mine"))
        self.assertContains(response, reverse("events:my_participations"))
        self.assertContains(response, "&larr; My Signups")

    def test_an_unknown_marker_falls_back_rather_than_breaking(self):
        # ⚠️ The marker is a key into a table in the view, never a URL. Anything
        #    not in that table is simply not a destination.
        self.login(self.lisi)
        response = self.client.get(self.url("https://example.com/evil"))
        self.assertNotContains(response, "example.com")
        self.assertContains(response, "&larr; Events")


class DraftPreviewTests(PageTestCase):
    """2026-08-06: an unpublished event opens for the people who may publish it.

    The bug, in one sentence: `event_detail` looked up through
    `visible_to_volunteers()` and nothing else, so a draft 404'd for **everybody**
    — including the ministry admin who wrote it, and including the management
    list, where every draft's name is a link to exactly this page.

    ⭐ The regression this class exists to hold is
       `test_the_link_on_the_management_list_actually_opens`. The rest describe
       the shape of the preview; that one is the reported failure.

    ⚠️ It is a widening of who may see an unpublished event, so half of these
       tests are the other direction — a volunteer, and an admin of a different
       ministry, must still get 404 and not 403. 403 would confirm that
       something exists at that id, which is the one fact a draft withholds.
    """

    def setUp(self):
        super().setUp()
        self.draft = make_event(
            ministry=self.pantry, owner=self.zhang.contact,
            name="Christmas hamper packing", status=Event.Status.DRAFT)
        make_role(self.draft, "packing", needed_count=4)
        self.url = reverse("events:event_detail", args=[self.draft.pk])

    def foundation_account(self):
        boss = self.account("boss", "Boss", birth_date=datetime.date(1975, 1, 1))
        boss.groups.add(foundation_admin_group())
        # Re-read: group membership is cached on the instance that added it.
        return type(boss).objects.get(pk=boss.pk)

    # --- who gets in -------------------------------------------------------

    def test_the_owning_ministrys_admin_can_open_it(self):
        self.login(self.zhang)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.draft.name)
        self.assertTrue(response.context["preview"])

    def test_the_foundation_tier_can_open_any_ministrys_draft(self):
        # Same predicate as the signups / attendance / report pages, which have
        # opened on drafts for this tier all along. Making the event page the
        # one exception would be a rule nobody could guess from the other five.
        other = make_event(ministry=self.tax, owner=self.other_admin.contact,
                           name="Tax draft", status=Event.Status.DRAFT)
        self.login(self.foundation_account())
        response = self.client.get(reverse("events:event_detail", args=[other.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["preview"])

    def test_a_volunteer_still_gets_404(self):
        self.login(self.lisi)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_an_admin_of_a_different_ministry_gets_404_and_not_403(self):
        """⚠️ 404, deliberately. The number is the assertion.

        403 answers a question the volunteer side is not entitled to ask: it
        says an event exists at this id. For anybody outside the ministry an
        unpublished event must be indistinguishable from no event at all — which
        is byte-for-byte what this URL did before drafts could be previewed.
        """
        self.login(self.other_admin)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_the_link_on_the_management_list_actually_opens(self):
        """⭐ The reported bug, walked end to end.

        The list renders every event's name as a link to the detail page. Before
        this change every draft on it was a link that 404'd — and it was the only
        broken link on a page whose whole purpose is to be the entrance to the
        drafts (C0.2.4).
        """
        self.login(self.zhang)
        listing = self.client.get(reverse("events:event_manage_list"))
        target = f'{self.url}?from=manage'
        self.assertContains(listing, target)
        self.assertEqual(self.client.get(target).status_code, 200)

    # --- what the preview says ---------------------------------------------

    def test_it_says_it_is_a_preview_and_names_the_status(self):
        # The colour is decoration; the words carry it. Both are asserted
        # because the banner's whole job is to stop somebody reading the page as
        # if it were live.
        self.login(self.zhang)
        response = self.client.get(self.url)
        self.assertContains(response, "Preview — not published")
        self.assertContains(response, "Draft")

    def test_a_published_event_carries_no_banner(self):
        self.login(self.zhang)
        response = self.client.get(
            reverse("events:event_detail", args=[self.event.pk]))
        self.assertFalse(response.context["preview"])
        self.assertNotContains(response, "Preview — not published")

    def test_the_banner_points_at_the_page_where_the_status_is_changed(self):
        self.login(self.zhang)
        response = self.client.get(self.url)
        self.assertContains(response, reverse("events:event_update", args=[self.draft.pk]))

    def test_the_read_only_tier_is_not_offered_the_edit_link(self):
        # ⚠️ Edit is a 403 for this tier. A link that refuses whoever clicks it
        #    reads as a broken site, not as a page that is not for them — the
        #    same rule _event_nav.html follows.
        self.login(self.foundation_account())
        response = self.client.get(self.url)
        self.assertContains(response, "Preview — not published")
        self.assertNotContains(
            response, reverse("events:event_update", args=[self.draft.pk]))

    # --- the signup button --------------------------------------------------

    def test_the_signup_button_is_drawn_but_disabled(self):
        """The point of a preview is to show what will be there, inert.

        ⚠️ Matched as an **attribute on the tag**, not as the substring
           "disabled" anywhere on the page. The button component's class string
           carries `disabled:opacity-50 disabled:pointer-events-none` — Tailwind
           variants that style a disabled button and do not make one. A plain
           `assertContains(response, "disabled")` passes with the attribute
           deleted, which is the entire failure this test is here to catch.

        ⚠️ `disabled` is presentation either way. The refusal that counts is on
           the signup view, and it is asserted separately below.
        """
        self.login(self.zhang)
        response = self.client.get(self.url)
        page = response.content.decode()
        self.assertRegex(page, r"<button[^>]*\sdisabled[\s>]")
        self.assertIn("Sign up", page)
        self.assertIn("Volunteers will see this button once you publish", page)

    def test_the_preview_offers_no_working_signup_link(self):
        self.login(self.zhang)
        response = self.client.get(self.url)
        self.assertNotContains(
            response, reverse("events:event_signup", args=[self.draft.pk]))

    def test_signing_up_on_a_draft_is_still_refused_at_the_view(self):
        """⭐ The boundary, and the reason the button above may be cosmetic.

        Posting straight at the URL skips every button on every page, which is
        what a form posted from anywhere at all does.
        """
        self.login(self.zhang)
        role = self.draft.roles.first()
        response = self.client.post(
            reverse("events:event_signup", args=[self.draft.pk]),
            {"event_role": role.pk})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Participation.objects.filter(event_role=role).count(), 0)

    # --- the predicate it is keyed on ---------------------------------------

    def test_every_status_a_volunteer_may_see_renders_without_a_banner(self):
        """⚠️ Keyed on VISIBLE_TO_VOLUNTEERS, never on `== DRAFT`.

        Four statuses, one loop. Spelled as a comparison against DRAFT, the day
        somebody adds a status to that frozenset the new one would be published
        to everybody with no banner and nothing saying so.
        """
        self.login(self.zhang)
        for status in Event.VISIBLE_TO_VOLUNTEERS:
            with self.subTest(status=status):
                self.draft.status = status
                self.draft.save()
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.context["preview"])


class FeatherLayerTests(PageTestCase):
    """2026-08-06: one or two white feathers drift across the volunteer event list.

    Decoration, so almost nothing about it is testable from the server — how it
    moves lives in a rAF loop. What *is* testable is the half that fails
    silently, and all four of those failures look like "the feathers just
    stopped appearing", with no error anywhere:

      · the layer missing from the page;
      · a URL in it pointing at a file that is not there;
      · a file on disk that nothing points at;
      · the layer sitting inside the block HTMX swaps.
    """

    ASSET_DIR = Path(settings.BASE_DIR) / "core" / "static" / "core" / "img"

    def layer(self, response):
        """The `data-feathers` attribute of the drifting layer, or None."""
        found = re.search(
            r'class="feather-sky[^"]*"[^>]*data-feathers="([^"]*)"',
            response.content.decode())
        return found.group(1) if found else None

    def urls(self, response):
        raw = self.layer(response)
        return [u for u in (raw or "").split(",") if u]

    def get_list(self, **extra):
        self.login(self.lisi)
        return self.client.get(reverse("events:event_list"), **extra)

    def test_the_events_page_carries_the_layer(self):
        response = self.get_list()
        self.assertIsNotNone(self.layer(response))
        # ⚠️ It holds no words at all, so it must be out of the accessibility
        #    tree — otherwise a screen reader interrupts to announce nothing.
        self.assertContains(response, 'class="feather-sky" aria-hidden="true"')

    def test_the_public_home_page_carries_it_too(self):
        # 2026-08-06: the second of the two pages that have it. Anonymous —
        # the home page is public, and the layer must not depend on a login.
        response = self.client.get(reverse("home"))
        self.assertIsNotNone(self.layer(response))

    def test_the_home_page_asks_for_the_on_photo_variant(self):
        """⚠️ The home page never gets `.dark`, and its backdrop is a photo.

        It deliberately does not follow dark mode (a full-bleed photo with white
        text has no dark version), so `<html>` never carries `.dark` there and
        the `.dark .feather` rule cannot fire. Without this modifier the home
        page would get the hard 1px shadow meant for a near-white page, which on
        a darkened photo is very close to no shadow at all.
        """
        response = self.client.get(reverse("home"))
        self.assertContains(response, "feather-sky--on-photo")
        # And the events page must *not* have it: there the backdrop is ink-50
        # in light mode, and dark mode is handled by `.dark`.
        self.assertNotContains(self.get_list(), "feather-sky--on-photo")

    def test_both_pages_list_exactly_the_same_assets(self):
        """One component, so the two lists cannot drift apart.

        ⚠️ This is the whole reason the markup was pulled into
           `core/components/_feather_sky.html`. With a copy in each template,
           adding an eighth feather means remembering two places, and forgetting
           one raises nothing — one page would simply never show that shape.
        """
        self.assertEqual(
            self.urls(self.get_list()),
            self.urls(self.client.get(reverse("home"))),
        )

    def test_every_asset_on_disk_is_listed_and_every_listing_exists(self):
        """⭐ Both directions, because each one fails the same silent way.

        ⚠️ The URLs are built by `{% static %}` in the template rather than
           assembled in JavaScript, and that is not a style preference: in
           production static files are served by
           CompressedManifestStaticFilesStorage, which puts a content hash in
           every filename. A path built from an index in JS resolves in
           development and 404s on every one of them after deploy — with no
           error in the console, because a broken `new Image().src` is silent.
           `core.tests.AssetPathsComeFromTemplatesGuardTests` holds that line;
           this test holds the two lists in step.
        """
        on_disk = sorted(p.name for p in self.ASSET_DIR.glob("feather-*.webp"))
        self.assertTrue(on_disk, "no feather assets found — did they get deleted?")

        listed = self.urls(self.get_list())
        # Basenames, because the production names carry a hash the test cannot
        # predict; what matters is that the two sets describe the same files.
        self.assertEqual(len(listed), len(on_disk))
        for url in listed:
            self.assertTrue(url.startswith(settings.STATIC_URL), url)
            name = url.rsplit("/", 1)[-1]
            self.assertIn(name, on_disk)

    def test_the_layer_survives_a_filter(self):
        """⚠️ It has to live outside `#event-results`.

        The period filter swaps that block through HTMX. With the layer inside
        it, every filter would tear the feathers out by the roots and the JS
        would never put them back — and the page would look completely normal.
        """
        fragment = self.get_list(HTTP_HX_REQUEST="true")
        self.assertIsNone(
            self.layer(fragment),
            "the swapped fragment carries the layer, so filtering will destroy it")

    def test_the_other_pages_do_not_have_it(self):
        # 2026-08-06: this page only. Asserted rather than assumed, because the
        # layer is fixed-position — dropped into a base template by mistake it
        # would show up everywhere including the attendance and report pages.
        self.login(self.lisi)
        self.assertIsNone(
            self.layer(self.client.get(reverse("events:my_participations"))))
        detail = self.client.get(reverse("events:event_detail", args=[self.event.pk]))
        self.assertIsNone(self.layer(detail))


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
        self.sender = get_user_model().objects.create_user(email="zhang@example.com", password="x")
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

    # --- what happens when the provider says no ---------------------------
    #
    # ⭐ These pin the defect found on 2026-08-17, the day the mail provider
    #    changed to a free tier: the sending used to happen inside
    #    transaction.atomic(), so a quota refusing message 47 rolled the record
    #    back — 46 people had an email in their inbox and the database said
    #    nobody had been told. See 03-roadmap.md's 计划外记录 for C3.3.

    def three_adults(self):
        people = [
            make_person(name, birth_date=datetime.date(1980, 1, 1),
                        email=f"{n}@example.com")
            for n, name in enumerate(["李", "王", "陈"])
        ]
        for person in people:
            self.signup(person)
        return people

    def test_a_refused_message_lands_in_failed_and_not_in_recipients(self):
        people = self.three_adults()
        notification = notify_event_change(
            self.event,
            reason=EventNotification.Reason.TIME_CHANGED,
            message="Moved to Sunday.",
            sent_by=self.sender,
            backend=RefusingEmailBackend(allowance=1),
        )
        self.assertEqual(
            [row.contact for row in notification.recipients.all()], people[:1])
        self.assertEqual(
            [row.contact for row in notification.failed.all()], people[1:])

    def test_the_record_survives_the_quota_running_out_partway(self):
        # The regression itself. What must exist afterwards is the record —
        # the messages that already went out cannot be un-sent, so a rollback
        # does not undo the send, it only destroys the evidence of it.
        self.three_adults()
        notification = notify_event_change(
            self.event,
            reason=EventNotification.Reason.TIME_CHANGED,
            message="Moved to Sunday.",
            sent_by=self.sender,
            backend=RefusingEmailBackend(allowance=1),
        )
        self.assertEqual(EventNotification.objects.count(), 1)
        self.assertEqual(notification.recipients.count(), 1)
        self.assertEqual(notification.failed.count(), 2)

    def test_everybody_signed_up_lands_in_exactly_one_of_the_three_groups(self):
        told = self.three_adults()
        missed = make_person("无", birth_date=datetime.date(1980, 1, 1))
        Participation.objects.create(
            contact=missed, event_role=make_role(self.event, "welcome"))
        notification = notify_event_change(
            self.event,
            reason=EventNotification.Reason.TIME_CHANGED,
            message="Moved to Sunday.",
            sent_by=self.sender,
            backend=RefusingEmailBackend(allowance=2),
        )
        groups = [
            {row.contact for row in notification.recipients.all()},
            {row.contact for row in notification.failed.all()},
            {row.contact for row in notification.unreachable.all()},
        ]
        self.assertEqual(set.union(*groups), {*told, missed})
        # Exclusive: the sizes add up only if nobody is in two of them.
        self.assertEqual(sum(len(group) for group in groups), 4)
        self.assertEqual(groups[2], {missed})

    def test_a_backend_answering_short_records_the_rest_as_failed(self):
        # A broken backend is not a reason to lose people. "Check on these" is
        # recoverable; "they were told" is not.
        class SilentBackend:
            def send(self, messages):
                return []

        people = self.three_adults()
        notification = notify_event_change(
            self.event,
            reason=EventNotification.Reason.TIME_CHANGED,
            message="Moved to Sunday.",
            sent_by=self.sender,
            backend=SilentBackend(),
        )
        self.assertEqual(notification.recipients.count(), 0)
        self.assertEqual(
            {row.contact for row in notification.failed.all()}, set(people))

    def test_the_sending_happens_outside_the_transaction(self):
        # ⭐ The other half of the 2026-08-17 fix, and the half no assertion
        #    about counts can reach: with the send inside transaction.atomic(),
        #    anything raising partway rolled back a record of emails that had
        #    already left, and a batch of a hundred held a database transaction
        #    open for as long as the mail server took.
        #
        # Depth, not a boolean: every test here already runs inside a
        # transaction, so "are we in one" answers True either way. An atomic
        # block around the send would push one more savepoint than the caller
        # is standing in.
        depth = []

        class DepthProbingBackend:
            def send(self, messages):
                from core.notifications.base import DeliveryResult

                depth.append(len(connection.savepoint_ids))
                return [DeliveryResult(message=m, accepted=True) for m in messages]

        self.three_adults()
        outside = len(connection.savepoint_ids)
        notify_event_change(
            self.event,
            reason=EventNotification.Reason.TIME_CHANGED,
            message="Moved to Sunday.",
            sent_by=self.sender,
            backend=DepthProbingBackend(),
        )
        self.assertEqual(depth, [outside])

    def test_who_failed_does_not_change_after_a_later_notice_succeeds(self):
        # Same snapshot rule as unreachable: this record says what happened at
        # 19:04, and sending again makes a second record, not a correction.
        people = self.three_adults()
        first = notify_event_change(
            self.event,
            reason=EventNotification.Reason.TIME_CHANGED,
            message="Moved to Sunday.",
            sent_by=self.sender,
            backend=RefusingEmailBackend(allowance=0),
        )
        self.notify()
        first.refresh_from_db()
        self.assertEqual(
            {row.contact for row in first.failed.all()}, set(people))
        self.assertEqual(first.recipients.count(), 0)

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

    @override_settings(NOTIFICATION_BACKEND="events.tests.RefusingEmailBackend")
    def test_a_notice_that_could_not_be_sent_says_so_and_names_who(self):
        # ⭐ The page has to be the place this becomes visible. A green
        #    "Notified 1" over a message that never left is the same silent
        #    failure the "Cannot be reached" group exists to prevent — and with
        #    a provider on a daily quota it is the likelier of the two.
        Participation.objects.create(contact=self.lisi.contact, event_role=self.role)
        self.login(self.zhang)
        response = self.client.post(
            reverse("events:event_notify", args=[self.event.pk]),
            {"reason": EventNotification.Reason.TIME_CHANGED, "message": "Moved."},
            follow=True)
        self.assertContains(response, "could not be sent")
        self.assertContains(response, str(self.lisi.contact))
        notification = EventNotification.objects.get()
        self.assertEqual(notification.recipients.count(), 0)
        self.assertEqual(notification.failed.count(), 1)

    def test_a_message_past_the_cap_is_a_form_error_and_sends_nothing(self):
        # ⚠️ The assertion that matters is the second one. A form error is
        #    cosmetic if the notice went out anyway — and this is the one page
        #    in the project whose side effect reaches every volunteer's inbox,
        #    so "refused" has to mean "nobody was written to".
        Participation.objects.create(contact=self.lisi.contact, event_role=self.role)
        self.login(self.zhang)
        response = self.client.post(
            reverse("events:event_notify", args=[self.event.pk]),
            {"reason": EventNotification.Reason.TIME_CHANGED,
             "message": "x" * (LONG_TEXT + 1)})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(EventNotification.objects.exists())


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
            # ⚠️ Named, not `filter(status=COMPLETED).first()`. That version
            #    assumed there was only ever one finished event, and it broke
            #    the day seed_demo grew a dozen filler events for the scrolling
            #    demo — `.first()` started returning scenery that was never
            #    meant to carry this scenario.
            past = Event.objects.filter(name="Last month's distribution").first()
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

    def as_role(self, role):
        # Logs in by address, looked up from seed_demo's own dictionary rather
        # than written out here — see DEMO_ACCOUNTS for why a second copy of
        # these addresses is the thing that breaks this walk.
        self.assertTrue(
            self.client.login(email=demo_login(role), password=self.PASSWORD))

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
        self.assertEqual(grant.granted_by.email, demo_login("foundation_admin"))

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

    def test_a_volunteer_sees_published_events_including_the_full_one(self):
        # 2026-08-17: the list is "what is on", not "what you can join". A full
        # event is on it, saying it is full; a draft is not on it at all,
        # because that is a different question — whether it is published.
        self.as_role("volunteer_adult")
        response = self.client.get(reverse("events:event_list"))
        listed = {event.name for event in response.context["events"]}
        self.assertIn("Saturday distribution", listed)
        self.assertIn("English corner (full)", listed)            # confirmed
        self.assertNotIn("Christmas distribution (not published yet)", listed)     # draft

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

    def test_all_six_columns_are_there_with_no_report(self):
        # 2026-08-05: Ministry and Ends give up their columns **only while the
        # report is open**. Closed, this is the page it always was — which is
        # easier to remember than "two columns gone and a third one shortened".
        self.login(self.zhang)
        response = self.client.get(self.url())
        for column in ("Event", "Ministry", "Starts", "Ends", "Status"):
            self.assertContains(response, f"<th>{column}</th>")

    def test_two_columns_step_aside_for_the_report(self):
        self.login(self.zhang)
        response = self.client.get(self.url(), {"report": "1"})
        self.assertNotContains(response, "<th>Ministry</th>")
        self.assertNotContains(response, "<th>Ends</th>")
        # Starts stays: a list filtered to August with nothing on it saying
        # August is the one thing dropping all three would have cost (D27).
        self.assertContains(response, "<th>Starts</th>")

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


class NoShowPageTests(PageTestCase):
    """The button, and the two things it must not do."""

    def setUp(self):
        super().setUp()
        self.row = Participation.objects.create(
            contact=self.lisi.contact, event_role=self.role)

    def url(self):
        return reverse("events:event_attendance", args=[self.event.pk])

    def post(self, **extra):
        return self.client.post(
            self.url(), {"participation": self.row.pk, "action": "absent", **extra})

    def test_the_button_is_drawn_for_the_ministrys_own_admin(self):
        self.login(self.zhang)
        self.assertContains(self.client.get(self.url()), 'value="absent"')

    def test_marking_absent_from_the_page(self):
        self.login(self.zhang)
        self.post()
        self.row.refresh_from_db()
        self.assertEqual(self.row.status, Participation.Status.ABSENT)

    def test_the_read_only_tier_is_refused(self):
        # ⚠️ Not drawing the button is interface. This is the boundary — a form
        #    posted from anywhere at all arrives at the view the same shape.
        admin = self.account("fadmin2", "方", birth_date=datetime.date(1980, 1, 1))
        admin.groups.add(foundation_admin_group())
        self.login(admin)
        self.assertNotContains(self.client.get(self.url()), 'value="absent"')
        self.assertEqual(self.post().status_code, 403)
        self.row.refresh_from_db()
        self.assertEqual(self.row.status, Participation.Status.REGISTERED)

    def test_a_refusal_is_shown_rather_than_swallowed(self):
        # A button that quietly does nothing reads as a broken page: the person
        # clicks it again, then goes looking for the row somewhere else.
        record_hours(self.row, Decimal("3.00"))
        self.login(self.zhang)
        response = self.client.post(
            self.url(), {"participation": self.row.pk, "action": "absent"},
            follow=True)
        self.assertContains(response, "Clear the hours first")
        self.row.refresh_from_db()
        self.assertEqual(self.row.status, Participation.Status.ATTENDED)


class UndoAttendanceTests(TestCase):
    """Undoing a check-in clicked on the wrong row.

    ⚠️ This gap was open, and **written down as open**, from 2026-08-05 until
       2026-08-08: mark_absent()'s docstring named it and phase-c.md's
       known-gaps table carried it, both saying the fix would be an explicit
       Undo rather than a second job for the No-show button. This class is that
       fix, so it also guards the separation.
    """

    def setUp(self):
        self.event = make_event()
        self.role = make_role(self.event, "lifting")
        self.wang = make_person("Wang", birth_date=datetime.date(1980, 5, 5))
        self.participation = Participation.objects.create(
            contact=self.wang, event_role=self.role)

    def test_a_checked_in_row_goes_all_the_way_back(self):
        check_in(self.participation)
        self.assertTrue(undo_attendance(self.participation))
        self.participation.refresh_from_db()
        self.assertIsNone(self.participation.checked_in_at)
        self.assertEqual(self.participation.status, Participation.Status.REGISTERED)

    def test_a_checked_out_row_loses_both_timestamps_and_the_hours(self):
        """⚠️ The hours have to go, and it is not a choice: the constraint
        `participation_hours_only_when_attended` means a row carrying hours
        **must** be attended, so "registered, 3 hours" cannot be stored at all.
        Any version of undo that kept them would have to leave the row saying
        somebody attended."""
        check_in(self.participation)
        check_out(self.participation)
        self.participation.refresh_from_db()
        self.assertIsNotNone(self.participation.hours)

        undo_attendance(self.participation)
        self.participation.refresh_from_db()
        self.assertIsNone(self.participation.checked_in_at)
        self.assertIsNone(self.participation.checked_out_at)
        self.assertIsNone(self.participation.hours)
        self.assertEqual(self.participation.status, Participation.Status.REGISTERED)

    def test_the_deleted_hours_are_recoverable_from_the_history(self):
        """⚠️ This is half of what makes deleting the hours acceptable rather
        than reckless — the other half is that the button names the number
        before removing it. Nothing in the table records whether an hours
        figure came from the clock or from a person, so undo cannot spare the
        hand-typed ones; it can only make sure they are not *gone*."""
        record_hours(self.participation, Decimal("3.00"))
        undo_attendance(self.participation)
        past = [h.hours for h in self.participation.history.all()]
        self.assertIn(Decimal("3.00"), past)

    def test_a_cancelled_row_keeps_its_status(self):
        """⚠️ Only `attended` goes back to `registered`. Somebody who signed in
        and then pulled out is a real state, and moving that row to registered
        would erase the second fact in order to undo the first — the same
        reasoning that makes cancel() a status change rather than a delete."""
        check_in(self.participation)
        cancel(self.participation)
        undo_attendance(self.participation)
        self.participation.refresh_from_db()
        self.assertIsNone(self.participation.checked_in_at)
        self.assertEqual(self.participation.status, Participation.Status.CANCELLED)

    def test_undoing_a_row_with_nothing_on_it_changes_nothing(self):
        # Safe to call twice, and says so — the caller can tell a real undo
        # from a second click.
        self.assertFalse(undo_attendance(self.participation))

    def test_it_is_not_mark_absent(self):
        """⚠️ The separation the whole gap was left open for. "I clicked the
        wrong row" and "they did not come" are different facts; one button
        doing both would put every mis-click into the no-show rate, which is a
        number nobody can audit afterwards."""
        check_in(self.participation)
        undo_attendance(self.participation)
        self.participation.refresh_from_db()
        self.assertNotEqual(self.participation.status, Participation.Status.ABSENT)

    def test_after_undoing_the_row_can_be_marked_absent(self):
        """The two do compose, in the order that makes sense: undo the mistake,
        then record what actually happened. mark_absent() refuses a row with a
        check-in on it, so before undo existed this was simply unreachable."""
        check_in(self.participation)
        undo_attendance(self.participation)
        mark_absent(self.participation)
        self.participation.refresh_from_db()
        self.assertEqual(self.participation.status, Participation.Status.ABSENT)


class ClearHoursTests(TestCase):
    """Removing an hours figure without touching anything else.

    ⚠️ Empty is not zero, and until 2026-08-08 only zero was reachable: the
       form field is a required number. The attendance page already draws the
       two differently — 0 means somebody looked and recorded that no time was
       worked, "—" means nobody has recorded anything — so a figure typed by
       mistake could only ever be corrected to a wrong-but-plausible 0.
    """

    def setUp(self):
        self.event = make_event()
        self.role = make_role(self.event, "lifting")
        self.wang = make_person("Wang", birth_date=datetime.date(1980, 5, 5))
        self.participation = Participation.objects.create(
            contact=self.wang, event_role=self.role)

    def test_hours_go_back_to_nothing_rather_than_to_zero(self):
        record_hours(self.participation, Decimal("3.00"))
        self.assertTrue(clear_hours(self.participation))
        self.participation.refresh_from_db()
        self.assertIsNone(self.participation.hours)

    def test_the_status_is_left_alone(self):
        """⚠️ "They turned up" and "they worked N hours" are two assertions.
        A row reading attended with no hours is ordinary — somebody checked in
        and the hours are not worked out yet. Reverting the status here would
        delete the first fact in order to undo the second; clearing everything
        at once is undo_attendance(), and it is a different button on purpose.
        """
        check_in(self.participation)
        check_out(self.participation)
        clear_hours(self.participation)
        self.participation.refresh_from_db()
        self.assertEqual(self.participation.status, Participation.Status.ATTENDED)
        self.assertIsNotNone(self.participation.checked_in_at)

    def test_clearing_twice_changes_nothing(self):
        self.assertFalse(clear_hours(self.participation))


class UndoFromThePageTests(PageTestCase):
    """The two new buttons, and who may press them."""

    def setUp(self):
        super().setUp()
        self.row = Participation.objects.create(
            contact=self.lisi.contact, event_role=self.role)

    def url(self):
        return reverse("events:event_attendance", args=[self.event.pk])

    def post(self, action):
        return self.client.post(
            self.url(), {"participation": self.row.pk, "action": action})

    def test_undo_is_not_offered_on_a_row_with_nothing_to_undo(self):
        """⚠️ A button that does nothing when pressed reads as a broken page —
        the person presses it again, then goes looking for the row elsewhere.
        The service is idempotent as well, but that is the backstop, not the
        reason this is hidden."""
        self.login(self.zhang)
        self.assertNotContains(self.client.get(self.url()), 'value="undo"')

    def test_undo_appears_once_somebody_is_checked_in(self):
        check_in(self.row)
        self.login(self.zhang)
        self.assertContains(self.client.get(self.url()), 'value="undo"')

    def test_undoing_from_the_page(self):
        check_in(self.row)
        self.login(self.zhang)
        self.post("undo")
        self.row.refresh_from_db()
        self.assertIsNone(self.row.checked_in_at)
        self.assertEqual(self.row.status, Participation.Status.REGISTERED)

    def test_the_confirmation_names_the_hours_it_is_about_to_delete(self):
        """⚠️ Not "are you sure". Undo removes the hours, and hours are the one
        authoritative value in this system a human is answerable for having
        typed — the sentence has to say that is what is happening."""
        check_in(self.row)
        check_out(self.row)
        self.login(self.zhang)
        page = self.client.get(self.url()).content.decode()
        self.assertIn("hours recorded on this row", page)

    def test_clear_hours_is_offered_only_when_there_are_hours(self):
        self.login(self.zhang)
        self.assertNotContains(self.client.get(self.url()), 'value="clear_hours"')
        record_hours(self.row, Decimal("3.00"))
        self.assertContains(self.client.get(self.url()), 'value="clear_hours"')

    def test_clearing_hours_from_the_page(self):
        record_hours(self.row, Decimal("3.00"))
        self.login(self.zhang)
        self.post("clear_hours")
        self.row.refresh_from_db()
        self.assertIsNone(self.row.hours)

    def test_the_read_only_tier_is_refused_both(self):
        """⚠️ Not drawing the buttons is interface. This is the boundary — a
        form posted from anywhere at all arrives at the view the same shape."""
        check_in(self.row)
        admin = self.account("fadmin3", "方", birth_date=datetime.date(1980, 1, 1))
        admin.groups.add(foundation_admin_group())
        self.login(admin)
        page = self.client.get(self.url()).content.decode()
        self.assertNotIn('value="undo"', page)
        for action in ("undo", "clear_hours"):
            with self.subTest(action=action):
                self.assertEqual(self.post(action).status_code, 403)
        self.row.refresh_from_db()
        self.assertIsNotNone(self.row.checked_in_at)

    def test_a_volunteer_cannot_undo_their_own_row(self):
        check_in(self.row)
        self.login(self.lisi)
        self.assertEqual(self.post("undo").status_code, 403)
        self.row.refresh_from_db()
        self.assertIsNotNone(self.row.checked_in_at)


class ManageListReportPageTests(PageTestCase):
    """The filter and the report panel on the management list (D27).

    ⭐ The invariant under test throughout: **the panel describes the list next
       to it**. Both tiers reach it, and neither can see a figure covering an
       event its own list does not show.
    """

    def url(self):
        return reverse("events:event_manage_list")

    def foundation_admin(self):
        admin = self.account("fadmin", "方", birth_date=datetime.date(1980, 1, 1))
        admin.groups.add(foundation_admin_group())
        return admin

    def test_the_report_is_not_computed_until_it_is_asked_for(self):
        # Thirteen figures are a dozen aggregate queries, and most of the time
        # somebody changing a date is only reading the list.
        self.login(self.zhang)
        self.assertNotContains(self.client.get(self.url()), "Recorded hours")
        self.assertContains(
            self.client.get(self.url(), {"report": "1"}), "Recorded hours")

    def test_changing_the_filter_without_asking_again_drops_the_report(self):
        # A report computed for last month's filter, sitting beside this
        # month's list, is worse than no report — the two read as one page.
        self.login(self.zhang)
        response = self.client.get(self.url(), {"ministry": self.pantry.pk})
        self.assertNotContains(response, "Recorded hours")

    def test_a_ministry_admin_only_ever_reports_on_their_own(self):
        theirs = make_event(ministry=self.tax, owner=self.other_admin.contact,
                            name="Somebody else's event")
        make_role(theirs, "greeting")
        self.login(self.zhang)
        response = self.client.get(self.url(), {"report": "1"})
        self.assertNotContains(response, "Somebody else&#x27;s event")
        # One event in the list, so one event in the panel beside it.
        self.assertEqual(response.context["report"]["figures"]["events"], 1)

    def test_a_ministry_admin_is_only_offered_their_own_ministries(self):
        # Offered every ministry, picking one and getting an empty list reads
        # as a broken filter rather than as scope.
        self.login(self.zhang)
        response = self.client.get(self.url())
        offered = response.context["period"].fields["ministry"].queryset
        self.assertEqual(list(offered), [self.pantry])

    def test_the_foundation_tier_reports_over_every_ministry(self):
        make_event(ministry=self.tax, owner=self.other_admin.contact)
        self.login(self.foundation_admin())
        response = self.client.get(self.url(), {"report": "1"})
        self.assertEqual(response.context["report"]["figures"]["events"], 2)
        # And is offered all of them, because its list holds all of them.
        offered = response.context["period"].fields["ministry"].queryset
        self.assertEqual(list(offered), [self.pantry, self.tax])

    def test_the_report_follows_the_ministry_filter(self):
        make_event(ministry=self.tax, owner=self.other_admin.contact)
        self.login(self.foundation_admin())
        response = self.client.get(
            self.url(), {"report": "1", "ministry": self.tax.pk})
        self.assertEqual(response.context["report"]["figures"]["events"], 1)

    def test_the_report_follows_the_date_window(self):
        make_event(ministry=self.pantry, owner=self.zhang.contact,
                   name="Long ago", start_time=NOW - 400 * DAY,
                   end_time=NOW - 400 * DAY + HOUR)
        self.login(self.zhang)
        window = (local_now() - datetime.timedelta(days=7)).date()
        response = self.client.get(
            self.url(), {"report": "1", "start": window.isoformat()})
        self.assertEqual(response.context["report"]["figures"]["events"], 1)

    def test_the_htmx_fragment_carries_the_report_too(self):
        # ⚠️ The filter's hx-target is #event-results, and the panel lives
        #    inside it. Rendered outside, filtering would leave a stale report
        #    on the page — and HTMX reports nothing when a target is missing.
        self.login(self.zhang)
        response = self.client.get(
            self.url(), {"report": "1"}, headers={"HX-Request": "true"})
        self.assertContains(response, 'id="event-results"')
        self.assertContains(response, "Recorded hours")


class PaginationTests(PageTestCase):
    """20 / 20 / 50, and the two ways paging can lie (2026-08-05)."""

    # ⚠️ The `past=True` half of this helper went with Past Events (2026-08-17).
    #    Kept it as a forward-only maker rather than leaving a branch nothing
    #    takes — a helper with an unreachable mode reads as "somebody will need
    #    this", and the next person writes a test around it.
    def make_many(self, count, *, ministry=None):
        for index in range(count):
            offset = (index + 2) * DAY
            make_event(
                ministry=ministry or self.pantry, name=f"Filler {index}",
                owner=self.zhang.contact,
                start_time=NOW + offset, end_time=NOW + offset + HOUR,
                status=Event.Status.OPEN,
            )

    def test_the_volunteer_list_holds_twenty(self):
        self.make_many(25)
        self.login(self.lisi)
        response = self.client.get(reverse("events:event_list"))
        self.assertEqual(len(response.context["events"]), 20)
        # ⚠️ The count is the whole filtered set, not the page. "20 events"
        #    under a filter that matched 26 answers a question nobody asked.
        self.assertEqual(response.context["total"], 26)

    def test_the_management_list_holds_fifty(self):
        self.make_many(55)
        self.login(self.zhang)
        response = self.client.get(reverse("events:event_manage_list"))
        self.assertEqual(len(response.context["events"]), 50)

    def test_no_event_is_repeated_or_skipped_across_pages(self):
        """⚠️ Ordering by `-start_time` alone is not a total order.

        Twenty-five events starting at the same instant have no defined order
        between them, so Postgres may hand back a different sequence for page 1
        and page 2 — a row appears twice, or vanishes, and nothing reports it.
        The tiebreaker is `-pk`.
        """
        same_instant = NOW + 5 * DAY
        for index in range(25):
            make_event(ministry=self.pantry, name=f"Simultaneous {index}",
                       owner=self.zhang.contact, start_time=same_instant,
                       end_time=same_instant + HOUR)
        self.login(self.lisi)
        url = reverse("events:event_list")
        seen = []
        for page in (1, 2):
            response = self.client.get(url, {"page": page})
            seen += [event.pk for event in response.context["events"]]
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(len(seen), 26)

    def test_paging_keeps_the_filter(self):
        # ⚠️ The link is built by {% querystring %}, which preserves everything
        #    else. Hand-writing "?page=2" drops the filter — and the symptom is
        #    "I turned the page and my filter disappeared".
        self.make_many(25, ministry=self.tax)
        self.login(self.lisi)
        response = self.client.get(
            reverse("events:event_list"), {"ministry": self.tax.pk, "page": 2})
        self.assertContains(response, f"ministry={self.tax.pk}")
        for event in response.context["events"]:
            self.assertEqual(event.ministry_id, self.tax.pk)

    def test_the_report_covers_the_filter_not_the_page(self):
        # ⭐ The invariant restated for pagination (D27). A figure that moved
        #    when you clicked Next would mean nothing at all.
        self.make_many(55)
        self.login(self.zhang)
        response = self.client.get(
            reverse("events:event_manage_list"), {"report": "1", "page": 2})
        self.assertEqual(len(response.context["events"]), 6)
        self.assertEqual(response.context["report"]["figures"]["events"], 56)


class FullReportPageTests(PageTestCase):
    """The page the panel sends you to, and the artefact somebody prints."""

    def url(self):
        return reverse("events:ministry_report")

    def test_it_is_not_paginated(self):
        # Half an artefact is not an artefact.
        for index in range(60):
            make_event(ministry=self.pantry, name=f"Filler {index}",
                       owner=self.zhang.contact,
                       start_time=NOW + (index + 2) * DAY,
                       end_time=NOW + (index + 2) * DAY + HOUR)
        self.login(self.zhang)
        response = self.client.get(self.url())
        self.assertEqual(response.context["total"], 61)
        self.assertEqual(len(response.context["events"]), 61)

    def test_the_event_list_is_printed_under_the_figures(self):
        # "一份文件自足" — the board reads what the numbers are about.
        self.login(self.zhang)
        self.assertContains(self.client.get(self.url()), self.event.name)

    def test_it_says_what_it_covers(self):
        # ⚠️ A printed report with no statement of its scope gets read as
        #    "everything".
        self.login(self.zhang)
        response = self.client.get(self.url(), {"ministry": self.pantry.pk})
        self.assertContains(response, "Food Pantry")

    def test_it_is_scoped_the_same_way_the_list_is(self):
        theirs = make_event(ministry=self.tax, owner=self.other_admin.contact,
                            name="Somebody else's event")
        self.login(self.zhang)
        response = self.client.get(self.url())
        self.assertNotContains(response, "Somebody else&#x27;s event")
        self.assertEqual(response.context["report"]["figures"]["events"], 1)
        self.assertNotIn(theirs, response.context["events"])

    def test_a_plain_volunteer_is_refused(self):
        self.login(self.lisi)
        self.assertEqual(self.client.get(self.url()).status_code, 403)

    def test_the_panel_links_to_it_carrying_the_filter(self):
        # ⚠️ Without the query string the full report shows a *different*
        #    filter's numbers, and looks exactly the same doing it.
        self.login(self.zhang)
        response = self.client.get(
            reverse("events:event_manage_list"),
            {"report": "1", "ministry": self.pantry.pk})
        self.assertContains(response, f"{self.url()}?report=1&amp;ministry={self.pantry.pk}")


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
        self.assertContains(response, "All Events")
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
        self.assertContains(response, "Events I Manage")

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
        self.assertIn("All Events", page)

    # --- ?scope=all: the other view, for somebody who holds both hats --------

    def both_hats(self):
        """zhang administers the pantry **and** is in the foundation group."""
        self.zhang.groups.add(foundation_admin_group())
        user = type(self.zhang).objects.get(pk=self.zhang.pk)
        self.login(user)
        return user

    def someone_elses_event(self):
        """An event belonging to a ministry zhang does not administer."""
        return make_event(ministry=self.tax, owner=self.other_admin.contact,
                          name="Tax clinic nobody here runs")

    def test_scope_all_shows_another_ministrys_events_to_somebody_with_both_hats(self):
        """⭐ The gap this closed: the foundation-wide **read** authority they
        already held had no entrance for them at all, because the default view
        (rightly) keeps their own ministries editable.
        """
        theirs = self.someone_elses_event()
        self.both_hats()
        response = self.client.get(reverse("events:event_manage_list"), {"scope": "all"})
        self.assertContains(response, theirs.name)
        self.assertFalse(response.context["can_manage"])

    def test_scope_all_does_not_hand_over_a_single_write(self):
        """⚠️ The whole safety of this parameter. Read widens; write does not.

        The POST is on another ministry's event, from the page that is now
        allowed to *show* it.
        """
        theirs = self.someone_elses_event()
        self.both_hats()
        response = self.client.post(
            f"{reverse('events:event_manage_list')}?scope=all",
            {"event": theirs.pk, "status": Event.Status.CANCELLED})
        self.assertEqual(response.status_code, 403)
        theirs.refresh_from_db()
        self.assertNotEqual(theirs.status, Event.Status.CANCELLED)

    def test_scope_all_is_ignored_for_somebody_not_in_the_tier(self):
        # A plain ministry admin typing the parameter gets their own ministries,
        # editable, exactly as before — not everybody's.
        theirs = self.someone_elses_event()
        self.login(self.zhang)
        response = self.client.get(reverse("events:event_manage_list"), {"scope": "all"})
        self.assertTrue(response.context["can_manage"])
        self.assertNotContains(response, theirs.name)

    def test_the_filter_carries_the_scope_so_it_survives_a_click(self):
        """⚠️ A method="get" form throws away the action's query string and sends
        its own fields instead. Without a hidden field the page silently narrows
        back to their own ministries on the first Filter — and the list looks
        completely normal, only shorter.
        """
        self.both_hats()
        page = self.client.get(
            reverse("events:event_manage_list"), {"scope": "all"}).content.decode()
        self.assertIn('name="scope" value="all"', page)

    def test_the_full_report_stays_foundation_wide(self):
        # It shares _scoped_events(), and the panel links to it with the whole
        # query string — so a report that quietly covered a different set of
        # events than the list it was opened from would be the one figure nobody
        # could check.
        theirs = self.someone_elses_event()
        self.both_hats()
        response = self.client.get(reverse("events:ministry_report"), {"scope": "all"})
        self.assertContains(response, theirs.name)

    def test_a_ministry_admin_still_sees_the_managing_label(self):
        self.login(self.zhang)
        page = self.client.get(reverse("events:event_list")).content.decode()
        self.assertIn("Events I Manage", page)
        self.assertNotIn("All Events", page)

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

    def test_the_full_size_picture_is_never_held_twice(self):
        """⚠️ 2026-08-13. The same shape as gallery's, and the same measurement.

        `convert()` into the mode an image is already in still returns a
        full-size copy — 72 MB for a phone photograph, spent to produce pixels
        identical to the ones it was handed. Dropping it took this path from
        198 MB of peak memory to 113 MB, on a 512 MB instance already holding
        two workers.

        ⚠️ The one duplicate that used to remain was Pillow's own —
           `exif_transpose` copies when there is no orientation tag to act on —
           and it went later the same day, when a batch of ten photographs
           killed the instance a second time. `in_place=True` is what removed
           it; the measurement and the reasoning live over the same call in
           gallery/services.py, and the byte-for-byte check was run on this
           path too.

        ⚠️ A regression here is invisible everywhere else — the stored image is
           byte-for-byte the same either way. It shows up as a restart under
           load, which is how the front page's version of this was found.
        """
        from unittest import mock

        size = (3000, 2000)
        seen = []

        def spy_on(name):
            real = getattr(PILImage.Image, name)

            def spy(self, *args, **kwargs):
                seen.append((name, self.size))
                return real(self, *args, **kwargs)
            return mock.patch.object(PILImage.Image, name, spy)

        with spy_on("copy"), spy_on("convert"):
            form = self.upload(a_photo(size=size))
            self.assertTrue(form.is_valid(), form.errors)

        at_full_size = [name for name, seen_size in seen if seen_size == size]
        self.assertEqual(
            at_full_size, [],
            f"the original was duplicated before being resized "
            f"({at_full_size}); each of those is 72 MB on a phone photograph")

    def test_a_large_photograph_is_never_decoded_at_its_full_size(self):
        """⚠️ The guard on `core.images.draft_to`, on this side of it.

        The geometry is shared with gallery; the policy is not (see
        `normalise_event_image`'s docstring on why the two normalise functions
        stay separate). So the shared arithmetic has its own tests in
        `core.tests`, and each caller still has to show that it actually calls
        it — an import is not a use.

        ⚠️ This started as a *copy* of the arithmetic rather than a call, held
           together by exactly this check in each app. It was not enough: the
           two copies produced pictures a pixel apart within the day, and both
           guards stayed green throughout, because both were only ever asked
           "was this decoded smaller?" and on both the answer was yes. The
           lesson is in the check's limits, not in the check — it can tell you
           the decode was cheapened, never that it was cheapened correctly.

        What costs the memory is the pixel count after decoding, and a small
        file can hold a very large picture; the 10 MB upload limit does not
        protect against this and never did.

        ⚠️ Delete the `draft_to` call and nothing else goes red. The stored image
           is the right size, the right format and visually the same picture —
           the only difference is a number that surfaces as an instance restart
           under load.

        ⚠️ The likeliest way to break this is not deleting the call but giving
           it a **square** target: Pillow picks its scale from
           `min(w // tw, h // th)`, so a square target lets the short edge pin
           it at 1 and the call silently does nothing.
        """
        from unittest import mock

        native = (4000, 3000)
        seen = []
        real = PILImage.Image.resize

        def spy(inner, *args, **kwargs):
            seen.append(inner.size)
            return real(inner, *args, **kwargs)

        with mock.patch.object(PILImage.Image, "resize", spy):
            form = self.upload(a_photo(size=native))
            self.assertTrue(form.is_valid(), form.errors)

        self.assertNotEqual(seen, [], "nothing was resized — has the pipeline moved?")
        self.assertLess(
            max(seen[0]), max(native),
            f"a {native[0]}x{native[1]} photograph reached the resize still at "
            f"its full size ({seen[0]}), so it was decoded whole — has the "
            f"draft_to call gone, or is its target square again?")

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
class EventRowTests(PageTestCase):
    """The list rows: the whole row is one link, and every row is the same height.

    ⚠️ The geometry itself (112×112 thumbnails, equal row heights) is CSS and is
       verified in a browser, not here — a Django test cannot measure a box. What
       *can* be pinned here is everything the CSS depends on: that the row carries
       the class the height is attached to, that a missing location still renders a
       line (or the row would come out short inside a fixed height), and that the
       name is not an `<a>` any more.
    """

    def rows_html(self, url_name, **params):
        self.login(self.lisi)
        return self.client.get(reverse(url_name), params).content.decode()

    def test_the_whole_row_is_a_link_to_the_event(self):
        html = self.rows_html("events:event_list")
        self.assertIn("event-row-link", html)
        self.assertIn(reverse("events:event_detail", args=[self.event.pk]), html)

    def test_there_is_exactly_one_link_per_row(self):
        """⭐ The accessibility claim behind the stretched link.

        Wrapping the whole card in an `<a>` would have been simpler and would put
        the title, the ministry badge and the times inside one link — which a
        screen reader then reads out as a single run-on name. One link whose
        accessible name is only the event's name is the point of doing it this way.
        """
        html = self.rows_html("events:event_list")
        row = re.search(r'<li class="event-row.*?</li>', html, re.S).group(0)
        self.assertEqual(row.count("<a "), 1)
        # And its name is the event's name, carried by the sr-only span.
        # ⚠️ `[^>]*` 是 2026-08-19 放宽的：那个链接上现在挂着 hx-/x- 属性（日程开着
        #    时就地打开详情），于是 class 后面不再紧跟 `>`。放宽的是**属性**，
        #    钉住的仍然是那件要紧的事 —— 这个链接里除了 sr-only 那一个 span
        #    什么都没有，它的可访问名就只有活动名。
        self.assertRegex(row, r'class="event-row-link"[^>]*>\s*<span class="sr-only">')

    def test_the_event_name_is_no_longer_a_link_of_its_own(self):
        html = self.rows_html("events:event_list")
        row = re.search(r'<li class="event-row.*?</li>', html, re.S).group(0)
        self.assertNotRegex(row, r'<a[^>]*>\s*' + re.escape(self.event.name))

    def test_a_row_carries_the_class_its_height_hangs_off(self):
        # If this class is ever renamed in the template and not in app.css, every
        # row silently goes back to its natural height and the thumbnails stop
        # being square. Nothing errors.
        self.assertIn('class="event-row', self.rows_html("events:event_list"))

    def test_an_event_with_no_location_still_fills_the_line(self):
        """⚠️ The whole reason the rows can share one fixed height.

        A blank location used to make that row one line shorter. Inside a fixed
        height the row no longer shrinks — the *text* does, leaving a gap — so the
        line is filled with something true instead.
        """
        self.event.location = ""
        self.event.save(update_fields=["location"])
        self.assertIn("Location to be announced",
                      self.rows_html("events:event_list"))

    def test_a_finished_event_from_today_gets_the_same_row(self):
        """⚠️ Past Events had its own row template (`.event-row-past`) until
           2026-08-17. Now that finished events appear on this list until
           midnight, they go through **the same row as everything else** —
           which is the point of deleting that page rather than folding its
           template in: one row, one set of rules about it.
        """
        make_event(
            ministry=self.pantry, owner=self.zhang.contact, name="Over and done",
            status=Event.Status.COMPLETED, location="",
            start_time=day_start(local_today()) + HOUR,
            end_time=day_start(local_today()) + 2 * HOUR)
        html = self.rows_html("events:event_list")
        self.assertNotIn("event-row-past", html)
        self.assertIn("Over and done", html)
        self.assertIn("event-row-link", html)
        self.assertIn("Location to be announced", html)


class EventRowHeadingTests(PageTestCase):
    """The name line: ministry after the name, status at the far right (2026-08-17).

    The two badges say different kinds of thing and the stylesheet's rule for
    that is not decorative: `brand` is for **categories**, the four semantic
    tones are for **states**. A ministry borrowing a state colour gives a label
    that means nothing in particular a tone of voice.
    """

    def row(self):
        self.login(self.lisi)
        html = self.client.get(reverse("events:event_list")).content.decode()
        return re.search(r'<li class="event-row.*?</li>', html, re.S).group(0)

    def test_the_ministry_sits_between_the_name_and_the_status(self):
        row = self.row()
        name = row.index(self.event.name)
        ministry = row.index("Food Pantry")
        status = row.index("Open for signup")
        self.assertLess(name, ministry, "the ministry must follow the name")
        self.assertLess(ministry, status, "the status must be last, on the right")

    def test_the_ministry_keeps_the_category_tone(self):
        # bg-brand-50 is what badge.html renders for tone="brand".
        badge = re.search(r'<span class="inline-flex[^"]*"[^>]*>Food Pantry',
                          self.row())
        self.assertIsNotNone(badge, "no badge around the ministry name")
        self.assertIn("bg-brand-50", badge.group(0))

    def test_open_is_the_only_status_that_gets_a_colour(self):
        """⭐ Green means "you can still join", so only one status may be green.

        ⚠️ And `cancelled` must **not** be danger — the house rule from
           `_status_badge.html`: it is a truthful record, not an accident, and
           danger means "something is wrong here" everywhere else in this
           interface.
        """
        self.assertIn("bg-success-bg", self.row())

        for status in [Event.Status.CONFIRMED, Event.Status.CANCELLED,
                       Event.Status.COMPLETED]:
            with self.subTest(status=status):
                self.event.status = status
                self.event.save(update_fields=["status"])
                row = self.row()
                self.assertNotIn("bg-success-bg", row)
                self.assertNotIn("bg-danger-bg", row)
                self.assertIn(self.event.get_status_display(), row)

    def test_the_name_is_wrapped_so_a_long_one_cannot_push_the_badge_out(self):
        """⚠️ `min-w-0` on the wrapper, not only on the name.

        A flex item defaults to `min-width: auto` — "never shrink below your
        content" — which beats the `overflow: hidden` that `line-clamp` sets.
        Without it a long name shoves the ministry badge past the card's right
        edge instead of being clipped, and the narrower the card gets the more
        of them do it.
        """
        markup = (Path(settings.BASE_DIR) / "events" / "templates" / "events"
                  / "_event_list_results.html").read_text()
        wrapper = re.search(r'<span class="flex[^"]*">\s*<span class="event-name',
                            markup)
        self.assertIsNotNone(wrapper, "the name and the ministry are not wrapped")
        self.assertIn("min-w-0", wrapper.group(0))


class LiveFilterTests(PageTestCase):
    """筛选和搜索实时化（2026-08-17）—— 没有 Filter 按钮了。

    ⚠️ 这一组存在的直接原因：把 `Filter` 挪进 `<noscript>` 之后，**整个测试套件
       一条都没红** —— `assertContains` 读的是 HTML 源码，而 `<noscript>` 里的
       内容照样在源码里。也就是说，原来没有任何一条测试真的钉着「筛选是怎么
       触发的」，只钉着「页面上有 Filter 这四个字母」。
    """

    def form(self, url_name="events:event_list", user=None):
        self.login(user or self.lisi)
        html = self.client.get(reverse(url_name)).content.decode()
        return re.search(r"<form[^>]*hx-get[^>]*>", html).group(0)

    def test_typing_triggers_the_request_and_a_pause_debounces_it(self):
        trigger = re.search(r'hx-trigger="([^"]*)"', self.form()).group(1)
        self.assertIn("input", trigger)
        self.assertRegex(trigger, r"delay:\d+ms")

    def test_the_trigger_is_input_and_not_keyup(self):
        """⚠️ `keyup` misses three ordinary ways a box changes: paste, an IME
           candidate, and the browser's own clear button on `type="search"`.
           All three look the same from the outside — the text changed and the
           list did not move.
        """
        trigger = re.search(r'hx-trigger="([^"]*)"', self.form()).group(1)
        self.assertNotIn("keyup", trigger)

    def test_the_trigger_does_not_carry_the_changed_modifier(self):
        """🔴 `changed` reads `value` off **the element the trigger is on**, and
           this one is on the `<form>`. `form.value` is undefined, so "has it
           changed" answers "no" forever and not one request is ever sent.
           Nothing errors; the filter is simply dead.
        """
        trigger = re.search(r'hx-trigger="([^"]*)"', self.form()).group(1)
        self.assertNotIn("changed", trigger)

    def test_enter_is_still_caught(self):
        # A form with no submit button can still submit implicitly on Enter,
        # and which browsers do it depends on how many fields there are. Not
        # catching it means one keypress reloads the whole page.
        self.assertIn("submit",
                      re.search(r'hx-trigger="([^"]*)"', self.form()).group(1))

    def test_the_url_is_replaced_rather_than_pushed(self):
        """⭐ The half of live filtering that is invisible until somebody presses
        Back. Pushing means one history entry per debounced keystroke — seven
        presses to get out of "kitchen". Replacing keeps the filter shareable
        and leaves Back meaning "the page I came from".
        """
        form = self.form()
        self.assertIn('hx-replace-url="true"', form)
        self.assertNotIn("hx-push-url", form)

    def test_a_slower_answer_cannot_overwrite_a_newer_one(self):
        """⚠️ Debouncing reduces concurrency; it does not remove it.

        On a slow connection the request for "kitch" can come back *after* the
        one for "kitchen", and htmx paints whichever lands last — so the list
        shows the previous keystroke's results while the box says something
        else. It corrects itself on the next keypress, which is why it reads as
        "it glitches sometimes" rather than as a bug with a cause.
        """
        self.assertIn('hx-sync="this:replace"', self.form())

    def test_the_filter_button_exists_only_for_people_without_javascript(self):
        self.login(self.lisi)
        html = self.client.get(reverse("events:event_list")).content.decode()
        noscript = re.search(r"<noscript>.*?</noscript>", html, re.S)
        self.assertIsNotNone(noscript, "the no-JS submit path is gone")
        self.assertIn(">Filter</button>", noscript.group(0))
        self.assertEqual(html.count(">Filter</button>"), 1,
                         "a Filter button outside <noscript> does nothing when "
                         "the filter is live, and reads as a step you missed")

    def test_clear_survives_because_it_is_not_a_filter_button(self):
        # Four boxes to empty by hand is exactly what it saves, and live
        # filtering does not empty them for you.
        self.login(self.lisi)
        self.assertContains(
            self.client.get(reverse("events:event_list")), ">Clear</a>")

    def test_the_management_list_is_live_too_and_keeps_its_report_button(self):
        form = self.form("events:event_manage_list", user=self.zhang)
        self.assertIn("hx-trigger", form)
        html = self.client.get(reverse("events:event_manage_list")).content.decode()
        self.assertIn(">Generate report</button>", html)


class ScheduleToggleTests(PageTestCase):
    """The Schedule button and the shell it moves (2026-08-17).

    The room, not what is in it — the calendar itself is SchedulePageTests
    (2026-08-18). The split is worth keeping: everything here is about what
    happens to the *list* when the panel opens, and it stayed true when the
    placeholder inside became a real schedule.
    """

    def page(self):
        self.login(self.lisi)
        return self.client.get(reverse("events:event_list")).content.decode()

    def test_the_button_is_on_the_events_page(self):
        html = self.page()
        self.assertIn(">Schedule</button>", html)
        self.assertIn('aria-controls="schedule-panel"', html)

    def test_it_is_not_a_submit_button(self):
        """🔴 The trap. It lives inside the filter form, and `<button>` defaults
           to `type="submit"` — so the default would make one click submit the
           filter, reload the list, and reset the toggle. On screen that reads
           as "the button does nothing", which is the hardest kind of broken to
           chase.
        """
        html = self.page()
        button = re.search(r'<button[^>]*>Schedule</button>', html).group(0)
        self.assertIn('type="button"', button)

    def test_the_management_list_does_not_get_one(self):
        # Same filter card, one parameter apart. The management list is a table
        # of every event including finished ones — a day's schedule beside it
        # would be answering a question that page is not asking.
        self.login(self.zhang)
        html = self.client.get(reverse("events:event_manage_list")).content.decode()
        self.assertNotIn(">Schedule</button>", html)

    def test_the_toggle_state_lives_outside_the_swapped_fragment(self):
        """🔴 Why the `x-data` is on the shell and not on the card or the results.

        HTMX replaces `#event-results` on every filter and every page turn. A
        state declared inside that block is destroyed and recreated with each
        one — so the schedule would slam shut whenever somebody filtered or
        paged, and only ever on the real site, never in a unit test that reads
        the first render.
        """
        html = self.page()
        shell = html.index("schedule: false")
        results = html.index('id="event-results"')
        self.assertLess(shell, results,
                        "the schedule state must be declared before, and outside, "
                        "the fragment HTMX swaps")

    def test_the_panel_is_not_in_what_htmx_swaps_in(self):
        """Same reason, checked on the payload itself rather than on the page.

        The filter swaps `#event-results` with `outerHTML`. If the panel or the
        state were part of that response they would be replaced — the panel by a
        second copy of itself, the state by a fresh `false` — every time
        somebody filtered.
        """
        self.login(self.lisi)
        fragment = self.client.get(
            reverse("events:event_list"), HTTP_HX_REQUEST="true"
        ).content.decode()
        self.assertIn('id="event-results"', fragment)
        self.assertNotIn('id="schedule-panel"', fragment)
        self.assertNotIn("schedule: false", fragment)

    def test_the_panel_is_named_by_a_real_heading_now_that_it_has_contents(self):
        """2026-08-18：占位框那一版用的是 `aria-label`。

        ⚠️ 当时的理由是「一个指向空盒子的标题，在读屏的标题列表里是条死线索」。
           盒子里现在有东西了，所以那条理由反过来成立：一整块日历必须在标题
           列表里有一条，否则读屏用户只能一路 Tab 过去才知道它在。
        """
        html = self.page()
        self.assertIn('aria-labelledby="schedule-heading"', html)
        self.assertIn('id="schedule-heading"', html)
        # ⚠️ 仍然是 sr-only：屏幕上那块日历自己说得出自己是什么（星期、日期、
        #    格子），再画一行「Schedule」是给已经看得见的人重复一遍。
        self.assertRegex(html, r'id="schedule-heading" class="sr-only"')


class PastEventsIsGoneTests(PageTestCase):
    """2026-08-17: the page was deleted, not hidden.

    ⚠️ A route left in place with no entrance is the state this project keeps
       paying for (C0.2.4: event_roles was the hub of the management side and
       had no way in). Deleting the entrance and keeping the view would be that
       shape again, this time on purpose.
    """

    def test_the_route_does_not_exist(self):
        from django.urls import NoReverseMatch

        with self.assertRaises(NoReverseMatch):
            reverse("events:past_events")

    def test_the_old_address_is_a_404(self):
        self.login(self.lisi)
        self.assertEqual(self.client.get("/events/past/").status_code, 404)

    def test_nothing_links_to_it_any_more(self):
        """⚠️ A grep, because the failure is silent in a different way: Django
           raises NoReverseMatch at **render** time, so a leftover
           `{% url 'events:past_events' %}` in a rarely-drawn branch (an empty
           state, a 404 page) breaks that page and nothing else.
        """
        roots = [Path(settings.BASE_DIR) / app for app in
                 ["events", "core", "accounts", "org", "contact", "gallery"]]
        offenders = [
            str(path.relative_to(settings.BASE_DIR))
            for root in roots if root.exists()
            for path in root.rglob("*.html")
            if "past_events" in path.read_text()
        ]
        self.assertEqual(offenders, [])


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

    def test_the_detail_page_carries_no_picture_yet(self):
        # Not decided where it should sit; drawn nowhere until it is.
        self.event.image.save("x.webp", ContentFile(a_photo(size=(20, 20)).read()),
                              save=True)
        self.login(self.lisi)
        self.assertNotContains(
            self.client.get(reverse("events:event_detail", args=[self.event.pk])),
            "event-thumb")


# --- D28: the QR check-in -------------------------------------------------


class CheckInTokenTests(SimpleTestCase):
    """The token, on its own. No database, no request, no mocked clock.

    ⭐ These are the tests the module was shaped for. `TimestampSigner` would
       have done the same job with less code, and was rejected because its clock
       cannot be injected — which would have made the two boundary tests below
       reach for a mock. If a future change makes these tests need one, the
       change has undone the reason this module exists.
    """

    def test_a_fresh_token_gives_back_what_was_signed(self):
        token = tokens.issue(12, tokens.CHECK_IN)
        self.assertEqual(tokens.verify(token), (12, tokens.CHECK_IN))

    def test_it_is_still_valid_one_second_before_the_limit(self):
        at = local_now()
        token = tokens.issue(12, tokens.CHECK_IN, at=at)
        later = at + datetime.timedelta(seconds=tokens.MAX_AGE_SECONDS - 1)
        self.assertEqual(tokens.verify(token, at=later), (12, tokens.CHECK_IN))

    def test_it_is_refused_one_second_after_the_limit(self):
        at = local_now()
        token = tokens.issue(12, tokens.CHECK_IN, at=at)
        later = at + datetime.timedelta(seconds=tokens.MAX_AGE_SECONDS + 1)
        with self.assertRaises(tokens.InvalidCheckInToken):
            tokens.verify(token, at=later)

    def test_every_token_lives_exactly_as_long_as_every_other(self):
        # The whole reason the instant of issue travels inside the payload. With
        # time buckets the remaining life depends on where in the bucket the
        # scan landed — one volunteer gets 89 seconds and the next a tenth of a
        # second — which is the symptom the original design added a tolerance to
        # paper over. Issue at three unrelated moments; all three die at 90.
        for offset in (0, 7, 29):
            at = local_now() + datetime.timedelta(seconds=offset)
            token = tokens.issue(12, tokens.CHECK_IN, at=at)
            edge = at + datetime.timedelta(seconds=tokens.MAX_AGE_SECONDS)
            with self.subTest(offset=offset):
                self.assertEqual(tokens.verify(token, at=edge), (12, tokens.CHECK_IN))
                with self.assertRaises(tokens.InvalidCheckInToken):
                    tokens.verify(token, at=edge + datetime.timedelta(seconds=2))

    def test_a_token_stamped_in_the_future_is_refused(self):
        # Not a clock-skew case to be tolerated: this process signed it, so a
        # future stamp means the payload is not what we think it is.
        at = local_now()
        token = tokens.issue(12, tokens.CHECK_IN, at=at + datetime.timedelta(minutes=5))
        with self.assertRaises(tokens.InvalidCheckInToken):
            tokens.verify(token, at=at)

    def test_a_rewritten_payload_is_refused(self):
        # The interesting forgery: keep the signature, change what it covers.
        # ⚠️ Tested by re-encoding a payload rather than by flipping characters
        #    at random offsets — base64's final character carries only two
        #    significant bits, so several of its values decode to identical
        #    bytes and "every character matters" is simply not true of the
        #    encoding. Asserting it would be asserting something false about
        #    base64 while believing it said something about the HMAC.
        encoded, _, signature = tokens.issue(12, tokens.CHECK_IN).partition(".")
        payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
        forged = payload.replace("12:", "99:", 1)
        rewritten = base64.urlsafe_b64encode(forged.encode()).decode().rstrip("=")
        with self.assertRaises(tokens.InvalidCheckInToken):
            tokens.verify(f"{rewritten}.{signature}")

    def test_a_rewritten_signature_is_refused(self):
        encoded, _, signature = tokens.issue(12, tokens.CHECK_IN).partition(".")
        for index in (0, len(signature) // 2, len(signature) - 1):
            swapped = "a" if signature[index] != "a" else "b"
            forged = signature[:index] + swapped + signature[index + 1:]
            with self.subTest(index=index), self.assertRaises(tokens.InvalidCheckInToken):
                tokens.verify(f"{encoded}.{forged}")

    def test_a_token_for_one_event_cannot_be_read_as_another(self):
        # The event id is inside the signature and nowhere else — which is the
        # reason the URL does not repeat it. Two sources for one fact is a whole
        # class of "which one wins" bugs that this shape simply does not have.
        self.assertNotEqual(
            tokens.issue(12, tokens.CHECK_IN), tokens.issue(13, tokens.CHECK_IN))
        self.assertEqual(tokens.verify(tokens.issue(13, tokens.CHECK_IN))[0], 13)

    def test_the_direction_is_signed_too(self):
        # Otherwise a volunteer holding a check-in code could edit the URL into
        # a check-out and write their own hours.
        self.assertEqual(
            tokens.verify(tokens.issue(12, tokens.CHECK_OUT))[1], tokens.CHECK_OUT)
        self.assertNotEqual(
            tokens.issue(12, tokens.CHECK_IN), tokens.issue(12, tokens.CHECK_OUT))

    def test_rubbish_is_refused_rather_than_raising_something_else(self):
        # These arrive from a URL, so "not a token at all" is an ordinary input
        # and has to come back as the same refusal a stale one does.
        for rubbish in ["", "abc", "....", "a.b", "%%%.%%%", None]:
            with self.subTest(rubbish=rubbish), self.assertRaises(
                    tokens.InvalidCheckInToken):
                tokens.verify(rubbish)

    def test_the_expiry_handed_to_the_browser_is_an_absolute_instant(self):
        # ⚠️ Absolute, never "valid for N seconds". An iPad's timers are
        #    throttled the moment it sleeps, so a page counting down from its own
        #    clock wakes believing a dead code is fresh — and a dead QR looks
        #    exactly like a live one.
        at = local_now()
        token, expires_at = tokens.issue_with_expiry(12, tokens.CHECK_IN, at=at)
        self.assertEqual(tokens.verify(token, at=at), (12, tokens.CHECK_IN))
        self.assertEqual(
            expires_at, int(at.timestamp()) + tokens.MAX_AGE_SECONDS)

    def test_an_unknown_direction_is_a_programming_error_not_a_refusal(self):
        with self.assertRaises(ValueError):
            tokens.issue(12, "sideways")


class CheckInWindowTests(TestCase):
    """When the display may hand out codes at all.

    ⚠️ This exists for a tab left open on an office screen. Without it that page
       is a permanently valid clock-in machine that looks entirely normal.
    """

    def setUp(self):
        self.event = make_event()

    def test_it_is_open_during_the_event(self):
        self.assertTrue(tokens.window_is_open(
            self.event, at=self.event.start_time + HOUR))

    def test_it_is_shut_more_than_two_hours_before_the_start(self):
        just_early = self.event.start_time - tokens.WINDOW_BEFORE - datetime.timedelta(minutes=1)
        just_late = self.event.start_time - tokens.WINDOW_BEFORE + datetime.timedelta(minutes=1)
        self.assertFalse(tokens.window_is_open(self.event, at=just_early))
        self.assertTrue(tokens.window_is_open(self.event, at=just_late))

    def test_it_is_shut_more_than_four_hours_after_the_end(self):
        inside = self.event.end_time + tokens.WINDOW_AFTER - datetime.timedelta(minutes=1)
        outside = self.event.end_time + tokens.WINDOW_AFTER + datetime.timedelta(minutes=1)
        self.assertTrue(tokens.window_is_open(self.event, at=inside))
        self.assertFalse(tokens.window_is_open(self.event, at=outside))

    def test_a_draft_or_cancelled_event_never_opens(self):
        # A route that manufactures attendance for an event nobody published, or
        # one that is off, should not exist at any hour of the day.
        during = self.event.start_time + HOUR
        for status in (Event.Status.DRAFT, Event.Status.CANCELLED):
            self.event.status = status
            with self.subTest(status=status):
                self.assertFalse(tokens.window_is_open(self.event, at=during))

    def test_the_explanation_comes_from_the_same_two_constants(self):
        # ⚠️ Not from the template. "Opens two hours before" written in markup is
        #    a second copy of a rule, free to drift from the one enforced above.
        early = self.event.start_time - tokens.WINDOW_BEFORE - HOUR
        late = self.event.end_time + tokens.WINDOW_AFTER + HOUR
        self.assertIn("opens", tokens.window_message(self.event, at=early).lower())
        self.assertIn("closed", tokens.window_message(self.event, at=late).lower())
        self.assertIsNone(
            tokens.window_message(self.event, at=self.event.start_time + HOUR))


class CheckInMethodTests(TestCase):
    """Which of the three write paths recorded the attendance (D28)."""

    def setUp(self):
        self.event = make_event()
        self.role = make_role(self.event, "lifting")
        self.person = make_person("Wang", birth_date=datetime.date(1990, 1, 1))
        self.participation = sign_up(contact=self.person, event_role=self.role)

    def test_an_old_row_is_empty_rather_than_claimed_for_an_admin(self):
        # ⚠️ No default on the column, deliberately. A default of "admin" would
        #    back-date a claim onto every historical row — vouching for something
        #    nobody checked.
        self.assertEqual(self.participation.checked_in_method, "")

    def test_each_of_the_three_routes_records_its_own_source(self):
        # All three, because _mark_attended() already had to learn that a rule
        # with three entrances and one guard is a rule with two ways round it.
        cases = [
            (lambda p: check_in(p, method=Participation.CheckInMethod.SELF_QR),
             Participation.CheckInMethod.SELF_QR),
            (lambda p: record_hours(p, Decimal("2.00")),
             Participation.CheckInMethod.ADMIN),
        ]
        for act, expected in cases:
            person = make_person(f"P{expected}", birth_date=datetime.date(1990, 1, 1))
            row = sign_up(contact=person, event_role=self.role)
            with self.subTest(expected=expected):
                self.assertEqual(act(row).checked_in_method, expected)

        # check_out() only establishes attendance when it ends up with hours.
        row = sign_up(
            contact=make_person("Out", birth_date=datetime.date(1990, 1, 1)),
            event_role=self.role)
        check_in(row, at=local_now() - HOUR, method=Participation.CheckInMethod.SELF_QR)
        check_out(row, method=Participation.CheckInMethod.SELF_QR)
        self.assertEqual(row.checked_in_method, Participation.CheckInMethod.SELF_QR)

    def test_an_admin_correcting_the_hours_does_not_rewrite_who_filled_it_in(self):
        # ⚠️ First write wins. The question the column answers is "did the
        #    volunteer fill this in, or did I?" — and the answer must survive the
        #    admin touching the row afterwards, which is the ordinary case.
        check_in(self.participation, method=Participation.CheckInMethod.SELF_QR)
        record_hours(self.participation, Decimal("4.00"))
        self.assertEqual(
            self.participation.checked_in_method, Participation.CheckInMethod.SELF_QR)

    def test_undoing_the_attendance_clears_it_with_everything_else(self):
        # Left behind it would say "a volunteer recorded this" about a row that
        # now records nothing, and first-write-wins would then be stuck with it.
        check_in(self.participation, method=Participation.CheckInMethod.SELF_QR)
        undo_attendance(self.participation)
        self.assertEqual(self.participation.checked_in_method, "")


class CheckInCredentialTests(TestCase):
    """The proof of presence that outlives the token. D28's two-stage split."""

    def test_it_survives_long_enough_to_type_a_password(self):
        at = local_now()
        credential = issue_credential(7, tokens.CHECK_IN, at=at)
        still_fine = at + CREDENTIAL_MAX_AGE - datetime.timedelta(seconds=1)
        self.assertEqual(
            read_credential(credential, at=still_fine), (7, tokens.CHECK_IN))

    def test_it_does_not_survive_for_ever(self):
        at = local_now()
        credential = issue_credential(7, tokens.CHECK_IN, at=at)
        with self.assertRaises(CredentialExpired):
            read_credential(credential, at=at + CREDENTIAL_MAX_AGE + HOUR)

    def test_a_missing_or_malformed_credential_reads_as_expired(self):
        for rubbish in [None, {}, {"event": 7}, {"event": 7, "mode": "in", "at": "x"}]:
            with self.subTest(rubbish=rubbish), self.assertRaises(CredentialExpired):
                read_credential(rubbish)

    def test_it_is_json_serialisable_because_a_session_has_to_hold_it(self):
        # Sessions are serialised as JSON by default. A datetime here would blow
        # up at the moment of the redirect — i.e. on every single scan.
        json.dumps(issue_credential(7, tokens.CHECK_IN))


class ScanTargetsTests(TestCase):
    """Which of somebody's rows a scan applies to, and which it would repeat."""

    def setUp(self):
        self.event = make_event()
        self.lifting = make_role(self.event, "lifting")
        self.desk = make_role(self.event, "desk", name="Welcome desk")
        self.person = make_person("Wang", birth_date=datetime.date(1990, 1, 1))

    def test_a_signup_is_pending_for_check_in_and_done_once_checked_in(self):
        row = sign_up(contact=self.person, event_role=self.lifting)
        targets = scan_targets(self.person, self.event, tokens.CHECK_IN)
        self.assertEqual(targets.pending, [row])
        check_in(row)
        self.assertEqual(
            scan_targets(self.person, self.event, tokens.CHECK_IN).done, [row])

    def test_checking_out_needs_a_check_in_first_and_says_so_separately(self):
        # Its own list, not "pending": the sentence on the phone is different
        # ("ask the organiser to switch the screen"), and so is the fix.
        row = sign_up(contact=self.person, event_role=self.lifting)
        targets = scan_targets(self.person, self.event, tokens.CHECK_OUT)
        self.assertEqual(targets.needs_check_in, [row])
        self.assertEqual(targets.pending, [])

    def test_a_cancelled_signup_is_not_a_signup(self):
        # ⚠️ Excluded, not listed as done. The row survives only so the
        #    notification history has something to point at; somebody who pulled
        #    out and then scanned should be told they are not signed up.
        row = sign_up(contact=self.person, event_role=self.lifting)
        cancel(row)
        self.assertFalse(scan_targets(self.person, self.event, tokens.CHECK_IN).any_signup)

    def test_two_roles_at_one_event_produce_two_pending_rows(self):
        # D19 allows it, so the scan has to answer for it — and the answer is to
        # ask, because both alternatives invent a number (D28 五).
        sign_up(contact=self.person, event_role=self.lifting)
        sign_up(contact=self.person, event_role=self.desk)
        self.assertEqual(
            len(scan_targets(self.person, self.event, tokens.CHECK_IN).pending), 2)


class ApplyScanTests(TestCase):
    """The write itself: locked, scoped, and safe to repeat."""

    def setUp(self):
        self.event = make_event()
        self.role = make_role(self.event, "lifting")
        self.person = make_person("Wang", birth_date=datetime.date(1990, 1, 1))
        self.other = make_person("Chen", birth_date=datetime.date(1990, 1, 1))
        self.row = sign_up(contact=self.person, event_role=self.role)

    def test_a_second_identical_scan_changes_nothing_and_is_not_an_error(self):
        # ⭐ The idempotence this feature rests on. The commonest way to arrive
        #    here twice is a slow page and an impatient thumb; reporting the
        #    second tap as a failure sends that person off to find an admin over
        #    something that already worked.
        _, changed = apply_scan(
            self.row.pk, contact=self.person, event_id=self.event.pk,
            mode=tokens.CHECK_IN)
        self.assertTrue(changed)
        self.row.refresh_from_db()
        first_time = self.row.checked_in_at

        row, changed_again = apply_scan(
            self.row.pk, contact=self.person, event_id=self.event.pk,
            mode=tokens.CHECK_IN)
        self.assertFalse(changed_again)
        self.assertEqual(row.checked_in_at, first_time)

    def test_a_repeat_leaves_no_second_history_row(self):
        # The visible half of the test above. A no-op that still writes would
        # put a meaningless revision into the one table this project relies on
        # to recover a value somebody deleted.
        apply_scan(self.row.pk, contact=self.person, event_id=self.event.pk,
                   mode=tokens.CHECK_IN)
        before = self.row.history.count()
        apply_scan(self.row.pk, contact=self.person, event_id=self.event.pk,
                   mode=tokens.CHECK_IN)
        self.assertEqual(self.row.history.count(), before)

    def test_somebody_elses_row_cannot_be_reached_by_primary_key(self):
        # The scope is the belt: the lookup is narrowed to this contact and this
        # event before the primary key is applied at all.
        theirs = sign_up(contact=self.other, event_role=self.role)
        with self.assertRaises(Participation.DoesNotExist):
            apply_scan(theirs.pk, contact=self.person, event_id=self.event.pk,
                       mode=tokens.CHECK_IN)

    def test_checking_out_writes_the_hours_and_the_source(self):
        check_in(self.row, at=local_now() - 2 * HOUR,
                 method=Participation.CheckInMethod.SELF_QR)
        row, changed = apply_scan(
            self.row.pk, contact=self.person, event_id=self.event.pk,
            mode=tokens.CHECK_OUT)
        self.assertTrue(changed)
        self.assertEqual(row.hours, Decimal("2.00"))
        self.assertEqual(row.checked_in_method, Participation.CheckInMethod.SELF_QR)

    def test_checking_out_without_checking_in_does_nothing(self):
        _, changed = apply_scan(
            self.row.pk, contact=self.person, event_id=self.event.pk,
            mode=tokens.CHECK_OUT)
        self.assertFalse(changed)
        self.row.refresh_from_db()
        self.assertIsNone(self.row.checked_out_at)


class DefaultCheckInModeTests(TestCase):
    """What the iPad starts on — read once, at page load, and never again."""

    def setUp(self):
        self.event = make_event()

    def test_before_the_midpoint_it_starts_on_check_in(self):
        just_started = self.event.start_time + datetime.timedelta(minutes=1)
        self.assertEqual(
            default_checkin_mode(self.event, at=just_started), tokens.CHECK_IN)

    def test_after_the_midpoint_it_starts_on_check_out(self):
        nearly_over = self.event.end_time - datetime.timedelta(minutes=1)
        self.assertEqual(
            default_checkin_mode(self.event, at=nearly_over), tokens.CHECK_OUT)


class CheckInPageTests(PageTestCase):
    """The two halves of a scan, hit as URLs.

    ⭐ The acceptance point for the whole feature is the first test: a volunteer
       who is not logged in on their phone — which is most of them, the first
       time — still gets checked in, and the token's 90 seconds are not spent
       waiting for them to type a password.
    """

    def setUp(self):
        super().setUp()
        self.event.start_time = local_now() - HOUR
        self.event.end_time = local_now() + 2 * HOUR
        self.event.save()
        self.participation = sign_up(contact=self.lisi.contact, event_role=self.role)

    def scan_url(self, mode=tokens.CHECK_IN, event=None, at=None):
        token = tokens.issue((event or self.event).pk, mode, at=at)
        return reverse("events:checkin_scan", args=[token])

    def test_a_volunteer_not_yet_logged_in_can_still_check_in(self):
        # Scan first, log in second. The credential carries "somebody was at
        # this screen"; the login decides who that somebody is.
        response = self.client.get(self.scan_url())
        self.assertRedirects(
            response, reverse("events:checkin_confirm"),
            target_status_code=302)

        confirm = reverse("events:checkin_confirm")
        self.assertRedirects(
            self.client.get(confirm), f"{reverse('accounts:login')}?next={confirm}")

        # ⚠️ login() rotates the session key but keeps its contents, which is
        #    what makes this whole design work. If that ever stopped being true
        #    the feature would break silently for every logged-out volunteer.
        self.login(self.lisi)
        self.assertContains(self.client.get(confirm), "Check in")
        self.client.post(confirm, {"participation": self.participation.pk})

        self.participation.refresh_from_db()
        self.assertIsNotNone(self.participation.checked_in_at)
        self.assertEqual(
            self.participation.checked_in_method,
            Participation.CheckInMethod.SELF_QR)

    def test_the_scan_itself_writes_nothing(self):
        # ⚠️ A GET, so link previewers in messaging apps, browser prefetch and
        #    corporate URL scanners all fetch it without a human touching it.
        #    The original design recorded attendance here, which means forwarding
        #    the link into any chat window would have checked somebody in.
        self.client.get(self.scan_url())
        self.participation.refresh_from_db()
        self.assertIsNone(self.participation.checked_in_at)

    def test_an_expired_code_is_refused_with_something_to_do_about_it(self):
        stale = self.scan_url(at=local_now() - datetime.timedelta(minutes=5))
        response = self.client.get(stale)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "expired", status_code=400)

    def test_the_word_confirm_is_not_read_as_a_token(self):
        # The URL ordering. The other way round, /events/checkin/confirm/ would
        # match the token pattern and every volunteer would be told their code
        # had expired.
        self.login(self.lisi)
        self.client.get(self.scan_url())
        self.assertEqual(self.client.get(reverse("events:checkin_confirm")).status_code, 200)

    def test_somebody_who_never_signed_up_is_offered_the_signup_page(self):
        # ⚠️ Offered, never signed up on their behalf. Creating the row here
        #    would walk past sign_up()'s two gates, and on the other side of them
        #    is a minor recorded as present with nobody to call.
        self.login(self.zhang)
        self.client.get(self.scan_url())
        response = self.client.get(reverse("events:checkin_confirm"))
        self.assertContains(response, "not signed up", status_code=400)
        self.assertContains(
            response, reverse("events:event_signup", args=[self.event.pk]),
            status_code=400)
        self.assertFalse(
            Participation.objects.filter(contact=self.zhang.contact).exists())

    def test_a_minor_with_no_consent_gets_a_sentence_and_not_a_500(self):
        # The gate already exists in _mark_attended(); what is new is that the
        # person it refuses is standing in a hall holding a phone.
        minor = self.account(
            "min", "Min", birth_date=local_today() - datetime.timedelta(days=365 * 15))
        give_emergency_contact(minor.contact)
        row = Participation.objects.create(
            event_role=self.role, contact=minor.contact, registered_at=local_now())
        self.login(minor)
        self.client.get(self.scan_url())
        response = self.client.post(
            reverse("events:checkin_confirm"), {"participation": row.pk})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "consent", status_code=400)
        row.refresh_from_db()
        self.assertIsNone(row.checked_in_at)

    def test_two_roles_produce_a_choice_and_only_the_chosen_row_is_written(self):
        desk = make_role(self.event, "desk", name="Welcome desk")
        second = sign_up(contact=self.lisi.contact, event_role=desk)
        self.login(self.lisi)
        self.client.get(self.scan_url())

        response = self.client.get(reverse("events:checkin_confirm"))
        self.assertContains(response, "more than one role")

        self.client.post(
            reverse("events:checkin_confirm"), {"participation": second.pk})
        self.participation.refresh_from_db()
        second.refresh_from_db()
        self.assertIsNone(self.participation.checked_in_at)
        self.assertIsNotNone(second.checked_in_at)

    def test_a_row_belonging_to_somebody_else_cannot_be_posted(self):
        # Interface keeps nobody out — a form posted from anywhere at all
        # arrives at this view with the same shape.
        theirs = sign_up(contact=self.zhang.contact, event_role=self.role)
        self.login(self.lisi)
        self.client.get(self.scan_url())
        self.assertEqual(
            self.client.post(
                reverse("events:checkin_confirm"), {"participation": theirs.pk}
            ).status_code, 404)

    def test_a_second_tap_reports_the_time_rather_than_an_error(self):
        self.login(self.lisi)
        self.client.get(self.scan_url())
        self.client.post(reverse("events:checkin_confirm"),
                         {"participation": self.participation.pk})
        self.participation.refresh_from_db()
        first = self.participation.checked_in_at

        self.client.get(self.scan_url())
        response = self.client.post(
            reverse("events:checkin_confirm"),
            {"participation": self.participation.pk}, follow=True)
        self.assertContains(response, "already checked in")
        self.participation.refresh_from_db()
        self.assertEqual(self.participation.checked_in_at, first)

    def test_checking_out_lands_on_my_signups_with_the_hours(self):
        check_in(self.participation, at=local_now() - 2 * HOUR)
        self.login(self.lisi)
        self.client.get(self.scan_url(mode=tokens.CHECK_OUT))
        response = self.client.post(
            reverse("events:checkin_confirm"),
            {"participation": self.participation.pk}, follow=True)
        self.assertRedirects(response, reverse("events:my_participations"))
        self.assertContains(response, "hours recorded")

    def test_checking_out_before_checking_in_says_what_to_ask_for(self):
        self.login(self.lisi)
        self.client.get(self.scan_url(mode=tokens.CHECK_OUT))
        self.assertContains(
            self.client.get(reverse("events:checkin_confirm")), "not checked in yet")

    def test_the_credential_does_not_outlive_its_ten_minutes(self):
        self.login(self.lisi)
        self.client.get(self.scan_url())
        session = self.client.session
        session[CHECKIN_CREDENTIAL_KEY] = issue_credential(
            self.event.pk, tokens.CHECK_IN, at=local_now() - CREDENTIAL_MAX_AGE - HOUR)
        session.save()
        self.assertEqual(
            self.client.get(reverse("events:checkin_confirm")).status_code, 400)

    def test_arriving_at_the_confirmation_page_without_scanning_is_refused(self):
        self.login(self.lisi)
        self.assertEqual(
            self.client.get(reverse("events:checkin_confirm")).status_code, 400)

    def test_my_signups_shows_the_times_in_one_column(self):
        check_in(self.participation, at=local_now() - 2 * HOUR)
        check_out(self.participation)
        self.login(self.lisi)
        response = self.client.get(reverse("events:my_participations"))
        self.assertContains(response, "Attendance")
        self.assertContains(
            response, formats.time_format(localtime(self.participation.checked_out_at)))


class CheckInDisplayTests(PageTestCase):
    """The iPad page and the endpoint behind it."""

    def setUp(self):
        super().setUp()
        self.event.start_time = local_now() - HOUR
        self.event.end_time = local_now() + 2 * HOUR
        self.event.save()
        self.token_url = reverse("events:checkin_token", args=[self.event.pk])

    def test_a_volunteer_cannot_fetch_a_code_from_their_sofa(self):
        # ⭐ This check **is** the scheme. Without it every rotating-code measure
        #    above it is decoration: any signed-in account could pull a live
        #    token and book itself in from anywhere.
        self.login(self.lisi)
        self.assertEqual(
            self.client.get(self.token_url, {"mode": "in"}).status_code, 403)
        self.assertEqual(
            self.client.get(
                reverse("events:checkin_display", args=[self.event.pk])).status_code,
            403)

    def test_an_admin_of_another_ministry_is_refused_too(self):
        self.login(self.other_admin)
        self.assertEqual(
            self.client.get(self.token_url, {"mode": "in"}).status_code, 403)

    def test_the_code_it_hands_out_is_a_whole_url_that_verifies(self):
        # ⚠️ Assembled on the server. A script building this address from parts
        #    would be a second definition of the route, and the QR is the one
        #    place where being subtly wrong produces a code that scans perfectly
        #    and goes nowhere.
        self.login(self.zhang)
        payload = self.client.get(self.token_url, {"mode": "in"}).json()
        self.assertIn(payload["url"], payload["url"])
        token = payload["url"].rstrip("/").rsplit("/", 1)[-1]
        self.assertEqual(tokens.verify(token), (self.event.pk, tokens.CHECK_IN))
        self.assertGreater(payload["expires_at"], local_now().timestamp())

    def test_outside_the_window_it_refuses_and_explains(self):
        self.event.start_time = local_now() + DAY
        self.event.end_time = local_now() + DAY + 3 * HOUR
        self.event.save()
        self.login(self.zhang)
        response = self.client.get(self.token_url, {"mode": "in"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("opens", response.json()["error"].lower())

    def test_a_draft_event_hands_out_nothing(self):
        self.event.status = Event.Status.DRAFT
        self.event.save()
        self.login(self.zhang)
        self.assertEqual(
            self.client.get(self.token_url, {"mode": "in"}).status_code, 409)

    def test_an_unknown_direction_is_refused(self):
        self.login(self.zhang)
        self.assertEqual(
            self.client.get(self.token_url, {"mode": "sideways"}).status_code, 404)

    def test_the_page_carries_the_endpoint_and_the_starting_direction(self):
        # Both from the server: the JS reads them out of data attributes and
        # assembles no URL of its own.
        self.login(self.zhang)
        response = self.client.get(
            reverse("events:checkin_display", args=[self.event.pk]))
        self.assertContains(response, f'data-token-url="{self.token_url}"')
        self.assertContains(response, 'data-mode="')

    def test_the_attendance_page_marks_the_rows_the_volunteer_filled_in(self):
        # ⭐ D28's mitigation, and the only one there is. The forwarding hole
        #    cannot be closed — a live accomplice on site defeats any rotation —
        #    so what the design buys instead is that an admin can **see** which
        #    rows he did not fill in. A problem nobody can see is a problem
        #    nobody handles, which is why this is a test and not a nicety.
        row = sign_up(contact=self.lisi.contact, event_role=self.role)
        check_in(row, method=Participation.CheckInMethod.SELF_QR)
        self.login(self.zhang)
        page = reverse("events:event_attendance", args=[self.event.pk])
        self.assertContains(self.client.get(page), "self check-in")

    def test_a_row_an_admin_filled_in_carries_no_marking_at_all(self):
        # ⚠️ The other half, and the one that keeps the first honest. An empty
        #    column means "this row predates the feature", which is a different
        #    fact from "an admin recorded it" — printing a label on every row
        #    would vouch for something nobody checked, and would also make the
        #    self ones stop standing out, which was the entire point.
        row = sign_up(contact=self.lisi.contact, event_role=self.role)
        check_in(row)
        self.login(self.zhang)
        page = reverse("events:event_attendance", args=[self.event.pk])
        self.assertNotContains(self.client.get(page), "self check-in")

    def test_the_management_list_shows_the_link_only_to_who_may_use_it(self):
        # Same rule as Edit and Notify on that row: a link that refuses the
        # person who clicked it reads as a broken site.
        qr_link = reverse("events:checkin_display", args=[self.event.pk])
        self.login(self.zhang)
        self.assertContains(
            self.client.get(reverse("events:event_manage_list")), qr_link)

        self.lisi.is_staff = False
        self.lisi.save()
        foundation_admin_group().user_set.add(self.lisi)
        self.login(self.lisi)
        self.assertNotContains(
            self.client.get(reverse("events:event_manage_list")), qr_link)


class ScheduleLayoutTests(TestCase):
    """把活动摆进格子里（2026-08-18）。

    这一整组测的是 events/schedule.py，不碰 URL —— 摆放是算术，而算术错了的
    表现全都是「画面上差一点」，不报错、不 500，只有量一量才看得出来。
    """

    def setUp(self):
        self.ministry = Ministry.objects.create(code="food_pantry", name="Food Pantry")
        self.today = local_today()

    def at(self, day, hour, minute=0):
        return day_start(day) + datetime.timedelta(hours=hour, minutes=minute)

    def event(self, start, end, **kwargs):
        return make_event(ministry=self.ministry, start_time=start, end_time=end, **kwargs)

    def one_column(self, day=None, now=None):
        day = day or self.today
        events = list(Event.objects.all())
        return schedule.columns(events, [day], now=now or local_now())[0]

    def test_a_card_starts_and_ends_where_its_hours_say(self):
        self.event(self.at(self.today, 15), self.at(self.today, 17))
        card = self.one_column().cards[0]
        self.assertEqual(card.top, 15 * schedule.PX_PER_HOUR)
        self.assertEqual(card.height, 2 * schedule.PX_PER_HOUR)

    def test_a_very_short_event_still_has_a_card_you_can_see(self):
        """⚠️ 十分钟的活动按比例是 8px —— 一条线。下限存在的理由是它仍然要能
           被读出来是一张卡；代价是它的高度不再等于它的时长。
        """
        self.event(self.at(self.today, 9), self.at(self.today, 9, 10))
        self.assertEqual(self.one_column().cards[0].height, schedule.MIN_CARD_PX)

    def test_a_zero_length_event_is_still_drawn(self):
        """模型只约束 end >= start，所以 end == start 是合法的一行数据。

        ⚠️ 它会被「相交」那条判断判掉（end 不大于这一天的开始），所以 _segments
           里专门有一条补丁。少了那条，这样一场活动在日程上**根本不存在**，
           而它在左边的列表里好端端地列着。
        """
        moment = self.at(self.today, 14)
        self.event(moment, moment)
        self.assertEqual(len(self.one_column().cards), 1)

    def test_a_zero_length_event_at_midnight_is_the_case_that_needed_the_patch(self):
        """⚠️ 14:00 那一场其实走不到那条补丁上 —— 真正被「相交」判断判掉的是
           **正好在零点**的那一场：它的 end 不大于这一天的开始。
           这条测试和上面那条是一对，少了它，补丁可以被整段删掉而测试全绿。
        """
        moment = day_start(self.today)
        self.event(moment, moment)
        column = self.one_column()
        self.assertEqual(len(column.cards), 1)
        self.assertEqual(column.cards[0].top, 0)
        self.assertEqual(column.cards[0].height, schedule.MIN_CARD_PX)

    def test_a_card_never_hangs_below_its_own_day(self):
        # 23:50 的十分钟活动：下限会把它撑到 23:52，多出来的两像素就画在
        # 第二天的位置上了。
        self.event(self.at(self.today, 23, 50), self.at(self.today, 23, 59))
        card = self.one_column().cards[0]
        self.assertLessEqual(card.top + card.height, schedule.DAY_PX)

    def test_an_overnight_event_gets_one_card_in_each_day(self):
        tomorrow = self.today + datetime.timedelta(days=1)
        self.event(self.at(self.today, 22), self.at(tomorrow, 1))
        events = list(Event.objects.all())
        first, second = schedule.columns(events, [self.today, tomorrow])

        self.assertEqual(len(first.cards), 1)
        self.assertEqual(len(second.cards), 1)
        # 第一段裁到当天末尾，第二段从零点开始。
        self.assertEqual(first.cards[0].top + first.cards[0].height, schedule.DAY_PX)
        self.assertEqual(second.cards[0].top, 0)
        self.assertTrue(first.cards[0].continues_after)
        self.assertTrue(second.cards[0].continues_before)

    def test_both_halves_of_an_overnight_event_say_the_real_hours(self):
        """⚠️ 第二天那张写「12am – 1am」是错的：人是拿这行字安排时间的，
           而那场活动是**昨晚十点**开始的。
        """
        tomorrow = self.today + datetime.timedelta(days=1)
        self.event(self.at(self.today, 22), self.at(tomorrow, 1))
        events = list(Event.objects.all())
        first, second = schedule.columns(events, [self.today, tomorrow])
        self.assertEqual(first.cards[0].label, "10pm – 1am")
        self.assertEqual(second.cards[0].label, first.cards[0].label)

    def test_an_event_that_ends_at_midnight_does_not_leak_into_the_next_day(self):
        tomorrow = self.today + datetime.timedelta(days=1)
        self.event(self.at(self.today, 22), day_start(tomorrow))
        events = list(Event.objects.all())
        self.assertEqual(len(schedule.columns(events, [tomorrow])[0].cards), 0)

    def test_two_events_at_exactly_the_same_time_do_not_cover_each_other(self):
        """你说的那一条：完全重叠时上面那张往右下偏一点。"""
        start, end = self.at(self.today, 15), self.at(self.today, 17)
        self.event(start, end, name="First")
        self.event(start, end, name="Second")
        depths = [card.depth for card in self.one_column().cards]
        self.assertEqual(depths, [0, 1])

    def test_a_partial_overlap_offsets_by_the_same_rule(self):
        self.event(self.at(self.today, 15), self.at(self.today, 17))
        self.event(self.at(self.today, 16), self.at(self.today, 18))
        self.assertEqual([card.depth for card in self.one_column().cards], [0, 1])

    def test_events_that_only_touch_are_not_treated_as_overlapping(self):
        # 3–5pm 和 5–7pm 是接着的，不是叠着的。都偏移的话，一整天连着排的
        # 活动会像楼梯一样一路往右下跑。
        self.event(self.at(self.today, 15), self.at(self.today, 17))
        self.event(self.at(self.today, 17), self.at(self.today, 19))
        self.assertEqual([card.depth for card in self.one_column().cards], [0, 0])

    def test_the_offset_stops_before_it_walks_off_the_column(self):
        start, end = self.at(self.today, 9), self.at(self.today, 12)
        for number in range(6):
            self.event(start, end, name=f"Event {number}")
        depths = [card.depth for card in self.one_column().cards]
        self.assertEqual(max(depths), schedule.MAX_DEPTH)
        self.assertEqual(depths, [0, 1, 2, 3, 3, 3])

    def test_a_cascade_counts_layers_not_neighbours(self):
        """⚠️ 「跟几张卡相交」和「压在第几层」是两个数。

        三张互不相交、但都跟第一张相交的卡，按张数算全部落在第 1 档 ——
        于是它们三张**完全重合**，而这正是偏移要解决的那件事。
        """
        self.event(self.at(self.today, 9), self.at(self.today, 18), name="All day")
        self.event(self.at(self.today, 10), self.at(self.today, 11), name="A")
        self.event(self.at(self.today, 12), self.at(self.today, 13), name="B")
        by_name = {card.event.name: card.depth for card in self.one_column().cards}
        self.assertEqual(by_name["All day"], 0)
        self.assertEqual(by_name["A"], 1)
        self.assertEqual(by_name["B"], 1)

    def test_the_order_of_two_identical_events_does_not_wander(self):
        """⚠️ 同一分钟开始、同样长的两场活动，排序键少了 pk 就是并列的 ——
           而并列的顺序取决于查询回来的次序，于是偏移和颜色会在两次请求之间
           自己换位。屏幕上是「刷新一下两张卡对调了」。
        """
        start, end = self.at(self.today, 15), self.at(self.today, 17)
        first = self.event(start, end, name="First")
        second = self.event(start, end, name="Second")
        forwards = schedule.columns([first, second], [self.today])[0]
        backwards = schedule.columns([second, first], [self.today])[0]
        self.assertEqual([card.event.pk for card in forwards.cards],
                         [card.event.pk for card in backwards.cards])

    # --- 颜色 ---------------------------------------------------------------

    def test_an_event_keeps_its_colour_across_windows(self):
        """「每场活动固定一色」（2026-08-18 拍板）。"""
        event = self.event(self.at(self.today, 15), self.at(self.today, 17))
        first = schedule.columns([event], [self.today])[0].cards[0].colour
        second = schedule.columns([event], [self.today])[0].cards[0].colour
        self.assertEqual(first, second)

    def test_the_colour_does_not_depend_on_a_salted_hash(self):
        """🔴 内置的 `hash()` 对 str 加了每进程的随机盐。

        用它的话同一场活动在两个 gunicorn worker 上是两个颜色 —— 刷新一下就变，
        而本机单进程跑起来一切正常，测试也全绿。这里钉的是「同一个 pk 永远
        算出同一个格子」这件事本身。
        """
        self.assertEqual(schedule._base_colour(4321), schedule._base_colour(4321))
        self.assertNotEqual(
            {schedule._base_colour(pk) for pk in range(1, 40)}, {0},
            "整盘颜色只用到了一格")

    def test_every_swatch_is_filed_under_a_family(self):
        """🔴 回避撞色比的是**色族**，不是格子的下标。

        盘里有四个粉。按下标算它们是四个不同的颜色，屏幕上是同一个 ——
        两张挨着的卡各配一个，读起来仍然像一张。第一版就是这么错的，
        而它**通过了**下面那条「不撞色」的测试。

        ⚠️ 这条守卫钉得住「每一格都归了族、没有重复」，钉不住「归对了族」——
           后者只有眼睛看得出来，改色值时得重新看一遍。
        """
        filed = [slot for family in schedule.COLOUR_FAMILIES for slot in family]
        self.assertEqual(sorted(filed), list(range(schedule.PALETTE)))
        self.assertEqual(len(filed), len(set(filed)), "有格子归进了两个族")

    def test_neighbours_never_share_a_colour_family(self):
        """你说的那一条：不跟临近的活动撞色。

        ⚠️ 比的是族。写成 `assertNotEqual(colour, colour)` 的话，两张挨着的
           粉卡会通过 —— 那正是第一版在浏览器里被看出来的样子。
        """
        for hour in range(9, 20):
            self.event(self.at(self.today, hour), self.at(self.today, hour + 1))
        cards = self.one_column().cards
        for earlier, later in zip(cards, cards[1:]):
            self.assertNotEqual(
                schedule._FAMILY_OF[earlier.colour],
                schedule._FAMILY_OF[later.colour],
                f"{earlier.event.name} 和紧跟着它的那一场是同一族颜色")

    def test_neighbours_never_share_a_colour(self):
        """族之外，格子本身也不该重复。"""
        for hour in range(9, 20):
            self.event(self.at(self.today, hour), self.at(self.today, hour + 1))
        cards = self.one_column().cards
        for earlier, later in zip(cards, cards[1:]):
            self.assertNotEqual(
                earlier.colour, later.colour,
                f"{earlier.event.name} 和紧跟着它的那一场同色")

    def test_overlapping_events_never_share_a_colour(self):
        start, end = self.at(self.today, 15), self.at(self.today, 17)
        for number in range(4):
            self.event(start, end, name=f"Event {number}")
        colours = [card.colour for card in self.one_column().cards]
        self.assertEqual(len(set(colours)), len(colours))
        families = [schedule._FAMILY_OF[colour] for colour in colours]
        self.assertEqual(len(set(families)), len(families))

    def test_more_overlaps_than_families_still_never_repeats_a_swatch(self):
        """⚠️ 九张互相挨着的卡，八个族不够分。退而求其次是「至少不同格」——
           直接退回本命色的话，第九张会和第一张**完全**同色，而它们正挨着。
        """
        start, end = self.at(self.today, 9), self.at(self.today, 18)
        for number in range(len(schedule.COLOUR_FAMILIES) + 1):
            self.event(start, end, name=f"Event {number}")
        colours = [card.colour for card in self.one_column().cards]
        self.assertEqual(len(set(colours)), len(colours), "两张重叠的卡拿到了同一格")

    def test_a_cancelled_event_gets_no_colour_at_all(self):
        """⚠️ 照常上色是会主动误导人的：报过名的人正是需要看见它黄了的人，
           而一张和旁边一样实的浅色卡读起来是「照常举行」。
        """
        self.event(self.at(self.today, 15), self.at(self.today, 17),
                   status=Event.Status.CANCELLED)
        card = self.one_column().cards[0]
        self.assertIsNone(card.colour)
        self.assertTrue(card.is_cancelled)

    def test_a_cancelled_event_does_not_use_up_a_colour_its_neighbour_wanted(self):
        # 它不参与配色，所以也不该占着一个格子让旁边那张躲开。
        self.event(self.at(self.today, 15), self.at(self.today, 17),
                   status=Event.Status.CANCELLED)
        live = self.event(self.at(self.today, 15), self.at(self.today, 17))
        cards = {card.event.pk: card for card in self.one_column().cards}
        self.assertEqual(cards[live.pk].colour, schedule._base_colour(live.pk))

    # --- 现在几点 -----------------------------------------------------------

    def test_an_event_that_is_over_is_drawn_faded(self):
        now = self.at(self.today, 18)
        self.event(self.at(self.today, 15), self.at(self.today, 17))
        self.assertTrue(self.one_column(now=now).cards[0].is_past)

    def test_an_event_still_running_is_not_faded(self):
        now = self.at(self.today, 16)
        self.event(self.at(self.today, 15), self.at(self.today, 17))
        self.assertFalse(self.one_column(now=now).cards[0].is_past)

    def test_the_red_line_only_exists_on_today(self):
        tomorrow = self.today + datetime.timedelta(days=1)
        today_column, tomorrow_column = schedule.columns([], [self.today, tomorrow])
        self.assertIsNotNone(today_column.now_top)
        self.assertIsNone(tomorrow_column.now_top,
                          "一条画在明天上的「现在」是一句假话")

    def test_the_countdown_names_the_event_that_finishes_first(self):
        """⚠️ 红线同时压着三张卡时，一卡一个倒计时就是三个互相矛盾的数字并排。
           写的是最快结束的那一场 —— 唯一一个「马上要发生变化」的数。
        """
        now = self.at(self.today, 16)
        self.event(self.at(self.today, 15), self.at(self.today, 18), name="Long")
        self.event(self.at(self.today, 15), self.at(self.today, 16, 42), name="Short")
        self.assertEqual(self.one_column(now=now).now_left, "42m left")

    def test_there_is_no_countdown_when_the_line_crosses_nothing(self):
        now = self.at(self.today, 20)
        self.event(self.at(self.today, 15), self.at(self.today, 17))
        self.assertIsNone(self.one_column(now=now).now_left)

    def test_the_countdown_rounds_up_so_it_never_reads_zero_while_running(self):
        """⚠️ 向下取整的话最后 59 秒写的是「0m left」，而那一分钟活动还在进行。
           一个写着 0 的倒计时读起来是「已经结束了」。
        """
        self.assertEqual(schedule.remaining(datetime.timedelta(seconds=1)), "1m left")
        self.assertEqual(schedule.remaining(datetime.timedelta(minutes=81)),
                         "1h 21m left")
        self.assertEqual(schedule.remaining(datetime.timedelta(hours=2)), "2h left")

    def test_the_clock_reads_the_way_the_reference_does(self):
        self.assertEqual(schedule.clock(datetime.time(15, 0)), "3pm")
        self.assertEqual(schedule.clock(datetime.time(10, 15)), "10:15am")
        # 中午和午夜是这个写法唯一两个容易写反的地方。
        self.assertEqual(schedule.clock(datetime.time(0, 0)), "12am")
        self.assertEqual(schedule.clock(datetime.time(12, 0)), "12pm")


class ScheduleWindowTests(TestCase):
    """窗口停在哪几天，箭头能翻到哪儿（2026-08-18 拍板：一次四天，不早于今天）。"""

    def setUp(self):
        self.today = local_today()

    def test_the_window_opens_on_today(self):
        self.assertEqual(schedule.first_day(None, schedule.floor_day()), self.today)

    def test_it_covers_four_days(self):
        days = schedule.window(self.today)
        self.assertEqual(len(days), 4)
        self.assertEqual(days[-1], self.today + datetime.timedelta(days=3))

    def test_a_day_in_the_past_is_pulled_forward_to_today(self):
        """左边的列表是「今天零点起」的。日程翻得比它早，翻出来的活动在左边
        永远找不到 —— 两块并排的东西就不再答同一个问题了。
        """
        asked = self.today - datetime.timedelta(days=10)
        self.assertEqual(schedule.first_day(asked, schedule.floor_day()), self.today)

    def test_a_day_in_the_future_is_honoured(self):
        asked = self.today + datetime.timedelta(days=10)
        self.assertEqual(schedule.first_day(asked, schedule.floor_day()), asked)

    def test_a_later_filter_start_carries_the_window_with_it(self):
        """筛 From=下个月，日程就该跳到下个月 —— 否则左边整屏九月、右边四天空白。"""
        later = day_start(self.today + datetime.timedelta(days=30))
        floor = schedule.floor_day(later)
        self.assertEqual(schedule.first_day(None, floor), local_date_of(later))

    def test_an_earlier_filter_start_does_not_drag_the_window_back(self):
        """⚠️ 单向夹紧。往回拽的话，「改一个筛选」就等于「丢掉我翻到的位置」。"""
        parked = self.today + datetime.timedelta(days=20)
        floor = schedule.floor_day(day_start(self.today))
        self.assertEqual(schedule.first_day(parked, floor), parked)

    def test_a_filter_start_in_the_past_never_unlocks_the_past(self):
        past = day_start(self.today - datetime.timedelta(days=30))
        self.assertEqual(schedule.floor_day(past), self.today)

    def test_a_broken_date_in_the_url_falls_back_instead_of_exploding(self):
        # 这个参数会出现在分享出去的链接里，而链接是会被人手改的。
        for raw in ["banana", "", None, "2026-13-45"]:
            self.assertIsNone(schedule.parse_day(raw))

    def test_there_is_one_pair_of_arrows_for_each_width(self):
        """🔴 一次翻几天 = 那一档**看得见**几列，而那是 CSS 说了算的 ——
           所以三档各有一对自己的箭头，目标日期由服务端算好。
        """
        nav = schedule.navigation(self.today, self.today)
        self.assertEqual([tier["days"] for tier in nav], list(schedule.VISIBLE_DAYS))
        for tier in nav:
            self.assertEqual(
                tier["next"], self.today + datetime.timedelta(days=tier["days"]))

    def test_the_range_label_covers_only_the_days_that_show(self):
        """⚠️ 中间那档只画两列，标题写四天的范围就是一句屏幕当场证伪的话。"""
        nav = {tier["days"]: tier for tier in schedule.navigation(self.today, self.today)}
        self.assertEqual(nav[2]["upto"], self.today + datetime.timedelta(days=1))
        self.assertEqual(nav[4]["upto"], self.today + datetime.timedelta(days=3))

    def test_the_left_arrow_is_spent_at_the_floor(self):
        for tier in schedule.navigation(self.today, self.today):
            self.assertIsNone(tier["prev"])

    def test_the_left_arrow_never_steps_past_the_floor(self):
        first = self.today + datetime.timedelta(days=2)
        for tier in schedule.navigation(first, self.today):
            self.assertEqual(tier["prev"], self.today,
                             "往回一步跨过了今天")


class SchedulePageTests(PageTestCase):
    """日程作为页面的一部分（2026-08-18）。"""

    def setUp(self):
        super().setUp()
        self.today = local_today()

    def at(self, hour, minute=0, day=None):
        return day_start(day or self.today) + datetime.timedelta(hours=hour, minutes=minute)

    def page(self, **params):
        self.login(self.lisi)
        return self.client.get(reverse("events:event_list"), params).content.decode()

    def cards(self, html):
        return re.findall(r'<a class="schedule-card[^"]*"[^>]*>(.*?)</a>', html, re.S)

    def test_the_panel_holds_a_schedule_now_and_not_a_placeholder(self):
        html = self.page()
        self.assertIn('id="schedule"', html)
        self.assertNotIn("schedule-placeholder", html)
        self.assertNotIn("data-size-readout", html)

    def test_the_schedule_is_not_limited_to_one_page_of_the_list(self):
        """🔴 你说的那一条：左边二十条，右边该是那几天的**全部**。

        列表是分页的（EVENTS_PER_PAGE = 20），日程不是 —— 一个「今天有多少事」
        的答案被翻页截断，是这一块存在的意义本身被截断。
        """
        for number in range(EVENTS_PER_PAGE + 5):
            make_event(ministry=self.pantry, owner=self.zhang.contact,
                       name=f"Event {number}",
                       start_time=self.at(9), end_time=self.at(10))
        html = self.page()
        # ⚠️ 期望值从数据库来，不写死一个数：PageTestCase 自己还建了一场活动，
        #    而一个手抄的常数会在下一个人往 setUp 里加东西时无声地对不上。
        expected = Event.objects.visible_to_volunteers().count()
        self.assertGreater(expected, EVENTS_PER_PAGE, "这个测试要先撑破一页才有意义")
        self.assertEqual(html.count("data-schedule-card"), expected)

    def test_the_schedule_follows_the_search_box(self):
        """2026-08-18 拍板：跟筛选，不跟分页。"""
        make_event(ministry=self.pantry, owner=self.zhang.contact, name="Kitchen shift",
                   start_time=self.at(9), end_time=self.at(10))
        make_event(ministry=self.pantry, owner=self.zhang.contact, name="Garden day",
                   start_time=self.at(11), end_time=self.at(12))
        html = self.page(q="kitchen")
        self.assertEqual(html.count("data-schedule-card"), 1)
        self.assertIn("Kitchen shift", "".join(self.cards(html)))

    def test_the_schedule_follows_the_ministry_dropdown(self):
        make_event(ministry=self.tax, owner=self.zhang.contact, name="Tax clinic",
                   start_time=self.at(9), end_time=self.at(10))
        html = self.page(ministry=self.pantry.pk)
        self.assertNotIn("Tax clinic", "".join(self.cards(html)))

    def test_a_draft_never_reaches_the_schedule(self):
        """⚠️ 同一道门（visible_to_volunteers），不是一条绕过草稿的旁路。
           这一块是新的，而新的取数路径正是权限最容易被漏掉的地方。
        """
        make_event(ministry=self.pantry, owner=self.zhang.contact, name="Secret plan",
                   start_time=self.at(9), end_time=self.at(10),
                   status=Event.Status.DRAFT)
        self.assertNotIn("Secret plan", self.page())

    def test_this_mornings_event_is_still_on_todays_column(self):
        """⚠️ 日程的查询里**没有** from_today()：那条按 start_time 切，会切掉
           「昨晚开始、今天早上才结束」的那一场 —— 而它在今天这一列上是要画的。
        """
        yesterday = self.today - datetime.timedelta(days=1)
        make_event(ministry=self.pantry, owner=self.zhang.contact, name="Overnight watch",
                   start_time=self.at(22, day=yesterday), end_time=self.at(2))
        self.assertIn("Overnight watch", self.page())

    def test_the_filter_carries_the_window_as_a_form_field(self):
        """⚠️ `method="get"` 的表单只带它自己的字段。窗口位置不作为字段待在
           表单里的话，翻到第三屏之后随便改一个筛选，日程就自己弹回起点 ——
           而列表看起来完全正常。`scope` 当初栽的是同一个跟头。
        """
        html = self.page()
        self.assertIn('name="from"', html)
        self.assertIn('id="schedule-from"', html)

    def test_filtering_brings_a_fresh_schedule_back_with_it(self):
        """🔴 筛选换掉的是 `#event-results`，而日程在它外面。

        少了 out-of-band 那一块，筛完之后左边是新结果、右边还是上一次的日程，
        而两边**各自**看起来都正常 —— 只有并排读才会发现它们答的不是同一个问题。
        """
        self.login(self.lisi)
        fragment = self.client.get(
            reverse("events:event_list"), HTTP_HX_REQUEST="true").content.decode()
        self.assertIn('id="schedule"', fragment)
        self.assertIn('hx-swap-oob="true"', fragment)
        # ⚠️ 它必须和 #event-results **平级**：htmx 只在响应顶层找 oob，
        #    写在里面不报错，那一整块日程会被当成普通内容贴进活动卡片中间。
        self.assertLess(fragment.index("</div>"), fragment.index('id="schedule"'))

    def test_the_whole_page_does_not_carry_two_copies_of_it(self):
        html = self.page()
        self.assertEqual(html.count('id="schedule"'), 1)
        self.assertNotIn('hx-swap-oob', html)

    def test_the_arrows_are_real_links_as_well_as_htmx(self):
        """⭐ D24 的渐进增强：关掉 JS 照样翻，只是整页重来。"""
        html = self.page()
        arrow = re.search(r'<a class="schedule-arrow"[^>]*>', html).group(0)
        self.assertIn('href="/events/?', arrow)
        self.assertIn('hx-get="/events/schedule/?', arrow)

    def test_the_arrow_endpoint_returns_the_block_and_moves_the_hidden_field(self):
        self.login(self.lisi)
        target = self.today + datetime.timedelta(days=4)
        html = self.client.get(
            reverse("events:event_schedule"), {"from": target.isoformat()},
        ).content.decode()
        self.assertIn('id="schedule"', html)
        # ⚠️ 翻页要顺手把筛选卡里那个 from 也改掉，否则「先翻页、再筛选」
        #    会退回翻页之前的窗口。
        self.assertIn('id="schedule-from"', html)
        self.assertIn(f'value="{target.isoformat()}"', html)
        # 而它自己**不是** oob —— 它就是被换掉的那一块。
        block = html[html.index('id="schedule"') - 40:html.index('id="schedule"') + 200]
        self.assertNotIn("hx-swap-oob", block)

    def test_the_schedule_needs_a_login_like_every_other_page_here(self):
        response = self.client.get(reverse("events:event_schedule"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response["Location"])

    def test_today_is_marked_on_the_column_and_on_its_heading(self):
        html = self.page()
        self.assertIn("schedule-day is-today", html)
        self.assertIn("schedule-col is-today", html)


class ScheduleGeometryGuardTests(SimpleTestCase):
    """🔴 每小时多少像素这个数**存在三份**，而三份之间没有任何东西连着。

    Python 算卡片的 top/height，CSS 画格线，JS 摆红线。对不上的表现不是报错，
    是每张卡都和它自己的时间差半格 —— 而这在只有一场活动的开发数据上看不出来。
    """

    def source(self, *parts):
        return (Path(settings.BASE_DIR).joinpath(*parts)).read_text()

    def test_the_stylesheet_agrees_with_python_about_the_hour(self):
        css = self.source("assets", "app.css")
        self.assertIn(f"--schedule-hour: {schedule.PX_PER_HOUR}px", css)

    def test_the_clock_is_one_timer_on_the_document_not_one_per_block(self):
        """🔴 翻页和筛选每次都把整块日程换成新的 DOM。

        计时器挂在「一块日程」上的话，旧的那份既不会被回收也没人停得掉 ——
        翻五十次页就是五十个 `setInterval` 对着五十棵脱离文档的树跑，外加
        五十个永远摘不掉的 `visibilitychange`。⚠️ 画面全对，只有页面开久了
        才慢慢变卡，所以这一条只能靠守卫钉着。
        """
        js = self.source("assets", "js", "app.js")
        self.assertIn("setInterval(paintAllSchedules", js)
        self.assertNotRegex(
            js, r"setInterval\(\(\) => paintSchedule\(root\)",
            "计时器又挂回单块日程上了")

    def test_the_script_falls_back_to_the_same_hour(self):
        # JS 是从 CSS 变量里读的，这个数字只是读不到时的兜底 —— 但兜底错了
        # 同样是「差半格」，而且只在样式表没加载出来的那一刻。
        js = self.source("assets", "js", "app.js")
        self.assertIn(f"|| {schedule.PX_PER_HOUR};", js)

    def test_an_offset_card_narrows_instead_of_leaving_its_own_day(self):
        """🔴 第一版把整张卡 `translate` 过去，第三档就探进了隔壁那一列 ——
           读起来像是那天的活动。

        ⚠️ 这个错**在这一层的测试里没有任何表现**：top / height / depth 全是对的，
           是浏览器里看出来的。所以钉的是写法本身：右边缘不许参与偏移。
        """
        css = re.sub(r"/\*.*?\*/", "", self.source("assets", "app.css"), flags=re.S)
        block = re.search(r"\.schedule-card \{(.*?)\}", css, re.S).group(1)
        self.assertRegex(block, r"left:\s*calc\([^)]*var\(--depth")
        self.assertRegex(block, r"right:\s*[\d.]+rem;",
                         "右边缘必须是定值 —— 它一动，卡片就会走出自己那一列")
        self.assertRegex(block, r"translate:\s*0 ",
                         "水平方向不许再用 translate")

    def test_the_day_columns_are_written_out_rather_than_repeated_from_a_variable(self):
        """⚠️ `repeat(var(--n), …)` 不报错，它**整条声明失效** —— 四列会叠成
           一列。列数因此写死在每个断点里。
        """
        # ⚠️ 先剥注释再查 —— 上面那条禁忌本身就写在 app.css 的注释里，
        #    不剥的话这条守卫抓到的是那句话，而不是代码。
        css = re.sub(r"/\*.*?\*/", "", self.source("assets", "app.css"), flags=re.S)
        self.assertNotIn("repeat(var(", css)
        for count in schedule.VISIBLE_DAYS:
            self.assertIn(f"repeat({count}, minmax(0, 1fr))", css)


class SchedulePanelTests(PageTestCase):
    """点日程上的一张卡：详情就地打开，左边跟着翻页并高亮（2026-08-18）。"""

    def setUp(self):
        super().setUp()
        self.today = local_today()

    def at(self, hour, day_offset=0):
        return day_start(self.today + datetime.timedelta(days=day_offset)) + hour * HOUR

    def make(self, name, hour=9, day_offset=0, **kwargs):
        return make_event(
            ministry=self.pantry, owner=self.zhang.contact, name=name,
            start_time=self.at(hour, day_offset),
            end_time=self.at(hour + 1, day_offset), **kwargs)

    def panel(self, event, user=None, **params):
        self.login(user or self.lisi)
        return self.client.get(
            reverse("events:event_detail_panel", args=[event.pk]), params,
        )

    def test_it_returns_the_detail_and_the_list_in_one_response(self):
        """🔴 一次请求，两块东西。

        分成两次的话，慢网下它们会先后落地，而中间那一下是「右边已经是新活动、
        左边还高亮着上一个」—— 一个自相矛盾的画面，而且只在慢网上出现。
        """
        event = self.make("Kitchen shift")
        html = self.panel(event).content.decode()
        self.assertIn("Kitchen shift", html)
        self.assertIn('id="event-results"', html)
        self.assertIn('hx-swap-oob="true"', html)

    def test_the_list_it_returns_is_the_page_that_event_is_on(self):
        """左边要翻到**那一场所在的**那一页，不是第一页。"""
        # 第一页塞满，把目标顶到第二页去。
        for number in range(EVENTS_PER_PAGE):
            self.make(f"Filler {number}", hour=8)
        target = self.make("Late in the list", hour=23, day_offset=6)
        html = self.panel(target).content.decode()
        self.assertIn("Late in the list", html)
        self.assertIn("Page 2 of", html)

    def test_the_row_for_that_event_carries_the_highlight(self):
        event = self.make("Kitchen shift")
        html = self.panel(event).content.decode()
        row = re.search(r'<li class="event-row[^"]*"\s+data-event="%d"' % event.pk, html)
        self.assertIsNotNone(row, "找不到那一行")
        self.assertIn("is-picked", row.group(0))

    def test_only_that_one_row_is_highlighted(self):
        event = self.make("Kitchen shift")
        self.make("Another one", hour=11)
        html = self.panel(event).content.decode()
        self.assertEqual(html.count("is-picked"), 1)

    def test_an_event_the_list_cannot_show_opens_without_highlighting_anybody(self):
        """⚠️ 日程画的是「那几天里全部的活动」，列表还带着筛选。两边天然可以
           不重合 —— 这时候要开的是详情，**不是**高亮一个碰巧在第一页的别人。
        """
        event = self.make("Kitchen shift")
        self.make("Garden day", hour=11)
        # 筛掉它自己：日程上点得到，列表里没有。
        html = self.panel(event, q="garden").content.decode()
        self.assertIn("Kitchen shift", html)        # 详情照开
        self.assertNotIn("is-picked", html)         # 但没人被圈上

    def test_the_page_is_computed_under_the_filter_that_is_on(self):
        """⚠️ 卡片的 hx-get 带着当前查询串，因为「第几页」取决于筛选。
           不带的话左边会翻到「没有筛选时的第几页」，也就是错的一页。
        """
        for number in range(EVENTS_PER_PAGE):
            self.make(f"Filler {number}", hour=8)
        target = self.make("Kitchen shift", hour=23, day_offset=6)
        # 筛掉那二十条之后，它是唯一一条，因此在第一页。
        html = self.panel(target, q="kitchen").content.decode()
        self.assertNotIn("Page 2 of", html)
        self.assertIn("is-picked", html)

    def test_a_draft_is_a_404_here_too(self):
        """⚠️ 面板是一条**新开的取数路径**，而新开的取数路径正是权限最容易漏掉
           的地方。它和整页共用 `_detail()`，这条测试钉的就是那次共用。
        """
        draft = self.make("Secret plan", status=Event.Status.DRAFT)
        self.assertEqual(self.panel(draft).status_code, 404)

    def test_it_needs_a_login(self):
        event = self.make("Kitchen shift")
        response = self.client.get(
            reverse("events:event_detail_panel", args=[event.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response["Location"])

    def test_the_panel_copy_leaves_out_the_back_link(self):
        """⚠️ 这是那一份正文在两处唯一的差别。面板里那条链接会把整页导航走，
           而人只是想关掉右边那一块 —— 而关掉的 × 就在几十像素之外。
        """
        event = self.make("Kitchen shift")
        panel = self.panel(event).content.decode()
        self.login(self.lisi)
        page = self.client.get(
            reverse("events:event_detail", args=[event.pk])).content.decode()
        self.assertIn("&larr; Events", page)
        self.assertNotIn("&larr; Events", panel)

    def test_the_body_is_one_file_shared_with_the_full_page(self):
        """⚠️ 不许有第二份「面板专用的精简详情」：两份会在第二次改动之后说两件
           不一样的事，而两边都渲染成功。守卫钉的是**两处 include 同一个文件**。
        """
        for name in ("event_detail.html", "_schedule_detail.html"):
            markup = (Path(settings.BASE_DIR) / "events" / "templates" / "events"
                      / name).read_text()
            self.assertIn("events/_event_detail_body.html", markup, name)

    def test_the_card_is_a_real_link_as_well_as_an_htmx_target(self):
        """⭐ D24 的渐进增强：没有 JS 时点一张日程卡就是整页跳到详情去。"""
        self.make("Kitchen shift")
        self.login(self.lisi)
        html = self.client.get(reverse("events:event_list")).content.decode()
        card = re.search(r'<a class="schedule-card[^>]*>', html, re.S).group(0)
        self.assertRegex(card, r'href="/events/\d+/"')
        self.assertRegex(card, r'hx-get="/events/\d+/panel/')
        self.assertIn('hx-target="#schedule-detail"', card)

    def test_the_panel_holds_the_schedule_and_the_detail_side_by_side(self):
        """⚠️ 日程那一块是**藏起来**，不是换掉 —— 关掉详情要回到你翻到的那个
           窗口、那个滚动位置。所以两块都在 DOM 里，由一个 Alpine 布尔选。
        """
        self.login(self.lisi)
        html = self.client.get(reverse("events:event_list")).content.decode()
        self.assertIn("detail: false", html)
        self.assertIn('id="schedule-detail"', html)
        self.assertRegex(html, r'x-show="!detail"')

    def test_a_left_hand_card_opens_the_panel_only_while_the_schedule_is_open(self):
        """🔴 一份标记，两种行为 —— 靠 `hx-trigger` 上的事件过滤器，不是两套标记。

        过滤器为假（日程关着）时 htmx **完全不接管**这次点击，于是浏览器照常
        跟着 `href` 走，跳到整页详情。⭐ 没有 JS 时也是这条路（D24）。

        ⚠️ 过滤器读的是外壳上的 class，不是 Alpine 的变量：这个链接在外壳里、
           却不在面板的作用域里。
        """
        self.make("Kitchen shift")
        self.login(self.lisi)
        html = self.client.get(reverse("events:event_list")).content.decode()
        link = re.search(r'<a href="/events/\d+/" class="event-row-link".*?>', html, re.S)
        self.assertIsNotNone(link, "左边卡片的整块点击区没了")
        opened = link.group(0)
        self.assertRegex(opened, r'hx-get="/events/\d+/panel/')
        self.assertIn('hx-target="#schedule-detail"', opened)
        self.assertIn("events-shell.is-open", opened,
                      "少了这个过滤器，日程关着时点卡片也不会跳页了")

    def test_both_ways_in_reach_the_same_endpoint(self):
        """⚠️ 从日程点、从列表点，是**同一场活动**的两个入口。两边打不同的端点，
           「右边开着的是哪一场」迟早会有两个答案。

        ⚠️ 比的是同一个 pk 的两处。第一版拿整页所有 hx-get 去比，于是页面上有
           两场活动就当场失败 —— 那测的是「只有一场活动」，不是这里要的东西。
        """
        event = self.make("Kitchen shift")
        self.login(self.lisi)
        html = self.client.get(reverse("events:event_list")).content.decode()
        card = re.search(
            r'<a class="schedule-card.*?data-event="%d"[^>]*>' % event.pk, html, re.S)
        self.assertIsNotNone(card, "日程上没有这一场的卡片")
        row = re.search(
            r'<a href="/events/%d/" class="event-row-link".*?>' % event.pk, html, re.S)
        self.assertIsNotNone(row, "列表里没有这一场的点击区")

        def endpoint(markup):
            return re.search(r'hx-get="([^"]+)"', markup).group(1)

        self.assertEqual(endpoint(card.group(0)), endpoint(row.group(0)))

    def test_the_detail_state_lives_outside_both_of_the_blocks_it_switches(self):
        """🔴 和外层那个 `schedule` 开关同一条规矩。

        `#schedule` 会被箭头翻页整个换掉，`#schedule-detail` 会被每次点击换掉。
        状态声明在被换掉的那块里，等于每换一次自己归零一次 —— 而这件事只在
        真站上发生，读第一次渲染的单元测试永远看不到。
        """
        self.login(self.lisi)
        html = self.client.get(reverse("events:event_list")).content.decode()
        # ⚠️ `detail` 2026-08-19 从面板挪到了外壳，和 `schedule` 同一个 x-data ——
        #    左边那些活动卡片也要翻它，而它们够不着面板的作用域。这条守卫钉的
        #    仍然是同一件事：它必须在**被换掉的那两块之外**。
        state = html.index("detail: false")
        self.assertLess(state, html.index('id="event-results"'))
        self.assertLess(state, html.index('id="schedule"'))
        self.assertLess(state, html.index('id="schedule-detail"'))


class ScheduleOpeningViewTests(SimpleTestCase):
    """打开时停在哪儿，以及日程开着时钉住的筛选卡（2026-08-18）。

    两件事都是「不报错的」那一类：滚动位置错了只是第一眼看到一片空白，
    sticky 少一个条件只是「没钉住」，看起来都像本来就这样。
    """

    def source(self, *parts):
        return (Path(settings.BASE_DIR).joinpath(*parts)).read_text()

    def styles(self):
        return re.sub(r"/\*.*?\*/", "", self.source("assets", "app.css"), flags=re.S)

    def block(self, selector):
        found = [body for sel, body in
                 re.findall(r"([^{}]+?)\{([^{}]*?)\}", self.styles(), re.S)
                 if selector in {one.strip() for one in sel.split(",")}]
        self.assertTrue(found, f"`{selector}` 从 app.css 里没了")
        return "\n".join(found)

    def test_it_opens_on_the_morning_not_on_midnight(self):
        """🔴 一天里最不可能有活动的正是凌晨那几小时。从 0:00 开始的话，
           日程第一眼是一块空白，人得先自己往下拖才知道有没有东西 ——
           而一屏日历的全部意义就是不必先做这一步。
        """
        js = self.source("assets", "js", "app.js")
        self.assertIn("const SCHEDULE_OPENS_AT_HOUR = 7;", js)
        self.assertIn("SCHEDULE_OPENS_AT_HOUR * hourPx", js)

    def test_now_is_a_floor_on_that_and_never_pulls_it_earlier(self):
        """⚠️ 两条规则取**更晚**的那个：7am 打底，而傍晚打开时窗口跟着「现在」走。

        写成「有红线就跟红线」的话（2026-08-18 之前就是），早上八点打开会停在
        凌晨 5 点半 —— 比 7am 还早，正是这次要改掉的那件事。
        """
        js = self.source("assets", "js", "app.js")
        self.assertRegex(js, r"top = Math\.max\(top, parseFloat\(line\.style\.top\)")

    def test_an_early_event_does_not_drag_the_opening_position_around(self):
        """⚠️ 凌晨的活动**不会**把窗口往上拽（2026-08-18 明确选择）。

        宁可让那种少见的情况多滚一下，也不要「每翻一页起点都不一样」——
        不可预测的起点是每次都要重新找位置。所以那段「找最早一张卡」的兜底
        连同它的 8am 常数一起删了。
        """
        js = self.source("assets", "js", "app.js")
        self.assertNotIn("Math.min(...tops)", js)
        self.assertNotIn("8 * scheduleHourPx", js)

    def test_the_filter_card_is_pinned_only_while_the_schedule_is_open(self):
        """⚠️ 关着那一档是全站最常打开的默认视图，而它现在没有人抱怨 ——
           「关着时逐像素等于改之前」那条决定在这里同样成立。
        """
        css = self.styles()
        pinned = re.findall(r"([^{}]*\.filter-card)\s*\{([^}]*position:\s*sticky[^}]*)\}",
                            css, re.S)
        self.assertTrue(pinned, "筛选卡没有被钉住的规则了")
        for selector, _ in pinned:
            self.assertIn(".is-open", selector,
                          f"`{selector.strip()}` 在日程关着的时候也钉住了")

    def test_it_hangs_below_the_same_top_bar_the_panel_clears(self):
        # 两块并排的东西钉在不同的高度上，是一眼看得出来的错位。
        block = self.block(".events-shell.is-open .filter-card")
        self.assertRegex(block, r"top:\s*calc\(var\(--top-bar-h\)")

    def test_it_stacks_above_the_rows_that_scroll_under_it(self):
        """⚠️ 活动行带 `relative`（模板上那个 class），也就是它们参与层叠。
           少了 z-index，滚上来的卡片会从筛选卡**上面**穿过去。
        """
        self.assertRegex(self.block(".events-shell.is-open .filter-card"),
                         r"z-index:\s*\d+")

    def test_paging_scrolls_clear_of_the_pinned_filter(self):
        """🔴 钉住的东西会挡住「滚到这里」。

        翻页用 `show:#event-results:top`，也就是把结果区顶到**视口最上面** ——
        而钉住的筛选卡正好压在那儿。表现：翻到下一页，第一张卡完全不见了。
        这是钉住筛选卡那一批引入的，浏览器没做错任何事，只是那个位置底下压着
        别的东西。

        ⚠️ 让开的距离要含**实测的**卡高（`--filter-h`）—— 那张卡窄屏上换行、
           报错时多一行字，写死一个数在这两种情况下要么还挡着、要么空一截。
        """
        block = self.block(".events-shell.is-open #event-results")
        self.assertRegex(block, r"scroll-margin-top:\s*calc\(")
        self.assertIn("var(--filter-h", block)
        self.assertIn("var(--top-bar-h)", block)

    def test_the_filter_height_is_measured_rather_than_written_down(self):
        js = self.source("assets", "js", "app.js")
        self.assertIn("--filter-h", js)
        # ⚠️ ResizeObserver 而不是 resize 事件：开关日程那 560ms 过渡里列宽变化
        #    带来的换行根本不触发 resize，于是读数会停在过渡前的值。
        self.assertRegex(js, r"new ResizeObserver\([^)]*\)[\s\S]{0,200}\.observe\(card\)")

    def test_the_hook_is_a_class_of_its_own_not_form_dot_card(self):
        """⚠️ 靠 `form.card` 去匹配会命中将来任何一张长在表单里的卡片。"""
        markup = self.source("events", "templates", "events", "_period_filter.html")
        self.assertIn('class="filter-card card', markup)
        self.assertNotIn("form.card", self.styles())


class ScheduleColourTests(SimpleTestCase):
    """那 24 格颜色：够不够、归没归族、深色下变不变。"""

    def source(self, *parts):
        return (Path(settings.BASE_DIR).joinpath(*parts)).read_text()

    def test_every_slot_in_the_palette_has_a_colour_and_a_class(self):
        """⚠️ 少一格，尾巴上那个颜色会落到 `--schedule-fill` 的兜底值上 ——
           一张灰卡混在彩色里。多一格则是那个变量根本没定义，CSS 不报错，
           它渲染成一张**透明**的卡。
        """
        css = self.source("assets", "app.css")
        for slot in range(schedule.PALETTE):
            self.assertIn(f"--schedule-fill-{slot}:", css)
            self.assertIn(f".schedule-card--{slot}", css)
        self.assertNotIn(f"--schedule-fill-{schedule.PALETTE}:", css)
        # ⚠️ 色族表是**对这些色值的判断**，所以它和上面那 24 行是一对：
        #    加一格颜色而忘了归族，配到它的活动会在 `_FAMILY_OF` 上 KeyError ——
        #    那是 500，不是画得难看，所以这里连着钉。
        filed = [slot for family in schedule.COLOUR_FAMILIES for slot in family]
        self.assertEqual(sorted(filed), list(range(schedule.PALETTE)))

    #: 两格颜色「看起来一样」的判据。HLS 空间，色相加权 ×3（同一亮度下，
    #: 色相差是人最先看出来的那一维）。0.04 是量出来的：改这张盘的那一天，
    #: 跨族最近的一对是 0.048，而出问题的那一对是 0.034。
    SAME_LOOKING = 0.04

    def swatches(self):
        css = self.source("assets", "app.css")
        return {int(slot): value for slot, value in
                re.findall(r"--schedule-fill-(\d+):\s*(#[0-9a-fA-F]{6})", css)}

    def test_two_swatches_that_may_sit_side_by_side_never_look_the_same(self):
        """🔴 「归对了族」以前只有眼睛看得出来 —— 现在能量了。

        撞色回避只保证**不同族**的两格可以相邻。所以这张表要是把两个长得一样的
        色值分进了两族，回避就等于没有：它们照样会挨在一起，而测试全绿。

        这不是假想的：2026-08-19 把偏灰的几格兑白之后，薄荷绿（3）和青族那几格
        变得几乎一样，而它当时归在绿族 —— 也就是**允许相邻**。这条守卫就是那次
        的产物，它会当场抓住同一类改动。

        ⚠️ 同族内部随便像，它们永远不会相邻。
        """
        import colorsys
        import itertools

        swatches = self.swatches()
        family_of = {slot: number
                     for number, family in enumerate(schedule.COLOUR_FAMILIES)
                     for slot in family}

        def hls(value):
            red, green, blue = (int(value[at:at + 2], 16) / 255 for at in (1, 3, 5))
            return colorsys.rgb_to_hls(red, green, blue)

        def apart(one, other):
            hue, light, sat = hls(one)
            hue2, light2, sat2 = hls(other)
            # ⚠️ 色相是环状的，所以走短的那一边 —— 不绕的话红和粉会被算成天差地别。
            spin = min(abs(hue - hue2), 1 - abs(hue - hue2))
            return (spin * 3) ** 2 + (light - light2) ** 2 + (sat - sat2) ** 2

        too_close = [
            (round(apart(swatches[a], swatches[b]), 4), a, swatches[a], b, swatches[b])
            for a, b in itertools.combinations(sorted(swatches), 2)
            if family_of[a] != family_of[b]
            and apart(swatches[a], swatches[b]) < self.SAME_LOOKING
        ]
        self.assertEqual(
            too_close, [],
            "这两格长得一样却分在两族，于是允许相邻 —— 要么把它们并成一族，"
            "要么把其中一格调开：\n" + "\n".join(map(str, sorted(too_close))))

    def test_dark_mode_does_not_repaint_the_cards(self):
        """🔴 深色下卡片颜色和浅色**一模一样**（2026-08-18 定）。

        日程是这一页唯一的彩色信息载体，而「这一场是什么颜色」必须在两个模式下
        是同一个答案 —— 白天记住的粉色，晚上还得是那个粉色。

        ⚠️ 连带的一条：文字必须留在深色。浅底白字在这里不是「看不清」，是几乎
           看不见 —— 而这个项目深色模式下别处的规矩全是「文字翻白」，所以这条
           是最可能被下一个人顺手改掉的。
        """
        css = re.sub(r"/\*.*?\*/", "", self.source("assets", "app.css"), flags=re.S)
        # ⚠️ 连 `.is-cancelled` 一起查 —— 它是这条规矩最容易被开的一个例外，
        #    而第一版正是在它身上破的功。
        for selector, block in re.findall(
                r"(\.dark [^{]*\.schedule-card[^{]*)\{([^}]*)\}", css):
            self.assertNotIn("background-color", block,
                             f"`{selector.strip()}` 在深色下又换了一次底色")
            self.assertNotIn("color:", block,
                             f"`{selector.strip()}` 在深色下又换了一次字色")


class ScheduleLooksLikeCardsGuardTests(SimpleTestCase):
    """竖线去掉、整块坐在卡片上（2026-08-18）。全是「不报错的」那一类。"""

    def source(self, *parts):
        return (Path(settings.BASE_DIR).joinpath(*parts)).read_text()

    def styles(self):
        return re.sub(r"/\*.*?\*/", "", self.source("assets", "app.css"), flags=re.S)

    def block(self, selector):
        css = self.styles()
        found = [body for sel, body in re.findall(r"([^{}]+?)\{([^{}]*?)\}", css, re.S)
                 if selector in {one.strip() for one in sel.split(",")}]
        self.assertTrue(found, f"`{selector}` 从 app.css 里没了")
        return "\n".join(found)

    def test_the_columns_are_not_separated_by_a_line(self):
        # 竖线会把四天读成一张表格，而它们是四叠并排的卡片。
        self.assertNotRegex(self.block(".schedule-col"), r"border-left")

    def test_they_are_separated_by_a_gap_instead(self):
        # ⚠️ 去掉线之后，分开两天的**只剩这道缝** —— 它不能也一起没了。
        css = self.styles()
        self.assertRegex(css, r"--schedule-column-gap:\s*[\d.]+rem")
        # ⚠️ 表头和格子必须用**同一个**变量：两行用不同的缝，星期几就会和它
        #    底下那一列错开，而错开一两像素看起来只是「字没对齐」。
        self.assertEqual(css.count("column-gap: var(--schedule-column-gap)"), 1,
                         "两行的缝不是同一条声明给的")

    def test_the_hour_lines_are_painted_across_the_whole_grid(self):
        """⚠️ 画在**列**上的话，每道竖缝都会把横线切断一截 —— 一条断成四段的线
           读起来不是刻度，是四个盒子各自的边框，正好把刚去掉的竖线暗示回来。
        """
        self.assertIn("repeating-linear-gradient", self.block(".schedule-grid"))
        self.assertNotIn("repeating-linear-gradient", self.block(".schedule-col"))

    def test_the_panel_is_the_card_component_rather_than_a_second_copy_of_it(self):
        """⚠️ 浅色白卡、深色 ink-900、有大图时的毛玻璃，全部由现成的 `.card` 给。
           在这里另写一套颜色就是分叉的开始。
        """
        markup = self.source("events", "templates", "events", "event_list.html")
        self.assertIn('class="schedule-panel card"', markup)
        block = self.block(".schedule-panel")
        self.assertNotRegex(block, r"background-color",
                            "面板不该自己写底色 —— 那是 .card 的事")

    def test_the_card_component_does_not_quietly_unpin_the_panel(self):
        """🔴 `.card` 在 app.css 里排在 `.schedule-panel` **后面**，而它带着
           `position: relative`。同特异性、后来者胜 —— 于是日程会跟着列表一路
           滚出屏幕，而不是钉在顶栏底下。不报错，看起来像「本来就这样」。

        所以按回去的是 `.schedule-panel.card`（0-2-0），不是源码顺序：
        顺序会被下一个人重排，特异性不会。
        """
        self.assertRegex(self.block(".schedule-panel.card"), r"position:\s*sticky")
        css = self.styles()
        self.assertLess(css.index("  .schedule-panel {"), css.index("  .card {"),
                        "两条规则的先后变了 —— 这条守卫的前提没了，重新想一遍")

    def test_the_detail_does_not_divide_by_its_own_zoom(self):
        """🔴 `height: 100%`，不是 `calc(100% / 0.85)`。

        百分比在**已经缩放过的**坐标系里解析，所以 100% 渲染出来正好等于父元素
        的高度；再除一次是重复计算，详情比面板高出 84px（量出来的），多出来的
        那一截被面板的 `overflow: hidden` 裁掉**而且滚不到** —— 屏幕上看起来
        只是「这个活动没写那么多」。
        """
        block = self.block(".schedule-detail")
        self.assertRegex(block, r"height:\s*100%")
        self.assertNotRegex(block, r"height:\s*calc\(100%\s*/")

    def test_the_detail_scrolls_inside_itself(self):
        # 面板是一屏（上一批的决定），装不下的归里面这一层滚。
        self.assertRegex(self.block(".schedule-detail"), r"overflow-y:\s*auto")

    def test_the_neon_ring_keeps_the_cards_own_elevation(self):
        """🔴 光圈和「悬浮」共用 `box-shadow` 这一个属性，而它不能累加。

        直接写第二条会把卡片那两层悬浮影整个换掉 —— 选中的那张当场变平，
        而周围每一张都还浮着。所以光圈那几层后面必须跟着 `var(--card-elevation)`。

        ⚠️ 上一版用的是 `outline`，理由正是「box-shadow 那一格被占着」。
           变量把那条约束解掉了，而 outline **画不出辉光**：它只有宽度和颜色，
           没有模糊半径。
        """
        for selector in (".event-row.is-picked > .card",
                         ".dark .event-row.is-picked > .card"):
            block = self.block(selector)
            self.assertIn("var(--card-elevation)", block,
                          f"{selector} 把卡片的悬浮影冲掉了")
            self.assertIn("box-shadow", block)

    def test_the_no_shadow_case_is_a_transparent_shadow_not_none(self):
        """🔴 `--card-elevation` 会被代进一串逗号分隔的影子列表里，而 `none`
           在列表中间是**语法错误** —— 浏览器丢掉整条声明，霓虹光圈在深色模式下
           一起消失。不报错，只是「深色下这个功能没了」。
        """
        self.assertRegex(self.block(".dark .card"),
                         r"--card-elevation:\s*0 0 #0000")
        self.assertNotRegex(self.block(".dark .card"),
                            r"--card-elevation:\s*none")

    def test_the_glow_is_not_drawn_on_a_pseudo_element(self):
        """🔴 `.event-row > .card` 是 `overflow: hidden` 的，伪元素的外辉光会被
           整个裁掉 —— 屏幕上是「有环、没有光」，而 CSS 里每一层都写着。
           元素**自己**的 box-shadow 不受自己的 overflow 影响。

        ⚠️ 而且 `::after` 那一格已经被深色玻璃的亮边环占着。
        """
        css = self.styles()
        self.assertRegex(css, r"\.event-row > \.card \{[^}]*overflow:\s*hidden",
                         "这条守卫的前提（卡片自己裁内容）没了，重新想一遍")
        self.assertNotIn("is-picked > .card::", css)
        self.assertNotIn("is-picked .card::", css)

    def test_the_ring_is_remembered_by_event_not_by_page_number(self):
        """🔴 记页码的话，筛选一变，同一个页码指向的是另一批人 ——
           而高亮会安静地落在一个陌生的活动上。
        """
        js = self.source("assets", "js", "app.js")
        self.assertIn("let pickedEvent = null;", js)
        self.assertIn("row.dataset.event", js)
