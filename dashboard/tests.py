"""`/me/` —— 每张卡只画属于这个人的东西，而卡片的取舍本身也钉在这里。"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from accounts.models import User
from contact.models import Contact
from core.timeutils import local_now, local_today
from dashboard import calendar as month
from events.models import (
    Event,
    EventRole,
    EventType,
    Participation,
    ParticipationRole,
)
from notices.models import Notice
from org.models import Assignment, Ministry, MinistryRole, Position
from org.permissions import FOUNDATION_ADMIN_GROUP

NOW = local_now()
DAY = datetime.timedelta(days=1)
HOUR = datetime.timedelta(hours=1)

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


def make_account(email, contact):
    return User.objects.create_user(email=email, password="pw", contact=contact)


class DashboardTestCase(TestCase):
    """一套够五张卡都画得出来的最小数据。"""

    def setUp(self):
        self.pantry = Ministry.objects.create(code="food_pantry", name="Food Pantry")
        self.tax = Ministry.objects.create(code="tax_help", name="Tax Help")
        self.kind, _ = EventType.objects.get_or_create(
            code="distribution", defaults={"name": "Distribution"})
        self.role, _ = ParticipationRole.objects.get_or_create(
            code="lifting",
            defaults={"name": "Lifting",
                      "nature": ParticipationRole.Nature.HELPING})

        self.me = make_person("Volunteer")
        self.account = make_account("me@example.com", self.me)

    # --- helpers ---------------------------------------------------------

    def an_event(self, ministry=None, start=None, **kwargs):
        start = start or NOW + DAY
        fields = {
            "name": "Autumn food drive",
            "event_type": self.kind,
            "ministry": ministry or self.pantry,
            "start_time": start,
            "end_time": start + 3 * HOUR,
            "owner": make_person("Owner"),
            "status": Event.Status.OPEN,
            "visible_to_outsiders": True,
            "visible_to_all_staff": True,
        }
        fields.update(kwargs)
        return Event.objects.create(**fields)

    def a_role(self, event, **kwargs):
        role = EventRole.objects.create(
            event=event, role=self.role,
            visible_to_outsiders=True, visible_to_all_staff=True, **kwargs)
        return role

    def sign_up(self, contact=None, event=None, **kwargs):
        event = event or self.an_event()
        return Participation.objects.create(
            contact=contact or self.me,
            event_role=self.a_role(event),
            **kwargs)

    def employ(self, contact, code="lead", ministry=None, **dates):
        post = Position.objects.create(
            code=code, name=code.replace("_", " ").title(),
            kind=Position.Kind.STAFF,
            compensation=Position.Compensation.PAID,
            ministry=ministry or self.pantry)
        dates.setdefault("start_date", TODAY - 30 * DAY)
        return Assignment.objects.create(contact=contact, position=post, **dates)

    def make_admin(self, contact, ministry=None):
        return MinistryRole.objects.create(
            contact=contact, ministry=ministry or self.pantry,
            role=MinistryRole.Role.ADMIN, start_date=TODAY - DAY)

    def page(self, account=None):
        self.client.force_login(account or self.account)
        return self.client.get("/me/")


class TheDashboardIsForOnePersonTests(DashboardTestCase):
    def test_it_needs_a_session(self):
        response = self.client.get("/me/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_it_does_not_replace_the_public_front_page(self):
        """⚠️ D25 的判据还在：`/` 对所有人开放，且登录之后**不跳转**。

        仪表盘存在之后最容易发生的一次「顺手」，就是给 `/` 加一个
        「登录了就送到 /me/ 去」—— 那正是 D25 推翻过的那个调度器。
        """
        self.client.force_login(self.account)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_the_dashboard_only_lists_signups_that_have_not_happened_yet(self):
        past = self.an_event(start=NOW - 10 * DAY)
        past.name = "Last month"
        past.save()
        self.sign_up(event=past)
        self.sign_up(event=self.an_event(start=NOW + 2 * DAY))
        page = self.page().content.decode()
        self.assertIn("Autumn food drive", page)
        self.assertNotIn("Last month", page)

    def test_it_shows_only_notices_for_this_person(self):
        Notice.objects.create(
            title="For everybody", body="…", ministry=self.pantry,
            owner=self.me, status=Notice.Status.PUBLISHED,
            starts_showing=NOW - DAY, stops_showing=NOW + DAY,
            visible_to_outsiders=True, visible_to_all_staff=True)
        Notice.objects.create(
            title="Staff away day", body="…", ministry=self.pantry,
            owner=self.me, status=Notice.Status.PUBLISHED,
            starts_showing=NOW - DAY, stops_showing=NOW + DAY,
            visible_to_all_staff=True)
        page = self.page().content.decode()
        self.assertIn("For everybody", page)
        self.assertNotIn("Staff away day", page)

    def test_my_signups_and_the_dashboard_agree_on_what_is_mine(self):
        """⭐ 抽 `mine()` 那一步的钉子。

        两个页面问的是同一个方法，所以它们**不可能**对「哪些报名算我的」
        给出两个答案。分成两份写的那一版会：一处漏掉
        `visible_to_participants()`，另一处没漏，而没有任何东西会说出来。
        """
        self.sign_up(event=self.an_event(start=NOW + 2 * DAY))
        hidden = self.an_event(start=NOW + 3 * DAY, status=Event.Status.DRAFT)
        hidden.name = "Not published"
        hidden.save()
        self.sign_up(event=hidden)

        self.client.force_login(self.account)
        dashboard = self.client.get("/me/").content.decode()
        signups = self.client.get("/me/participations/").content.decode()
        for page in (dashboard, signups):
            self.assertNotIn("Not published", page)


class ThePostsLineTests(DashboardTestCase):
    def test_somebody_on_the_books_sees_every_post_they_hold(self):
        """🔴 一人多岗。`.first()` 会让这条红 —— 它在页面上抹掉第二个身份。"""
        self.employ(self.me, code="pantry_lead", ministry=self.pantry)
        self.employ(self.me, code="tax_lead", ministry=self.tax)
        page = self.page().content.decode()
        self.assertIn("Pantry Lead", page)
        self.assertIn("Tax Lead", page)

    def test_an_outside_volunteer_sees_no_post_block_at_all(self):
        """⚠️ 不画「你没有职位」——「不是在编」不是一种缺失（D27）。"""
        page = self.page().content.decode()
        self.assertNotIn("no current post", page)
        self.assertNotIn("No post", page)

    def test_a_post_that_has_ended_is_not_listed(self):
        self.employ(self.me, code="gone", end_date=TODAY - DAY)
        self.assertNotIn("Gone", self.page().content.decode())


class TheHoursCardTests(DashboardTestCase):
    """🔴 D36：两个账本永不相加，所以这张卡只认一个。"""

    def test_it_counts_volunteering_only(self):
        event = self.an_event(start=NOW - 5 * DAY)
        Participation.objects.create(
            contact=self.me, event_role=self.a_role(event),
            hours=Decimal("3.00"), status=Participation.Status.ATTENDED,
            served_as=Participation.ServedAs.VOLUNTEER)
        other = self.an_event(start=NOW - 4 * DAY)
        Participation.objects.create(
            contact=self.me, event_role=self.a_role(other),
            hours=Decimal("5.00"), status=Participation.Status.ATTENDED,
            served_as=Participation.ServedAs.WORK)
        total = (Participation.objects.mine(self.me)
                 .volunteering().hours_given())
        self.assertEqual(total, Decimal("3.00"))
        self.assertIn("3", self.page().content.decode())

    def test_somebody_with_no_hours_gets_a_zero_not_a_blank(self):
        """⚠️ 0 是一个真答案；None 不是。藏起来会读成功能坏了。"""
        self.assertEqual(
            Participation.objects.mine(self.me).volunteering().hours_given(),
            Decimal("0"))
        self.assertIn("hours volunteered", self.page().content.decode())

    def test_the_card_is_named_after_the_ledger(self):
        """叫 `Volunteer hours` 而不是 `My hours` —— 后者在暗示这是全部。

        ⚠️ 这一条看起来是在测文案，它测的是 D2a 之后不用改口：那时
           `Work hours` 并排加进来，这张卡一个字都不用动。
        """
        page = self.page().content.decode()
        self.assertIn("Volunteer hours", page)
        self.assertNotIn("My hours", page)


class TheMinistryAdminCardsTests(DashboardTestCase):
    def test_a_volunteer_does_not_get_them(self):
        page = self.page().content.decode()
        self.assertNotIn("Needs you", page)
        self.assertNotIn("Recent signups", page)

    def test_a_ministry_admin_does(self):
        self.make_admin(self.me)
        page = self.page().content.decode()
        self.assertIn("Needs you", page)
        self.assertIn("Recent signups", page)

    def test_a_ministry_admin_only_sees_their_own_ministrys_shortfalls(self):
        """⚠️ 这张卡是这一页唯一会泄露别的 ministry 的地方。"""
        self.make_admin(self.me, ministry=self.pantry)
        mine = self.an_event(ministry=self.pantry)
        mine.name = "Mine and short"
        mine.save()
        self.a_role(mine, needed_count=5)
        theirs = self.an_event(ministry=self.tax)
        theirs.name = "Theirs and short"
        theirs.save()
        self.a_role(theirs, needed_count=5)
        page = self.page().content.decode()
        self.assertIn("Mine and short", page)
        self.assertNotIn("Theirs and short", page)

    def test_an_event_that_ended_without_being_wrapped_up_is_flagged(self):
        """借 `Status.COMPLETED` 的既有语义，不造第三个口径。"""
        self.make_admin(self.me)
        ended = self.an_event(ministry=self.pantry, start=NOW - 5 * DAY)
        ended.name = "Ended and open"
        ended.save()
        wrapped = self.an_event(ministry=self.pantry, start=NOW - 6 * DAY,
                                status=Event.Status.COMPLETED)
        wrapped.name = "Ended and wrapped"
        wrapped.save()
        page = self.page().content.decode()
        self.assertIn("Ended and open", page)
        self.assertNotIn("Ended and wrapped", page)

    def test_the_foundation_tier_gets_no_card_of_its_own(self):
        """说不出他每天要在首页看什么，所以不画 —— 入口在侧边菜单。"""
        self.account.groups.add(
            Group.objects.get_or_create(name=FOUNDATION_ADMIN_GROUP)[0])
        page = self.page().content.decode()
        self.assertNotIn("Needs you", page)


class TheMonthGridTests(DashboardTestCase):
    def test_it_marks_only_days_this_person_has_signed_up_for(self):
        day = local_today()
        marked = month.marked_days([])
        self.assertEqual(marked, set())
        grid = month.month_grid({day}, day)
        flat = [cell for week in grid for cell in week if cell["day"]]
        self.assertEqual([c["date"] for c in flat if c["marked"]], [day])

    def test_the_grid_keeps_seven_cells_a_week(self):
        """⚠️ 不属于本月的格子给 `day=None` 而不是省略 —— 省略会让格子塌掉。"""
        for week in month.month_grid(set(), local_today()):
            self.assertEqual(len(week), 7)

    def test_it_covers_days_earlier_this_month_too(self):
        """⭐ 月历问的不是「接下来」，是「我这个月哪几天有事」。

        ⚠️ 第一版把点子从卡片那个 `[:4]` 的列表里取，于是月历最多标四天，
           而少标的那几天看起来就是「那天没事」—— 一个没有任何迹象的错。
        """
        earlier = local_today().replace(day=1)
        grid = month.month_grid({earlier}, local_today())
        flat = [c for week in grid for c in week if c["day"]]
        self.assertTrue(any(c["marked"] for c in flat))

    def test_the_week_starts_on_sunday(self):
        """⚠️ Python 的 calendar 默认周一起，不设的话格子整体错一列。"""
        self.assertEqual(month.WEEKDAY_INITIALS[0], "S")
        self.assertEqual(month.WEEKDAY_INITIALS[1], "M")


class TheGreetingTests(TestCase):
    def test_it_follows_the_clock(self):
        def at(hour):
            return local_now().replace(hour=hour, minute=0)
        self.assertEqual(month.greeting(at(9)), "Good morning")
        self.assertEqual(month.greeting(at(14)), "Good afternoon")
        self.assertEqual(month.greeting(at(20)), "Good evening")


class TheQueryBudgetTests(DashboardTestCase):
    """这一页是全站查询最多的一个，所以它的上限写下来。

    ⚠️ 不是一个性能测试，是一个**回归**测试：仪表盘天生是「再加一张卡」的地方，
       而每一张卡都是一次查询。一个会随卡片数量悄悄爬升的数字，需要有人在它
       爬升的那一次被拦一下 —— 那时该问的是「这张卡值不值这次查询」，而不是
       把这个数字往上调。
    """

    #: ⚠️ 改这个数之前先读上面那段。
    BUDGET = 20

    def test_it_stays_within_its_budget(self):
        """⚠️ 上限，不是精确值 —— 所以用 `CaptureQueriesContext` 而不是
           `assertNumQueries`。后者是**等于**：少查了一次它也红，于是
           「顺手优化掉一次查询」会变成一次要改测试的改动，而那正好教人
           别去优化。这里要钉的是天花板。
        """
        self.make_admin(self.me)
        self.employ(self.me)
        self.sign_up()
        self.client.force_login(self.account)
        with CaptureQueriesContext(connection) as queries:
            self.client.get("/me/")
        self.assertLessEqual(
            len(queries), self.BUDGET,
            f"/me/ 用了 {len(queries)} 次查询，上限是 {self.BUDGET}。"
            "先问这张新卡值不值这次查询，再考虑抬这个数。")
