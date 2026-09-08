"""侧栏那个小月历的格子。算术在这里，视图只取数据。

照 `events/schedule.py` 的先例：摆放全是算术，而算术写在模板里既测不了也读不懂。

🔴 **这是一个跳转控件，不是一个日历页面。** 它画当月的格子，在有报名的日子上点
   一个点，点哪一天都跳到 `/events/schedule/`。它**不翻月**、不显示活动名、
   不可点开某一场 —— 那些是 `/events/schedule/` 的活，而本项目已经判过
   「不做第三个日历页面」。多一颗能翻月的按钮，就是在这里重新长出那一页。

⚠️ 一天的边界一律走 `core.timeutils`（D16）。库里存的是 UTC，下午 5 点之后的
   活动用 UTC 取日期会整个跳到第二天的格子里 —— 不报错，只是点错一天。
"""

import calendar

from core.timeutils import local_date_of, local_today

#: 周日起，和美国的日历一致。⚠️ Python 的 calendar 默认是周一起（欧洲惯例），
#: 不设这个值的话格子会整体错一列，而那是一种「看着像对的」的错。
_WEEKS = calendar.Calendar(firstweekday=6)

#: 表头那一行。⚠️ 写死而不是从 `calendar.day_abbr` 取：那个跟着 locale 走，
#: 而 D23 定了界面统一英文 —— 一台设了别的 locale 的服务器会让这一行变成
#: 另一种语言，页面其余部分不变。
WEEKDAY_INITIALS = ["S", "M", "T", "W", "T", "F", "S"]


def marked_days(participations):
    """有报名的那几天，作为一个 `date` 集合。

    ⚠️ 收的是**已经取好的行**，不是一个 queryset —— 侧栏和「Coming up」那张卡
       画的是同一批报名，让它们查两次数据库，就是让两块并排的东西有可能
       不一致，还多一次查询。
    """
    return {
        local_date_of(row.event_role.event.start_time)
        for row in participations
    }


def month_grid(marked, today=None):
    """当月的格子：一个「周」的列表，每周七个格。

    每一格是一个字典，模板直接渲染，不做判断：

        {"day": 6, "date": date(2026, 9, 6), "marked": True, "is_today": False}

    ⚠️ 不属于本月的那几格给 `day=None` 而不是省略。省略会让最后一周少几个
       `<td>`，格子塌掉；给 0 会在页面上真的印出一个 0。
    """
    today = today or local_today()
    weeks = []
    for week in _WEEKS.monthdatescalendar(today.year, today.month):
        row = []
        for day in week:
            if day.month != today.month:
                row.append({"day": None, "date": None,
                            "marked": False, "is_today": False})
                continue
            row.append({
                "day": day.day,
                "date": day,
                "marked": day in marked,
                "is_today": day == today,
            })
        weeks.append(row)
    return weeks


def month_name(today=None):
    """`September 2026`，给格子上方那一行。"""
    today = today or local_today()
    return f"{calendar.month_name[today.month]} {today.year}"
