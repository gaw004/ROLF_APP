"""One page of a list, ordered so that paging cannot lie.

⚠️ It lives in `core` rather than beside the first list that needed it, and the
   move (2026-09-03) has the same shape as `Audience` going to `org` on
   08-31: `notices` wanted it, and importing it out of `events` for a reason
   that has nothing to do with events would have been the wrong dependency to
   draw (D41 第四节). Only the arithmetic is general; nothing here knows what a
   row is.

🔴 **Both functions are here so that the ordering rule is in one file.**
   `page_holding()` has to sort **identically** to `page_of()` — down to the
   trailing tiebreak — or "jump to the page that row is on" lands on the
   neighbouring page every so often, which reads as random flakiness. Splitting
   them across two modules is exactly the drift their comments warn about.
"""

from django.core.paginator import Paginator


def ordering_for(rows):
    """The ordering a page will actually be sliced with, tiebreak included.

    🔴 **It must end in a unique column.** `-start_time` alone is not unique —
       two events starting in the same minute have no defined order between
       them, so Postgres may return them in a different order for page 1 and
       page 2. What you see is a row appearing twice, or one vanishing
       entirely, and nothing anywhere reports it.

    🔴 **`Meta.ordering` is read as a fallback, and that is not a nicety**
       (2026-09-03). `qs.query.order_by` is empty for a queryset that never
       called `.order_by()` itself — the model's `Meta.ordering` is applied
       later, when the SQL is compiled. So the older version of this function,
       which appended `-pk` to `query.order_by` alone, **replaced** a model's
       ordering with `-pk` for any such caller instead of extending it.

       Events never noticed: `_scoped_events()` orders explicitly. `Notice` does
       not — its order (`-starts_showing`, `-id`) lives on `Meta` — so the first
       notices page to use this would have come out sorted by id, silently, and
       looking like a list that is merely "in some order".

    ⚠️ Appending `-pk` when the ordering already ends in `-id` is harmless: a
       repeated key in ORDER BY changes nothing, and spelling the rule once is
       worth more than the branch that would avoid the duplicate.
    """
    declared = list(rows.query.order_by) or list(rows.model._meta.ordering or ())
    return [*declared, "-pk"]


def page_of(request, rows, per_page, number=None):
    """One page of a filtered list.

    ⚠️ The caller keeps the unpaginated queryset. Any total or report is
       computed from **that**, not from this page: a figure that changed when
       you turned the page would mean nothing at all (D27).

    ⚠️ `number` 覆盖 URL 上的 `?page=`（2026-08-18）。它只有一个调用方：日程上
       点开一场活动时，左边要翻到**那一场所在的**那一页，而那一页是算出来的，
       不是人点出来的。
    """
    ordered = rows.order_by(*ordering_for(rows))
    return Paginator(ordered, per_page).get_page(number or request.GET.get("page"))


def page_holding(rows, pk, per_page):
    """Which page of that list the given row is on. None if it is not on any.

    ⚠️ 全量取一次 pk 再 `.index()`，而不是用窗口函数数「有多少行排在它前面」。
       理由和 events/forms.py 那条搜索一样：试点期只有几十场活动，这里没有东西
       要优化；而窗口函数那一版要在两个地方各写一遍同样的排序，也就是
       `ordering_for()` 存在的理由再多一份。

    ⚠️ 返回 None 是一个**正常**结果，不是错误：日程画的是「那几天里全部的活动」，
       而列表还带着筛选和「今天起」那一刀。两边天然可以不重合。
    """
    ordered = rows.order_by(*ordering_for(rows))
    keys = list(ordered.values_list("pk", flat=True))
    if pk not in keys:
        return None
    return keys.index(pk) // per_page + 1
