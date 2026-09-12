"""`/me/` —— 每张卡只画属于这个人的东西，而卡片的取舍本身也钉在这里。"""

import datetime
import re
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase
from django.utils import timezone
from django.test.utils import CaptureQueriesContext

from accounts.models import User
from contact.models import Contact
from core.timeutils import local_now, local_today, year_bounds
from dashboard import calendar as month
from dashboard.services import _greeting, dashboard_for
from events.models import (
    Event,
    EventRole,
    Participation,
    ParticipationRole,
    Session,
    SessionAttendance,
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
    # ⚠️ `email_verified=True`：一个没验证过地址的账号**登不进来**
    #    （SiteAuthenticationForm 在密码之后拦一道），而这一份里有一条测试真的
    #    去走登录表单。不设它的话那条测试收到的是 200 + 一条表单错误，
    #    而不是它在等的 302。
    return User.objects.create_user(
        email=email, password="pw", contact=contact, email_verified=True)


class DashboardTestCase(TestCase):
    """一套够五张卡都画得出来的最小数据。"""

    def setUp(self):
        self.pantry = Ministry.objects.create(code="food_pantry", name="Food Pantry")
        self.tax = Ministry.objects.create(code="tax_help", name="Tax Help")
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
        """登录之后那一屏 —— 它现在是 `/` 的下半页（D44，2026-09-11 改）。

        ⚠️ 以前这里取的是 `/me/`。那个地址还在，但它已经是一条回 `/` 的跳转：
           一页只能有一个地址，否则登录之后落在 `/me/` 的人往上滚不到 hero。
        """
        self.client.force_login(account or self.account)
        return self.client.get("/")


class TheDashboardIsForOnePersonTests(DashboardTestCase):
    def test_it_needs_a_session(self):
        """⚠️ 钉的是旧地址仍然**先要登录**，而不是把没登录的人丢到公开首页上 ——
           他点这条链接要的是自己那一屏，不是那张照片。
        """
        response = self.client.get("/me/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_the_old_address_now_leads_to_the_one_page(self):
        """`/me/` 留着，但它是一条跳转 —— 它进过菜单、进过登录后的落点，
           也可能在谁的书签里。删掉是 404，跳转是「你要的东西在那边」。
        """
        self.client.force_login(self.account)
        self.assertRedirects(self.client.get("/me/"), "/")

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
        dashboard = self.client.get("/").content.decode()
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


class TheHoursFigureTests(DashboardTestCase):
    """🔴 D36：两个账本永不相加，所以这个数只认一个。

    ⚠️ 2026-09-11 改名（原 `TheHoursCardTests`）：D44 把这个数从一张卡搬进了
       问候那一行的右端。测的东西一条没变，只是它现在不在卡里了。
    """

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
        page = self.page().content.decode()
        self.assertIn("Volunteer hours this year", page)
        self.assertIn("since you started", page)

    def test_it_is_named_after_the_ledger(self):
        """叫 `Volunteer hours` 而不是 `My hours` —— 后者在暗示这是全部。

        ⚠️ 这一条看起来是在测文案，它测的是 D2a 之后不用改口：那时
           `Work hours` 并排加进来，这一行一个字都不用动。

        🔴 D44 差点在这里丢掉它。按截图，标签本来写的是 "Hours this year" ——
           年份加对了，账本名掉了，而掉了账本名的那句话和 `My hours` 犯的是
           同一个错。是这条测试红了才发现的。
        """
        page = self.page().content.decode()
        self.assertIn("Volunteer hours", page)
        self.assertNotIn("My hours", page)

    def test_this_year_and_all_time_are_two_different_numbers(self):
        """大字是今年，小字是累计（D44 第六节）—— 两个数都印，因为少任何一个
           都会有人误会自己的贡献没了。
        """
        this_year = self.an_event(start=NOW - DAY)
        Participation.objects.create(
            contact=self.me, event_role=self.a_role(this_year),
            hours=Decimal("4.00"), status=Participation.Status.ATTENDED,
            served_as=Participation.ServedAs.VOLUNTEER)
        long_ago = self.an_event(start=NOW - 800 * DAY)
        Participation.objects.create(
            contact=self.me, event_role=self.a_role(long_ago),
            hours=Decimal("6.00"), status=Participation.Status.ATTENDED,
            served_as=Participation.ServedAs.VOLUNTEER)

        mine = Participation.objects.mine(self.me).volunteering()
        total, within = mine.hours_given_and_within(*year_bounds(NOW.year))
        self.assertEqual(total, Decimal("10.00"))
        self.assertEqual(within, Decimal("4.00"))


class TheMinistryAdminCardsTests(DashboardTestCase):
    def test_a_volunteer_gets_the_band_but_none_of_the_admin_ways_in(self):
        """D44：通栏那一块两种人都看得见，但里面说的不是同一句话。

        ⚠️ 2026-09-11 改口。原来这一条断言的是志愿者**看不到** `Needs you`，
           而那句话随着它从卡片变成通栏一起失效了 —— 现在要钉的是：
           他看得见这一块，但拿不到任何一条管理员的入口。
        """
        page = self.page().content.decode()
        self.assertIn("Needs you", page)
        self.assertNotIn("Events I manage", page)

    def test_a_ministry_admin_does(self):
        self.make_admin(self.me)
        self.assertIn("Needs you", self.page().content.decode())

    def test_the_needs_you_card_does_not_wear_a_warning_sign(self):
        """🔴 ⚠️ 的意思是「出错了 / 有危险」，而这张卡说的是**有事在等你**
           （还缺人的工种，和结束了没收尾的出勤；空态就写着
           "Nothing waiting on you."）。

        一个管理员打开首页看到一个警告三角，第一反应是「哪里坏了」——
        而实际上只是有个工种还差两个人。2026-09-03 设计评审第 9 条，
        用户判定只改这一个符号：其余四个是长相问题，这一个是意思错了。

        ⚠️ 钉的是**不许是警告三角**，不是「必须是铃铛」：换个更好的符号
           是设计决定，而用一个说错话的符号是缺陷。
        """
        markup = (Path(settings.BASE_DIR) / "dashboard" / "templates"
                  / "dashboard" / "_needs_you_band.html").read_text()
        body = re.sub(r"\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}", "",
                      markup, flags=re.S)
        self.assertNotIn("\u26a0", body,
                         "这一块又戴上警告三角了 —— 它说的是「有事在等你」，"
                         "不是「出错了」")

    def test_a_ministry_admin_only_sees_their_own_ministrys_shortfalls(self):
        """⚠️ 这张卡是这一页唯一会泄露别的 ministry 的地方。

        ⚠️ 断言收在**这张卡里面**，不是「整页里找不到这个名字」。
           第一版是后者，而 `Happening soon` 一上线它就红了 —— 别的 ministry
           的活动**本来就该**出现在那张卡上（那张卡问的是「我能报名什么」，
           而一个 Food Pantry 的 admin 当然报得了 Tax Help 的活动）。
           整页找字符串会把两张卡的语义混成一个断言。
        """
        self.make_admin(self.me, ministry=self.pantry)
        mine = self.an_event(ministry=self.pantry)
        mine.name = "Mine and short"
        mine.save()
        self.a_role(mine, needed_count=5)
        theirs = self.an_event(ministry=self.tax)
        theirs.name = "Theirs and short"
        theirs.save()
        self.a_role(theirs, needed_count=5)
        card = self.needs_you_card(self.page().content.decode())
        self.assertIn("Mine and short", card)
        self.assertNotIn("Theirs and short", card)

    @staticmethod
    def needs_you_card(page):
        """`Needs you` 那张卡的 HTML，从它的标题切到下一张卡的开头。

        ⚠️ 粗糙但够用，而且**故意粗糙**：一个真的 HTML 解析器会把这条测试
           变成一件需要维护的东西，而它要问的只是「这个名字有没有出现在这张卡
           里」。切错了会红，而红了就是有人动了卡片结构 —— 那正是该看一眼的时候。
        """
        start = page.index("Needs you")
        rest = page[start:]
        end = rest.find("<section", 1)
        return rest if end == -1 else rest[:end]

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

    def test_the_foundation_tier_gets_no_block_of_its_own(self):
        """说不出他每天要在首页看什么，所以不画 —— 入口在侧边菜单。

        ⚠️ 判据是「有没有**专属于**基金会那一档的东西」，不是「看不看得见
           Needs you」：他不管任何一个 ministry，所以他读到的是志愿者那一档
           （`ministry_ids_administered_by()` 不看 foundation 组）。
        """
        self.account.groups.add(
            Group.objects.get_or_create(name=FOUNDATION_ADMIN_GROUP)[0])
        page = self.page().content.decode()
        # ⚠️ 这里曾经还断言过 `"Recent signups"` 不出现（两处）。那句话
        #    **在这个仓库里从来没有存在过** —— 它只在 D42 里当作一张设想中的
        #    卡片被提过一次，代码里一个字节都没有。一条永远为真的
        #    `assertNotIn` 不是守卫，是一句看起来像守卫的话；
        #    2026-09-12 两处一起删了。
        self.assertNotIn("Events I manage", page)


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


class TheQueryBudgetTests(DashboardTestCase):
    """**装配仪表盘本身**要几次查询 —— 不含外壳。

    ⚠️ 不是一个性能测试，是一个**回归**测试：仪表盘天生是「再加一张卡」的地方，
       而每一张卡都是一次查询。一个会随卡片数量悄悄爬升的数字，需要有人在它
       爬升的那一次被拦一下 —— 那时该问的是「这张卡值不值这次查询」，而不是
       把这个数字往上调。

    🔴 **它量的是 `dashboard_for()`，不是一次 GET**（2026-09-12 改）。
       在此之前它 GET `/me/`；D44 把 `/me/` 变成 302 之后它被顺手改成 GET `/`，
       于是它和下面 `TheFrontPageQueryBudgetTests` 那一条**逐字节相同** ——
       同样的 fixture、同样的请求、同样的上限 20。两条测试、一个断言，
       而它们对「`/` 允许花多少」给出两个答案，将来必然各改各的。
       现在两个数各有各的意思：这里是**卡片的账**，那边是**整页的账**，
       差出来的就是外壳（会话、鉴权、`HomePage`、导航）那几次。
    """

    #: 实测 10（2026-09-12，ministry admin 那条最贵的路径）。
    #: ⚠️ 改这个数之前先读上面那段。
    BUDGET = 12

    def test_it_stays_within_its_budget(self):
        """⚠️ 上限，不是精确值 —— 所以用 `CaptureQueriesContext` 而不是
           `assertNumQueries`。后者是**等于**：少查了一次它也红，于是
           「顺手优化掉一次查询」会变成一次要改测试的改动，而那正好教人
           别去优化。这里要钉的是天花板。
        """
        self.make_admin(self.me)
        self.employ(self.me)
        self.sign_up()
        with CaptureQueriesContext(connection) as queries:
            dashboard_for(self.account)
        self.assertLessEqual(
            len(queries), self.BUDGET,
            f"装配仪表盘用了 {len(queries)} 次查询，上限是 {self.BUDGET}。"
            "先问这张新卡值不值这次查询，再考虑抬这个数。")

class HappeningSoonTests(DashboardTestCase):
    """「我还能报什么」，缺人的排前面。"""

    def test_it_leaves_out_what_i_have_already_signed_up_for(self):
        """⚠️ 否则它和 Coming up 说的是同一件事，一屏两张卡讲一个故事。"""
        joined = self.an_event(start=NOW + DAY)
        joined.name = "Already mine"
        joined.save()
        self.sign_up(event=joined)
        free = self.an_event(start=NOW + 2 * DAY)
        free.name = "Still open"
        free.save()
        self.a_role(free)
        page = self.page().content.decode()
        card = page[page.index("Happening soon"):]
        self.assertIn("Still open", card)
        self.assertNotIn("Already mine", card)

    def test_an_event_that_still_needs_people_comes_first(self):
        """⭐ 「还缺人的赢过更早的」—— 这是一条判断，所以它在 queryset 上。"""
        sooner = self.an_event(start=NOW + DAY)
        sooner.name = "Sooner but full"
        sooner.save()
        self.a_role(sooner, needed_count=1)
        Participation.objects.create(
            contact=make_person("Somebody"), event_role=sooner.roles.first())

        later = self.an_event(start=NOW + 5 * DAY)
        later.name = "Later but short"
        later.save()
        self.a_role(later, needed_count=5)

        names = [e.name for e in
                 Event.objects.open_for_signup(NOW).with_shortfall()]
        self.assertEqual(names[0], "Later but short")

    def test_a_role_with_no_limit_is_never_short(self):
        """⚠️ needed_count 为空是「不限人数」，不是「差无穷个」——
        口径不在这里重写，`is_short` 那个注解本来就带着这个陷阱。
        """
        event = self.an_event(start=NOW + DAY)
        self.a_role(event)          # needed_count 留空
        row = Event.objects.open_for_signup(NOW).with_shortfall().get(pk=event.pk)
        self.assertFalse(row.needs_people)

    def test_it_only_offers_events_this_person_can_see(self):
        hidden = self.an_event(start=NOW + DAY, visible_to_outsiders=False,
                               visible_to_all_staff=True)
        hidden.name = "Staff only"
        hidden.save()
        self.a_role(hidden)
        page = self.page().content.decode()
        self.assertNotIn("Staff only", page)

    def test_the_empty_state_says_so(self):
        self.assertIn("Nothing coming up that you can join.",
                      self.page().content.decode())


class ThePostsLineIsDefensiveTests(DashboardTestCase):
    """⚠️ 数据干净时 active() 本来就不会返回同岗位两条 —— 这是防御，不是修复。

    走查那天页面上出现了四行一模一样的 `Food Pantry lead · Food Pantry`，
    起因是 seed 的 get_or_create 键里含一个每天都在变的日期。那个 bug 修了，
    但**数据库仍然拦不住重叠任职**，所以这一行自己也要站得住。
    """

    def test_two_overlapping_tenures_in_one_post_show_as_one_line(self):
        post = Position.objects.create(
            code="lead", name="Food Pantry lead", kind=Position.Kind.STAFF,
            compensation=Position.Compensation.PAID, ministry=self.pantry)
        Assignment.objects.create(
            contact=self.me, position=post, start_date=TODAY - 30 * DAY)
        Assignment.objects.create(
            contact=self.me, position=post, start_date=TODAY - 20 * DAY)
        page = self.page().content.decode()
        self.assertEqual(page.count("Food Pantry lead"), 1)

    def test_two_different_posts_still_show_as_two_lines(self):
        """⚠️ 去重按**岗位**，不是「只显示一条」—— D32：一人可以多岗。"""
        self.employ(self.me, code="pantry_lead", ministry=self.pantry)
        self.employ(self.me, code="tax_lead", ministry=self.tax)
        page = self.page().content.decode()
        self.assertIn("Pantry Lead", page)
        self.assertIn("Tax Lead", page)


class TheLandingPageAfterLoginTests(DashboardTestCase):
    def test_logging_in_lands_on_the_dashboard(self):
        """⚠️ 2026-09-11 起落在 `/`，不再是 `/me/`。

        那不是换个地址而已：登录之后落在哪，决定了他**往上滚能不能看到 hero**。
        落在 `/` 上，仪表盘就是同一页的下半身；落在 `/me/` 上，它是一张没有上半身
        的页面。用户的原话是「我现在在 home page 完全不能 scroll up 看到 hero image」。
        """
        from django.urls import reverse
        response = self.client.post(reverse("accounts:login"), {
            "username": self.account.email, "password": "pw"})
        self.assertRedirects(response, "/")

    def test_the_public_front_page_still_does_not_redirect(self):
        """🔴 D25。登录之后 `/` 仍然是那张给所有人看的门面页。"""
        self.client.force_login(self.account)
        self.assertEqual(self.client.get("/").status_code, 200)


class TheCardsRespectTheirOwnRowCapTests(DashboardTestCase):
    """⭐ D42 的门槛第二条：内容天然不超过四行。

    ⚠️ `Needs you` 是唯一一张由**两组**拼起来的卡，所以也是唯一一张能悄悄
       画到八行的。走查那天它画了五行 —— 我自己定的规矩被自己破的第一处。
    """

    def test_needs_you_never_draws_more_than_the_cap(self):
        from dashboard.services import BAND_ITEMS as ROWS_PER_CARD
        from dashboard.services import _needs_you
        self.make_admin(self.me)
        for n in range(6):
            short = self.an_event(start=NOW + (n + 1) * DAY)
            self.a_role(short, needed_count=5)
            over = self.an_event(start=NOW - (n + 1) * DAY)
            over.status = Event.Status.OPEN
            over.save()
        # ⚠️ 第三个参数是**已经取回来的**「对我开放的活动」（2026-09-12 起，
        #    见 `_open_to_me()`）。这两条走的是 ministry admin 那一支，
        #    那一支一行都不读它 —— 传空列表，而不是再造一份假数据。
        rows = _needs_you({self.pantry.pk}, NOW, [])
        self.assertLessEqual(
            len(rows["short"]) + len(rows["unfinished"]), ROWS_PER_CARD)

    def test_the_places_that_still_need_people_get_the_slots_first(self):
        """⚠️ 还来得及做点什么的排在已经发生的前面。

        ⚠️ 上限从四条收到三条（`BAND_ITEMS`，2026-09-11）：那一块横过整幅、末尾还要
           放一格出口，三加一正好排满一行。这条测的是**优先级**不是那个数字，
           所以它跟着常量走。
        """
        from dashboard.services import BAND_ITEMS, _needs_you
        self.make_admin(self.me)
        for n in range(BAND_ITEMS + 1):
            short = self.an_event(start=NOW + (n + 1) * DAY)
            self.a_role(short, needed_count=5)
        over = self.an_event(start=NOW - DAY)
        over.status = Event.Status.OPEN
        over.save()
        rows = _needs_you({self.pantry.pk}, NOW, [])
        self.assertEqual(len(rows["short"]), BAND_ITEMS)
        self.assertEqual(rows["unfinished"], [])


class TheGreetingTests(DashboardTestCase):
    """按时段打招呼（D44）。

    🔴 小时必须从 `local_hour_of()` 来。`local_now()` 是 UTC-aware 的，洛杉矶
       上午十点它的 `.hour` 是 17 —— 直接问它，每个志愿者吃早饭时都会被祝一句
       晚上好，而没有任何东西会报错。D16 那个坑的第五种拼法。
    """

    def a_local_moment(self, hour):
        """本地时间当天的某个整点，还原成一个 aware 的瞬间。"""
        return timezone.make_aware(
            datetime.datetime.combine(TODAY, datetime.time(hour=hour)))

    def test_it_changes_three_times_a_day(self):
        for hour, expected in [(0, "Good morning"), (8, "Good morning"),
                               (11, "Good morning"), (12, "Good afternoon"),
                               (16, "Good afternoon"), (17, "Good evening"),
                               (23, "Good evening")]:
            with self.subTest(hour=hour):
                self.assertEqual(
                    _greeting(self.a_local_moment(hour)), expected)

    def test_it_reads_the_clock_in_the_foundations_timezone(self):
        """⚠️ 这一条才是上面那段 🔴 的验收点：同一个瞬间，本地是早上，UTC 是傍晚。

        不用 `local_hour_of()` 而直接向那个 aware 的瞬间要钟点的话，这条会拿到
        "Good evening"，而页面上那句话对着一个刚起床的人。
        """
        morning_here = self.a_local_moment(9)
        self.assertGreaterEqual(morning_here.astimezone(datetime.timezone.utc).hour, 16)
        self.assertEqual(_greeting(morning_here), "Good morning")

    def test_the_page_says_it_out_loud(self):
        page = self.page().content.decode()
        self.assertIn(_greeting(local_now()), page)
        self.assertIn(self.account.display_name(), page)


class TheYearFigureTests(DashboardTestCase):
    """本年度那个大字，以及它最容易错的那一半（D44 第六节）。"""

    def a_course(self, start):
        """一门课：活动本身加两次讲次，签到的工时挂在讲次上。"""
        event = self.an_event(start=start)
        signup = Participation.objects.create(
            contact=self.me, event_role=self.a_role(event),
            status=Participation.Status.ATTENDED,
            served_as=Participation.ServedAs.VOLUNTEER)
        return event, signup

    def test_a_course_that_crosses_the_new_year_is_split_by_evening(self):
        """🔴 报名那一半按活动的日期，签到那一半按**讲次自己的**日期。

        一门去年十二月开课、一月还在上的课，如果两半都按活动日期切，整期工时
        会被算进去年 —— 而这类课正是 `SessionAttendance` 存在的理由。
        一月一号那天打开首页的人会看到自己今年归零，而他上周还去上过课。
        """
        start_of_this_year, _ = year_bounds(TODAY.year)
        event, signup = self.a_course(start=start_of_this_year - 20 * DAY)

        for when, hours in [(start_of_this_year - 10 * DAY, Decimal("2.00")),
                            (start_of_this_year + 10 * DAY, Decimal("3.00"))]:
            session = Session.objects.create(
                event=event, start_time=when, end_time=when + 2 * HOUR)
            SessionAttendance.objects.create(
                participation=signup, session=session, hours=hours,
                status=Participation.Status.ATTENDED)

        mine = Participation.objects.mine(self.me).volunteering()
        total, within = mine.hours_given_and_within(*year_bounds(TODAY.year))
        self.assertEqual(total, Decimal("5.00"))
        self.assertEqual(within, Decimal("3.00"),
                         "跨年的课被整期算进了它开课的那一年")

    def test_last_years_signup_counts_in_the_running_total_only(self):
        long_ago = self.an_event(start=NOW - 400 * DAY)
        Participation.objects.create(
            contact=self.me, event_role=self.a_role(long_ago),
            hours=Decimal("7.50"), status=Participation.Status.ATTENDED,
            served_as=Participation.ServedAs.VOLUNTEER)
        mine = Participation.objects.mine(self.me).volunteering()
        total, within = mine.hours_given_and_within(*year_bounds(TODAY.year))
        self.assertEqual(total, Decimal("7.50"))
        self.assertEqual(within, Decimal("0"))

    def test_work_hours_never_reach_either_number(self):
        """🔴 D36：两个账本永不相加。这个方法也只认志愿那一本。"""
        event = self.an_event(start=NOW - DAY)
        Participation.objects.create(
            contact=self.me, event_role=self.a_role(event),
            hours=Decimal("9.00"), status=Participation.Status.ATTENDED,
            served_as=Participation.ServedAs.WORK)
        mine = Participation.objects.mine(self.me).volunteering()
        self.assertEqual(mine.hours_given_and_within(*year_bounds(TODAY.year)),
                         (Decimal("0"), Decimal("0")))


class TheBandForAVolunteerTests(DashboardTestCase):
    """通栏那一块对**不管任何 ministry** 的人说的那句话（D44 第七节）。"""

    def an_open_event(self, name="Beach clean-up", start=None):
        event = self.an_event(start=start or NOW + 3 * DAY)
        event.name = name
        event.save()
        self.a_role(event, needed_count=5)
        return event

    def test_it_offers_events_that_still_need_people(self):
        event = self.an_open_event()
        rows = dashboard_for(self.account)["needs_you"]
        self.assertEqual([e.pk for e in rows["open"]], [event.pk])
        self.assertEqual(rows["short"], [])
        self.assertEqual(rows["count"], 1)

    def test_an_event_with_enough_people_is_not_waiting_on_anybody(self):
        event = self.an_event(start=NOW + 3 * DAY)
        self.a_role(event, needed_count=None)
        rows = dashboard_for(self.account)["needs_you"]
        self.assertEqual(rows["open"], [])
        self.assertEqual(rows["count"], 0)

    def test_it_leaves_out_what_i_have_already_signed_up_for(self):
        event = self.an_open_event()
        Participation.objects.create(
            contact=self.me, event_role=event.roles.first())
        self.assertEqual(dashboard_for(self.account)["needs_you"]["open"], [])

    def test_happening_soon_does_not_repeat_the_band(self):
        """🔴 两块并排说同一场活动，是这一版最容易长出来的毛病。

        通栏那一块和 `Happening soon` 的取数口径几乎一样，所以下面那张卡必须
        排除掉上面已经列出的 —— 同 `mine` 那条既有的排除，不是新规矩。
        """
        listed = self.an_open_event()
        other = self.an_open_event(name="Pantry sorting", start=NOW + 5 * DAY)
        other.roles.update(needed_count=None)

        context = dashboard_for(self.account)
        self.assertEqual([e.pk for e in context["needs_you"]["open"]], [listed.pk])
        self.assertNotIn(listed.pk, [e.pk for e in context["happening_soon"]])
        self.assertIn(other.pk, [e.pk for e in context["happening_soon"]])

    def test_the_two_blocks_cost_one_query_between_them(self):
        """🔴 通栏和 `Happening soon` 读的是**同一次**查询（2026-09-12）。

        在此之前它们各跑一遍几乎相同的链，第二遍再 `exclude` 掉第一遍的结果。
        那条链是这一页最重的形状 —— `for_audience()` 是一个 annotation 加三条
        `EXISTS`，`with_shortfall()` 再加一条带两个 `COUNT(DISTINCT)` 的 `EXISTS`
        —— 而志愿者（也就是大多数人）每次打开 `/` 都要付两遍。

        ⚠️ 钉的是**次数**，不是那条 SQL 长什么样：合并的正确性由上面
           `test_happening_soon_does_not_repeat_the_band` 和它的邻居们管，
           这一条只管「别再变回两次」。
        """
        self.an_open_event()
        self.an_open_event(name="Pantry sorting", start=NOW + 5 * DAY)
        with CaptureQueriesContext(connection) as queries:
            dashboard_for(self.account)
        shortfall = [q for q in queries.captured_queries
                     if "needs_people" in q["sql"]]
        self.assertEqual(
            len(shortfall), 1,
            f"那条带 shortfall 的查询跑了 {len(shortfall)} 次 —— "
            "通栏和 Happening soon 又各查了一遍。")

    def test_a_ministry_admin_gets_the_other_shape_instead(self):
        self.make_admin(self.me)
        self.an_open_event()
        rows = dashboard_for(self.account)["needs_you"]
        self.assertEqual(rows["open"], [])
        self.assertEqual(len(rows["short"]), 1)


class TheFrontPageCarriesTheDashboardTests(DashboardTestCase):
    """`/` 登录之后往下滚就是这一页（D44），而陌生人那一页一个字节没变。"""

    def test_a_stranger_gets_the_front_page_and_nothing_of_mine(self):
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        body = page.content.decode()
        self.assertNotIn('class="deck', body)
        self.assertNotIn("Good morning", body)
        self.assertNotIn("Good afternoon", body)
        self.assertNotIn("Good evening", body)

    def test_signed_in_it_carries_the_whole_deck(self):
        self.sign_up()
        self.client.force_login(self.account)
        body = self.client.get("/").content.decode()
        self.assertIn('class="deck"', body)
        self.assertIn("Needs you", body)
        self.assertIn("Volunteer hours this year", body)
        self.assertIn("Autumn food drive", body)

    def test_the_whole_deck_is_on_the_one_page(self):
        """⚠️ 2026-09-11 改口：这条原来测的是「一份 partial 两个壳」，
           而现在只剩一个壳 —— `/me/` 是跳转，不再自己渲染一份。
           改成钉「这一页上几块全在」：少了任何一块都说明 partial 被拆了。

        ⚠️ 2026-09-12：照片和仪表盘之间的过渡整套移除了（改在模拟器里重做），
           所以和过渡有关的地标一并去掉 —— 这一条只钉 partial 有没有被拆开。
        """
        self.client.force_login(self.account)
        body = self.client.get("/").content.decode()
        # ⚠️ 每个地标都必须是**真正渲染出来、并且有样式挂着**的东西
        #    （2026-09-12 改）。这里原来有一个 `deck-grid`，而那个 class 在
        #    app.css 里一条规则都没有 —— 于是这条断言成了它唯一的「使用者」，
        #    一个空 class 因此看起来像有人在用。地标要认页面，不要养 class。
        for landmark in ["deck-head", "deck-hours-figure", "band-head",
                         "deck-hours-label"]:
            self.assertIn(landmark, body)

    def test_signed_in_the_front_page_offers_the_theme_switch(self):
        """⚠️ 2026-09-11 加。这一页现在跟随深色模式（下面有一整屏卡片），
           所以它也得有那个开关 —— 跟随而不给开关，等于把选择权收走了。

        ⚠️ 匿名那一半照旧：那一页是满屏照片配白字，不跟随主题，也就没有可切的。
        """
        self.client.force_login(self.account)
        self.assertContains(self.client.get("/"), "themeToggle")

    def test_a_stranger_gets_no_theme_switch(self):
        self.assertNotContains(self.client.get("/"), "themeToggle")

    def test_it_still_does_not_redirect_anybody(self):
        """🔴 D25 那一条一个字没改，而它是这一轮最容易顺手破掉的。"""
        self.client.force_login(self.account)
        self.assertEqual(self.client.get("/").status_code, 200)


class TheFrontPageQueryBudgetTests(DashboardTestCase):
    """`/` 现在也要付仪表盘那笔账，所以它也有一个上限。

    ⚠️ 和 `TheQueryBudgetTests` 一样是**回归**测试不是性能测试：数字爬升的那一次
       该问的是「这一块值不值这次查询」，不是把上限往上调。

    🔴 匿名那一次单列，而它是这一条里最要紧的一半：陌生人打开首页**一次
       仪表盘查询都不该付**。忘掉这件事的写法（先装配再判断登录）在页面上
       看不出任何区别。
    """

    #: 实测 18（2026-09-11），比 `/me/` 多一次 —— 那一次是首页那一行
    #: `HomePage`。⚠️ 改这个数之前先读上面那段。
    BUDGET = 20
    #: 🔴 实测 **1**：只有 `HomePage` 那一行。这个数字小得刺眼是有意的 ——
    #: 它是「陌生人不付仪表盘的账」这句话唯一说得出口的证据。
    #: ⚠️ 上限写 2 留一格，而**真正钉住那句话的不是这个数**（2026-09-12 补）：
    #:    数字总可以被往上调，所以下面那条还直接检查了 context 里
    #:    一个仪表盘的键都没有 —— 那句话没有「差不多」的版本。
    ANONYMOUS_BUDGET = 2

    def test_signed_in_it_stays_within_its_budget(self):
        self.make_admin(self.me)
        self.employ(self.me)
        self.sign_up()
        self.client.force_login(self.account)
        with CaptureQueriesContext(connection) as queries:
            self.client.get("/")
        self.assertLessEqual(
            len(queries), self.BUDGET,
            f"/ 用了 {len(queries)} 次查询，上限是 {self.BUDGET}。")

    def test_a_stranger_pays_for_none_of_it(self):
        self.sign_up()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get("/")
        self.assertLessEqual(
            len(queries), self.ANONYMOUS_BUDGET,
            f"匿名访客用了 {len(queries)} 次查询 —— 仪表盘在未登录时"
            "不该装配。")
        # 🔴 数字之外再钉一次那句话本身：不是「少装配一点」，是**一个键都没有**。
        #    忘掉这件事的写法（先装配、再在模板里判断登录）查询数会跳，
        #    但如果哪天装配变便宜了，只靠上限那个数就拦不住它了。
        for key in ["greeting", "needs_you", "weeks", "hours_this_year"]:
            self.assertNotIn(
                key, response.context,
                f"匿名访客的 `/` 上下文里出现了 `{key}` —— 仪表盘装配了。")


class TheCalendarAgreesWithTheDateLineTests(DashboardTestCase):
    """🔴 侧栏月历圈的那一天，必须就是问候语上面那一行写的那一天。

    2026-09-11 修的一个既有 bug：`services.py` 直接问那个 aware 的 `now` 要日子，
    拿到的是 UTC 那一天。洛杉矶下午五点之后 UTC 已经翻页，于是月历把**明天**
    圈成今天；每个月最后一晚更狠 —— `month_name` 和整片格子一起跳到下个月，
    错七个小时，而没有任何东西报错。

    ⚠️ 它在仓库里从仪表盘上线那天就在，一直全绿：在 D44 把日期印在月历旁边之前，
       这一页上**没有第二个地方说今天几号**，所以没有任何东西能和它对质。
    """

    def an_evening(self, year, month, day):
        """本地时间那天傍晚七点，**按 `local_now()` 的样子造**：带 UTC 时区。

        🔴 这里差一点写成 `timezone.make_aware(...)`，而那样这两条测试**永远是绿的**：
           `make_aware` 挂上的是洛杉矶时区，于是问它要日子恰好给对 —— 而生产环境里
           `local_now()` 给的是 `timezone.now()`，挂的是 UTC。也就是说测试造出来的
           那个 `now` 和页面真正拿到的那个，在这件事上性质相反。
           反着验一遍（把修复装回旧写法、看测试红不红）才发现，守卫红了、这两条没红。
        """
        # ⚠️ 时区**在构造时就挂上**，不是先造一个裸的再 `make_aware()`
        #    （2026-09-12 改）。两种写法算出来完全一样，而裸的那个是 ruff 的
        #    `DTZ001` —— 这个项目引入 ruff 的**唯一**理由就是 DTZ（见 pyproject
        #    那段注释和 D16）。在它身上开第一个 `noqa` 来换一行写法，不值。
        local = datetime.datetime(
            year, month, day, 19, 0, tzinfo=timezone.get_current_timezone())
        return local.astimezone(datetime.timezone.utc)

    def test_after_five_pm_the_grid_still_circles_today(self):
        evening = self.an_evening(2026, 9, 11)
        self.assertEqual(
            evening.astimezone(datetime.timezone.utc).date(),
            datetime.date(2026, 9, 12),
            "这条测试的前提没了：那一刻 UTC 必须已经翻页，否则它证明不了什么")

        weeks = dashboard_for(self.account, now=evening)["weeks"]
        today = [cell["day"] for week in weeks for cell in week
                 if cell.get("is_today")]
        self.assertEqual(today, [11])

    def test_on_the_last_evening_of_a_month_the_grid_stays_in_that_month(self):
        """⚠️ 这一条比上一条重的地方：错的不是一个圈，是整片格子和标题。"""
        context = dashboard_for(self.account, now=self.an_evening(2026, 9, 30))
        self.assertEqual(context["month_name"], "September 2026")
        days = [cell["day"] for week in context["weeks"] for cell in week
                if cell.get("day")]
        self.assertEqual(max(days), 30)

    def test_on_that_evening_the_dots_come_from_that_month_too(self):
        """🔴 **上面两条查的是格子，这一条查的是格子上的点** —— 而 2026-09-12
           之前它们来自两个不同的月份。

        标题和格子用的是 `local_date_of(now)`（本地），点用的是
        `month_bounds(<那个 aware 的 now>.year, .month)`（UTC）。所以在每个月的
        最后一晚，这一页画的是本地的当月、标的是**下个月**的活动 —— 两边各自看
        都说得通，而上面那两条断言一条都碰不到点，于是它们在自己被写来盯的那一晚
        照样全绿。

        ⚠️ 这是本项目第三次差点留下一条「为某个场景写的、而在那个场景里也绿」
           的守卫。反向验过：把服务层改回按 `now` 取年月，这一条红、上面两条绿。
        """
        evening = self.an_evening(2026, 9, 30)
        self.sign_up(event=self.an_event(
            start=self.an_evening(2026, 9, 24), name="September drive"))
        self.sign_up(event=self.an_event(
            start=self.an_evening(2026, 10, 8), name="October drive"))

        weeks = dashboard_for(self.account, now=evening)["weeks"]
        marked = sorted(cell["day"] for week in weeks for cell in week
                        if cell.get("marked"))
        self.assertEqual(
            marked, [24],
            "月历上的点来自 UTC 那个月，而格子来自本地那个月。")
