"""这一页要摆的每一样东西，装配在这里。D18：逻辑进 services，视图是薄壳。

⚠️ 搬进来的直接原因是 `ViewsAreThinGuardTests` —— 它拦 views.py 里的
   `local_now(` / `month_bounds(` / `Sum(`，而这一页两样都要。
   但那条守卫拦的不是这两个名字，是**会被抄成好几份的日期运算**：
   这一页有五处要问「现在」，各问各的话，问候语、月历和 upcoming() 有可能
   落在午夜两侧 —— 一年发生一次，且没有任何东西会报错。
   所以 `now` 在这里取一次，往下传。
"""

from core.timeutils import local_now, month_bounds
from events.models import Event, EventRole, Participation
from notices.models import Notice
from org.models import Assignment
from org.permissions import in_foundation_tier, ministry_ids_administered_by

from . import calendar as month

#: 每张卡最多几行。⚠️ 一个数，不是每张卡一个 —— 卡片的门槛写着「内容**天然**
#: 不超过四行；常常要截断的，那是一个页面不是一张卡」，而每张卡各有各的上限
#: 会让那句话变成「四行是这一张的口味」。
ROWS_PER_CARD = 4

#: 公告取三条而不是四条：它排在最上面，而最上面那张卡矮一点，整屏才不像一堵墙。
NOTICES_SHOWN = 3


def _posts(contact):
    """他此刻持有的每一条任职。

    🔴 **存在的每一条，不是 `.first()`。** D32 的不变量是一个人可以同时多岗，
       而 `.first()` 在这张表上是 bug —— 它会在页面上把一个人的第二个身份
       悄悄抹掉，什么都不报。
    """
    if contact is None:
        return []
    return list(
        Assignment.objects.active()
        .filter(contact=contact)
        .select_related("position__ministry")
        .order_by("position__name")
    )


def _needs_you(ministry_ids, now):
    """我管的活动里，两类还等着我的事。

    ⚠️ 两类都用**已有的口径**，一个新的都不造：

    · `EventRole.objects.understaffed()` —— 已经在 events/models.py 里，
      报名页那个 short 徽章读的是同一个 annotation；
    · 「结束了但跟进没做完」直接借 `Event.Status.COMPLETED` 的既有语义 ——
      那个状态的注释写死了它就是「出勤记了、工时记了」。所以「过了时间、
      状态还不是 COMPLETED」本身就是这句话的反面，不需要第三个口径。

    ⚠️ 不合并成一个列表再排序：它们是两种不同的欠账（一个在未来、一个在过去），
       模板分两组画，而合在一起排完序之后没有任何东西说得出某一行属于哪一种。
    """
    if not ministry_ids:
        return {"short": [], "unfinished": []}
    short = (
        EventRole.objects.understaffed()
        .filter(event__ministry_id__in=ministry_ids,
                event__status=Event.Status.OPEN,
                event__end_time__gt=now)
        .select_related("event", "role")
        .order_by("event__start_time")[:ROWS_PER_CARD]
    )
    unfinished = (
        Event.objects.filter(ministry_id__in=ministry_ids, end_time__lte=now)
        .exclude(status__in=[Event.Status.COMPLETED, Event.Status.CANCELLED,
                             Event.Status.DRAFT])
        .order_by("-end_time")[:ROWS_PER_CARD]
    )
    return {"short": list(short), "unfinished": list(unfinished)}


def _this_month(mine, now):
    """他这个月有报名的那些行 —— 只给侧栏那个月历打点用。

    ⚠️ 半开区间由 `month_bounds` 给（D16），过滤的是活动的 `start_time`：
       月历上一个点的意思是「那天你要去一趟」，而那是开始的那一天。
    """
    start, end = month_bounds(now.year, now.month)
    return list(
        mine.filter(event_role__event__start_time__gte=start,
                    event_role__event__start_time__lt=end)
        .select_related("event_role__event")
    )


def dashboard_for(user, now=None):
    """`/me/` 上的每一样东西，一个 dict。

    五张卡按紧急程度排，顺序固定：**你必须知道的 → 你承诺了什么 →
    别人在等你 → 好消息 → 回顾**。不按人配置 —— 可配置要一张 per-user 的
    布局表，而卡片总共五张、且身份已经决定了谁看得见哪几张。
    """
    now = now or local_now()
    contact = getattr(user, "contact", None)
    ministry_ids = ministry_ids_administered_by(user)

    mine = Participation.objects.mine(contact)
    coming = list(
        mine.upcoming(now)
        .select_related("event_role__event__ministry", "event_role__role")[:ROWS_PER_CARD]
    )

    return {
        "greeting": month.greeting(now),
        "posts": _posts(contact),
        # ⚠️ N3 那个 partial 收的就是一个列表，两个挂载点同一份渲染。
        "notices": list(
            Notice.objects.showing(now).for_audience(contact)
            .select_related("ministry")[:NOTICES_SHOWN]
        ),
        "coming": coming,
        "hours_given": mine.volunteering().hours_given(),
        "needs_you": _needs_you(ministry_ids, now),
        "recent_signups": list(
            Participation.objects.filter(
                event_role__event__ministry_id__in=ministry_ids)
            .select_related("contact", "event_role__event", "event_role__role")
            .order_by("-created_at")[:ROWS_PER_CARD]
        ) if ministry_ids else [],
        "is_ministry_admin": bool(ministry_ids),
        "is_foundation": in_foundation_tier(user),
        # 侧栏。
        #
        # ⚠️ 月历的点子**不能**从上面那个 `coming` 里取，虽然那样省一次查询。
        #    `coming` 被 `[:ROWS_PER_CARD]` 截断了，于是月历最多只标得出四天，
        #    而少标的那几天**没有任何迹象** —— 它看起来就是一个正常的、
        #    那天没事的月历。第一版就是这么写的。
        #
        # ⚠️ 而且这两块问的本来就不是同一个问题：卡片问「接下来最近的四件事」，
        #    月历问「我这个月哪几天有事」—— 包括这个月已经过去的那几天。
        "month_name": month.month_name(now.date()),
        "weekday_initials": month.WEEKDAY_INITIALS,
        "weeks": month.month_grid(month.marked_days(_this_month(mine, now)),
                                  now.date()),
    }
