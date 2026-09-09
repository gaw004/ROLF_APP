"""A notice goes up, comes down, and is only ever seen by the people it is for."""

import datetime
import re

from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError
from django.test import TestCase

from contact.models import Contact
from core.timeutils import local_now, local_today
from org.audience import Audience
from org.models import Assignment, Ministry, Position
from org.permissions import can_manage_notice, can_publish_notice
from notices.models import Notice
from notices.services import publish, take_down

NOW = local_now()
DAY = datetime.timedelta(days=1)

#: 🔴 **日期一律从这里出发。** 不要去问那个 aware 的 `NOW` 常量要它的日子 ——
#: D16 那条「"今天"只有一种写法」在测试里同样成立，而 `local_now()` 是 aware 的，
#: 问它日期给的是它自己那个时区（UTC）的那一天。
#:
#: ⚠️ 这个错**一天里只有一部分时间是错的**：UTC 下午先翻页，于是「昨天」算出来
#:    正好等于本地的今天，`active()` 照收不误。所以它带着绿色上线，几天后在一次
#:    什么都没改的运行里变红 —— 读起来像是代码坏了。
#: 守卫见 core.tests.TimeSourceGuardTests.test_nobody_takes_the_day_off_an_aware_now。
TODAY = local_today()


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
        dates.setdefault("start_date", TODAY - 30 * DAY)
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
            start_date=TODAY - DAY)
        self.assertEqual(self.seen_by(newcomer), [notice])

    def test_somebody_who_has_left_stops_seeing_it(self):
        """The same clock, read the other way — the leaver is an outsider today."""
        notice = make_notice(
            visible_to_outsiders=False, visible_to_all_staff=False,
            visible_to_ministries=[self.pantry])
        leaver = make_person("Leaver")
        self.employ(
            leaver, self.a_staff_post(code="gone", ministry=self.pantry),
            start_date=TODAY - 30 * DAY, end_date=TODAY - DAY)
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
            role=MinistryRole.Role.ADMIN, start_date=TODAY - DAY)

    def test_a_ministry_admin_may_publish_for_their_own_ministry(self):
        self.assertTrue(can_publish_notice(self.admin, self.pantry))

    def test_a_ministry_admin_may_not_publish_for_another_ministry(self):
        self.assertFalse(can_publish_notice(self.admin, self.tax))

    def test_a_ministry_admin_cannot_manage_another_ministrys_notice(self):
        theirs = make_notice(ministry=self.pantry)
        somebody_elses = make_notice(ministry=self.tax)
        self.assertTrue(can_manage_notice(self.admin, theirs))
        self.assertFalse(can_manage_notice(self.admin, somebody_elses))

    def chief(self):
        """A foundation admin holding no MinistryRole at all."""
        from django.contrib.auth.models import Group
        from accounts.models import User
        from org.permissions import FOUNDATION_ADMIN_GROUP
        person = make_person("Chief")
        chief = User.objects.create_user(
            email="chief@example.com", password="x", contact=person)
        chief.groups.add(Group.objects.get_or_create(name=FOUNDATION_ADMIN_GROUP)[0])
        return chief

    def test_the_foundation_tier_can_take_down_anybodys_notice(self):
        """⚠️ Removing a harmful notice must not wait for its author."""
        self.assertTrue(can_manage_notice(self.chief(), make_notice(ministry=self.tax)))

    def test_the_foundation_tier_may_publish_in_any_ministrys_name(self):
        """2026-09-03, on the foundation's word — D41's restart condition, met.

        ⚠️ This asserted the **opposite** until today, and the sentence it
           carried ("writing one in a ministry's name is a different act from
           removing one") was true of the old policy rather than wrong. What
           changed is the policy, not an oversight; the reasoning both ways is
           on org.permissions.can_publish_notice.
        """
        chief = self.chief()
        self.assertTrue(can_publish_notice(chief, self.tax))
        self.assertTrue(can_publish_notice(chief, self.pantry))

    def test_a_volunteer_still_may_not_publish_anywhere(self):
        """⚠️ The widening is one tier wide, not a hole. Without this, the whole
           of the check above is satisfied by `return True`.
        """
        from accounts.models import User
        nobody = User.objects.create_user(
            email="nobody@example.com", password="x", contact=make_person("Nobody"))
        self.assertFalse(can_publish_notice(nobody, self.pantry))
        self.assertFalse(can_publish_notice(nobody, self.tax))


class NoticePagesTests(TestCase):
    """The three pages, and who each of them lets in."""

    def setUp(self):
        from accounts.models import User
        from org.models import MinistryRole
        self.pantry = Ministry.objects.create(code="food_pantry", name="Food Pantry")
        self.tax = Ministry.objects.create(code="tax_help", name="Tax Help")

        admin_contact = make_person("Admin")
        self.admin = User.objects.create_user(
            email="admin@example.com", password="pw", contact=admin_contact)
        MinistryRole.objects.create(
            contact=admin_contact, ministry=self.pantry,
            role=MinistryRole.Role.ADMIN, start_date=TODAY - DAY)

        self.volunteer = User.objects.create_user(
            email="vol@example.com", password="pw", contact=make_person("Volunteer"))

    def test_the_board_needs_a_session(self):
        response = self.client.get("/notices/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_a_volunteer_sees_the_board_but_not_the_manage_page(self):
        self.client.force_login(self.volunteer)
        self.assertEqual(self.client.get("/notices/").status_code, 200)
        self.assertEqual(self.client.get("/notices/manage/").status_code, 403)
        self.assertEqual(self.client.get("/notices/new/").status_code, 403)

    def test_a_ministry_admin_with_no_notices_gets_a_page_not_a_refusal(self):
        """⚠️ D27: "you have not written one yet" and "this is not for you" must
        not look the same. A new admin gets an empty page and a button.
        """
        self.client.force_login(self.admin)
        response = self.client.get("/notices/manage/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "have not put up any notices")

    def test_the_board_only_shows_notices_for_this_person(self):
        mine = make_notice(ministry=self.pantry, visible_to_outsiders=True,
                           visible_to_all_staff=False)
        staff_only = make_notice(ministry=self.pantry, title="Staff away day",
                                 visible_to_outsiders=False,
                                 visible_to_all_staff=True)
        self.client.force_login(self.volunteer)
        response = self.client.get("/notices/")
        self.assertContains(response, mine.title)
        self.assertNotContains(response, staff_only.title)

    def test_a_notice_that_came_down_moves_to_the_past_section(self):
        gone = make_notice(title="No more cheques",
                           starts_showing=NOW - 30 * DAY, stops_showing=NOW - DAY)
        self.client.force_login(self.volunteer)
        response = self.client.get("/notices/")
        # ⚠️ Still on the page — the whole point of a required come-down date.
        self.assertContains(response, gone.title)
        self.assertContains(response, "Recently taken down")

    def test_the_manage_page_shows_a_draft_that_the_board_does_not(self):
        draft = make_notice(ministry=self.pantry, title="Not ready",
                            status=Notice.Status.DRAFT)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get("/notices/manage/"), draft.title)
        self.assertNotContains(self.client.get("/notices/"), draft.title)

    def test_a_ministry_admin_only_manages_their_own_ministrys_notices(self):
        mine = make_notice(ministry=self.pantry, title="Mine")
        theirs = make_notice(ministry=self.tax, title="Theirs")
        self.client.force_login(self.admin)
        response = self.client.get("/notices/manage/")
        self.assertContains(response, mine.title)
        self.assertNotContains(response, theirs.title)
        self.assertEqual(
            self.client.get(f"/notices/{theirs.pk}/edit/").status_code, 403)

    def test_putting_up_a_notice_stamps_the_person_who_did_it(self):
        self.client.force_login(self.admin)
        response = self.client.post("/notices/new/", {
            "title": "Car park closed",
            "body": "Resurfacing.",
            "ministry": self.pantry.pk,
            "visible_to_outsiders": "on",
            "visible_to_all_staff": "on",
            "starts_showing": (NOW - DAY).strftime("%Y-%m-%dT%H:%M"),
            "stops_showing": (NOW + 30 * DAY).strftime("%Y-%m-%dT%H:%M"),
            "status": Notice.Status.PUBLISHED,
        })
        self.assertEqual(response.status_code, 302)
        notice = Notice.objects.get(title="Car park closed")
        self.assertEqual(notice.owner, self.admin.contact)
        self.assertTrue(notice.is_showing)

    def test_publishing_for_a_ministry_you_do_not_run_is_refused(self):
        """⚠️ The dropdown stops a slip; this stops a POST naming any id."""
        self.client.force_login(self.admin)
        response = self.client.post("/notices/new/", {
            "title": "Not mine", "body": "…",
            "ministry": self.tax.pk,
            "visible_to_all_staff": "on",
            "starts_showing": (NOW - DAY).strftime("%Y-%m-%dT%H:%M"),
            "stops_showing": (NOW + DAY).strftime("%Y-%m-%dT%H:%M"),
            "status": Notice.Status.PUBLISHED,
        })
        # Refused either by the narrowed queryset (a form error) or by the
        # second check (403) — never by creating the row.
        self.assertFalse(Notice.objects.filter(title="Not mine").exists())
        self.assertIn(response.status_code, (200, 403))

    def test_a_notice_for_nobody_is_refused_by_the_form(self):
        self.client.force_login(self.admin)
        response = self.client.post("/notices/new/", {
            "title": "Nobody", "body": "…",
            "ministry": self.pantry.pk,
            "starts_showing": (NOW - DAY).strftime("%Y-%m-%dT%H:%M"),
            "stops_showing": (NOW + DAY).strftime("%Y-%m-%dT%H:%M"),
            "status": Notice.Status.PUBLISHED,
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Say who needs to know")
        self.assertFalse(Notice.objects.filter(title="Nobody").exists())

    def test_taking_down_needs_a_post(self):
        """⚠️ A write reachable by GET is one a crawler can perform."""
        notice = make_notice(ministry=self.pantry)
        self.client.force_login(self.admin)
        self.client.get(f"/notices/{notice.pk}/take-down/")
        notice.refresh_from_db()
        self.assertTrue(notice.is_showing)
        self.client.post(f"/notices/{notice.pk}/take-down/")
        notice.refresh_from_db()
        self.assertFalse(notice.is_showing)

    def test_somebody_elses_notice_cannot_be_taken_down(self):
        theirs = make_notice(ministry=self.tax)
        self.client.force_login(self.admin)
        self.assertEqual(
            self.client.post(f"/notices/{theirs.pk}/take-down/").status_code, 403)
        theirs.refresh_from_db()
        self.assertTrue(theirs.is_showing)


class TheFoundationTierPublishesTooTests(TestCase):
    """2026-09-03: the tier may now write a notice, not only take one down.

    🔴 The class exists because the permission is only half the change. D41's
       restart table promised "改动只在 can_publish_notice 一个函数里", and that
       was wrong in a way no permission test would have caught: `NoticeForm`
       builds its ministry dropdown from ministry_ids_administered_by(), which is
       **empty** for this account. The check would have passed and the page would
       have refused every submission with "this field is required" — permitted to
       publish, with nothing to publish for, and it reads as a broken form rather
       than as a missing permission.
    """

    def setUp(self):
        from accounts.models import User
        from org.permissions import foundation_admin_group
        self.pantry = Ministry.objects.create(code="food_pantry", name="Food Pantry")
        self.tax = Ministry.objects.create(code="tax_help", name="Tax Help")
        self.retired = Ministry.objects.create(
            code="old_thing", name="Old Thing", is_active=False)
        self.chief = User.objects.create_user(
            email="chief@example.com", password="x", contact=make_person("Chief"))
        self.chief.groups.add(foundation_admin_group())

    def form(self):
        from notices.forms import NoticeForm
        return NoticeForm(user=self.chief)

    def test_the_dropdown_offers_every_active_ministry(self):
        offered = set(self.form().fields["ministry"].queryset)
        self.assertEqual(offered, {self.pantry, self.tax})

    def test_a_retired_ministry_is_not_offered(self):
        """⚠️ Matching what ministry_ids_administered_by() already filters for the
           other branch — a ministry nobody runs any more is not somewhere new
           notices go up.
        """
        self.assertNotIn(self.retired, self.form().fields["ministry"].queryset)

    def test_nothing_is_pre_ticked_because_there_is_no_own_ministry(self):
        """⚠️ Right rather than a gap. Decision 14's prefill claims "your own
           ministry is the narrowest useful start", and this account has none —
           ticking one for them would be the form choosing an audience.
        """
        self.assertFalse(self.form().initial.get("visible_to_ministries"))

    def test_they_can_put_up_a_notice_for_a_ministry_they_do_not_run(self):
        self.client.force_login(self.chief)
        response = self.client.post("/notices/new/", {
            "title": "Office closed on Monday",
            "body": "The whole foundation.",
            "ministry": self.tax.pk,
            "visible_to_all_staff": "on",
            "starts_showing": (NOW - DAY).strftime("%Y-%m-%dT%H:%M"),
            "stops_showing": (NOW + DAY).strftime("%Y-%m-%dT%H:%M"),
            "status": Notice.Status.PUBLISHED,
        })
        self.assertEqual(response.status_code, 302)
        notice = Notice.objects.get(title="Office closed on Monday")
        self.assertEqual(notice.ministry, self.tax)
        self.assertEqual(notice.owner, self.chief.contact)

    def test_a_volunteer_still_cannot(self):
        """⚠️ The other side of the widening, asserted through the view rather
           than the function: a page that 403s is what a volunteer must still
           meet.
        """
        from accounts.models import User
        nobody = User.objects.create_user(
            email="nobody@example.com", password="x", contact=make_person("Nobody"))
        self.client.force_login(nobody)
        self.assertEqual(self.client.get("/notices/new/").status_code, 403)


class BothNoticePagesTurnPagesTests(TestCase):
    """翻页（2026-09-03 第三轮，用户问「如果太多了会翻页吗」—— 当时两页都不会）。

    🔴 在此之前**两个列表都没有上限**：管理页把整个 queryset 直接交给模板，
       板上那一列也是。ministry admin 看到的是自己那一两个 ministry，长得慢；
       而 foundation tier 的管理页走的是 `Notice.objects.all()` —— 全部
       ministry、从第一天起、连已经下架的都在，全在一页上。
    """

    def setUp(self):
        from accounts.models import User
        from org.models import MinistryRole
        self.pantry = Ministry.objects.create(code="food_pantry", name="Food Pantry")
        person = make_person("Admin")
        self.admin = User.objects.create_user(
            email="admin@example.com", password="x", contact=person)
        MinistryRole.objects.create(
            contact=person, ministry=self.pantry,
            role=MinistryRole.Role.ADMIN, start_date=TODAY - DAY)
        self.client.force_login(self.admin)

    def make_many(self, count, **kwargs):
        """`count` 条公告，**起止时刻完全相同**。

        🔴 相同是重点，不是省事：排序的第一列因此全部打平，能不能翻页就全靠
           `page_of()` 补的那个唯一列收尾。少了它，第 1 页和第 2 页之间的先后
           是未定义的 —— 一行出现两次、或者一行凭空消失，而没有任何东西会报错。
        """
        return [make_notice(ministry=self.pantry, title=f"Notice {i:03d}", **kwargs)
                for i in range(count)]

    def rows_on(self, url, page=None):
        """这一页上画出来的公告标题，以及整份 HTML。

        ⚠️ 匹配的是 `>Notice 001<`（标签之间的文本），不是裸的 `Notice 001`：
           2026-09-03 之后每一行的「⋯」按钮上还有一个
           `aria-label="Actions for Notice 001"` —— 标题在一行里出现**两次**，
           而其中一次是给读屏的、不是一行数据。裸匹配会把每一行数成两行。
        """
        html = self.client.get(url, {"page": page} if page else {}).content.decode()
        return re.findall(r">(Notice \d{3})<", html), html

    def test_the_manage_page_stops_at_twenty(self):
        """2026-09-09：四个列表统一 20（原来这一页是 50）。

        ⚠️ 断言那个常量的**值**，不只是「翻页器画出来了」：这一条要钉住的正是
           那个数字本身，而「超过一页就分页」那件事任何一个数都成立。
        """
        from notices.views import MANAGED_NOTICES_PER_PAGE
        self.assertEqual(MANAGED_NOTICES_PER_PAGE, 20)
        self.make_many(21)
        rows, html = self.rows_on("/notices/manage/")
        self.assertEqual(len(rows), 20)
        self.assertIn("Page 1 of 2", html)

    def test_the_board_stops_at_twenty(self):
        """⚠️ 20，和 `events.views.EVENTS_PER_PAGE` 同一个数：两页是同一个人在问
           同一个问题（「现在有什么」），两个读的列表滚动长度不一样是要解释的。
        """
        from notices.views import BOARD_PER_PAGE
        from events.views import EVENTS_PER_PAGE
        self.assertEqual(BOARD_PER_PAGE, EVENTS_PER_PAGE)
        self.make_many(BOARD_PER_PAGE + 1)
        rows, html = self.rows_on("/notices/")
        self.assertEqual(len(rows), BOARD_PER_PAGE)
        self.assertIn("Page 1 of 2", html)

    def test_no_row_is_shown_twice_or_lost_between_pages(self):
        """🔴 这一条是整组里最要紧的：**翻页不许说谎**。

        51 条起止时刻一模一样的公告，**每一页拼起来**必须正好是这 51 条 ——
        不多一条、不少一条。排序末尾少了唯一列时这一条会红，而屏幕上只是
        「某一行好像见过两次」。

        ⚠️ 走完所有页，页数由 `MANAGED_NOTICES_PER_PAGE` 算出来，不写死。
           2026-09-09 每页从 50 改成 20 时，原来那版（只读第 1、2 页）当场变红 ——
           它没有说谎，它只是把「翻完」写成了「翻两页」。那两个数字本来就
           不该在这一条里出现：它问的是「拼起来对不对」，不是「一页装几行」。
        """
        from notices.views import MANAGED_NOTICES_PER_PAGE

        made = {n.title for n in self.make_many(51)}
        pages = -(-len(made) // MANAGED_NOTICES_PER_PAGE)   # 向上取整
        seen = []
        for page in range(1, pages + 1):
            rows, _ = self.rows_on("/notices/manage/", page=page)
            seen.append(rows)

        flat = [title for rows in seen for title in rows]
        self.assertEqual(len(flat), 51)
        self.assertEqual(set(flat), made)
        self.assertEqual(len(set(flat)), len(flat),
                         "有公告同时出现在两页上 —— 排序末尾没有唯一列")

    def test_the_manage_page_keeps_its_own_order_across_paging(self):
        """🔴 `Notice` 的排序写在 `Meta.ordering` 上，而 `query.order_by` 是空的。

        `page_of()` 读不到 `Meta` 的那一版会把整个排序**换成** `-pk`，于是这一页
        从「最近上板的在最前」变成「最后建的在最前」—— 两者在测试数据里常常
        长得一模一样，而在真数据上不是。理由写在 core/pagination.py。
        """
        newest = make_notice(ministry=self.pantry, title="Notice 999",
                             starts_showing=NOW + 5 * DAY,
                             stops_showing=NOW + 9 * DAY)
        self.make_many(3)
        rows, _ = self.rows_on("/notices/manage/")
        self.assertEqual(rows[0], newest.title,
                         "Meta.ordering 没了 —— 这一页改成按 id 排了")

    def test_the_past_section_is_still_a_cap_and_not_a_second_paginator(self):
        """⚠️ 下面那一列照旧截断在 PAST_SHOWN（用户拍板只翻上面那一列）。

        一页上两个翻页器的话，`?page=` 指哪一列得看参数名才知道 —— 而
        「板上的」和「已经下架的」是两个不同的问题，不是一列的两截。
        """
        from notices.views import PAST_SHOWN
        self.make_many(PAST_SHOWN + 5,
                       starts_showing=NOW - 30 * DAY, stops_showing=NOW - DAY)
        rows, html = self.rows_on("/notices/")
        self.assertEqual(len(rows), PAST_SHOWN)
        self.assertNotIn("Page 1 of", html,
                         "往期那一列画出了翻页器 —— 它是截断，不是分页")

    def test_the_paginator_stays_out_of_sight_at_normal_size(self):
        """⚠️ 「不该很多」不是「不可能很多」：这个翻页器是安全网，不是常驻元件。
           组件只在装不下时才画，所以正常规模下这两页和加它之前逐像素相同。
        """
        self.make_many(3)
        for url in ("/notices/", "/notices/manage/"):
            with self.subTest(url=url):
                self.assertNotIn("Page 1 of", self.rows_on(url)[1])


class TheTwoNoticePagesShareOneHeadTests(TestCase):
    """「Notices」和「Notices I publish」是顶栏底下同一条 bar 上的两格（2026-09-03）。

    用户那三句：标题要像活动列表一样在顶栏底下；「Notices I publish」是右边
    那一格、自己一页；而「Put up a notice」**只在**那一页上。
    """

    def setUp(self):
        from accounts.models import User
        from org.models import MinistryRole
        self.pantry = Ministry.objects.create(code="food_pantry", name="Food Pantry")
        person = make_person("Admin")
        self.admin = User.objects.create_user(
            email="admin@example.com", password="x", contact=person)
        MinistryRole.objects.create(
            contact=person, ministry=self.pantry,
            role=MinistryRole.Role.ADMIN, start_date=TODAY - DAY)
        self.volunteer = User.objects.create_user(
            email="vol@example.com", password="x", contact=make_person("Volunteer"))

    def page(self, user, url):
        self.client.force_login(user)
        return self.client.get(url).content.decode()

    def test_the_board_has_no_put_up_a_notice_button(self):
        """🔴 用户 2026-09-03：任何 notices 页面都不该有这颗按钮，只有
           Notices I publish 那一页有。

        ⚠️ 对 admin 也一样 —— 这一页是**读**公告的地方。他并没有因此少一条路：
           右边那一格就是入口，而且它是常驻的。
        """
        for user in (self.admin, self.volunteer):
            with self.subTest(user=user.email):
                self.assertNotIn("Put up a notice", self.page(user, "/notices/"))

    def test_the_actions_column_has_a_name_for_screen_readers(self):
        """⚠️ 设计评审第 4 条。原来那一格是 `<th></th>` —— 读屏软件念出一个
           没有名字的列，而屏幕上完全正常，所以它躺了很久没人发现。

        ⚠️ 钉的是「没有空表头」而不是「有 Actions 这个词」：换个词是设计决定，
           而留一个空的 `<th>` 是缺陷。
        """
        # ⚠️ 先得有一条 —— 没有公告时这一页画的是空状态，根本没有表格。
        make_notice(ministry=self.pantry)
        html = self.page(self.admin, "/notices/manage/")
        found = re.search(r"<thead>.*?</thead>", html, re.S)
        self.assertIsNotNone(found, "管理页没有表格了")
        head = found.group(0)
        self.assertNotRegex(head, r"<th[^>]*>\s*</th>", "表头里又有一格是空的")
        self.assertIn("sr-only", head)

    def test_a_notice_that_came_down_gets_no_empty_menu(self):
        """🔴 **一个点开是空的入口，比没有入口更糟。**

        已经下架的公告什么都做不了：`take_down()` 对它无意义，重新上板是新写
        一条。改之前那一行本来就只有 Edit、没有第二颗按钮；菜单落地那一版
        照画不误，点开是一个空面板。

        ⚠️ 这个缺陷是**在浏览器里看出来的**，而当时所有测试都是绿的 ——
           每一条断言的都是「该有的项在不在」，没有一条问过「会不会一项都没有」。
           这条守卫补的就是那个反面。
        """
        gone = make_notice(ministry=self.pantry, title="Old news",
                           starts_showing=NOW - 30 * DAY, stops_showing=NOW - DAY)
        live = make_notice(ministry=self.pantry, title="Still up")
        self.assertFalse(gone.has_actions)
        self.assertTrue(live.has_actions)

        html = self.page(self.admin, "/notices/manage/")
        rows = re.findall(r"<tr>.*?</tr>", html, re.S)
        for row in rows:
            title = re.search(r'class="font-medium">([^<]*)<', row)
            if not title:
                continue
            with self.subTest(notice=title.group(1)):
                if "row-menu-trigger" in row:
                    self.assertRegex(
                        row, r"<(?:button|a)[^>]*class=\"row-menu-item",
                        "这一行画了「⋯」，而菜单里一项都没有")

    def test_every_live_state_still_has_its_action(self):
        """⚠️ 上一条的镜像：别为了躲开空菜单把有事可做的行也一起藏了。
           四档里三档各有一个动作，只有「已经下架」没有。
        """
        from notices.models import Notice
        draft = make_notice(ministry=self.pantry, title="A draft",
                            status=Notice.Status.DRAFT)
        ahead = make_notice(ministry=self.pantry, title="Not yet",
                            starts_showing=NOW + DAY, stops_showing=NOW + 9 * DAY)
        showing = make_notice(ministry=self.pantry, title="On the board")
        for notice in (draft, ahead, showing):
            with self.subTest(notice=notice.title):
                self.assertTrue(notice.has_actions)

    def test_the_manage_page_keeps_it(self):
        self.assertIn("Put up a notice", self.page(self.admin, "/notices/manage/"))

    def test_the_word_is_in_both_places_and_the_h1_is_the_one_in_the_page(self):
        """两处都有字，而 `<h1>` 只有版心里那一个（2026-09-03 第二轮）。

        用户把分工说得很清楚：「顶栏的 title 是为了网页下滑可以看到这是哪一个
        页面，但是页面本身也要有 title」。所以页头条那两格是 `<nav>` 里的链接。

        🔴 仍然只有一个 h1 —— 一页两个会让读屏的标题大纲变成两棵并列的树，
           而屏幕上两处写着同一个词，谁也不会觉得不对。
        """
        for user, url, title in [(self.volunteer, "/notices/", "Notices"),
                                 (self.admin, "/notices/", "Notices"),
                                 (self.admin, "/notices/manage/",
                                  "Notices I publish")]:
            with self.subTest(url=url, user=user.email):
                html = self.page(user, url)
                self.assertEqual(html.count("<h1"), 1, "这一页出现了第二个 h1")
                bar = re.search(r'<div class="page-bar">(.*?)</nav>', html, re.S)
                self.assertIsNotNone(bar, "页头条没渲染出来")
                self.assertIn(title, bar.group(1), "页头条上没有这一页那一格")
                self.assertNotIn(
                    "<h1", bar.group(1),
                    "页头条又成了标题 —— 它是路标，不是这一页的标题")
                self.assertRegex(
                    html, rf"<h1[^>]*>\s*{title}\s*</h1>",
                    "版心里那个标题不见了 —— 这一页本身没有标题了")

    def test_the_manage_dots_are_only_drawn_for_somebody_who_can_open_them(self):
        """⚠️ 画给别人就是一个点了必定 403 的链接 —— 读起来是「站坏了」，
           而不是「这一页不归你」。

        ⚠️ 2026-09-03 第三轮：入口从页头条第二格换成了标题行右端那颗 ⋮
           （站点菜单里那一格同日撤掉，这是它现在唯一的可见入口），
           而「只画给点得动的人」这条规矩一个字没变。
        """
        page = self.page(self.admin, "/notices/")
        self.assertIn("manage-link", page, "有权限的人看不到那颗 ⋮")
        self.assertIn("/notices/manage/", page)
        self.assertNotIn("manage-link", self.page(self.volunteer, "/notices/"),
                         "志愿者看到了一个点了必定 403 的入口")

    def test_the_page_bar_is_where_you_are_plus_where_you_came_from(self):
        """🔴 **不对称，而且不对称是要的**（2026-09-03 第四轮）：

            /notices/         → 一格：Notices
            /notices/manage/  → 两格：Notices（回去）· Notices I publish（当前）

        ⚠️ 读的那一页**不画**管理页那一格 —— 去管理页的入口是标题行右端那颗 ⋮，
           一个入口一处。而管理页必须画读页那一格，否则**回不去**：
           第三轮就是这么栽的（用户：「从 Events 点 manage 进去以后，
           回不到 Events 界面了」）。

        ⚠️ 顺序写死在组件里：外层在左、当前在右。左右按当前页对调的话，
           同一条 bar 上的字会在跳转的一瞬间横着挪。
        """
        for url, cells, here in [
            ("/notices/", ["Notices"], "Notices"),
            ("/notices/manage/", ["Notices", "Notices I publish"],
             "Notices I publish"),
        ]:
            with self.subTest(url=url):
                html = self.page(self.admin, url)
                bar = re.search(r'<div class="page-bar">(.*?)</nav>', html, re.S).group(1)
                drawn = [c.strip() for c in
                         re.findall(r'<a class="page-bar-link"[^>]*>([^<]*)<', bar)]
                self.assertEqual(drawn, cells)
                current = re.search(
                    r'<a class="page-bar-link"[^>]*aria-current="page"[^>]*>([^<]*)<',
                    bar)
                self.assertIsNotNone(current, "没有一格声称自己是当前页")
                self.assertEqual(current.group(1).strip(), here)

    def test_the_manage_page_can_get_back_to_the_board(self):
        """🔴 用户报的那一条：「从 Events 点 manage 进去以后，回不到 Events
           界面了」—— 公告这边一模一样。

        ⚠️ 钉的是那一格的 **href**，不只是那几个字：写着「Notices」而点不回去
           的一格，比没有那一格更糟。
        """
        html = self.page(self.admin, "/notices/manage/")
        bar = re.search(r'<div class="page-bar">(.*?)</nav>', html, re.S).group(1)
        back = re.search(r'<a class="page-bar-link" href="([^"]*)"(?![^>]*aria-current)',
                         bar)
        self.assertIsNotNone(back, "这一页没有回去的那一格")
        self.assertEqual(back.group(1), "/notices/")

    def test_the_foundation_tier_reaches_the_manage_page_from_the_board(self):
        """🔴 它的入口一直是缺的：`can_reach_notice_manage()` 从这个 app 写出来
           那天就放行 foundation tier，而菜单和页面上没有任何东西指向它 ——
           只能靠手敲 URL。
        """
        from accounts.models import User
        from org.permissions import foundation_admin_group
        chief = User.objects.create_user(
            email="chief@example.com", password="x", contact=make_person("Chief"))
        chief.groups.add(foundation_admin_group())
        page = self.page(chief, "/notices/")
        self.assertIn("manage-link", page, "这一层看不到通往管理页的 ⋮")
        self.assertIn("/notices/manage/", page)
        self.assertEqual(self.client.get("/notices/manage/").status_code, 200)


class TheNewNoticeFormStartsSomewhereSensibleTests(TestCase):
    """Decision 4 and decision 14, as the boxes a person actually meets."""

    def setUp(self):
        from accounts.models import User
        from org.models import MinistryRole
        self.pantry = Ministry.objects.create(code="food_pantry", name="Food Pantry")
        contact = make_person("Admin")
        self.admin = User.objects.create_user(
            email="admin@example.com", password="pw", contact=contact)
        MinistryRole.objects.create(
            contact=contact, ministry=self.pantry,
            role=MinistryRole.Role.ADMIN, start_date=TODAY - DAY)

    def form(self, **kwargs):
        from notices.forms import NoticeForm
        return NoticeForm(user=self.admin, **kwargs)

    def test_a_new_notice_comes_down_in_thirty_days_by_default(self):
        from notices.forms import DEFAULT_RUN_DAYS
        form = self.form()
        gap = form.initial["stops_showing"] - form.initial["starts_showing"]
        self.assertEqual(gap.days, DEFAULT_RUN_DAYS)

    def test_a_new_notice_starts_with_only_the_publishers_own_ministry_ticked(self):
        self.assertEqual(
            list(self.form().initial["visible_to_ministries"]), [self.pantry.pk])

    def test_editing_does_not_re_tick_a_ministry_somebody_untucked(self):
        """⚠️ Silent widening. Nothing on the page would say it had happened.

        ⚠️ Asserted as "the pantry is not in there", not as "initial is None".
           A bound ModelForm reads the saved M2M, so the value on an edit is
           `[]` rather than absent — and a test asserting None would pass today
           for the wrong reason and fail the day somebody saves a notice with
           one ministry ticked.
        """
        notice = make_notice(ministry=self.pantry, visible_to_outsiders=True,
                             visible_to_all_staff=False)
        form = self.form(instance=notice)
        self.assertNotIn(
            self.pantry.pk, list(form.initial.get("visible_to_ministries") or []))

    def test_the_ministry_dropdown_offers_only_what_they_run(self):
        Ministry.objects.create(code="tax_help", name="Tax Help")
        offered = list(self.form().fields["ministry"].queryset)
        self.assertEqual(offered, [self.pantry])


class PuttingADraftUpTests(TestCase):
    """The one-click path from the manage list, and the words on the buttons."""

    def setUp(self):
        from accounts.models import User
        from org.models import MinistryRole
        self.pantry = Ministry.objects.create(code="food_pantry", name="Food Pantry")
        contact = make_person("Admin")
        self.admin = User.objects.create_user(
            email="admin@example.com", password="pw", contact=contact)
        MinistryRole.objects.create(
            contact=contact, ministry=self.pantry,
            role=MinistryRole.Role.ADMIN, start_date=TODAY - DAY)
        self.client.force_login(self.admin)

    def test_a_draft_can_go_up_from_the_list(self):
        draft = make_notice(ministry=self.pantry, status=Notice.Status.DRAFT)
        self.client.post(f"/notices/{draft.pk}/put-up/")
        draft.refresh_from_db()
        self.assertTrue(draft.is_showing)

    def test_putting_up_needs_a_post(self):
        draft = make_notice(ministry=self.pantry, status=Notice.Status.DRAFT)
        self.client.get(f"/notices/{draft.pk}/put-up/")
        draft.refresh_from_db()
        self.assertTrue(draft.is_draft)

    def test_the_list_offers_the_action_that_fits_each_state(self):
        """🔴 The label says what will happen, not which function runs.

        "Not up yet" and "showing" both go through take_down() and land in
        different places — draft and past. A button reading the same word for
        both would be describing the implementation.
        """
        make_notice(ministry=self.pantry, title="A draft",
                    status=Notice.Status.DRAFT)
        make_notice(ministry=self.pantry, title="Up now")
        make_notice(ministry=self.pantry, title="Later",
                    starts_showing=NOW + DAY, stops_showing=NOW + 30 * DAY)
        page = self.client.get("/notices/manage/").content.decode()
        for label in ("Put it up", "Take down", "Back to draft"):
            self.assertIn(label, page)

    def test_a_notice_that_came_down_offers_no_action_but_edit(self):
        make_notice(ministry=self.pantry, title="Old news",
                    starts_showing=NOW - 30 * DAY, stops_showing=NOW - DAY)
        page = self.client.get("/notices/manage/").content.decode()
        self.assertIn("Old news", page)
        self.assertNotIn("Take down", page)
        self.assertNotIn("Put it up", page)

    def test_somebody_elses_draft_cannot_be_put_up(self):
        tax = Ministry.objects.create(code="tax_help", name="Tax Help")
        theirs = make_notice(ministry=tax, status=Notice.Status.DRAFT)
        self.assertEqual(
            self.client.post(f"/notices/{theirs.pk}/put-up/").status_code, 403)
        theirs.refresh_from_db()
        self.assertTrue(theirs.is_draft)


class TheDateBoxesDoNotSuggestSecondsTests(TestCase):
    """⚠️ Browser-found. `datetime-local` renders whatever it is handed."""

    def setUp(self):
        from accounts.models import User
        from org.models import MinistryRole
        self.pantry = Ministry.objects.create(code="food_pantry", name="Food Pantry")
        contact = make_person("Admin")
        self.admin = User.objects.create_user(
            email="admin@example.com", password="pw", contact=contact)
        MinistryRole.objects.create(
            contact=contact, ministry=self.pantry,
            role=MinistryRole.Role.ADMIN, start_date=TODAY - DAY)

    def test_the_prefilled_dates_are_whole_minutes(self):
        from notices.forms import NoticeForm
        form = NoticeForm(user=self.admin)
        for name in ("starts_showing", "stops_showing"):
            with self.subTest(field=name):
                value = form.initial[name]
                self.assertEqual((value.second, value.microsecond), (0, 0))
