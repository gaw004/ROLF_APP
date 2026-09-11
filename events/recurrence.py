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

from dateutil.rrule import rrulestr
from django.utils import timezone

#: 一条规则一次最多生成几场（决定 31，2026-09-10）。
#:
#: ⚠️ **「必须带 UNTIL 或 COUNT」挡不住这一格。** 那条规则挡的是「每周二一直
#:    下去」，而 `COUNT=5000` 完全合法、完全有限，并且会在一次点击里造出五千个
#:    活动、五千批角色和五千行历史。两条是两个不同的失败，各要一句话。
#:
#: ⚠️ 52 是「一年周更」。跨年的周会因此要重建一条规则，这是有意的代价：
#:    活动本来就有结束（[D33](../docs/planning/decisions/D33-work-schedule.md)
#:    第三节那条分歧的整个前提），而一个永不结束的系列正是这一档拒绝表达的东西。
#:    ⚠️ 和 [D40 第五节](../docs/planning/decisions/D40-undo-a-pattern-batch.md)
#:    那个「七天」一样，这个数没有别的依据，只有一句常识。试点跑一轮之后回来看它。
#:
#: ⚠️ 不放在 `core/limits.py`：那个文件管的是「一个人手敲进来的值可以多长」，
#:    而这是「一条规则可以造出多少行」。两件事，两种失败（一句超长的描述 vs
#:    五千行数据），放在一起会让那个文件的判据说不清楚。
MAX_OCCASIONS = 52

#: 规则必须自己带一个结束。⚠️ 大小写不敏感 —— `rrulestr` 收得下小写，
#: 而一条 `count=12` 的规则被判成「没有结束」是一次说不通的拒绝。
ENDING_KEYWORDS = ("UNTIL=", "COUNT=")


def has_an_ending(rule):
    """这条规则自己说得出什么时候停吗？

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


def occasions(rule, *, starts_on, start_time, limit=MAX_OCCASIONS + 1):
    """把 `rule` 展开成一串 aware datetime。不碰数据库，不问现在几点。

    `starts_on` 是第一次的日期（RRULE 的 `DTSTART`，拆出来传是因为规则本身要能
    单独存在一列里、单独读得懂），`start_time` 是每一次的当地开始时刻。

    ⚠️ `limit` 默认是 `MAX_OCCASIONS + 1`，**多要一个**。少要一个的话
       「刚好 52 场」和「5000 场砍到 52」长得一模一样，而调用方要靠这个区别
       决定是放行还是拒绝。多出来的那一个就是那句拒绝的证据。

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
    moments = itertools.islice(
        rrulestr(_until_in_local_time(rule), dtstart=start), limit)
    return [timezone.make_aware(moment) for moment in moments]
