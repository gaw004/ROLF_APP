"""A notice goes up, comes down, and is only ever seen by the people it is for."""

import datetime

from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError
from django.test import TestCase

from contact.models import Contact
from core.timeutils import local_now
from org.audience import Audience
from org.models import Assignment, Ministry, Position
from org.permissions import can_manage_notice, can_publish_notice
from notices.models import Notice
from notices.services import publish, take_down

NOW = local_now()
DAY = datetime.timedelta(days=1)


def make_person(last_name, **kwargs):
    kwargs.setdefault("legal_first_name", "Ping")
    return Contact.objects.create(
        contact_type=Contact.ContactType.INDIVIDUAL,
        legal_last_name=last_name, **kwargs)


def make_notice(ministry=None, **kwargs):
    """A published notice, up now, unless the caller says otherwise.

    ⚠️ An audience by default, for the reason make_event() grew one: without it
       a row is visible to nobody at all, so every assertion about who can see
       it passes for the wrong reason.
    """
    # ⚠️ get_or_create, because a test may build several notices and `code` is
    #    unique — a second `create` here is an IntegrityError that poisons the
    #    transaction, so every later query in that test fails with a
    #    TransactionManagementError pointing nowhere near the real cause.
    if ministry is None:
        ministry, _ = Ministry.objects.get_or_create(
            code="facilities", defaults={"name": "Facilities"})
    fields = {
        "title": "Car park closed",
        "body": "The car park is closed while the surface is relaid.",
        "ministry": ministry,
        "owner": make_person("Owner"),
        "status": Notice.Status.PUBLISHED,
        "starts_showing": NOW - DAY,
        "stops_showing": NOW + 30 * DAY,
        "visible_to_outsiders": True,
        "visible_to_all_staff": True,
    }
    ministries = kwargs.pop("visible_to_ministries", None)
    fields.update(kwargs)
    notice = Notice.objects.create(**fields)
    if ministries:
        notice.visible_to_ministries.set(ministries)
    return notice


class TheBoardClearsItselfTests(TestCase):
    """`showing()` and `past()` are two halves of one line, and the line is a clock.

    ⭐ This is what makes a required `stops_showing` defensible. Every product
       with this feature bounds it, for one reason: a board that never clears
       itself stops being read. What buys that here is that coming down is not
       losing — the row and its history stay, and `past()` is where they are.
    """

    def test_a_notice_that_has_not_gone_up_yet_is_not_showing(self):
        make_notice(starts_showing=NOW + DAY, stops_showing=NOW + 30 * DAY)
        self.assertFalse(Notice.objects.showing().exists())
        # ⚠️ And it is not in past() either. Scheduled-ahead is a third place to
        #    be, and a notice landing in "past" before it has ever been up would
        #    read as one that came and went.
        self.assertFalse(Notice.objects.past().exists())

    def test_a_notice_past_its_come_down_date_moves_to_past(self):
        notice = make_notice(starts_showing=NOW - 30 * DAY, stops_showing=NOW - DAY)
        self.assertFalse(Notice.objects.showing().exists())
        self.assertEqual(list(Notice.objects.past()), [notice])

    def test_a_draft_is_on_neither_list(self):
        make_notice(status=Notice.Status.DRAFT)
        self.assertFalse(Notice.objects.showing().exists())
        self.assertFalse(Notice.objects.past().exists())

    def test_the_row_and_the_queryset_agree_about_showing(self):
        """`is_showing` is written to match `showing()` condition for condition."""
        for kwargs in (
            {},
            {"starts_showing": NOW + DAY, "stops_showing": NOW + 2 * DAY},
            {"starts_showing": NOW - 2 * DAY, "stops_showing": NOW - DAY},
            {"status": Notice.Status.DRAFT},
        ):
            with self.subTest(**kwargs):
                notice = make_notice(**kwargs)
                self.assertEqual(
                    notice.is_showing,
                    Notice.objects.showing().filter(pk=notice.pk).exists())
                notice.delete()


class TheWindowHasToHaveLengthTests(TestCase):
    def test_a_notice_cannot_come_down_before_it_goes_up(self):
        notice = Notice(
            title="Backwards", body="…",
            ministry=Ministry.objects.get_or_create(
                code="facilities", defaults={"name": "Facilities"})[0],
            owner=make_person("Owner"),
            starts_showing=NOW + DAY, stops_showing=NOW,
            visible_to_all_staff=True,
        )
        with self.assertRaises(ValidationError) as caught:
            notice.full_clean()
        # The message lands on the box that was just typed in — CONSTRAINT_FIELD.
        self.assertIn("stops_showing", caught.exception.error_dict)

    def test_the_database_refuses_it_too(self):
        """⚠️ Not belt and braces: `bulk_create` never calls full_clean().

        D9 — a rule that can go in a constraint goes in a constraint.
        """
        ministry, _ = Ministry.objects.get_or_create(
            code="facilities", defaults={"name": "Facilities"})
        with self.assertRaises(IntegrityError):
            Notice.objects.bulk_create([Notice(
                title="Backwards", body="…", ministry=ministry,
                owner=make_person("Owner"),
                starts_showing=NOW + DAY, stops_showing=NOW,
            )])


class ComingDownEarlyTests(TestCase):
    """Two cases, because "did anybody see it" is a real difference."""

    def test_taking_down_a_notice_that_is_up_keeps_it_and_moves_it_to_past(self):
        notice = make_notice()
        take_down(notice)
        notice.refresh_from_db()
        self.assertEqual(notice.status, Notice.Status.PUBLISHED)
        self.assertFalse(Notice.objects.showing().exists())
        self.assertEqual(list(Notice.objects.past()), [notice])

    def test_taking_down_a_notice_that_never_went_up_returns_it_to_draft(self):
        """⚠️ Nobody saw it, so it has no past to be in.

        The first draft pulled both dates to now instead, and the constraint
        refused it outright — a 500 on a button labelled "Take down". Pinned
        here so that fix cannot be undone by somebody tidying the two branches
        into one.
        """
        notice = make_notice(starts_showing=NOW + DAY, stops_showing=NOW + 30 * DAY)
        take_down(notice)
        notice.refresh_from_db()
        self.assertEqual(notice.status, Notice.Status.DRAFT)
        self.assertFalse(Notice.objects.past().exists())

    def test_nothing_is_ever_deleted(self):
        notice = make_notice()
        take_down(notice)
        self.assertTrue(Notice.objects.filter(pk=notice.pk).exists())

    def test_publishing_leaves_the_go_up_date_alone(self):
        """A notice written on Friday to go up on Monday goes up on Monday."""
        monday = NOW + 3 * DAY
        notice = make_notice(
            status=Notice.Status.DRAFT,
            starts_showing=monday, stops_showing=monday + 30 * DAY)
        publish(notice)
        notice.refresh_from_db()
        self.assertEqual(notice.starts_showing, monday)
        self.assertFalse(Notice.objects.showing().exists())


class WhoSeesANoticeTests(TestCase):
    """The same three ticks as an event, read by the same one implementation."""

    def setUp(self):
        self.pantry = Ministry.objects.create(code="food_pantry", name="Food Pantry")
        self.tax = Ministry.objects.create(code="tax_help", name="Tax Help")
        self.outsider = make_person("Outsider")
        self.staff = make_person("Staff")
        self.employ(self.staff, self.a_staff_post(ministry=self.pantry))

    def a_staff_post(self, code="officer", ministry=None):
        return Position.objects.create(
            code=code, name=code, kind=Position.Kind.STAFF,
            compensation=Position.Compensation.PAID,
            ministry=ministry or self.pantry)

    def employ(self, person, post, **dates):
        dates.setdefault("start_date", (NOW - 30 * DAY).date())
        return Assignment.objects.create(contact=person, position=post, **dates)

    def seen_by(self, contact):
        return list(Notice.objects.showing().for_audience(contact))

    def test_an_outsider_does_not_see_a_staff_only_notice(self):
        notice = make_notice(visible_to_outsiders=False, visible_to_all_staff=True)
        self.assertEqual(self.seen_by(self.staff), [notice])
        self.assertEqual(self.seen_by(self.outsider), [])

    def test_staff_do_not_see_an_outsiders_only_notice(self):
        """Decision 10: "people with no current post" is not the widest tick."""
        notice = make_notice(visible_to_outsiders=True, visible_to_all_staff=False)
        self.assertEqual(self.seen_by(self.outsider), [notice])
        self.assertEqual(self.seen_by(self.staff), [])

    def test_a_notice_for_one_ministry_is_hidden_from_another(self):
        notice = make_notice(
            visible_to_outsiders=False, visible_to_all_staff=False,
            visible_to_ministries=[self.pantry])
        other = make_person("Other")
        self.employ(other, self.a_staff_post(code="clerk", ministry=self.tax))
        self.assertEqual(self.seen_by(self.staff), [notice])
        self.assertEqual(self.seen_by(other), [])

    def test_an_account_with_no_contact_is_an_outsider(self):
        make_notice(visible_to_outsiders=False, visible_to_all_staff=True)
        self.assertEqual(self.seen_by(None), [])

    def test_somebody_who_joined_after_the_notice_went_up_still_sees_it(self):
        """⭐ The only reason `AUDIENCE_DAY = None` exists.

        A notice put up a month ago, ticked for the food pantry, read today by
        somebody who joined the food pantry yesterday. Judged on the day it went
        up he was an outsider and the notice is simply not there; judged today
        he is staff and he sees it. The second is what anybody would expect, and
        there is no defensible reading of the first.

        ⚠️ Without this test the None branch has no landing point at all, and
           the obvious "fix" — pointing AUDIENCE_DAY at `starts_showing`,
           because it is the only date on the table — would look right, pass
           every other test in this file, and silently hide notices from every
           new member of staff.
        """
        notice = make_notice(
            starts_showing=NOW - 30 * DAY,
            visible_to_outsiders=False, visible_to_all_staff=False,
            visible_to_ministries=[self.pantry])
        newcomer = make_person("Newcomer")
        self.employ(
            newcomer, self.a_staff_post(code="new", ministry=self.pantry),
            start_date=(NOW - DAY).date())
        self.assertEqual(self.seen_by(newcomer), [notice])

    def test_somebody_who_has_left_stops_seeing_it(self):
        """The same clock, read the other way — the leaver is an outsider today."""
        notice = make_notice(
            visible_to_outsiders=False, visible_to_all_staff=False,
            visible_to_ministries=[self.pantry])
        leaver = make_person("Leaver")
        self.employ(
            leaver, self.a_staff_post(code="gone", ministry=self.pantry),
            start_date=(NOW - 30 * DAY).date(), end_date=(NOW - DAY).date())
        self.assertEqual(self.seen_by(leaver), [])
        self.assertEqual(self.seen_by(self.staff), [notice])


class ANoticeForNobodyIsRefusedTests(TestCase):
    """The empty and redundant rules reach this table through refuse_bad_audience.

    ⚠️ Its third branch. Before 2026-08-31 anything that was not a role was
       assumed to be an event, so this table fell into the event branch and died
       on `row.roles` with an AttributeError.
    """

    def setUp(self):
        from events.models import refuse_bad_audience
        self.refuse = refuse_bad_audience
        self.pantry = Ministry.objects.create(code="food_pantry", name="Food Pantry")

    def test_a_notice_visible_to_nobody_is_refused(self):
        notice = make_notice()
        with self.assertRaises(ValidationError) as caught:
            self.refuse(row=notice, spec=Audience.Spec(
                outsiders=False, all_staff=False, ministries=frozenset()))
        self.assertIn("needs to know", str(caught.exception))

    def test_all_staff_plus_a_named_ministry_is_refused(self):
        notice = make_notice()
        with self.assertRaises(ValidationError):
            self.refuse(row=notice, spec=Audience.Spec(
                outsiders=False, all_staff=True,
                ministries=frozenset({self.pantry.pk})))

    def test_a_notice_is_never_asked_to_be_narrower_than_children_it_cannot_have(self):
        """The branch itself: a valid audience on a notice simply passes."""
        notice = make_notice()
        self.refuse(row=notice, spec=Audience.Spec(
            outsiders=True, all_staff=True, ministries=frozenset()))


class WhoMayPublishANoticeTests(TestCase):
    def setUp(self):
        from accounts.models import User
        from org.models import MinistryRole
        self.pantry = Ministry.objects.create(code="food_pantry", name="Food Pantry")
        self.tax = Ministry.objects.create(code="tax_help", name="Tax Help")
        person = make_person("Admin")
        self.admin = User.objects.create_user(
            email="admin@example.com", password="x", contact=person)
        MinistryRole.objects.create(
            contact=person, ministry=self.pantry,
            role=MinistryRole.Role.ADMIN, start_date=(NOW - DAY).date())

    def test_a_ministry_admin_may_publish_for_their_own_ministry(self):
        self.assertTrue(can_publish_notice(self.admin, self.pantry))

    def test_a_ministry_admin_may_not_publish_for_another_ministry(self):
        self.assertFalse(can_publish_notice(self.admin, self.tax))

    def test_a_ministry_admin_cannot_manage_another_ministrys_notice(self):
        theirs = make_notice(ministry=self.pantry)
        somebody_elses = make_notice(ministry=self.tax)
        self.assertTrue(can_manage_notice(self.admin, theirs))
        self.assertFalse(can_manage_notice(self.admin, somebody_elses))

    def test_the_foundation_tier_can_take_down_anybodys_notice(self):
        """⚠️ Removing a harmful notice must not wait for its author."""
        from django.contrib.auth.models import Group
        from accounts.models import User
        from org.permissions import FOUNDATION_ADMIN_GROUP
        person = make_person("Chief")
        chief = User.objects.create_user(
            email="chief@example.com", password="x", contact=person)
        chief.groups.add(Group.objects.get_or_create(name=FOUNDATION_ADMIN_GROUP)[0])
        self.assertTrue(can_manage_notice(chief, make_notice(ministry=self.tax)))
        # ⚠️ …but writing one in a ministry's name is a different act.
        self.assertFalse(can_publish_notice(chief, self.tax))
