"""Reading the org chart. Permanent asset — see goal.md D18.

⚠️ This module is the only place in the project that walks the reporting
   chain. core/tests.py greps for a second one and fails if it finds it. The
   reason is not tidiness: a loop that spans two rows (A reports to B, B
   reports to A) is two individually legal inserts, and no CHECK constraint can
   see it. Any recursion that meets one hangs. Putting the protection in one
   function beats "every traversal remembers to carry a visited set", which is
   discipline — the kind this project has already thrown out twice.
"""

import datetime
import logging
from collections import defaultdict
from dataclasses import dataclass

from django.core.exceptions import ValidationError

from django.db import models

from core.querysets import DateRange, in_effect_on
from core.timeutils import local_today

from .models import Assignment, Ministry, MinistryRole, Position

logger = logging.getLogger(__name__)

# A chain deeper than this is not an org chart, it is data damage. The visited
# set below already guarantees termination; this is a second floor under it, so
# that a pathological row costs a bounded number of queries rather than a hang.
MAX_CHAIN_DEPTH = 20


def creates_a_reporting_cycle(position):
    """Would `position.reports_to` close a loop? Used by Position.clean().

    Climbs from the proposed manager upward, one query per level. That is fine
    here and only here: it runs once per form submit on a chain that is a
    handful deep, not once per row of a changelist. build_org_tree() is the one
    that has to be cheap, and it takes the whole table in a single query.
    """
    seen = {position.pk} if position.pk else set()
    current = position.reports_to
    for _ in range(MAX_CHAIN_DEPTH):
        if current is None:
            return False
        if current.pk in seen:
            return True
        seen.add(current.pk)
        current = current.reports_to
    # Ran out of depth without reaching the top. With `seen` in hand this can
    # only mean the chain is absurdly long, so refuse it the same way.
    return True


def build_org_tree(positions=None):
    """The whole chart as a list of roots, each with a `children` list attached.

    Two things it guarantees, both of which have a test nailing them down:

    1. **One query.** Position is a table of a few dozen rows; fetching all of
       it and assembling the tree in memory beats any per-level query, and
       climbing `position.reports_to` row by row would be an N+1.
    2. **Bad data does not hang it.** clean() refuses to save a loop, but
       bulk_create never calls clean() — so a loop can exist. Anything caught
       in one is hung at the root and logged, rather than recursed into.

    Every position appears exactly once, so callers can render the result
    without ever knowing a loop was possible.
    """
    if positions is None:
        positions = list(Position.objects.select_related("ministry"))

    by_id = {position.pk: position for position in positions}
    children = defaultdict(list)
    roots = []
    looping = set()

    for position in positions:
        # .reports_to_id, never .reports_to: the attribute version would fire a
        # query per row and turn the promised single query into an N+1.
        manager_id = position.reports_to_id
        if manager_id is None or manager_id not in by_id:
            # No manager, or a manager outside the set we were handed: a root
            # as far as this chart is concerned.
            roots.append(position)
            continue
        if _climbs_into_a_loop(position, by_id):
            looping.add(position.pk)
            roots.append(position)
            continue
        children[manager_id].append(position)

    for position in positions:
        position.children = children[position.pk]

    if looping:
        logger.warning(
            "Reporting lines contain a loop; positions %s were hung at the root. "
            "Fix reports_to on one of them.",
            sorted(looping),
        )
    return roots


def _climbs_into_a_loop(position, by_id):
    """True if climbing from `position` revisits somebody it has already seen."""
    seen = set()
    current = position
    while current is not None and current.reports_to_id is not None:
        if current.pk in seen:
            return True
        seen.add(current.pk)
        current = by_id.get(current.reports_to_id)
    return False


def ministry_admins(ministry):
    """Every grant ever made on this ministry, current ones and finished ones.

    Here rather than in the view because the grant table is not something a
    view touches: permissions.py judges, this writes and reads, and views call
    one of the two. core/tests.py greps for a view reaching past both.
    """
    return (
        MinistryRole.objects.filter(ministry=ministry)
        .select_related("contact", "granted_by")
        .order_by("-start_date")
    )


def grant_ministry_admin(*, contact, ministry, granted_by, start_date=None):
    """Appoint somebody as a ministry's admin. P5.

    granted_by is passed in from the session by the caller and is never a field
    on a form — a box somebody can type in is a box somebody can lie in.

    🔴 **他已经有一条压着的授权 → 拒绝；否则新建一行。没有「恢复」那一支了**
       （D51，2026-09-17）。

       2026-09-16 到 09-17 之间这里有一个「恢复」分支：同一把钥匙上已经有一行
       （键是 `(contact, ministry, role, start_date)`，而表单的 `start_date` 默认留空）
       就把它的 `end_date` 清掉。它解决的是一个真问题 ——「授权 → 手滑撤销 →
       再授权」在那之前是一个 `IntegrityError`（500）—— 但它是**在给一条键错了
       列的约束打补丁**，代价是把「中间断过一段」从当前行上抹掉，
       在当前表里造出一段**从未存在过的连续授权**。

       约束换成区间排他之后，那个问题根上就没有了：今天撤销的那一行是
       `[…, 今天)`，今天再授权的是 `[今天, …)`，两段不重叠，于是那是**第二行**，
       断档如实留在当前表里。HRIS 的规范做法也正是这个（重新授权开新行、
       旧行不动，SCD Type 2）。

    ⚠️ `full_clean()` 不能省，即使上面那一支已经处理了重复：别的约束
       （`end_date >= start_date`）照旧要在存之前被问一次，而
       `core/constraints.py` 已经把违约码接到了具体那一格上 ——
       于是一次真正的冲突是表单上的一句话，不是 500。

    ⚠️ 和 `events.services.grant_event_admin()` **形状一样、各写一份**：
       它们是两张表，不是一条规则的两份实现。改这里想一想那边。

    抛 `ValidationError`，由调用方落到表单上。
    """
    # ⚠️ 表单上那一格留空时填**今天**，而不是交给数据库的 `default` —— 写入口在
    #    服务层（D18），而一个 `None` 走到模型层就是一次 NOT NULL 违约（D51 起
    #    `start_date` 不可为空）。
    start_date = start_date or local_today()
    if MinistryRole.objects.active().filter(
            contact=contact, ministry=ministry,
            role=MinistryRole.Role.ADMIN).exists():
        raise ValidationError({"contact": "They already administer this ministry."})

    grant = MinistryRole(
        contact=contact,
        ministry=ministry,
        role=MinistryRole.Role.ADMIN,
        start_date=start_date,
        granted_by=granted_by,
    )
    grant.full_clean()
    grant.save()
    return grant


def find_grant(ministry, pk):
    """One grant on this ministry, or None. Scoped by ministry deliberately:
    a pk from a form must not be able to reach another ministry's row."""
    return MinistryRole.objects.filter(ministry=ministry, pk=pk).first()


def revoke_ministry_role(grant, *, on=None):
    """Withdraw a grant by ending it, never by deleting the row.

    Ending is what end_date says — the rule Assignment already lives by. A
    deleted grant leaves no answer to "who could see this ministry's signups
    last March", and this table carries simple-history precisely because that
    question gets asked.
    """
    grant.end_date = on or local_today()
    grant.save(update_fields=["end_date", "updated_at"])
    return grant


# --- 员工名册（Phase D 的 D1.8 / D1.9，2026-09-15 落地） ---------------------
#
# ⚠️ **签名和 05-roadmap.md 的 D1.8 写的不一样，而那是有意的。** 原文是
#    `staff_directory(ministries, *, on=None)` —— 收一组 ministry。可
#    `Position.ministry` 是**可空**的（基金会级的岗位，见 Position.ministry 的
#    help_text），按 ministry 收窄会把那一批岗位整个漏掉：不报错，只是它们不在
#    任何一张名册上。这和 D2a.10 给 `on_duty()` 记下的是同一个坑，那里的处置是
#    「`ministry` 那个参数要允许 None」。
#
#    这里换成收**已经按权限收窄好的 `Position` queryset**，于是「含不含基金会级
#    岗位」由收窄的那一处回答（`org.views._scoped_positions()`，照
#    `events.views._scoped_events()` 的形状写的）—— 而那一处本来就要判权限。
#
# ⚠️ D1.8 原文那条「收 queryset 不收 id」的理由一个字没变，见 D27 的唯一不变量：
#    收 id 的写法需要在函数里再判一次权限，而那一处判断迟早和页面那一处走散。


@dataclass(frozen=True)
class RosterRow:
    """名册上的一行：一个岗位，以及此刻在这个岗位上的人。

    ⚠️ `holders` 可以是空的 —— 空缺是岗位的正常状态，也正是 `Position` 当初从
       `Assignment` 里拆出来的第一个理由（见 `PositionQuerySet.vacant()`）。
    """

    position: object
    holders: list


@dataclass(frozen=True)
class RosterSection:
    """名册上的一段：一个 ministry（或者基金会级），它的分组，和它的三个数。

    ⭐ **三个数是从同一批已取出的行上数的，不是第二条 `Count` 查询。**
       D1.8 原文把它们放在一个独立的 `ministry_headcounts()` 里，建立在
       `PositionQuerySet.with_headcounts()` 之上。那个写法**没有错**，但在这一页
       上它买不到东西、还要付两笔：

       ① 多一次往返，而两次查询之间有人入职的话，页面上的表和它顶上的数就对不上
          —— 一个不报错、又极难复现的差别；
       ② `with_headcounts()` 走的是 `in_effect_on(prefix="assignments__")`，
          而下面 `staff_directory()` 走的是 `Assignment.objects.active()` ——
          **同一个 `core.querysets.in_effect_on`**，所以两者本来就是同一批行。
          数已经在手上的那一批，比再问一次数据库更不可能出错。

       ⚠️ `with_headcounts()` **一个字没动**，它仍然是 SQL 侧要可排序、可分页的
          计数时该用的东西（admin 的 headcount 列在用它，`understaffed()`——
          D1.6，还没做——也会用它）。这里不用它，不是替代它。

    ⚠️ `posts` 只数 `is_active=True` 的岗位，`holders` 只数在效期内的任职 ——
       也就是**屏幕上画出来的那些**。一个统计数字如果和它底下那张表算的不是同一
       批行，那它迟早会被人拿去和别处对，而对不上。
    """

    ministry: object
    groups: list
    posts: int
    holders: int
    serving: int
    #: ⚠️ **这里曾经有一个 `awaiting`，2026-09-17 删了 —— 它一个读者都没有。**
    #:    它的 docstring 写着「索引页上那颗红点读它」，而索引页读的是
    #:    `MinistryCard.awaiting`，另一个 dataclass。于是每一次
    #:    `/staff/<pk>/` 都白跑一次 `needs_foundation_review` 查询去填一个
    #:    没人看的数 —— 而那一页**另外**已经为那条横幅跑了
    #:    `positions_awaiting_review()`，在同一批行上。
    #:    ⚠️ 真要再用到「这个 ministry 还有几个等着核验」，视图手上就有那个
    #:       列表（`len(awaiting)`），不必再查一次。


#: 名册的分组，**顺序就是画出来的顺序**，而每一组的判据写在 `_roster_group()` 里。
ROSTER_GROUPS = ("Vacant", "Leaders", "Board", "Staff — paid", "Staff — unpaid")


def _roster_group(position, holders):
    """这个岗位画在哪一组。**五组互斥且穷尽，一个岗位只属于一组。**

    🔴 **「空缺」必须排在最前面判，而且是排他的。** `PositionQuerySet` 自己立过
       这条不变量（vacant / occupied / retired 三态划分整张表，「never two of
       them and never none」），而它是从一个真实的 bug 里立出来的：把「空缺」
       写成一个附加标记的话，一个没人的组长岗位会**同时**出现在 Leaders 和
       Vacant 两组里，于是同一行列两遍、两个数对不上。

    ⚠️ 判据顺序是承重的，不是随手排的：
       ① 没人 → 空缺（上面那条）；
       ② 组长 → Leaders（`is_leader` 的 help_text 写着「Read by code when
          grouping the chart」—— 这里就是那个 code）；
       ③ 理事 → Board（`kind` 那一轴，和拿不拿钱无关）；
       ④⑤ 剩下的按 `Position.PAID_ARRANGEMENTS` 分两组。

    ⚠️ 第 ④⑤ 步用 `PAID_ARRANGEMENTS` 而不是 `== PAID`：`stipend` 归在拿钱那一
       档，是 `Compensation` 自己那段注释定的政策，别在这里重判一遍。
    """
    if not holders:
        return "Vacant"
    if position.is_leader:
        return "Leaders"
    if position.kind == Position.Kind.BOARD:
        return "Board"
    if position.compensation in Position.PAID_ARRANGEMENTS:
        return "Staff — paid"
    return "Staff — unpaid"


def staff_directory(positions, *, on=None):
    """这些岗位上此刻在任的每一段任职，平铺一层。

    `positions` 是**已经按权限收窄过的** Position queryset（见本节开头）。

    ⚠️ `active(on)` 而不是 `serving(on)`：名册回答的是「这个团队有谁」，而休假的
       人仍然是这个团队的人。`AssignmentQuerySet.serving()` 自己的注释写着同一
       句话（「The roster of who belongs to a team is active(), not this」）——
       这里就是它说的那张名册。

    ⚠️ `position__is_active` **不在这里过滤**：要不要列已撤销的岗位是调用方的
       问题（`ministry_roster()` 过滤，岗位详情页不过滤 —— 一个撤销掉的岗位仍然
       要能打开看它的历史）。在这里一刀切会让详情页无声地少一半内容。
    """
    return (
        Assignment.objects.active(on=on)
        .filter(position__in=positions)
        .select_related("contact", "position", "position__ministry", "employment_type")
        # ⚠️ 排序和 `Position.Meta.ordering` 一致（ministry → 组长在前 → 名字），
        #    再按人名。两处不一致的表现是名册和岗位详情页上人的先后不一样。
        .order_by("position__ministry__name", "-position__is_leader",
                  "position__name", "contact__legal_last_name")
    )


@dataclass(frozen=True)
class MinistryCard:
    """名册索引页上的一张卡：一个 ministry，和它的四个数。

    ⭐ **索引页不取任何一行岗位**（2026-09-15，用户定的版式：先选 ministry，
       点进去才看那一个的岗位和任职）。所以这里是四个聚合数，不是
       `RosterSection` 那种带着行的东西 —— 一个五十人的基金会也许只有两三个
       ministry，但取全部岗位再在 Python 里数，是一个会随规模安静变慢的写法。

    ⚠️ `ministry` 为 `None` 的那一张是**基金会级岗位**（`Position.ministry` 可空），
       不是缺数据。它只会出现在 foundation tier 的页面上 —— ministry admin 的
       queryset 里根本没有那些行。
    """

    ministry: object
    posts: int
    holders: int
    serving: int
    awaiting: int


def roster_index(positions, *, on=None):
    """名册索引页上的那一排卡片 —— **两次聚合查询，不取任何一行岗位**。

    ⚠️ 只数 `is_active=True` 的岗位和在效期内的任职 —— 也就是点进去之后**画得出来
       的那些**。一个统计数字如果和它点进去看到的表算的不是同一批行，那它迟早会
       被人拿去和那张表对，而对不上。
       ⚠️ 例外是 `roster_index()` 那颗红点（`MinistryCard.awaiting`），
          理由在 `positions_awaiting_review()` 上。

    ⚠️ `serving` 和 `holders` 的区别是 `AssignmentQuerySet.serving()` 立的：休假和
       停职的人仍然**占着**岗位，但不在值班名单上。这里只是同一条线画在 SQL 那
       一侧。
    """
    held = in_effect_on(on, prefix="assignments__")
    rows = (
        positions.filter(is_active=True)
        .values("ministry_id", "ministry__name")
        .annotate(
            posts=models.Count("id", distinct=True),
            holders=models.Count("assignments", filter=held, distinct=True),
            serving=models.Count(
                "assignments",
                filter=held & models.Q(assignments__status=Assignment.Status.ACTIVE),
                distinct=True),
        )
    )
    awaiting = dict(
        positions.filter(needs_foundation_review=True)
        .values_list("ministry_id")
        .annotate(n=models.Count("id"))
    )
    # ⚠️ 一次取回 Ministry 对象，而不是在循环里逐个取 —— 卡片上要画名字，
    #    而 `.values()` 只给得出 id 和名字，给不出一个能 `{% url %}` 的对象。
    # ⚠️ 名单要**两批都收**：只有待确认、一个在办岗位都没有的 ministry 不在
    #    `rows` 里，而它下面也要出一张卡（见那一段 🔴）。少了它，那张卡的
    #    `ministry` 是 `None` —— 而 `None` 在这一页上的意思是「基金会级岗位」，
    #    于是它会伪装成另一种东西，还排到最后去。
    ministries = {m.pk: m for m in Ministry.objects.filter(
        id__in=[r["ministry_id"] for r in rows if r["ministry_id"]]
               + [mid for mid in awaiting if mid])}

    cards = [
        MinistryCard(
            ministry=ministries.get(row["ministry_id"]),
            posts=row["posts"], holders=row["holders"], serving=row["serving"],
            awaiting=awaiting.get(row["ministry_id"], 0),
        )
        for row in rows
    ]
    # 🔴 **只有待确认、一个在办岗位都没有的 ministry 也要有一张卡**
    #    （2026-09-16 修）。上面那批卡从 `is_active=True` 来，而 `awaiting` 数的
    #    是全部（**有意的** —— `positions_awaiting_review()` 写着理由：
    #    「一个建错了又被撤销的岗位仍然该从待办里出现一次，否则『撤销』会变成
    #    一条绕过确认的路」）。
    #
    #    两者岔开时的后果不是数字难看，是**够不着**：待确认的那份名单画在
    #    `staff_ministry.html`（单个 ministry 那一页），而去那一页的唯一入口就是
    #    这里的卡片。于是菜单上那颗红点说「1 waiting」，点进去这一页一张卡片都
    #    没有，那条岗位在界面上**没有任何一条路到得了**。
    #
    #    ⚠️ **不是**把 `awaiting` 改成只数在办的 —— 那会打掉上面引的那条理由。
    listed = {row["ministry_id"] for row in rows}
    for ministry_id, count in awaiting.items():
        if ministry_id not in listed:
            cards.append(MinistryCard(
                # ⚠️ 这几个 0 是诚实的：这个 ministry 确实一个在办的岗位都没有。
                #    卡片上那颗红点才是它此刻的全部内容。
                ministry=ministries.get(ministry_id),
                posts=0, holders=0, serving=0, awaiting=count,
            ))
    # ⚠️ 排序按名字，基金会级（`ministry` 为 None）排最后 —— 它不属于任何一个
    #    ministry，夹在字母序中间读起来像一个叫不出名字的部门。
    cards.sort(key=lambda card: (card.ministry is None,
                                 card.ministry.name if card.ministry else ""))
    return cards


def ministry_roster(positions, *, on=None):
    """名册，按 ministry 分段、每段按 `ROSTER_GROUPS` 分组。

    回 `[(ministry_or_None, [(组名, [RosterRow, …]), …]), …]`。

    ⚠️ **分组是从 `staff_directory()` 的结果上分的，不是另写一个 filter。**
       D1.8 点名了这一条：两条 filter 迟早在「离职当天算不算」上分家，而分家之后
       名册页和别处各说一个数，两边都不报错。

    ⚠️ 只列 `is_active=True` 的岗位 —— 撤销掉的岗位不是空缺（`vacant()` 自己的
       注释：「an abolished post is not a vacancy, and mixing the two turns the
       hiring list into fiction」）。要看它得从岗位详情页进去。

    ⚠️ `ministry` 为 `None` 的那一段是**基金会级岗位**，不是缺数据 —— 页面上要
       有自己的标题。这一段只有 foundation tier 看得到，因为 ministry admin 的
       queryset 里根本没有它们（收窄在 `org.views._scoped_positions()`）。

    ⚠️ 空组不画：一个 ministry 没有理事，就不该出现一个空的 Board 标题。
    """
    listed = positions.filter(is_active=True).select_related("ministry")
    holders_by_position = defaultdict(list)
    for assignment in staff_directory(listed, on=on):
        holders_by_position[assignment.position_id].append(assignment)

    by_ministry = defaultdict(lambda: defaultdict(list))
    ministries = {}
    for position in listed:
        holders = holders_by_position[position.pk]
        ministries[position.ministry_id] = position.ministry
        by_ministry[position.ministry_id][_roster_group(position, holders)].append(
            RosterRow(position=position, holders=holders))

    sections = []
    # ⚠️ 排序按名字，`None`（基金会级）排最后 —— 它不属于任何一个 ministry，
    #    夹在字母序中间读起来像一个叫不出名字的部门。
    for ministry_id, groups in sorted(
            by_ministry.items(),
            key=lambda pair: (ministries[pair[0]] is None,
                              ministries[pair[0]].name if ministries[pair[0]] else "")):
        rows = [row for group in ROSTER_GROUPS for row in groups[group]]
        holders = [holder for row in rows for holder in row.holders]
        sections.append(RosterSection(
            ministry=ministries[ministry_id],
            groups=[(group, groups[group]) for group in ROSTER_GROUPS if groups[group]],
            posts=len(rows),
            holders=len(holders),
            # ⚠️ `serving` 是「今天真的上得了岗的人」——休假和停职的人仍然**占着**
            #    岗位（所以在 holders 里），但不在值班名单上。这两个数的区别是
            #    `AssignmentQuerySet.serving()` 立的，这里只是同一条线画在了
            #    Python 这一侧：`active()` 已经由 staff_directory 过掉了，
            #    剩下的那一半条件就是 status。
            serving=sum(1 for holder in holders
                        if holder.status == Assignment.Status.ACTIVE),
        ))
    return sections


def positions_awaiting_review_count():
    """全基金会还等着核验的岗位有几个 —— 站内菜单那颗计数徽章读它。

    ⚠️ 不收 queryset，因为**这个问题只有 foundation tier 会问**，而他看得见全部
       （`org.views._scoped_positions()` 给他的是 `all()`）。调用方判权限，
       这里只数 —— 同 `ministry_admins()` 的分工。

    ⚠️ 一次 `COUNT`，而菜单是**每个页面**都画的（连同每一个 HTMX 片段）。
       这是这一格进菜单要付的全部代价，写在这里免得下一个人以为它是免费的。
    """
    return Position.objects.filter(needs_foundation_review=True).count()


def positions_awaiting_review(positions):
    """还在等 foundation tier 确认薪酬档和汇报线的岗位。

    ⚠️ 不带 `on` —— 「有没有人确认过」和日期无关，它不是一段有起止的东西。

    ⚠️ 含 `is_active=False` 的：一个建错了又被撤销的岗位仍然该从待办里出现一次，
       否则「撤销」会变成一条绕过确认的路。
    """
    return (
        positions.filter(needs_foundation_review=True)
        .select_related("ministry")
        .order_by("ministry__name", "name")
    )


def post_still_held(position, *, on=None):
    """此刻这个岗位上还有没有人。**「能不能撤销它」唯一的判据。**

    ⚠️ `active(on)` 而不是 `serving(on)`：休假的人仍然占着这个岗位 ——
       同 `_has_a_holder()` 那一处（「somebody on leave still holds the post」）。
    """
    return Assignment.objects.active(on=on).filter(position=position).exists()


def refuse_retiring_a_held_post(position, *, on=None):
    """撤销一个还有人在任的岗位 —— 拦住，并说清先做哪一步。

    🔴 **为什么这条规则不是一条数据库约束**（D9 说规则能进约束就进约束）：
       它跨两张表 —— 「`Position.is_active` 翻成 False」要去看 `Assignment` 有没有
       在效期内的行，而 CheckConstraint 看不见别的表。这和
       `Participation` 不设 `event` 列时记的是同一个角落
       （「a cross-table condition no CheckConstraint can see」）。

    ⚠️ 于是按 D14 那条「约束和 clean() 是一对」的替代形态办：**强制在这里**
       （`update_position()` 调它），**哪一格变红在表单里**
       （`org.forms.PositionForm.clean()` 调同一个）。两处一个函数，不是两份判断。

    ⚠️ 不拦住会怎样，说清楚一点：`ministry_roster()` 只列 `is_active=True` 的岗位，
       所以那几个人会从名册上**整个消失**；而他们的 `Assignment` 还在生效，
       `org.audience.on_the_books_q()` 照旧算他们在编 —— 于是名册说他们不在、
       受众判断说他们在，两个页面对同一批人说两件事，而两边都不报错。
    """
    if post_still_held(position, on=on):
        raise ValidationError({"is_active": (
            "Somebody still holds this post. End their tenure first, then retire it — "
            "otherwise they vanish from the roster while still counting as staff.")})


def _verified_fields_changed(position):
    """`VERIFIED_FIELDS` 里有没有哪一格和库里存着的不一样。

    ⚠️ 逐格比**外键的 id**（`reports_to_id`），不比对象 —— 比对象会为了取那一行
       多发一次查询，而两个不同实例的 `==` 走的也正是 pk。

    ⚠️ 还没有 pk 的行不问：它整个就是一次新的声明，由 `create_position()` 直接
       落标记。
    """
    if not position.pk:
        return False
    fields = [
        f"{name}_id" if position._meta.get_field(name).is_relation else name
        for name in Position.VERIFIED_FIELDS
    ]
    stored = Position.objects.filter(pk=position.pk).values(*fields).first()
    if stored is None:
        return False
    return any(stored[name] != getattr(position, name) for name in fields)


def update_position(position, *, by_foundation, **save_kwargs):
    """改一个岗位。**撤销那一下在这里过闸，核验状态也在这里翻。**

    ⚠️ 表单已经拦过撤销那一下了，这里再拦一遍不是重复 ——
       `can_publish_notice()` 那段注释记着这个教训的原话：「检查不是门」。
       表单挡的是人，这里挡的是别的写入路径（脚本、以后的批量入口）。

    🔴 **核验状态在这里翻，两个方向**（2026-09-15，用户定的流程）：

       · foundation tier 保存 → 核验通过。**「改完就算核验过」是用户定的**：
         他打开一个待核验的岗位、把填错的薪酬档改对、保存 —— 那一下既是修正也是
         核验，再让他点第二颗「Verified」只会制造「已经改对了却还赖在待办里」。
         ⚠️ 代价如实说：数据上「我只是看了一眼」和「我改了一格」长得一样。
         能分开它们的是 simple-history —— 改了哪一格那里查得到。

       · ministry admin 改了 `VERIFIED_FIELDS` 里任何一格 → **重新待核验**。
         不这么做的话核验是一次性的：核完之后那几格再也没人看，而改一格比建一个
         新岗位容易得多。
         ⚠️ 改名字 / 说明 / `is_leader` 不触发 —— 一张被无关改动塞满的待办列表
            正是让人开始无视它的原因（同 deferred.md 里那条「下周班表已生成」的
            周期通知被否掉的理由）。

    ⚠️ `save_kwargs` 若带 `update_fields`，调用方要自己把
       `needs_foundation_review` 列进去 —— 今天没有这样的调用方，写下来是因为
       那种漏法**不报错**，只是标记没跟着翻。
    """
    if not position.is_active:
        refuse_retiring_a_held_post(position)
    if by_foundation:
        position.needs_foundation_review = False
    elif _verified_fields_changed(position):
        position.needs_foundation_review = True
    position.save(**save_kwargs)
    return position


def create_position(position, *, by_foundation):
    """建一个岗位。`by_foundation` 决定它要不要等人确认。

    ⚠️ 收一个**还没存的实例**（ModelForm 给的），而不是一堆关键字：岗位有七八个
       可填的列，抄成关键字列表的下一次改动一定会漏掉一个，而漏掉的表现是那一列
       无声地回到默认值。

    ⚠️ `by_foundation` 是**调用方已经算出来的权限答案**，传进来而不是在这里再问
       一次 —— 同 `core.context_processors.manage_list_name(user, foundation=…)`
       和 `events.services.default_served_as(on_the_books=…)` 的口子。
       这个模块不判权限（`org/permissions.py` 才判），它只按答案落值。

    ⚠️ 标记只在**建**的时候落，改的时候一个字不动：ministry admin 改得动的那几列
       （名字、说明、是不是组长）都不是 foundation tier 要确认的那两列，
       改一次名字就把一个已经确认过的岗位打回待办，是在制造没人要做的待办。
    """
    position.needs_foundation_review = not by_foundation
    # ⚠️ `code` 一个字不碰 —— 它默认是空的，而空是它的正常状态
    #    （见 `Position.code` 那一段）。要锚点的人在 Django admin 里设。
    position.save()
    return position


def confirm_position(position):
    """foundation tier 核验过这个岗位的编制条件了 —— 它们是对的，一个字不用改。

    ⚠️ 和「改完保存」是同一件事的两种走法（见 `update_position()`）：那一条是
       「改对了，顺带就算核验」，这一条是「本来就对，我只是签个字」。
       两条都要有 —— 只留前者的话，一个填得完全正确的岗位没有任何办法离开待办。

    ⚠️ 只翻这一格，不碰别的列。`updated_at` 要跟着写，否则 `auto_now` 在
       `update_fields` 这条路上不生效（同 `revoke_ministry_role`）。
    """
    position.needs_foundation_review = False
    position.save(update_fields=["needs_foundation_review", "updated_at"])
    return position


def refuse_a_second_live_tenure(*, contact, position, start_date=None, end_date=None):
    """同一个人在同一个岗位上已经有一段时间压着了 —— 拦住，给一句人话。

    🔴 **规则本身在数据库上**（`assignment_no_overlapping_tenure`，D51）。
       这个函数**不是**那条约束的第二份实现，它是那条约束在**一条特定路径上
       够不到的地方**补的一句话，而那条路径是实测出来的：

       `ExclusionConstraint.validate()` 开头有一句
       `if exclude and self._expression_refs_exclude(...): return`。
       而 `AssignmentForm` 是 ModelForm，字段只有
       `["contact", "employment_type", "start_date"]` ——
       `_get_validation_exclusions()` 因此把 `end_date` 放进 `exclude`
       （跑出来确认过），而约束的表达式 `daterange(start_date, end_date)`
       引用了它，于是**整条约束的模型层校验被直接跳过**。
       没有这个函数，那条路上一次重叠就是一个 `IntegrityError`（500），
       而不是表单上的一句话。

       ⚠️ 两张授权表没有这个问题：它们是 plain `forms.Form`，服务层
          `full_clean()` 不传 `exclude`，约束自己就落到 `contact` 那一格上。
          `AssignmentInline` 也没有 —— 它的 `fields` 含 `end_date`。
          **只有站点侧那张任职表单中招。**

    ⚠️ **问的是区间重叠，不是「今天两条都活着」。** 旧版查的是
       `active(on=今天)`，于是**历史上的重叠一条都拦不住** —— 补录一段 2020 年
       的任职压在另一段 2020 年的任职上，两条都不在今天生效，它一声不吭，
       而 `with_headcounts()` 在回溯报表里把这个人数成两个。
       现在复用 `DateRange`，和约束问的是同一句话。

    ⚠️ 旧版的 docstring 写着「exclusion constraint 做得了，而这个仓库没有用过
       那个东西，为一张几十行的表引入它不划算」。那条成本论证 D51 之后不成立 ——
       东西已经在库里了。

    ⚠️ 收**两个具体的对象**，不收一个 Assignment 实例，而这是踩出来的：
       `ModelForm.clean()` 跑在 `_post_clean()` **之前**，那一刻 instance 上的
       `contact_id` 还是空的，而 `Assignment.contact` 是非空外键 —— 读它抛的是
       `RelatedObjectDoesNotExist`，不是 None。收对象就没有这个生命周期可踩。

    ⚠️ 这一条**不和 `Position` 那句「允许交接期重叠」冲突**：那句话说的是
       **两个人**在同一个岗位上短暂并存（`Position` 的 docstring：
       「it would block co-holders and handover overlaps」），这里拦的是
       **同一个人**在同一个岗位上并存 —— 那不是交接，那是把一个人数成两个。
    """
    if contact is None or position is None:
        return
    # 表单留空时的那一段，和 `assign()` 存下去的会是同一段（那里也填今天）。
    span = (start_date or local_today(), end_date)
    clash = (Assignment.objects
             .annotate(span=DateRange())
             .filter(contact=contact, position=position, span__overlap=span))
    if clash.exists():
        raise ValidationError({"contact": (
            "They already hold this post over part of that period. End that "
            "tenure first, or pick somebody else — two live tenures on one "
            "post count them twice.")})


def assign(assignment):
    """把人放进一个岗位上。

    ⚠️ 写入路径在服务层收口（D18 的落点规矩，而 `HoursWriteGuardTests` /
       `ServedAsWriteGuardTests` 都是为同一件事立的）。视图直接 `form.save()`
       的话，下一个要在入职时做点什么的人（发通知、以后清未来班次）会把那段
       逻辑写进视图。

    ⚠️ 这里再拦一次重复，同 `update_position()`：表单挡的是人，这里挡的是别的
       写入路径 ——「检查不是门」，`can_publish_notice()` 那段注释记着这个教训。
    """
    # ⚠️ 表单上留空时填**今天**，同两个 `grant_*_admin()`：`start_date` 自 D51
    #    起不可为空，而 `blank=True` 让表单交上来的是一个 `None`。
    if assignment.start_date is None:
        assignment.start_date = local_today()
    # ⚠️ 只在**建**的时候问：改一段已有的任职（比如翻成休假）当然会撞上它自己。
    if assignment.pk is None:
        refuse_a_second_live_tenure(
            contact=assignment.contact, position=assignment.position,
            start_date=assignment.start_date, end_date=assignment.end_date)
    assignment.save()
    return assignment


def end_assignment(assignment, *, last_day=None):
    """结束一段任职：**记结束日期，永远不删行。**

    `last_day` 是他**最后一个在岗的日子**（默认今天），而存进去的是它的**次日** ——
    `end_date` 右开，是第一个不算数的日子（D51）。「做到 15 号」存 16 号。

    ⭐ **参数叫 `last_day` 而不是 `on`，这是这次改动的一半。** HR 语境里的「结束
       日期」永远是最后一天：Workday 的 termination effective date 就是 last day
       of employment，失业金和 COBRA 也以那一天为锚。调用方手上有的是那个日期，
       所以签名收那个日期，`+1` 只发生在这一行 —— 而不是让每个调用方自己记得加。

    ⚠️ **和撤销授权的区别只在这里，不在读法上。** `revoke_ministry_role()` 存的是
       「今天」，因为撤销那一刻起就不算了；这里存「最后一天的次日」，因为最后
       一天他还在岗。两者读的是同一条右开谓词。D51 之前，这个区别是靠**两套
       谓词**表达的，于是四种写法里有两种会走散。

    和 `revoke_ministry_role()` 一个字一个理由 —— 删掉的任职留不下「去年三月谁在
    这个岗位上」的答案，而 `Assignment` 带 simple-history 正是因为这个问题会被问。
    `Assignment.contact` / `.position` 两个外键都是 PROTECT，说的也是同一件事。

    ⚠️ **结束是 `end_date` 说的，不是 `status`。** `Assignment` 自己的 docstring
       写着「there is no is_active」「Ending is said by end_date and by nothing
       else」—— 顺手翻个状态是那句话预防的东西。

    ⚠️ 他**从次日起**不再算这个 ministry 的在编人员（`org.audience.on_the_books_q()`
       走 `in_effect_on()`），于是那一天起看不见勾了这个 ministry 的活动。
       🔴 这里原来写的是「**当场**不再算在编」，而那句话是**假的** —— 右闭的时候
          今天还在 `[start, end]` 里。写注释的人脑子里是右开、代码是右闭，
          没有任何测试钉这一格。D51 那一轮把它验出来了，钉子是
          `EndAssignmentTests.test_somebody_ended_today_is_still_on_the_books_for_the_rest_of_today`。
    """
    last_day = last_day or local_today()
    assignment.end_date = last_day + datetime.timedelta(days=1)
    assignment.save(update_fields=["end_date", "updated_at"])
    return assignment
