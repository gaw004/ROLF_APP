"""QuerySet behaviour shared by every table that carries a start/end date."""

from django.db import models
from django.db.models import Q

from core.timeutils import local_today


def in_effect_on(on=None, prefix=""):
    """The "covers this day" predicate as a Q, so related tables can reuse it.

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
    """
    on = on or local_today()
    start, end = f"{prefix}start_date", f"{prefix}end_date"
    return (
        # The start half is not optional: with only the end test, a row
        # starting 2027-01-01 with no end date counts as in effect today.
        (Q(**{f"{start}__isnull": True}) | Q(**{f"{start}__lte": on}))
        & (Q(**{f"{end}__isnull": True}) | Q(**{f"{end}__gte": on}))
    )


class DateRangeQuerySet(models.QuerySet):
    """The "in effect on a given day" predicate, defined exactly once.

    Assignment and MinistryRole both use it. That Q expression ends up behind
    the ministry page, the active-headcount numbers and several admin filters —
    written out ten times, one of them would be wrong, and a wrong one does not
    raise, it just reports a number that is quietly off.

    ⚠️ This layer knows about dates only, never about status. Assignment adds
       its own serving() (= active() AND status=active) on top; do not push
       status down into here — MinistryRole has no status, a grant of authority
       is never "suspended". See goal.md「Assignment.status」.
    """

    def active(self, on=None):
        """Rows whose date range covers `on` (default: today, foundation time)."""
        # The predicate itself lives in in_effect_on() above, because Position
        # needs the same one applied across a relation. One definition, two
        # callers — not two definitions that agree today.
        return self.filter(in_effect_on(on))

    def in_force(self, on=None):
        """`active()`，但 `end_date` 右开 —— **授权专用**（2026-09-15）。

        ⭐ 权限判断走这一个，事实记录（任职、工时、报表）走 `active()`。
           两者只在「`end_date` 正好是今天」的那一行上不同 —— 而在授权表上，
           那一行的唯一含义是「今天被撤销了」。整段理由在
           `_ended_on_or_before()` 上。

        ⚠️ 名字不叫 `active_for_permissions()`：它要短到每一处权限判断都愿意
           用它，而 "in force"（现行有效）正是一条授权此刻算不算数的说法。
        """
        on = on or local_today()
        return self.active(on).exclude(_ended_on_or_before(on))


def _ended_on_or_before(on, prefix=""):
    """授权表专用：`end_date` 按**右开**读 —— 填到今天就是今天起失效。

    🔴 **这和 `in_effect_on()` 的右闭语义是两条，而它们各自都对。**

       · **任职 / 工时 / 报表**走右闭：「做到 3 月 15 日」的日常含义是 15 号那天
         还算数。15 号当天不算在职的话，工时会少算一天。
         `core/tests.py` 的 `test_active_includes_a_row_ending_today` 钉着它，
         **别动**；
       · **授权**走右开，因为那一列在授权表上**只有一个来源**：撤销。

    ⭐ **「只有一个来源」是这条规则成立的全部条件**（2026-09-15，用户拍板）。
       授权的表单上**没有截止日期那一格** —— 一条授权只有「从哪天开始」和
       「撤销」两种状态，而一场活动的授权本来就管到这场活动收尾为止。
       于是 `end_date = 今天` 只可能是「今天被撤销了」，没有第二种读法。

       ⚠️ 不这么定的话就得加一列 `revoked_at` 来分开「今天被撤销」和「本来就排到
          今天为止」。而那一列的存在**只是为了补救表单上那一格** —— 去掉那一格，
          整个问题不存在。

       ⚠️ 第一版试过用 `updated_at` 的那一天当判据，**不成立**：
          `TimeStampedModel.updated_at` 是 `auto_now`，任何一次保存都会写它，
          `update_fields` 也拦不住。被一条测试当场抓到。

    ⚠️ 不这么做的后果是具体的：撤销把 `end_date` 填成今天，按右闭读等于
       「今天剩下的时间里他照旧有权限，明天起失效」。按钮说「撤销」，发生的是
       「明天起撤销」，而页面上没有任何地方说这件事 —— 安全上这叫 revocation
       lag，而真实系统（Okta / AWS IAM / 门禁）一律是即时的。
    """
    return Q(**{f"{prefix}end_date": on})


class DateRangeMixin:
    """The same predicate for a single row already in memory.

    ⚠️ This and DateRangeQuerySet.active() above are two implementations of one
       rule. They sit in the same file precisely so that "change one, change the
       other" is a glance rather than a promise — the alternative,
       `type(self).objects.filter(pk=self.pk).active().exists()`, costs one query
       per row, which in an admin changelist is an N+1.
    """

    @property
    def is_currently_active(self):
        on = local_today()
        return (
            (self.start_date is None or self.start_date <= on)
            and (self.end_date is None or self.end_date >= on)
        )
