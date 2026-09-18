"""QuerySet behaviour shared by every table that carries a start/end date."""

import datetime

from django.db import models
from django.db.models import Q

from core.timeutils import local_today


def in_effect_on(on=None, prefix=""):
    """The "covers this day" predicate as a Q, so related tables can reuse it.

    ⭐ **`end_date` 是第一个不算数的日子** —— 区间右开：`start_date <= on < end_date`。
       「做到 3 月 15 日」记成 `end_date = 3 月 16 日`；「今天撤销」记成
       `end_date = 今天`。整条规则和它的全部理由在 goal.md D51。

       ⚠️ 2026-09-17 之前这里是**右闭**的，而同一条规则在项目里有四种写法
          （`active()` / `in_force()` / `is_currently_active` / `is_in_force`）。
          其中两种是 9-15 和 9-16 分两天加的，中间那一天权限层和授权页各说各的。
          D51 把**语义轴整个消掉了**：现在只剩「问一批行」和「问一行」两种求值
          方式，规则只有一条。事实和权限的区别改由**写进去的是哪一天**表达 ——
          撤销写今天，任职结束写最后一天的次日。

    `prefix` walks a relation: in_effect_on(prefix="assignments__") is what
    Position uses to count the people currently in a post, and what its
    vacancy query is defined against. Without the prefix the expression would
    have to be spelled out a second time inside the aggregate — and the second
    copy is the one that quietly disagrees with the first.

    `on` is resolved here, at call time. A default argument holding a date
    would freeze at import and drift further off every day a worker stays up.

    ⚠️ `on` may also be a **database expression** rather than a date — an
       OuterRef onto a day the outer query annotated, which is how a correlated
       subquery asks this of a set of events that each have their own day
       (org.audience.on_the_books_exists). The `or` below is safe with one:
       an expression object is truthy, so it is never silently swapped for
       today. Checked rather than assumed.
       ⚠️ 那条路的边界有自己的测试（`org.tests.StaffRosterTests
          .test_a_tenure_ending_on_the_event_day_still_sees_that_event`），
          因为它判的是**活动那一天**而不是今天 —— 另外三条路径覆盖不到它。
    """
    on = on or local_today()
    start, end = f"{prefix}start_date", f"{prefix}end_date"
    return (
        # ⚠️ 三张表的 `start_date` 都不可为空（D51，也是 SQL:2011 对 `PERIOD`
        #    起止列的要求），所以这里不再有 isnull 那一支 —— 少一支就少一格
        #    没有人验过的边界。
        Q(**{f"{start}__lte": on})
        # 🔴 **`__gt` 而不是 `__gte`** —— 这一个字母就是整条 D51。
        #    `end_date` 为空读作「还没有结束」，那一支不能省。
        & (Q(**{f"{end}__isnull": True}) | Q(**{f"{end}__gt": on}))
    )


class DateRangeQuerySet(models.QuerySet):
    """The "in effect on a given day" predicate, defined exactly once.

    Assignment, MinistryRole and EventGrant all use it. That Q expression ends
    up behind the ministry page, the active-headcount numbers, every permission
    check in the system and several admin filters — written out ten times, one
    of them would be wrong, and a wrong one does not raise, it just reports a
    number that is quietly off.

    ⚠️ This layer knows about dates only, never about status. Assignment adds
       its own serving() (= active() AND status=active) on top; do not push
       status down into here — MinistryRole has no status, a grant of authority
       is never "suspended". See goal.md「Assignment.status」.

    ⚠️ **没有 `in_force()`，而那不是漏掉的一个方法。** 2026-09-15 到 09-17 之间
       有过一个：`active()` 的右开版本，专给权限判断用。D51 合并了两者 ——
       `active()` 自己就是右开的，权限和事实读同一条谓词。
    """

    def active(self, on=None):
        """Rows whose date range covers `on` (default: today, foundation time)."""
        # The predicate itself lives in in_effect_on() above, because Position
        # needs the same one applied across a relation. One definition, three
        # callers — not three definitions that agree today.
        return self.filter(in_effect_on(on))


class DateRangeMixin:
    """The same predicate for a single row already in memory.

    ⚠️ This and DateRangeQuerySet.active() above are two implementations of one
       rule. They sit in the same file precisely so that "change one, change the
       other" is a glance rather than a promise — the alternative,
       `type(self).objects.filter(pk=self.pk).active().exists()`, costs one query
       per row, which in an admin changelist is an N+1.

       🔴 **而这句话 2026-09-15 那天没有兑现** —— 集合谓词加了，行级那一半第二天
          才补上。所以现在不再只靠这句话：`core.tests.DatePredicateHalvesAgreeTests`
          逐格比对这两半，加了一个而忘了另一个，下一次就是红的。
    """

    @property
    def is_currently_active(self):
        """今天落在 `[start_date, end_date)` 里吗 —— `active()` 的行级那一半。

        ⚠️ **没有改名成 `is_active`，而这是有意的。** `is_active` 在这个项目里
           已经是 Contact / Ministry / Position / EmploymentType /
           ParticipationRole 上的**字段**，意思是「这个东西还存在」，而
           `position_detail.html` 就在任职表格的正上方打印 `position.is_active`。
           一个名字两个意思，正是 D51 要消灭的那类东西。
        """
        on = local_today()
        return (
            self.start_date <= on
            # 🔴 `>` 而不是 `>=` —— 和 `in_effect_on()` 的 `__gt` 是同一条规则，
            #    而它们分成两处写，只是因为一个问一批行、一个问一行。
            and (self.end_date is None or self.end_date > on)
        )

    # --- 给人看的那两个日期（D51 第四节）---------------------------------
    #
    # 🔴 **存的那一天和人该看到的那一天，差一天。** `end_date` 是第一个不算数的
    #    日子，所以一段做到 3 月 15 日的任职，那一列存的是 3 月 16 日。裸打印
    #    出来不报错，只是把每个人的最后一天说晚了一天。
    #
    # ⭐ 所以有**两个具名展示器**，各一处实现，模板只许用它们
    #    （守卫 `core.tests.EndDateIsNeverShownRawGuardTests`）。
    #    这不是又养出两条规则：被消灭的是两种**查询语义**，留下的是两个**标签**，
    #    而标签本来就该随业务领域不同 —— Google Calendar 对全天事件做的是同一件事
    #    （存 exclusive、显示 inclusive）。

    @property
    def last_day(self):
        """最后一个算数的日子，给**事实**那一侧看（任职：「做到哪天」）。

        没有结束日期就是 None —— 模板照旧印一个「—」。
        """
        if self.end_date is None:
            return None
        return self.end_date - datetime.timedelta(days=1)

    @property
    def revoked_on(self):
        """失效的那一天，给**授权**那一侧看（「撤销于 X 日」＝ X 日起没了）。

        ⚠️ 这一个**不减一天**，而那不是漏：右开的边界正是「撤销发生在哪一天」的
           自然说法，ISO 27001 A.5.18 说的「termination 时移除访问权」也是这个
           形状。它存在是为了让模板不必裸取 `end_date` —— 有一个名字，
           下一个人就不会顺手把任职那一列也照抄成裸打印。
        """
        return self.end_date
