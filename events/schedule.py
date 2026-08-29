"""日程：把活动摆进一天的格子里（2026-08-18）。

Events 页右边那块面板画的东西。摆放全是算术 —— 落在哪一天、离顶上多少像素、
跟谁重叠、于是偏移几档、配哪一个颜色 —— 而算术写在模板里既测不了也读不懂，
所以它在这里，视图只负责取数据和定窗口。

⚠️ 除了面板的几何，这里还住着 `clock()` 和 `when_labels()`（2026-08-29）——
   后者是**管理列表**那一列的两行字，和面板没关系。它在这个模块里，是因为
   两处的时刻必须走同一个 `clock()`：同一场活动在管理列表和日程上写着不一样的
   时间，是那种两边各自看都正常、放在一起才露馅的错。
   真要收拾的话，该动的是把 `clock()` 提到一个更底层的模块去，而不是让
   `when_labels()` 自己再拼一份时刻格式。

🔴 **这个模块不问「现在是几点」。** 红线的位置由**浏览器**算（app.js），
   服务端只渲染一个进入页面那一刻的值 —— 因为服务端渲染出来的「现在」在页面
   开着的第二分钟就是错的，而一条画错位置的红线比没有红线更糟：它是这一页上
   唯一一个人会拿来对时间的东西。
   两处都存在是渐进增强（D24）：没有 JS 的人看到的是一条静止但正确的线。

⚠️ 一天的边界一律走 core.timeutils.day_start（D16）。日程是按「当地的哪一天」
   分列的，而数据库里存的是 UTC —— 下午 5 点之后的活动用 UTC 取日期会整个
   跳到第二列去，不报错，只是画错一天。
"""

import datetime
import zlib
from dataclasses import dataclass

from django.utils import timezone

from core.timeutils import day_start, local_date_of, local_now, local_today

# --- 几何 -------------------------------------------------------------------
#
# ⚠️ 这几个数**同时**写在 app.css 里（`--schedule-hour` 等）。两边必须一致，
#    而它们之间没有任何东西连着 —— 卡片的 top/height 是这里算的像素，格线是
#    那边画的。对不上的表现不是报错，是「卡片和时间刻度差半格」。
#    守卫：events.tests.ScheduleGeometryTests。
PX_PER_HOUR = 48
DAY_PX = 24 * PX_PER_HOUR          # 1152

# ⚠️ 半小时的活动是 24px —— 正好一行字。再短的（15 分钟、以及 end == start 的
#    那种，模型只约束 end >= start）会被压到看不见，所以给一个下限。
#    代价如实说：下限生效的卡片，它的**高度不再等于它的时长**，于是两张挨着的
#    短活动在画面上会比实际更挤 —— 重叠判定因此也用画出来的框，不用真实时间，
#    否则屏幕上明明压在一起的两张卡，代码认为它们不重叠。
MIN_CARD_PX = 22

# 颜色/重叠的「紧邻」阈值：两张卡的框上下相差不到这么多像素，就算挨着。
# ⚠️ 不是「上一张」而是「近到看得出是一对」—— 早上九点和晚上八点两张卡同色
#    没有任何人会读错，而挨着的两张同色会读成一张。
TOUCH_PX = 8

# 完全重叠时上面那张往右下偏。封顶三档：再多就偏出列宽了，第四张之后统统
# 停在第三档上（会盖住，但盖住的是一张已经被盖了两层的卡）。
MAX_DEPTH = 3

# 浅色盘有几格。色值本身在 app.css（`--schedule-fill-0..23`），因为那是设计，
# 不是逻辑 —— 这里只需要知道有几格才好取模。
#
# ⚠️ 这个数和那边的格数必须一样。多了就有活动配到一个不存在的变量上，
#    表现是一张**透明**的卡（不报错，CSS 里未定义的变量就是这样）；少了则是
#    盘的尾巴永远用不上。守卫：events.tests.ScheduleColourTests。
PALETTE = 24

# 🔴 **盘里哪些格子看起来是同一个颜色。** 回避撞色比的是这个，不是格子的下标。
#
#    24 格里有四个粉、三个黄。按下标算它们是不同的颜色，屏幕上是同一个 ——
#    两张挨着的卡各配一个，读起来仍然像一张。第一版就是这么错的，而它「通过」了
#    不撞色的测试。
#
# 🔴 **这张表是量出来的，不是按色相名字排的**（2026-08-19 重排）。
#    上一版是凭眼睛按「蓝/粉/绿…」归的，于是薄荷绿（3）被归进绿族、而它在屏幕上
#    和青族那几格几乎一样 —— 两者不同族，也就**允许相邻**，正是这张表要防的事。
#    现在的分法让「跨族的最近一对」从 0.031 拉到 0.048（HLS 空间，色相加权 ×3）：
#    任何两张真的会挨在一起的卡，都明显分得开。
#
#    ⚠️ 蓝和靛并成了一族，因为 12 号那格同时贴着两边 —— 一格横跨两族时，
#       两族就得合并，否则它跟谁相邻都像撞色。
#
# ⚠️ 这张表和 app.css 里那 24 行色值是**一对**：改一个色值就要重新量它还属不属于
#    原来那一族。守卫钉得住「每一格都归了族、没有重复」，钉不住「归对了族」——
#    后者要用 events/schedule.py 旁边那段度量重跑一遍。
COLOUR_FAMILIES = (
    (0, 9, 12, 15, 19),   # 蓝 / 靛
    (1, 16, 18, 22),      # 粉
    (2, 4, 8, 21),        # 杏 / 奶油
    (5, 13),              # 黄绿
    (6, 10, 20),          # 黄
    (7, 14),              # 紫
    (3, 11, 17, 23),      # 青 / 薄荷
)
_FAMILY_OF = {slot: family
              for family, slots in enumerate(COLOUR_FAMILIES)
              for slot in slots}

# 服务端永远画四列；每一档屏幕实际看得见几列由 CSS 决定（4 / 2 / 3）。
#
# 🔴 **箭头一次翻几天 = 那一档看得见几列**，所以三档各有一对自己的箭头，
#    URL 由服务端算好，CSS 只显示其中一对。为什么不让 JS 算：
#    「翻四天」是日期运算，而日期运算不许出现在 Alpine/前端（phase-c.md 三）——
#    真正的理由不是洁癖，是 JS 算出来的那一天没有任何测试看得见，
#    而算错一天的表现是「箭头按下去跳过了一天」，没有人会当成 bug 报上来。
WINDOW_DAYS = 4
VISIBLE_DAYS = (4, 2, 3)           # ≥80rem / 64–80rem / <64rem


@dataclass(frozen=True)
class Card:
    """一张日程卡：一个活动落在某一天里的那一段。

    ⚠️ 是「活动 × 天」，不是「活动」。跨夜的活动在两列里各有一张卡，各自裁到
       自己那一天的边界 —— 不裁的话第二天那张的 top 是负的，画到表头上面去。
    """

    event: object
    top: float                     # 距离 0:00 的像素
    height: float
    depth: int                     # 偏移档，0 = 不偏
    colour: int | None             # 0..PALETTE-1；None = 已取消，不配色
    label: str                     # "3pm – 5pm"，活动**真实**的起止，不是裁过的
    continues_before: bool         # 这一段是从前一天延过来的
    continues_after: bool
    start_ms: int                  # 真实起止的 epoch 毫秒，给 app.js 算红线用
    end_ms: int
    # 已经结束了。卡片画成半透明（2026-08-18 加）——「今天还剩什么」是这一列
    # 最常被问的问题，而一张过去的卡和一张待办的卡长得一样时，那个问题得靠
    # 拿手指比着红线一张张数。
    # ⚠️ 它会过时：页面开着的时候活动会结束。app.js 每 30 秒重判一次。
    is_past: bool

    @property
    def is_cancelled(self):
        return self.colour is None


@dataclass(frozen=True)
class Column:
    """一天。"""

    day: datetime.date
    cards: list
    is_today: bool
    # 这一天零点的 epoch 毫秒。app.js 拿它算红线的位置，于是**不必知道浏览器
    # 在哪个时区** —— 差值是绝对的。浏览器自己取本地午夜的话，一个在纽约的
    # 志愿者看到的红线会差三小时，而基金会的时区是洛杉矶（D16）。
    day_start_ms: int
    # 进入页面那一刻红线的位置（像素），不是今天就是 None。
    # ⚠️ 它会过时，app.js 每 30 秒重算一次覆盖掉。
    now_top: float | None
    # 红线正压着的那场活动还剩多久，压不到就是 None。
    # ⚠️ 挂在**线**上，不是挂在卡上：线同时压着三张卡时，一卡一个倒计时就是
    #    三个互相矛盾的数字并排。压着好几张时写**最快结束**的那一场 ——
    #    那是唯一一个「马上要发生变化」的数。
    now_left: str | None


def clock(moment):
    """"3pm" / "10:15am"。整点不写 ":00" —— 参考图就是这么写的，也短。"""
    hour = moment.hour % 12 or 12
    suffix = "am" if moment.hour < 12 else "pm"
    return f"{hour}{suffix}" if moment.minute == 0 else f"{hour}:{moment.minute:02d}{suffix}"


def when_labels(event):
    """管理列表 When 那一格的两行字：开始一行，结束一行。

        同一天  ("Aug 30, 2026, 9am",  "Aug 30, 2026, 11am")
        跨午夜  ("Aug 30, 2026, 10pm", "Aug 31, 2026, 1am")

    返回的是两枚 `WhenLine`（`.text` / `.weekday`），不是两个字符串 ——
    星期几不写在行上，理由见 `WhenLine`。

    🔴 **两行的格式完全一样，不分同一天还是跨午夜**（2026-08-29 拍板）。

       上一版分两档：同一天写「日期一行 + `9am – 11am` 一行」，只有跨午夜才在
       结束那行补上日期和一个箭头。分档省下来的那点宽度不值得 —— 它让**同一列里
       两行的含义随行变化**：这一行的第二行是「时段」，下一行的第二行是「结束时刻」。
       而人是竖着扫这一列的。

       统一之后每一行都是「开始 / 结束」，于是跨午夜不再需要任何特殊记号：
       结束那行自己写着 `Aug 31`。上一版那条「只写 1am 会被读成同一天凌晨」的
       隐患也就不存在了 —— 不是靠一个箭头挡住，是靠格式本身没有那个歧义。

    ⚠️ 时刻走上面那个 `clock()`，不自己拼 —— 同一场活动在管理列表和日程上写着
       不一样的时间，是那种两边各自看都正常、放在一起才露馅的错。

    ⚠️ 年份两行都写。这一列是「这场活动什么时候」的唯一出处（表格里没有别的
       日期列了），而管理侧看得到往年已结束的活动 —— 一个不带年份的 `Aug 30`
       在筛「去年八月」时读起来和今年的一模一样。
    """
    return (_when_line(event.start_time), _when_line(event.end_time))


@dataclass(frozen=True)
class WhenLine:
    """When 那一格里的一行。

    ⚠️ 是两个字段而不是一个字符串，因为**星期几不写在行上**：那一行已经是
       「Aug 30, 2026, 9am」，再塞进「Sun, 」会让这一列宽出约 2.5rem，而这一整批
       要修的正是「每个 event 都太长了」。星期几进了一个悬停才出现的小窗
       （app.js）和一段读屏专用的文字（模板里的 `sr-only`）。

    ⚠️ `text` 和 `weekday` 必须来自**同一次** `localtime()`（见下面的
       `_when_line`）。分两次转的话，跨时区 DST 切换那一刻理论上能拿到对不上的
       一对 —— 而它的表现是小窗写着周日、旁边的日期写着周一。
    """

    text: str      # "Aug 30, 2026, 9am"
    weekday: str   # "Sunday"


def _when_line(moment):
    """一行「月 日, 年, 时刻」，外加它是星期几。

    ⚠️ `timezone.localtime()` 不能省（D16）：库里存的是 UTC，直接格式化会把
       下午 5 点之后开始的活动写成第二天 —— 不报错，只是差一天。星期几跟着一起
       错，而那是这一对里唯一会被人当场发现的一个。
       守卫：core.tests.TimeSourceGuardTests。

    ⚠️ 手写 `{m.day}` 而不是 `%-d`：那个去零的写法是 glibc/BSD 的扩展，
       Windows 上直接抛 ValueError。这里不需要为此赌一个部署平台。

    ⚠️ 星期几写全称（`%A` → "Sunday"），不是行里那种缩写：它是小窗里唯一的
       一个词，没有任何需要省的宽度，而 "Sun" 在只有它自己的一张小卡上
       读起来像被截断了。
    """
    m = timezone.localtime(moment)
    return WhenLine(f"{m:%b} {m.day}, {m.year}, {clock(m)}", f"{m:%A}")


def remaining(delta):
    """"1h 21m left" / "42m left"。红线压在卡上时写在卡的右边。

    ⚠️ 向上取整到分钟。向下取整的话最后 59 秒写的是「0m left」，而那一分钟里
       活动还在进行 —— 一个写着 0 的倒计时读起来是「已经结束了」。
    """
    minutes = -(-int(delta.total_seconds()) // 60)
    hours, minutes = divmod(max(minutes, 0), 60)
    if not hours:
        return f"{minutes}m left"
    return f"{hours}h left" if not minutes else f"{hours}h {minutes}m left"


def window(first_day, days=WINDOW_DAYS):
    """窗口里的那几天。"""
    return [first_day + datetime.timedelta(days=offset) for offset in range(days)]


def bounds(days):
    """窗口两端的瞬间，半开 [start, end)，给查询用。

    ⚠️ 宽一点是安全的、窄一点是 bug：这里只负责别漏掉活动，「这一段到底画不画」
       由 _segments 说了算。所以末端取的是最后一天的**次日零点**。
    """
    return day_start(days[0]), day_start(days[-1] + datetime.timedelta(days=1))


def floor_day(filter_start=None, today=None):
    """左箭头到哪一天为止。`filter_start` 是筛选卡的 From，一个**瞬间**或 None。

    「不能早于今天」（2026-08-18 拍板）：左边的列表是「今天零点起」的，日程要是
    能往回翻，翻出来的活动在左边永远找不到 —— 两块并排的东西答的就不是同一个
    问题了。筛选卡填了 From 时以它为准，于是日程和列表看到的仍然是同一段。

    ⚠️ 「今天」和「那个瞬间是哪一天」都走 core.timeutils（D16），而且是在
       **这里**走，不在视图里 —— core.tests.ViewsAreThinGuardTests 拦着
       views.py 里出现 `local_today(`，而那条守卫拦的正是这种会被连模板一起
       重写的日期运算。
    """
    today = today or local_today()
    if filter_start is None:
        return today
    return max(today, local_date_of(filter_start))


def first_day(requested, floor):
    """窗口从哪一天开始。

    ⚠️ 单向夹紧：`requested` 比下限早就抬到下限，比下限晚则**留在原地**。
       于是「把 From 改到更晚的一天」会把日程带过去（要的就是这个），
       而「把 From 改到更早的一天」只是把左箭头解锁，不会把人正在看的那一周
       拽回去 —— 后者会让「改一个筛选」变成「丢掉我翻到的位置」。
    """
    return max(requested, floor) if requested else floor


def parse_day(raw):
    """URL 上的 `from` → 一个日期。看不懂就当没填。

    ⚠️ 手敲坏的一个日期该退回默认窗口，不该 500 —— 这个参数会出现在分享出去的
       链接里，而链接是会被人手改的。
    """
    try:
        return datetime.date.fromisoformat(raw or "")
    except (TypeError, ValueError):
        return None


def navigation(first, floor):
    """三档屏幕各自的一对箭头。

    🔴 一次翻几天 = 那一档**看得见**几列，所以三对目标日期不一样，而哪一对
       露出来由 CSS 决定（`.schedule-nav--4/2/3`）。服务端算好三份是为了让
       前端一个日期运算都不做 —— 见文件开头 VISIBLE_DAYS 那一段。

    `prev` 为 None 表示已经到头，模板据此把按钮画成 disabled。
    """
    return [
        {
            "days": days,
            "prev": (max(floor, first - datetime.timedelta(days=days))
                     if first > floor else None),
            "next": first + datetime.timedelta(days=days),
            # 那一档**看得见**的最后一天，给「Aug 18 – Aug 21」那行标题用。
            # ⚠️ 不能拿窗口的最后一天：中间那档只画两列，标题写四天的范围就是
            #    一句屏幕当场证伪的话。
            "upto": first + datetime.timedelta(days=days - 1),
        }
        for days in VISIBLE_DAYS
    ]


def _base_colour(pk):
    """一个活动的「本命色」。

    🔴 **不能用内置的 `hash()`。** 它对 str 是加了每进程随机盐的（PYTHONHASHSEED），
       于是同一场活动在两个 gunicorn worker 上是两个颜色 —— 刷新一下就变，
       而这正是「每场活动固定一色」要排除的那件事。本机单进程跑起来一切正常。

    ⚠️ 也不能直接 `pk % PALETTE`：id 是连着发的，同一批建出来的活动会拿到
       一串连号的颜色，画面上读起来像是按顺序刷的，不像随机。crc32 打散它。
    """
    return zlib.crc32(str(pk).encode()) % PALETTE


def _segments(events, day):
    """把活动裁成「这一天里的那一段」，按开始时间排好。

    ⚠️ 用 `>` / `<` 而不是 `>=` / `<=` 判交集：正好在 0:00 结束的活动属于前一天，
       不该在第二天顶上留一张零高度的卡。
    """
    start_of_day = day_start(day)
    end_of_day = day_start(day + datetime.timedelta(days=1))
    found = []
    for event in events:
        if event.end_time <= start_of_day or event.start_time >= end_of_day:
            # ⚠️ 零长度的活动（模型只约束 end >= start）会被上面第一条判掉，
            #    因为它的 end 等于 start。补一条：它落在这一天里就要画。
            if not (event.start_time == event.end_time
                    and start_of_day <= event.start_time < end_of_day):
                continue
        found.append((
            max(event.start_time, start_of_day),
            min(event.end_time, end_of_day),
            event,
        ))
    # ⚠️ 排序键里带 pk：同一分钟开始、同样长的两场活动，光靠前两项是并列的，
    #    而并列的顺序在 Python 里取决于查询回来的次序 —— 于是偏移的前后关系
    #    和颜色会在两次请求之间自己换位。
    found.sort(key=lambda row: (row[0], -(row[1] - row[0]).total_seconds(), row[2].pk))
    return found, start_of_day


def _px(delta):
    """一段时长 → 像素，**取整**。

    ⚠️ 取整不是为了好看，是为了避开一个本地化的坑：这些数会被写进模板的
       `style="top: {{ ... }}px"`，而 `floatformat` 之类的东西在别的 locale 下
       会输出 `570,25` —— 一个**语法上无效**的 CSS 值，浏览器整条声明丢掉，
       卡片当场堆到顶上。整数没有小数点，也就没有这个问题。
       代价：最多差 1px，也就是 75 秒，画面上看不出来。
    """
    return round(delta.total_seconds() / 3600 * PX_PER_HOUR)


def _place(seg_start, seg_end, start_of_day):
    """一段时间 → (top, height) 像素。"""
    top = _px(seg_start - start_of_day)
    height = max(_px(seg_end - seg_start), MIN_CARD_PX)
    # ⚠️ 别让卡片探出这一天。裁到 0:00 的那一段本身不会，但 MIN_CARD_PX 会 ——
    #    23:50 的一场十分钟活动会被撑到 23:52，多出来的两像素画在下一天的位置上。
    return top, min(height, DAY_PX - top)


def _soonest_ending(cards, now):
    """红线压着的那些卡里，最快结束的那一场还剩多久。"""
    live = [card for card in cards
            if card.event.start_time <= now < card.event.end_time]
    if not live:
        return None
    return remaining(min(card.event.end_time for card in live) - now)


def _cards_for(day, events, now):
    """一列。摆放、偏移、配色，一趟走完。"""
    rows, start_of_day = _segments(events, day)
    cards = []
    for seg_start, seg_end, event in rows:
        top, height = _place(seg_start, seg_end, start_of_day)
        bottom = top + height

        # 偏移：跟已经摆上去的卡**画出来的框**相交就往下一档。
        # ⚠️ 是 `earlier.depth + 1` 而不是「相交的张数」：三张互不相交但都和
        #    第一张相交的卡，按张数会全部落在第 1 档、彼此完全重合。
        depth = 0
        for placed in cards:
            if placed.top < bottom and top < placed.top + placed.height:
                depth = max(depth, placed.depth + 1)
        depth = min(depth, MAX_DEPTH)

        if event.status == event.Status.CANCELLED:
            colour = None
        else:
            # 撞色回避：跟**画面上挨着**的已配色卡片错开。
            # ⚠️ 只看已经摆好的那些就够 —— 轮到后一张时它会回避前一张，
            #    所以每一对里至少有一次回避，不必再走第二趟。
            neighbours = [
                placed for placed in cards
                if placed.colour is not None
                and placed.top - TOUCH_PX < bottom and top < placed.top + placed.height + TOUCH_PX
            ]
            # ⚠️ 比的是**色族**，不是格子的下标。盘里有四个粉，按下标算它们是
            #    四个不同的颜色，屏幕上是同一个 —— 见 COLOUR_FAMILIES。
            taken = {_FAMILY_OF[placed.colour] for placed in neighbours}
            base = _base_colour(event.pk)
            # ⚠️ 从本命色开始往后顺延，而不是随便挑一个没被占的：这样只有真的
            #    撞上时颜色才会挪，同一场活动在大多数窗口里仍然是同一个色。
            #    代价如实说：邻居变了，它的颜色**可能**跟着挪一格。
            candidates = ((base + step) % PALETTE for step in range(PALETTE))
            colour = next(
                (slot for slot in candidates if _FAMILY_OF[slot] not in taken),
                # 八个族全被占了（一列里九张卡互相挨着）。退而求其次：只要不是
                # **同一格**就行。⚠️ 不能直接退回 base —— 那会让第九张和第一张
                # 完全同色，而它们正挨着。
                next(((base + step) % PALETTE for step in range(PALETTE)
                      if (base + step) % PALETTE
                      not in {placed.colour for placed in neighbours}),
                     base),
            )

        cards.append(Card(
            event=event,
            top=top,
            height=height,
            depth=depth,
            colour=colour,
            # ⚠️ 标签写活动**真实**的起止，不是裁过的那一段。跨夜那张卡上写
            #    「10pm – 12am」是错的 —— 它到第二天一点才结束，而人是拿这行字
            #    安排自己的时间的。
            label=f"{clock(event.start_time.astimezone(start_of_day.tzinfo))} – "
                  f"{clock(event.end_time.astimezone(start_of_day.tzinfo))}",
            continues_before=event.start_time < start_of_day,
            continues_after=event.end_time > start_of_day + datetime.timedelta(days=1),
            start_ms=int(event.start_time.timestamp() * 1000),
            end_ms=int(event.end_time.timestamp() * 1000),
            # ⚠️ 半开区间，和下面 now_left 用的是同一条界线：正好结束的那一刻
            #    既不算「还剩 0m」，也已经算过去了。
            is_past=event.end_time <= now,
        ))
    return cards


def columns(events, days, now=None):
    """窗口里每一天一列。

    `events` 是已经取好的那一批（视图负责查询和筛选）—— 这里不碰数据库，
    于是它可以拿一串普通对象测。
    """
    now = now or local_now()
    # ⚠️ `local_date_of(now)`，不是 `local_today()`：测试会把 `now` 冻在某个时刻，
    #    而 `local_today()` 读的是真实时钟 —— 两者一分岔，「今天是哪一列」就和
    #    红线画在哪一列不是同一个答案了。
    today = local_date_of(now)
    built = []
    for day in days:
        is_today = day == today
        cards = _cards_for(day, events, now)
        built.append(Column(
            day=day,
            day_start_ms=int(day_start(day).timestamp() * 1000),
            cards=cards,
            is_today=is_today,
            now_top=_px(now - day_start(day)) if is_today else None,
            now_left=_soonest_ending(cards, now) if is_today else None,
        ))
    return built


def hours():
    """左边那条刻度：24 行，每行一个整点。"""
    return [
        {"label": clock(datetime.time(hour=hour)), "top": hour * PX_PER_HOUR}
        for hour in range(24)
    ]
