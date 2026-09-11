"""一条重复规则展开成它落在的那些时刻（2026-09-10，06-roadmap L5.5）。

纯函数，一个都不碰数据库。规则是 RFC 5545 的 `RRULE`（不含 `DTSTART`），
展开器 `occasions()` 把它和一个起始日、一个墙钟时刻拼起来，交回一串
timezone-aware 的 datetime。

⚠️ **一个展开器，写给三个调用方 —— 而今天只有一个。** 三个是：
   · recurring events 生成 `Event`（`events/services.py`，本轮唯一在跑的那个）；
   · Program 按规则一次排完一期课的十二讲；
   · D2a 的 `WorkPattern` 生成 `Shift`（[D33](../docs/planning/decisions/D33-work-schedule.md)）。
   后两个**还没有任何一步在做** —— 06-roadmap 里排讲次仍然是 admin 一条一条敲。
   如实写在这里，是因为「三个调用方」原来写得像三个都存在，而这个项目已经为
   「两份文档各自把一件事推给对方」付过两次账（L5.1 的 `Event.duration`、
   L5.3 那句「批量建点名行一个字都没写」）。做那两步的时候调这里，别再写一个。

🔴 **墙钟时间，不是绝对时刻。** 规则在 naive datetime 上展开，落出来的每一天再
   和 `start_time` 拼成当地时刻。所以按当地 19:00 每周重复的活动，跨过十一月
   第一个周日之后仍然是 19:00 —— 而它对应的 UTC 时刻变了。
   反过来做（在 aware datetime 上加七天）会让那一场变成 18:00 或 20:00，
   **并且不报错**：日历上仍然是每周二，只是时间错了一小时，而发现它的人是那天
   晚上早到或晚到一小时的志愿者。
   这正是 [D33 第二节](../docs/planning/decisions/D33-work-schedule.md)给 `Shift`
   存 date + time 而不存 aware datetime 的同一条理由。

⚠️ 用 `python-dateutil`（`requirements.txt` 里已有），**不引入任何新依赖**。
   不用 `django-recurrence`：它多给的是一个字段类型和一个 widget，
   而按 [D18](../docs/planning/decisions/D18-admin-boundary.md) 的落点规矩，
   生成器本来就该是这里的纯函数。
"""

import datetime
import itertools
import re

from dateutil.relativedelta import relativedelta
from dateutil.rrule import rrulestr
from django.utils import timezone

#: 一次生成最多造几场 —— **一张安全网，不是一条政策**（2026-09-11，L5.9；
#: 原来叫 `MAX_OCCASIONS`，值是 52）。
#:
#: 🔴 **改名是因为意思变了。** 在这之前，一条会产出 53 场的规则被**拒绝保存**，
#:    而那条拒绝挡住的是真实排法：每周一次跑一年半是 78 场，而它给的建议是
#:    「分成几段短的」—— 让人替系统干体力活。现在规则照常保存，生成按一年的
#:    窗口滚（`services.HORIZON_MONTHS`），这个数只在一条规则密到连一年的窗口
#:    都装不下时才开口。名字不跟着意思走，下一个人就会照着旧名字读新行为 ——
#:    这个仓库为这种事付过账。
#:
#: ⚠️ 它和窗口是**两件事**：窗口管「排到多远」（时间），它管「一次造多少行」
#:    （数量）。2026-09-11 实测之后才看清这一点 —— 一条每日规则在「三年以内」
#:    这条时间边界里是 1096 场，所以时间边界兜不住行数，两个数必须同时存在。
#:
#: ⚠️ 750 没有别的依据，只有一句常识，**和它替换掉的 52 一样**：
#:    一年的每日规则 365 场、早晚祷（一天两次）730 场，两者都该放行；
#:    而高级框里一条 `FREQ=HOURLY` 是一年 8760 场，必须挡住。
#:    试点跑一轮之后回来看它。
#:
#: ⚠️ 不放在 `core/limits.py`：那个文件管的是「一个人手敲进来的值可以多长」，
#:    而这是「一次能造出多少行」。两件事，两种失败（一句超长的描述 vs
#:    一次点击八千行），放在一起会让那个文件的判据说不清楚。
BATCH_CEILING = 750

#: 一次「生成」排到多远（2026-09-11，L5.9）。
#:
#: ⭐ 生成是**滚动**的：按一次排出从今天到一年后的场次，明年再按一次再往前。
#:    所以一条没有结束的规则不再是「要生成到永远」，只是「今年这一年」。
#:
#: ⚠️ 12 个月是谈定的：和「一年周更」那个直觉一致，每周一次正好 53 场 ——
#:    和它替换掉的那条 52 场上限几乎一样大。一年回来按一次，负担很轻。
#:
#: ⚠️ 它住在这里而不是 `services.py`，是因为 `events/models.py` 的 `clean()`
#:    也要用它（「这条规则在一年里密到装不下吗」），而 models 不能反过来
#:    导入 services —— services 导入的是 models。
HORIZON_MONTHS = 12


def horizon_from(today):
    """从 `today` 算起，这一次排到哪一天为止。"""
    return today + relativedelta(months=HORIZON_MONTHS)


def horizon_for(starts_on, today):
    """一条系列这一次排到哪一天 —— 从今天和第一场里**靠后**的那个算起。

    🔴 **不是从今天算，而这个区别是 2026-09-11 代码评审抓出来的。** 从今天算的
       那一版对一条「明年秋天才开课」的系列是这样的：它完全合法、保存得下，
       然后预览一场都不显示（对着一张填满的表单说「填好上面几格」），
       按生成说「什么都没生成，检查规则和第一场」—— 而规则和第一场都是对的。
       一个静默的死胡同，而且唯一的线索指向两个没有问题的格子。

    ⚠️ 窗口的意思本来就是「一次排**这条系列**的一年」，不是「一次排日历上的
       下一年」。所以提前半年排明年的课，排出来的是它自己的头一年。
    """
    return horizon_from(max(today, starts_on) if starts_on else today)

#: 规则必须自己带一个结束。⚠️ 大小写不敏感 —— `rrulestr` 收得下小写，
#: 而一条 `count=12` 的规则被判成「没有结束」是一次说不通的拒绝。
ENDING_KEYWORDS = ("UNTIL=", "COUNT=")


def has_an_ending(rule):
    """这条规则自己说得出什么时候停吗？

    🔴 **它不再是一条校验，是一个问句**（2026-09-11，L5.9）。在这之前，
       答 False 会让保存被拒（「This rule never stops. Say when it ends」）；
       现在没有结束的规则是**合法**的 —— 生成按一年的窗口滚，「即日停止」是
       它的出口。这个函数留下来是因为**页面要按它分两种说法**：
       有结束说「共 12 场，X 到 Y」，没结束说「一年内 53 场，直到你停掉它」。

    ⚠️ 所以答 False **不是**一个问题。照旧把它当拒绝用的代码，会拒掉一种
       现在完全正常的排法。

    ⭐ 判在**字符串**上而不是 `rrule` 对象的 `_until` / `_count`：那两个是
       dateutil 的私有属性，而 `rrulestr()` 遇上带 `EXDATE` 的规则会交回一个
       `rruleset`，那上面根本没有这两个属性。问题本来就是 RFC 5545 层面的
       —— 「这条 RRULE 里有没有写 UNTIL 或 COUNT」—— 所以在那一层问它。

    ⚠️ 它不判规则合不合法，那是 `rrulestr()` 的事。两个问题分开问，是因为
       两句拒绝的话完全不同：一句是「这条规则读不懂」，一句是「这条规则读得懂，
       但它没有尽头」，而把后者说成前者会让人反复去检查语法。
    """
    return any(word in rule.upper() for word in ENDING_KEYWORDS)


#: `UNTIL=20261231T000000Z` — RFC 5545's own spelling, and what every calendar
#: exports. ⚠️ Case-insensitive and tolerant of the date-only form.
_UNTIL_IN_UTC = re.compile(r"(UNTIL=)(\d{8}(?:T\d{6})?)Z", re.IGNORECASE)


def _until_in_local_time(rule):
    """Rewrite a `UNTIL=…Z` into the wall-clock time this module works in.

    🔴 **The single most likely thing a real person will paste, and it was
       refused.** RFC 5545 requires `UNTIL` in UTC whenever `DTSTART` is zoned,
       so `FREQ=WEEKLY;BYDAY=TU;UNTIL=20261231T000000Z` is what Google
       Calendar and Outlook emit. Our `dtstart` is naive on purpose (the whole
       wall-clock argument above), and dateutil refuses the mismatch with
       *"RRULE UNTIL values must be specified in UTC when DTSTART is
       timezone-aware"* — which reached the admin as advice to put `UNTIL` in
       UTC, **which they had already done**. A loop with no exit, on the
       commonest input there is.

    ⚠️ Converted, not stripped. Dropping the `Z` and keeping the digits would
       move the cut-off by up to a day: an end of `20261231T000000Z` is
       31 December 16:00 in California, so a rule "until the 31st" would keep
       or lose the 31st depending on which reading you took. Converting says
       what the calendar that wrote it meant.

    ⚠️ Date-only `UNTIL=20261231` and the already-local form are left exactly
       as they are — there is nothing to convert and no reason to touch them.
    """
    def to_local(match):
        prefix, stamp = match.group(1), match.group(2)
        shape = "%Y%m%dT%H%M%S" if "T" in stamp else "%Y%m%d"
        moment = datetime.datetime.strptime(stamp, shape).replace(
            tzinfo=datetime.timezone.utc)
        return prefix + timezone.localtime(moment).strftime(shape)

    return _UNTIL_IN_UTC.sub(to_local, rule)


def looks_like_a_rule(rule):
    """Is this an RRULE at all, or is it a sentence somebody typed?

    ⭐ Asked **before** `has_an_ending()`, and the order is the whole point.
       That one is a substring test, so "every tuesday" contains no `COUNT=`
       and came back as "This rule never stops. Say when it ends" — advice for
       a rule that is fine except for its ending, handed to somebody whose
       input is not a rule at all. They add an ending in English and get the
       identical sentence. The docstring above spells out this hazard in the
       opposite direction; this is the mirror image of it.

    ⚠️ Deliberately shallow — `FREQ=` and nothing else. It is a triage question
       ("did they type the calendar format?"), not a validation: `rrulestr()`
       is what judges whether the rule is correct, and it says so far better
       than a regex here could. Anything stricter would start refusing rules
       dateutil accepts, which is the wrong side to be wrong on.
    """
    return "FREQ=" in (rule or "").upper().replace(" ", "")


def occasions(rule, *, starts_on, start_time, limit=BATCH_CEILING + 1,
              not_after=None):
    """把 `rule` 展开成一串 aware datetime。不碰数据库，不问现在几点。

    `starts_on` 是第一次的日期（RRULE 的 `DTSTART`，拆出来传是因为规则本身要能
    单独存在一列里、单独读得懂），`start_time` 是每一次的当地开始时刻。

    ⚠️ `limit` 默认是 `BATCH_CEILING + 1`，**多要一个** —— 理由 2026-09-11
       换了，别照着旧的读：在这之前，多出来的那一个是「该拒绝」的证据；
       现在没有拒绝了，它是**「后面还有」的证据**。
       「刚好 52 场」和「还剩一万场没排」长得一模一样，而页面要靠这个区别
       决定说「排完了」还是「再按一次还有」。

    ⚠️ 用 `islice` 而不是先 `list()`：一条没有结束的规则是一个无穷迭代器，
       `list()` 会在这里挂住，而挂住的表现是一个永远转圈的页面。`clean()` 拒绝
       它是**第二道**门；这一道保证第一道门有机会开口。

    ⚠️ 夏令时那一天两种奇怪的当地时刻都**不会抛**，而结论对、原来写的机制不对
       （2026-09-10 更正）。原文说 `make_aware()`「靠 `fold` 消歧」——
       Django 5 的 `make_aware()` 就是一句 `value.replace(tzinfo=…)`，
       **从不读 `fold`**。实测：春天那个不存在的 02:30 会得到一个读起来是
       `02:30-06:00` 的值，`timezone.localtime()` 原样返回它（`astimezone`
       在 tzinfo 相同时直接短路），而它从 Postgres 回来是 03:30。

       ⚠️ 所以「不抛、照常落一场」成立，「怎么落的」原来写错了。活动一般不排在
       凌晨两点，这里仍然选「照常落一场」而不是「跳过它」—— 跳过的话日历上会
       凭空少一周，而没有任何一处说得出为什么。

    ⚠️ 这里只决定**开始**的墙钟时刻。一场活动开多久是绝对时间，
       所以结束时刻在 `services.generate_occasions()` 里按 UTC 相加 ——
       两者在一年里有两个早上不一样，而那一格属于那边不属于这里。
    """
    start = datetime.datetime.combine(starts_on, start_time)
    # ⚠️ naive 进、naive 出。整条规则在墙钟上跑完，最后一步才落地到时区 ——
    #    见模块顶上那条 🔴。⚠️ `UNTIL=…Z` 先换算成当地时间，见上面那个函数。
    stream = rrulestr(_until_in_local_time(rule), dtstart=start)
    if not_after is not None:
        # ⚠️ `takewhile` 排在 `islice` **前面**，而两个都是惰性的 —— 所以一条
        #    没有结束的规则仍然会停：要么撞到窗口边界，要么撞到 `limit`。
        #    反过来写（先 islice 再 takewhile）会先取满 limit 再扔掉大半，
        #    对一条每日规则就是白算一年的日期。
        stream = itertools.takewhile(
            lambda moment: moment.date() <= not_after, stream)
    return [timezone.make_aware(moment)
            for moment in itertools.islice(stream, limit)]


# --- 选择器和规则字符串之间的那层翻译（2026-09-11，L5.8c）------------------
#
# 🔴 **这一段不新增任何存储。** 存的仍旧是 `EventSeries.rule` 那一列同一个
#    RRULE 字符串 —— 下面两个函数只是把它拆给控件看、再从控件拼回去。
#    加几列 `repeat_mode` / `repeat_interval` 会让「这条规则到底是什么」有两个
#    答案，而两个答案迟早说不一样的话（D14：规则只许有一处）。
#
# ⚠️ 覆盖的是**两种**排法：每 N 周的某几天、每 N 个月的第几个某天。别的一律
#    交给发布页上那个「高级」输入框原样收下 —— `decompose()` 交回 None 就是
#    「这条规则我表达不了」，而不是「这条规则不对」。两件事分开，是因为把后者
#    说成前者会让人去改一条本来就对的规则。

#: RRULE 的星期缩写，按周一起头排 —— 和 `datetime.date.weekday()` 同序。
WEEKDAYS = (
    ("MO", "Monday"), ("TU", "Tuesday"), ("WE", "Wednesday"),
    ("TH", "Thursday"), ("FR", "Friday"), ("SA", "Saturday"), ("SU", "Sunday"),
)

#: 「第几个」。⚠️ `-1` 是「最后一个」，**不等于**「第四个」：十月有五个周六，
#:    最后一个是 31 日而第四个是 24 日。两个选项都给，是因为「每月最后一个
#:    周六大扫除」和「每月第四个周六」是两种不同的安排。
ORDINALS = (
    ("1", "First"), ("2", "Second"), ("3", "Third"), ("4", "Fourth"),
    ("-1", "Last"),
)

#: 转轮的档位：每 1～4 周 / 每 1～4 个月（2026-09-11 定）。
MAX_INTERVAL = 4

WEEKLY, MONTHLY = "weekly", "monthly"

#: 本模块认得的键。⚠️ 出现任何别的键就交回 None 走「高级」——
#:    白名单而不是黑名单：漏掉一个没想到的键（`BYSETPOS`、`BYMONTHDAY`）
#:    会让选择器**默默改写**一条它其实没读懂的规则，而那是这一段最贵的失败。
_KNOWN_KEYS = {"FREQ", "INTERVAL", "BYDAY", "COUNT", "UNTIL"}

_ORDINAL_DAY = re.compile(r"^(-?\d+)([A-Z]{2})$")


def _parts_of(rule):
    """`FREQ=WEEKLY;BYDAY=TU` -> `{"FREQ": "WEEKLY", "BYDAY": "TU"}`。"""
    parts = {}
    for chunk in (rule or "").upper().replace(" ", "").split(";"):
        if "=" in chunk:
            key, _, value = chunk.partition("=")
            parts[key] = value
    return parts


def compose(*, mode, every=1, weekdays=(), ordinals=(), weekday=None,
            count=None, until=None):
    """把选择器上的几个答案拼成一条 RRULE。

    ⚠️ `INTERVAL=1` **不写出来**。它是 RFC 的默认值，而库里已有的规则
       （`FREQ=WEEKLY;BYDAY=TU;COUNT=12`）也没写 —— 拼出一条多一个键但意思
       一样的规则，会让「改了配方吗」这个问题在字符串比较上答错。

    ⚠️ `UNTIL` 落的是当地的 `T235959`，不是 `T000000`。人选「到 12 月 31 日
       为止」要的是把那天**算进去**，而 `T000000` 会把当天 19:00 那一场挡在
       外面 —— 少一场，不报错，而且只有数日期的人才会发现。
    """
    if mode == MONTHLY:
        days = ",".join(f"{ordinal}{weekday}" for ordinal in ordinals)
        rule = [f"FREQ={MONTHLY.upper()}"]
    else:
        days = ",".join(weekdays)
        rule = [f"FREQ={WEEKLY.upper()}"]
    if every and int(every) != 1:
        rule.append(f"INTERVAL={int(every)}")
    if days:
        rule.append(f"BYDAY={days}")
    if until:
        rule.append(f"UNTIL={until:%Y%m%d}T235959")
    elif count:
        rule.append(f"COUNT={int(count)}")
    return ";".join(rule)


def decompose(rule):
    """一条规则拆回选择器上的那几个答案，表达不了就交回 None。

    ⭐ 交回 None **不是**「这条规则有问题」。`FREQ=DAILY`、`BYMONTHDAY=15`
       都是完全合法的规则，只是这个选择器画不出来 —— 调用方的正确反应是把
       「高级」那一栏展开、把原字符串原样放进去，而不是报错或者改写它。
    """
    if not looks_like_a_rule(rule):
        return None
    parts = _parts_of(rule)
    if set(parts) - _KNOWN_KEYS:
        return None
    if parts.get("FREQ") not in {WEEKLY.upper(), MONTHLY.upper()}:
        return None

    every = int(parts.get("INTERVAL", 1))
    if not 1 <= every <= MAX_INTERVAL:
        return None

    days = [d for d in parts.get("BYDAY", "").split(",") if d]
    if not days:
        return None

    known_days = {code for code, _ in WEEKDAYS}
    answer = {"every": every, "count": None, "until": None}

    if parts["FREQ"] == WEEKLY.upper():
        if not set(days) <= known_days:        # 每周那一档不许带「第几个」
            return None
        answer.update(mode=WEEKLY, weekdays=days, ordinals=[], weekday=None)
    else:
        ordinals, weekday = [], None
        for day in days:
            matched = _ORDINAL_DAY.match(day)
            if not matched:
                return None
            ordinal, code = matched.groups()
            if ordinal not in {value for value, _ in ORDINALS}:
                return None
            if code not in known_days:
                return None
            # ⚠️ 「第一个周六和第三个**周日**」表达不了：这一档只有一个星期几
            #    的下拉。画得出来的是「同一个星期几的第几次」。
            if weekday is not None and code != weekday:
                return None
            weekday, _ = code, ordinals.append(ordinal)
        answer.update(mode=MONTHLY, weekdays=[], ordinals=ordinals,
                      weekday=weekday)

    if "COUNT" in parts and "UNTIL" in parts:
        return None                            # RFC 5545：两个不许同时出现
    if "COUNT" in parts:
        answer["count"] = int(parts["COUNT"])
    elif "UNTIL" not in parts:
        # ⚠️ 两个都没有 = 这条规则不结束，而那是**合法**的（2026-09-11，L5.9）。
        #    这里原来交回 None（当成「画不出来」），于是一条刚用选择器建出来的
        #    无限规则，再打开就掉进高级框里 —— 而它明明是选择器自己造的。
        pass
    elif "UNTIL" in parts:
        stamp = _parts_of(_until_in_local_time(rule))["UNTIL"].rstrip("Z")
        # ⚠️ 直接切出年月日，不走 `strptime()`：要的本来就是一个**日期**，
        #    而 strptime 造的是一个 naive datetime —— 本模块最上面那条 🔴
        #    讲的就是 naive 和 aware 混用的代价，这里没有理由再造一个。
        answer["until"] = datetime.date(
            int(stamp[0:4]), int(stamp[4:6]), int(stamp[6:8]))
    return answer
