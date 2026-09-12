"""这一页要摆的每一样东西，装配在这里。D18：逻辑进 services，视图是薄壳。

⚠️ 搬进来的直接原因是 `ViewsAreThinGuardTests` —— 它拦 views.py 里的
   `local_now(` / `month_bounds(` / `Sum(`，而这一页两样都要。
   但那条守卫拦的不是这两个名字，是**会被抄成好几份的日期运算**：
   这一页有五处要问「现在」，各问各的话，问候语、月历和 upcoming() 有可能
   落在午夜两侧 —— 一年发生一次，且没有任何东西会报错。
   所以 `now` 在这里取一次，往下传。
"""

from core.timeutils import (
    local_date_of,
    local_hour_of,
    local_now,
    month_bounds,
    year_bounds,
)
from events.models import Event, EventRole, Participation
from notices.models import Notice
from org.models import Assignment
from org.permissions import ministry_ids_administered_by

from . import calendar as month

#: 每张卡最多几行。⚠️ 一个数，不是每张卡一个 —— 卡片的门槛写着「内容**天然**
#: 不超过四行；常常要截断的，那是一个页面不是一张卡」，而每张卡各有各的上限
#: 会让那句话变成「四行是这一张的口味」。
ROWS_PER_CARD = 4

#: 公告取三条而不是四条：它排在最上面，而最上面那张卡矮一点，整屏才不像一堵墙。
NOTICES_SHOWN = 3

#: 🔴 通栏那一块**最多三条**，而这个数不是从 `ROWS_PER_CARD` 抄来的，是版面逼出来的：
#: 那一块还要在末尾放一格出口，三条加一格正好排满一行。四条就会把出口挤到第二行
#: 独自占一格，后面一整条空白 —— 用户 2026-09-11 看到的就是那个样子，原话是
#: 「events I manage 自己一行，后面空白真的很难看」。
#:
#: ⚠️ 所以「一行」是这一块的**不变量**，不是一次样式微调：它横过整幅、又是整屏
#: 唯一要人动手的地方，一旦长到两行就开始和下面的卡片网格抢「这一屏在说什么」。
BAND_ITEMS = 3


#: 问候语的两条界线，本地时间的小时。⚠️ 两个数写在一起，因为它们是**一句话的
#: 两个逗号**：分开写就会出现「下午到 16 点、傍晚从 18 点开始」这种中间有个洞的版本。
MORNING_ENDS, AFTERNOON_ENDS = 12, 17


def _greeting(now):
    """Good morning / afternoon / evening —— 打开这一页的那一刻该说哪一句。

    ⚠️ 小时从 `local_hour_of()` 取，**不许直接问那个 aware 的 `now` 要**：
       它带的是 UTC，洛杉矶上午十点它给出来的是 17 —— 于是每个志愿者吃早饭时
       都会被祝一句晚上好，而没有任何东西会报错。同 D16 那个坑，只是这一次
       错的是钟点不是日子。守卫：core.tests.TimeSourceGuardTests。
       （这段注释不照抄那个错写法，否则守卫会红在注释上。）

    ⚠️ 英文，D23：浏览器渲染出来的每一个字都必须是英文。
    """
    hour = local_hour_of(now)
    if hour < MORNING_ENDS:
        return "Good morning"
    if hour < AFTERNOON_ENDS:
        return "Good afternoon"
    return "Good evening"


def _posts(contact):
    """他此刻持有的每一条任职。

    🔴 **存在的每一条，不是 `.first()`。** D32 的不变量是一个人可以同时多岗，
       而 `.first()` 在这张表上是 bug —— 它会在页面上把一个人的第二个身份
       悄悄抹掉，什么都不报。
    """
    if contact is None:
        return []
    rows = (
        Assignment.objects.active()
        .filter(contact=contact)
        .select_related("position__ministry")
        .order_by("position__name", "start_date")
    )
    # ⚠️ 按**岗位**去重，而这是防御不是修复：数据干净时 active() 本来就不会
    #    返回同一个岗位的两条。它挡的是重叠任职（同一人同一岗位两段同时生效），
    #    而那个数据库现在还拦不住 —— 走查那天页面上真的出现了四行一模一样的
    #    `Food Pantry lead · Food Pantry`。
    #
    # ⚠️ 去重本身也是这一行该有的语义，不只是补丁：它答的是「**你现在是什么
    #    身份**」，不是「你有几段任职记录」。同一个岗位说一次就够了。
    seen, posts = set(), []
    for row in rows:
        if row.position_id in seen:
            continue
        seen.add(row.position_id)
        posts.append(row)
    return posts


def _counted(rows):
    """三个列表加一个总数 —— 通栏那一块的标题要印「2 items」。"""
    rows["count"] = len(rows["short"]) + len(rows["unfinished"]) + len(rows["open"])
    return rows


def _open_to_me(contact, mine, now):
    """**对我开放、我还没报名**的活动 —— 这一页上唯一一条最贵的查询，只跑一次。

    🔴 **它有两个使用者，而它们之前各写了一遍同一条链**（2026-09-12 合并）：
       通栏那一块（志愿者那一形态）和 `Happening soon` 那张卡。两份只差一个
       `.filter(needs_people=True)` 和切片长度，然后第二份再 `exclude` 掉第一份。
       两个代价：

       · **贵。** `for_audience()` 是一个 annotation 加三条 `EXISTS` 子查询，
         `with_shortfall()` 再加一条带两个 `COUNT(DISTINCT)` 的 `EXISTS` ——
         整页最重的形状，而它跑了两遍。
       · **会分叉。** 可见性规则哪天加一个条件、只改到其中一份，另一份就会
         继续显示这个人不该看见的行 —— 而那一份正好是通栏，整屏最显眼的地方。

    ⚠️ 取 `BAND_ITEMS + ROWS_PER_CARD` 条，**刚好够两个使用者切**：
       `with_shortfall()` 自己就按 `-needs_people, start_time` 排（见
       events/models.py），所以还缺人的那些天然排在前面 —— 通栏从头拿走最多
       三条，剩下的给那张卡，结果和「查两次再 exclude」逐行相同。
    """
    return list(
        Event.objects.open_for_signup(now)
        .for_audience(contact)
        .exclude(pk__in=mine.values("event_role__event_id"))
        .with_shortfall()
        .select_related("ministry")[:BAND_ITEMS + ROWS_PER_CARD]
    )


def _needs_you(ministry_ids, now, open_to_me):
    """还等着人的那些事 —— 而「等着谁」有两种答案（D44）。

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
        # ⚠️ 不是管理员的人在同一块地方读到的是另一句话：**还缺人、他看得见、
        #    他还没报名**的活动。三个条件都借既有的口径，一个新的都不造 ——
        #    `open_for_signup()` 管时钟和状态、`for_audience()` 管他看不看得见、
        #    `with_shortfall()` 的 `needs_people` 就是报名页那个 short 徽章
        #    读的同一个 annotation。
        #
        # ⚠️ 它会和下面的 `Happening soon` 说同一件事，去重在 `dashboard_for()`
        #    里做（那张卡本来就在排除「我报过的」，同一个模式）。
        # ⚠️ `count` 是**服务层数的**，不是模板里现算的：通栏那一块的标题上要写
        #    「2 items」，而 Django 模板加不了两个数 —— 硬要写就会变成
        #    「short 有几条」和「一共有几条」两句话，而它们只在管理员身上相等。
        # ⚠️ 行是 `_open_to_me()` 已经取回来的，这里只挑 —— 挑的条件就是
        #    `needs_people`（报名页那个 short 徽章读的同一个 annotation）。
        return _counted({"short": [], "unfinished": [], "open": [
            event for event in open_to_me if event.needs_people
        ][:BAND_ITEMS]})
    short = list(
        EventRole.objects.understaffed()
        .filter(event__ministry_id__in=ministry_ids,
                event__status=Event.Status.OPEN,
                event__end_time__gt=now)
        .select_related("event", "role")
        .order_by("event__start_time")[:BAND_ITEMS]
    )
    # 🔴 **两组加起来** 不超过 ROWS_PER_CARD，不是各自不超过。
    #
    #    第一版是各取四条，于是这张卡最多能画八行 —— 而卡片的四条门槛
    #    （D42）第二条写的是「内容**天然**不超过四行；常常要截断的，
    #    那是一个页面不是一张卡」。走查那天它画了五行，是我自己定的规矩
    #    被自己破的第一处。
    #
    # ⚠️ 名额优先给「还缺人」那一组：它讲的是**还来得及**做点什么的事，
    #    而「结束了没收尾」是已经发生的。一张卡只放得下四行时，
    #    先说还救得回来的那几件。
    unfinished = list(
        Event.objects.filter(ministry_id__in=ministry_ids, end_time__lte=now)
        .exclude(status__in=[Event.Status.COMPLETED, Event.Status.CANCELLED,
                             Event.Status.DRAFT])
        .order_by("-end_time")[:max(0, BAND_ITEMS - len(short))]
    )
    # ⚠️ `open` 恒为空，而不是这个键不存在：一块地方两种形态，模板问的是
    #    「这三个列表里有什么」。少一个键就要模板去判断「我是哪一种人」，
    #    而那正是 D24 明令不许落在 x- 属性和模板里的东西。
    return _counted({"short": short, "unfinished": unfinished, "open": []})


def _this_month(mine, today):
    """他这个月有报名的那些行 —— 只给侧栏那个月历打点用。

    ⚠️ 半开区间由 `month_bounds` 给（D16），过滤的是活动的 `start_time`：
       月历上一个点的意思是「那天你要去一趟」，而那是开始的那一天。

    🔴 收的是**一个本地日期**，不是那个 aware 的瞬间（2026-09-12 修）。
       上一版收 `now` 然后自己取 `.year` / `.month` —— 那是 UTC 的年月，
       而同一个函数里月历的标题和格子用的是本地的。于是每个月**最后一晚**，
       格子画的是本地的当月、点标的是 UTC 下个月的活动，两边各自看都对。
       口径归一件事：日期在 `dashboard_for()` 里算一次，这里只收结果。
    """
    start, end = month_bounds(today.year, today.month)
    return list(
        mine.filter(event_role__event__start_time__gte=start,
                    event_role__event__start_time__lt=end)
        .select_related("event_role__event")
    )


def dashboard_for(user, now=None):
    """登录之后 `/` 下半页上的每一样东西，一个 dict。

    ⚠️ 这段话 2026-09-12 改过口：原文写的是「`/me/` 上的每一样东西」和
       「五张卡」。`/me/` 现在是一条 302（D44 第三节），而五张卡在 D44 里重排成了
       **一条通栏加三张卡**，工时那两个数搬进了问候那一行的右端。

    排序按紧急程度，顺序固定：**你必须知道的 → 你承诺了什么 → 别人在等你 →
    好消息 → 回顾**。不按人配置 —— 可配置要一张 per-user 的布局表，
    而块数就这么几个、且身份已经决定了谁看得见哪一块。
    """
    now = now or local_now()
    # 🔴 **日历上的每一个值都从这一行派生**（2026-09-12）。`now` 是 aware 的，
    #    直接问它要 `.date()` / `.year` / `.month` 拿到的是 UTC 的那一天 ——
    #    洛杉矶下午五点之后 UTC 已经翻页。上一版只把 `.date()` 那两处改对了，
    #    隔三行的 `now.year` / `now.month` 原样留着，于是同一个 dict 里两种口径。
    #    算一次、往下发，是这件事**唯一**不会再错第三次的形状。
    #    ⚠️ `now` 从此只用来和数据库里存着的瞬间比大小（那些也是 UTC）。
    #    守卫：core.tests.TimeSourceGuardTests。
    today = local_date_of(now)
    contact = getattr(user, "contact", None)
    ministry_ids = ministry_ids_administered_by(user)

    mine = Participation.objects.mine(contact)
    open_to_me = _open_to_me(contact, mine, now)
    needs_you = _needs_you(ministry_ids, now, open_to_me)
    # ⚠️ 累计和本年度**一次问出来**，理由和口径都在
    #    `ParticipationQuerySet.hours_given_and_within()` 里 —— 尤其是
    #    「两半各按自己的日期算」那一条，别在这里重述第二份。
    hours_total, hours_this_year = (
        mine.volunteering().hours_given_and_within(*year_bounds(today.year)))
    coming = list(
        mine.upcoming(now)
        .select_related("event_role__event__ministry", "event_role__role")[:ROWS_PER_CARD]
    )

    return {
        # ⚠️ 问候语和日期都从**同一个** `now` 来。各问各的话，这两行会在
        #    午夜两侧打架 —— 一年一次，且没有任何东西会报错。
        "greeting": _greeting(now),
        "today": today,
        "posts": _posts(contact),
        # ⚠️ N3 那个 partial 收的就是一个列表，两个挂载点同一份渲染。
        "notices": list(
            Notice.objects.showing(now).for_audience(contact)
            .select_related("ministry")[:NOTICES_SHOWN]
        ),
        "coming": coming,
        # ⚠️ 「我报过的」在 `_open_to_me()` 里就排除掉了 —— 否则这张卡和
        #    `Coming up` 说的是同一件事。
        # ⚠️ 这里再去掉通栏已经列出的那几场（D44），否则一个志愿者会在同一屏上
        #    把同一场活动读两遍。⚠️ 去重在**内存里**做，因为两块读的是同一份
        #    已经取回来的行 —— 从前这是第二次完整查询加一句 `exclude`。
        "happening_soon": [
            event for event in open_to_me
            if event.pk not in {row.pk for row in needs_you["open"]}
        ][:ROWS_PER_CARD],
        "hours_given": hours_total,
        "hours_this_year": hours_this_year,
        "needs_you": needs_you,
        "is_ministry_admin": bool(ministry_ids),
        # 侧栏。
        #
        # ⚠️ 月历的点子**不能**从上面那个 `coming` 里取，虽然那样省一次查询。
        #    `coming` 被 `[:ROWS_PER_CARD]` 截断了，于是月历最多只标得出四天，
        #    而少标的那几天**没有任何迹象** —— 它看起来就是一个正常的、
        #    那天没事的月历。第一版就是这么写的。
        #
        # ⚠️ 而且这两块问的本来就不是同一个问题：卡片问「接下来最近的四件事」，
        #    月历问「我这个月哪几天有事」—— 包括这个月已经过去的那几天。
        # 🔴 日子来自函数顶上那个 `today`，这里不再各算各的（2026-09-12）。
        #    那一行上面写着为什么；这里只记这个坑长什么样：`now` 是 aware 的，
        #    问它要日子拿到的是 UTC 那一天 —— 洛杉矶下午五点之后 UTC 已经翻页，
        #    于是侧栏月历把**明天**圈成今天；每个月最后一晚更狠，整片格子和标题
        #    一起跳到下个月，错七个小时，而没有任何东西报错。
        #
        # ⚠️ 是 D44 把日期印在问候语那一行、和月历并排摆着，才让它显形的 ——
        #    在此之前这一页上没有第二个地方说「今天几号」。
        #    ⚠️ 这段注释**不照抄那个错写法**，否则守卫会红在注释上 ——
        #       和 `TimeSourceGuardTests` 自己那条「本文件必须扫得了自己」
        #       是同一条规矩。
        "month_name": month.month_name(today),
        "weekday_initials": month.WEEKDAY_INITIALS,
        "weeks": month.month_grid(month.marked_days(_this_month(mine, today)),
                                  today),
    }
