"""The self-service pages and the ministry-admin pages.

Thin shells, every one of them. Three rules hold across the whole file:

1. Visibility is decided in the query, never in the template. Hiding a draft
   with {% if %} still sent it to the browser; filtering it out means it was
   never fetched. Same for "mine": the queryset is narrowed to the logged-in
   contact, so somebody else's id in the URL can only 404.

   ⚠️ `event_detail` is the one deliberate exception (2026-08-06), and it is
      worth stating precisely so the next reader does not "fix" it: that page
      **must** serve a row the volunteer predicate excludes, because a draft's
      preview is the whole point. What the exception does not move is *where*
      the decision is made — it is still the view, before render, and the
      refusal is still 404. The rule this docstring is protecting is "no
      {% if %} in a template stands between a viewer and data the response
      already carries", and that still holds.
2. The permission check is the first thing each protected view does, and the
   check itself is one call into org.permissions — there is a grep guard on
   that.
3. No arithmetic here. Counts and totals come from QuerySet methods and
   services.py, because anything computed in a view gets rewritten along with
   the templates (D18) — and there is a guard for that too.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Prefetch
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django_ratelimit.decorators import ratelimit

from org.models import Ministry
from org.permissions import (
    SCOPED_DENIAL,
    in_foundation_tier,
    can_manage_event,
    can_publish_event,
    can_view_event_records,
    ministry_ids_administered_by,
)

from . import schedule
from .forms import (
    EventForm,
    EventPeriodForm,
    EventRoleForm,
    EventStatusForm,
    HoursForm,
    NotifyForm,
    SignUpForm,
)
from .models import Event, EventNotification, EventRole, Participation
from .tokens import (
    CHECK_IN,
    MODES,
    InvalidCheckInToken,
    issue_with_expiry,
    verify as verify_checkin_token,
    window_is_open,
    window_message,
)
from .services import (
    CHECKIN_CREDENTIAL_KEY,
    ConsentRequired,
    CredentialExpired,
    TurnedUp,
    apply_scan,
    cancel,
    checkin_result_message,
    default_checkin_mode,
    issue_credential,
    read_credential,
    scan_targets,
    check_in,
    check_out,
    clear_hours,
    confirm_signup,
    default_message,
    event_summary,
    mark_absent,
    ministry_report,
    ministry_staff_participation,
    notify_event_change,
    record_hours,
    reschedule,
    scheduled_hours,
    resolve_recipients,
    contacts_asked_about_serving,
    set_served_as,
    set_status,
    sign_up,
    undo_attendance,
)


def _template(request, full, fragment):
    """Whole page, or just the part HTMX asked to swap.

    ⚠️ Both branches are handed **the same context** by the caller. Building a
       smaller one for the fragment is the obvious optimisation and the thing
       that eventually makes the count on the fragment disagree with the count
       on the full page — and nothing would report that, because both renders
       succeed.

    ⭐ HTMX is only ever the fast path here. Every one of these views still
       answers a plain GET or POST with a complete page, which is what keeps the
       end-to-end tests (they never send HX-Request) testing the real thing —
       see D24's progressive-enhancement rule.
    """
    return fragment if request.headers.get("HX-Request") else full


def _back_link(request):
    """Where "back" goes from an event's page, and what it should be called.

    Decided by **where they came from**, not by who they are: a ministry admin
    who reached an event from the volunteer list wants the volunteer list back,
    and the same person arriving from the management list wants that one.
    Before this, the link was chosen by role alone and sent half the arrivals
    somewhere they had not been.

    ⚠️ Read from a `?from=` marker on the link, never from the Referer header.
       Referer is empty for a pasted URL, a new tab, a link in an email, or a
       browser configured not to send it — and when it is empty this function
       silently returns the default, which is exactly the case nobody tests.
       It is also attacker-controlled, and this value ends up in an href.

    ⚠️ The marker is a **key into a table written here**, never a URL. Taking a
       URL from the query string and rendering it into a link is how a page
       ends up with a "back" button pointing at somebody else's site.

    ⚠️ `manage` is honoured only if this account can actually open that page.
       A volunteer handed a `?from=manage` link would otherwise get a back
       button that 403s — a link that refuses the person who clicked it reads
       as a broken site rather than as a page not meant for them.
    """
    marker = request.GET.get("from")
    if marker == "mine":
        return reverse("events:my_participations"), "My Signups"
    # ⚠️ `past` was a marker here until 2026-08-17. It is not merely unused now
    #    — the page it pointed at is gone, so honouring it would send people to
    #    a 404. Unknown markers fall through to the default below, which is the
    #    behaviour an old bookmark carrying `?from=past` gets, and the reason
    #    this function was written as a whitelist in the first place.
    if marker == "manage":
        administers_any = bool(ministry_ids_administered_by(request.user))
        if administers_any or in_foundation_tier(request.user):
            # Same label the navigation uses for this account, so the two do
            # not name one page two different things.
            label = "Events I Manage" if administers_any else "All Events"
            return reverse("events:event_manage_list"), label
    return reverse("events:event_list"), "Events"


#: How many events one page of each list holds (2026-08-05).
#:
#: Two different numbers because the rows are two different heights: the
#: volunteer lists are cards with a thumbnail, the management list is a table
#: row. Fifty cards is a very long page; fifty table rows is one screen and a
#: bit, and the person reading that page is scanning across everything.
EVENTS_PER_PAGE = 20
MANAGED_EVENTS_PER_PAGE = 50


def _page(request, events, per_page, number=None):
    """One page of a filtered list, ordered so that paging cannot lie.

    ⚠️ The ordering **must** end in a unique column. `-start_time` alone is not
       unique — two events starting at the same minute have no defined order
       between them, so Postgres may return them in a different order for
       page 1 and page 2. The visible result is a row appearing twice, or one
       vanishing entirely, and nothing anywhere reports it.

    ⚠️ The caller keeps the unpaginated queryset. The report is computed from
       **that**, not from this page: a figure that changed when you turned the
       page would mean nothing at all (D27).

    ⚠️ `number` 覆盖 URL 上的 `?page=`（2026-08-18）。它只有一个调用方：日程上
       点开一场活动时，左边要翻到**那一场所在的**那一页，而那一页是算出来的，
       不是人点出来的。
    """
    ordered = events.order_by(*events.query.order_by, "-pk")
    return Paginator(ordered, per_page).get_page(number or request.GET.get("page"))


def _page_holding(events, pk, per_page):
    """Which page of that list the given event is on. None if it is not on any.

    ⚠️ 这里**必须**用和 `_page` 一模一样的排序，包括结尾那个 `-pk` ——
       少一截，一分钟内开始的两场活动在这里和在列表里的先后可以不同，
       于是「跳到那一页」偶尔会跳到相邻的一页。看起来像随机失灵。

    ⚠️ 全量取一次 pk 再 `.index()`，而不是用窗口函数数「有多少行排在它前面」。
       理由和 forms.py 那条搜索一样：试点期只有几十场活动，这里没有东西要优化；
       而窗口函数那一版要在两个地方各写一遍同样的排序，也就是上面那条注释
       防的东西再多一份。

    ⚠️ 返回 None 是一个**正常**结果，不是错误：日程画的是「那几天里全部的活动」，
       而列表还带着筛选和「今天起」那一刀。两边天然可以不重合。
    """
    ordered = events.order_by(*events.query.order_by, "-pk")
    keys = list(ordered.values_list("pk", flat=True))
    if pk not in keys:
        return None
    return keys.index(pk) // per_page + 1


def _my_contact(request):
    """The Contact behind the logged-in account, or None.

    None is a normal state — a superuser has no Contact by design — so pages
    cope with it rather than raising, the same rule permissions.py follows.
    """
    return getattr(request.user, "contact", None)


# --- B9: the volunteer's own pages --------------------------------------


@login_required
def event_list(request):
    """P3: what is on, from today forward.

    visible_to_volunteers() + from_today() (2026-08-17). It used to be
    open_for_signup().upcoming() — that predicate is gone now, deleted with
    past() once neither had a caller left — which is a narrower thing: *only*
    what you
    could still join, and *only* what had not started. Two consequences, both
    of them wrong for a page called "Events":

      · An event vanished the moment it filled up. The volunteers most likely
        to look it up are the ones who got in, and for them the page said the
        event did not exist. Same for a cancelled one — the people who need to
        see it is off are exactly the ones who signed up.
      · An event vanished the moment it ended, mid-morning, while people were
        still checking in.

    Being able to *see* an event and being able to *join* it stay different
    questions — that is the whole point of the two predicates. The join gate has
    not moved: event_signup still runs open_for_signup(), so a full or cancelled
    event 404s there no matter how it is listed. What the row carries now is its
    status, so the page says which of the two it is instead of hiding one.
    """
    period = EventPeriodForm(request.GET or None)
    return render(request, _template(
        request, "events/event_list.html", "events/_event_list_results.html"), {
        "period": period,
        **_listing(request, period),
        # 右边那块日程。⚠️ 它和上面那个 `events` 是**两个不同的集合**，故意的：
        #    列表是分页的二十条，日程是那几天的全部。两边共用的只有筛选。
        **_schedule(request, period),
        # 筛选是 HTMX 换掉 `#event-results`，而日程在那块外面 —— 所以筛选那一次
        # 请求要把日程作为 out-of-band 的第二块一起带回去，否则右边还画着上一次
        # 筛选的结果，而它看起来完全正常。
        #
        # ⚠️ 翻页走的也是这条路，于是也会重画一次日程。多余，但**不是错的**：
        #    翻页的链接保留全部查询参数，所以重画出来的是同一个窗口。
        #    分开处理要在这里判断请求来自哪个控件，而那比多渲染一块贵得多。
        "schedule_oob": bool(request.headers.get("HX-Request")),
        "schedule_partial": bool(request.headers.get("HX-Request")),
    })


def _visible_events(period):
    """左边那一列列的是什么：筛完、排好，还没分页。

    ⚠️ 单独一个函数，因为「第几页」要问它两次（先数出那一场排在第几，再取那
       一页），而两次必须是**同一个查询** —— 各写一遍的话，两份筛选迟早不一样，
       于是「跳到那一页」偶尔跳到相邻的一页，看起来像随机失灵。
    """
    return period.narrow(
        Event.objects.visible_to_volunteers()
        .from_today()
        # ⚠️ 每一行都要问「满了没」来决定那枚标签画不画成链接（2026-08-19）。
        #    不加这个注解的话那是**每行一次查询** —— 一页二十行，在全站被打得
        #    最多的一页上。判据本身没有在这里重写，见 `with_capacity()`。
        .with_capacity()
        .select_related("ministry", "event_type")
        .order_by("start_time")
    )


def _listing(request, period, page_number=None):
    """左边那一列的上下文。event_list 和日程点开的那一次共用。

    ⚠️ 只返回**模板真的要用的**东西。未分页的那个查询集不在里面 —— 需要它的
       是 `_page_holding`，而那是视图的事；塞进上下文就是把一个没人渲染的
       完整集合递给模板，正是本文件第一条规矩防的那件事。
    """
    page = _page(request, _visible_events(period), EVENTS_PER_PAGE, number=page_number)
    return {
        "events": page,
        "page": page,
        # R1, in the plainest possible form: how many, in the window they asked
        # for. ⚠️ The whole filtered set, not this page — "20 events" under a
        # filter that matched 180 would answer a question nobody asked.
        "total": page.paginator.count,
    }


def _schedule(request, period):
    """右边那块日程的上下文。event_list 和 event_schedule 共用一份。

    ⚠️ 共用，不是各建各的 —— `_template` 那条注释写的是同一件事：两个分支各自
       建上下文，迟早会在某个筛选下画出两份不一样的日程，而两边都渲染成功。

    ⚠️ 这里**只做取数和夹紧**，日期运算全在 events/schedule.py（本文件第三条
       规矩：视图里不算术）。
    """
    filter_start, _ = period.bounds()
    floor = schedule.floor_day(filter_start)
    first = schedule.first_day(schedule.parse_day(request.GET.get("from")), floor)
    days = schedule.window(first)
    start, end = schedule.bounds(days)
    # ⚠️ `visible_to_volunteers()`，和列表同一道门 —— 日程不是一条绕过草稿的
    #    旁路。⚠️ 但**不带 `from_today()`**，理由 2026-08-18 换了一个：原来是
    #    「那条按 start_time 切会切掉跨夜的活动」，而它现在按 end_time 切，
    #    不再切掉了。剩下的理由是这一条：日程要的是**和窗口相交**的活动，
    #    而窗口可以翻到下个月 —— 再叠一道「今天起」只会把它自己的下界抄第二遍。
    events = period.narrow(
        Event.objects.visible_to_volunteers().select_related("ministry"))
    events = events.filter(start_time__lt=end, end_time__gte=start).order_by("start_time")
    return {
        "schedule_columns": schedule.columns(events, days),
        "schedule_hours": schedule.hours(),
        "schedule_nav": schedule.navigation(first, floor),
        "schedule_from": first,
        "schedule_day_px": schedule.DAY_PX,
    }


@login_required
def event_schedule(request):
    """箭头翻页时换掉的那一块。整页里的是同一份模板，同一份上下文。

    ⚠️ 它是个**读**操作，所以按 D24 可以只有 HTMX 一条路 —— 但它偏偏也不需要：
       箭头是真的 `<a href>`，没有 JS 时点下去整页重来，日程停在新的窗口上。
    """
    period = EventPeriodForm(request.GET or None)
    return render(request, "events/_schedule.html", {
        "period": period,
        # 箭头翻页要顺手把筛选卡里那个隐藏的 `from` 也改掉，否则下一次筛选会
        # 把窗口拽回起点 —— 见 _period_filter.html 里那一段。
        "schedule_partial": True,
        **_schedule(request, period),
    })


# ⚠️ There is no past_events view any more (2026-08-17). The page it used to
#    draw is gone, deliberately, along with its two templates and its route —
#    see revisions.md. What answers R1 now, and for whom, is written down in
#    goal.md's R1 row: the foundation tier reads it off All Events, which can
#    already be filtered to any period. A volunteer's own finished events are
#    still on My Signups, which is where they were being looked up from anyway.
#
#    ⚠️ Do not bring it back as `event_list(past=True)`. The one thing that page
#       had that this one does not is a *backwards* window, and a flag that
#       flips a queryset's direction is how one view ends up answering two
#       questions badly.


@login_required
def event_detail(request, pk):
    """visible_to_volunteers(), so a full or finished event still opens.

    ⚠️ Written as status == OPEN this page would 404 the moment an event filled
       up — for exactly the people who had signed up, and for P6's "can't make
       the new time? cancel here" link, which is sent to precisely them.

    2026-08-06 — and one way past that predicate: **preview**. An event nobody
    has published yet 404'd for everybody, its own ministry's admin included,
    which made every draft's name on the management list a link that refuses the
    person who clicked it. It was also the odd one out: registrations,
    attendance, the report, the edit page and the notice page have all opened on
    a draft the whole time. Only this page did not, and nothing said why.

    ⭐ Same page, not a second one. The point of a preview is to read what the
       volunteers will read, so a preview that had its own template would be
       answering a different question by the second time somebody edited one of
       them. What `preview` adds is a banner and an inert signup button; nothing
       else on the page is drawn from it.

    ⚠️ The refusal stays **404, not 403**. To somebody with no business here a
       draft must not exist — a 403 says "there is an event at this id and it is
       not for you", which is exactly the sentence a draft is supposed to
       withhold. So the outcome for a volunteer is byte-for-byte what it was.

    ⚠️ `can_view_event_records`, the read check — not `can_manage_event`. The
       foundation tier can already open this event's signups, attendance and
       report; letting the *event page* be the one thing it cannot see would
       make the narrower rule the confusing one, and it would be a rule no
       reader could guess from the other five pages.

    ⚠️ Keyed on membership of VISIBLE_TO_VOLUNTEERS, never on `== DRAFT`. The
       set is the model's answer to "who may a volunteer see", listed in full
       for the reason written above it (events/models.py) — and the day somebody
       adds `postponed` to it, a branch spelled `== DRAFT` would quietly publish
       that event to everybody while this comment still claimed otherwise.
    """
    return render(request, "events/event_detail.html", _detail(request, pk))


def _detail(request, pk):
    """一场活动详情的上下文。整页和日程面板里那一份**共用**（2026-08-18）。

    ⚠️ 共用的理由和模板那边一样，也和 `_template` 那条注释一样：两处各建一份，
       迟早会在某个分支上说两件不一样的事，而两边都渲染成功。这里尤其要紧 ——
       里面有 `preview` 和 `can_manage` 两个**权限**判断，而面板是一条新开的
       取数路径。分叉在这里的名字叫「草稿从侧边栏漏出去了」。
    """
    event = get_object_or_404(
        Event.objects.select_related("ministry", "event_type"), pk=pk)
    preview = event.status not in Event.VISIBLE_TO_VOLUNTEERS
    if preview and not can_view_event_records(request.user, event):
        # Deliberately indistinguishable from "no such event" — see above.
        raise Http404("No event matches the given query.")
    contact = _my_contact(request)
    mine = Participation.objects.none()
    if contact is not None:
        mine = Participation.objects.filter(
            event_role__event=event, contact=contact,
        ).select_related("event_role__role")
    back_url, back_label = _back_link(request)
    return {
        "event": event,
        "roles": event.roles.with_signup_counts().select_related("role"),
        "mine": mine,
        # ⚠️ The property, not `status in OPEN_FOR_SIGNUP` (2026-08-19). It asks
        #    the clock as well, exactly as the `open_for_signup()` queryset
        #    behind event_signup does — and the reason it has to is that the two
        #    are the same gate seen from two sides. Reading only the status here
        #    put a Sign up button on events that finished last year, and the
        #    page it led to had already stopped accepting them.
        "can_sign_up": event.accepting_signups,
        "can_manage": can_manage_event(request.user, event),
        # ⚠️ False on every published event, so the banner is not something a
        #    template has to remember to switch off. It is only ever true for a
        #    viewer who already passed the check above.
        "preview": preview,
        "back_url": back_url,
        "back_label": back_label,
    }


@login_required
def event_detail_panel(request, pk):
    """点日程上的一张卡时换进面板的那一块（2026-08-18）。

    一次请求，两块东西：面板里的详情，以及**左边列表翻到那一场所在的那一页**
    （out-of-band）。分成两次请求的话，两块会在慢网下先后落地，而中间那一下
    是「右边已经是新活动、左边还高亮着上一个」。

    ⚠️ 权限走 `_detail()`，和整页同一条 —— 面板是一条新开的取数路径，而新开的
       取数路径正是权限最容易漏掉的地方。草稿在这里同样是 404。

    ⭐ 它是**读**操作，所以按 D24 可以只有 HTMX 一条路。日程上那张卡仍然是一个
       真的 `<a href>`，指向整页详情：没有 JS 时点下去就是整页跳过去。
    """
    period = EventPeriodForm(request.GET or None)
    context = _detail(request, pk)
    # ⚠️ 先算页码，再取那一页 —— 两次都用 `_listing` 的同一份查询。
    #    算不出来（那一场不在左边的列表里）时 `page_number` 是 None，
    #    `_page` 就退回默认的第一页，而下面 `picked` 也不会指向任何一行。
    pk = context["event"].pk
    context.update({"period": period, "in_panel": True})

    # 点击来自左边那一列时，不把列表送回去（2026-08-19）。
    #
    # 那一列已经停在正确的页上 —— 人就是从那儿点的。要变的只有一圈高亮，
    # 而那件事 app.js 在 `htmx:afterSettle` 上已经做了。响应因此从 42KB 降到 2KB。
    #
    # ⚠️ 这一段**曾经写着另一个理由**：说是「响应替换掉发起它的元素，htmx 那次
    #    swap 就落不到面板上」。那是我在没有浏览器时对「右边空白」的猜测，
    #    而真正的成因是触发器过滤器没返回布尔（见模板里那两个 `!!`）。
    #    读 htmx 源码也没有找到支持那条机制的地方：目标是在发请求**之前**就
    #    解析好的，out-of-band 换掉别处不影响它。
    #    留着这个分支是因为「少送 40KB」本身站得住，不是因为那条机制。
    #
    # ⚠️ 日程那边照旧要这一块 —— 它可能得翻到别的页去。
    if request.GET.get("from_list"):
        return render(request, "events/_schedule_detail.html", context)

    number = _page_holding(_visible_events(period), pk, EVENTS_PER_PAGE)
    context.update(_listing(request, period, page_number=number))
    context.update({
        # 左边那一列作为 out-of-band 的第二块跟着回去。
        "results_oob": True,
        # 高亮哪一行。⚠️ 算不出页码时是 None —— 模板据此**不画**高亮，
        #    而不是高亮一个碰巧在第一页的别人。
        "picked_pk": pk if number else None,
    })
    return render(request, "events/_schedule_detail.html", context)


@login_required
def event_signup(request, pk):
    """P3: join a role. Minors — and unknown birth dates — go through consent.

    2026-08-19 —— 同一个视图现在也是 Events 页右面板里那一块。

    ⭐ **整页那条路一行都没动**，这是 D24 对写操作的硬要求：没有 JS、屏幕窄到
       装不下面板、或者直接把这个 URL 贴进地址栏，走的都是原来那条 —— GET 画
       一整页表单，POST 成功之后 302 到活动详情。HTMX 只是它的快路。

    ⚠️ 两条路共用**同一个 `open_for_signup()`、同一个 `SignUpForm`、同一次
       `sign_up()`**。面板不是一条旁路：满员、取消、草稿在这里一律 404，
       和整页字节一致。分叉在这里的名字叫「从侧边栏报进了一个已经满了的活动」。

    ⚠️ 成功之后换回去的是**这场活动的详情**，不是一句「报名成功了」。人接着要
       看的是自己报到了哪个工种，而那件事详情页上那张 `mine` 的表已经在答；
       换成一块只有一句话的空面板，等于让人再点一次才能确认。

    ⚠️ 面板成功那一次**不重画左边那一列**。报名会让一场活动满员、于是列表上
       那个绿标签该变成 Full —— 这里没有跟着换。知情的取舍：换它要连着
       算一次分页（`_page_holding`）并回送 40KB，而那个标签在下一次筛选、翻页
       或刷新时自然就对了。写下来是因为「点了报名，左边标签没变」看起来像 bug，
       而它是这一行。
    """
    event = get_object_or_404(Event.objects.open_for_signup(), pk=pk)
    contact = _my_contact(request)
    if contact is None:
        raise PermissionDenied(SCOPED_DENIAL)

    # ⚠️ 读 header，不读查询串：这一块是不是画在面板里，取决于**谁在问**，
    #    而不是取决于一个可以被贴进地址栏的参数。带 `?in_panel=1` 打开这个
    #    URL 的人会拿到一块没有外壳、没有导航的碎片。
    in_panel = bool(request.headers.get("HX-Request"))

    form = SignUpForm(request.POST or None, event=event, contact=contact)
    if request.method == "POST" and form.is_valid():
        try:
            # The rule lives in sign_up(), not here: an admin registering
            # somebody from a paper list has to meet the same one.
            participation = sign_up(
                contact=contact,
                event_role=form.cleaned_data["event_role"],
                consent=form.consent(),
                # ⚠️ Passed into sign_up() rather than written afterwards: the
                #    signup and the identity on it are one act, and the version
                #    where a second call follows this one is the version where
                #    somebody eventually forgets it (D38). `.get()`, because
                #    the field is deleted from the form entirely for anybody
                #    the question does not apply to — and the service re-checks
                #    that regardless of what arrives here.
                served_as=form.cleaned_data.get("served_as") or None,
            )
        except (ConsentRequired, ValidationError) as error:
            form.add_error(None, error)
        else:
            # The confirmation is a courtesy on top of the row, not part of it:
            # a signup that was accepted must not be undone because a message
            # could not go out. confirm_signup() returns rather than raises.
            confirm_signup(participation)
            messages.success(request, "You are signed up. We have sent a confirmation.")
            if in_panel:
                # ⚠️ 上下文走 `_detail()`，和整页详情、和日程点开那一份是同一个 ——
                #    里面有 `preview` 和 `can_manage` 两个权限判断，而这是第三条
                #    通往那份正文的路。各建一份的分叉叫「草稿从侧边栏漏出去了」。
                context = _detail(request, pk)
                context.update({
                    "in_panel": True,
                    # 🔴 这一次**是**写操作，所以 messages 要跟着回去。详情那份
                    #    模板平时是读路径、不许 include messages（会把还没显示过
                    #    的消息提前消费掉），所以那里由这个标志点亮。
                    "messages_oob": True,
                })
                return render(request, "events/_schedule_detail.html", context)
            return redirect("events:event_detail", pk=event.pk)

    return render(request, _template(
        request, "events/event_signup.html", "events/_schedule_signup.html"), {
        "event": event, "form": form, "needs_consent": form.needs_consent,
        "in_panel": in_panel,
    })


@login_required
def my_participations(request):
    """Mine means mine — narrowed in the query, not in the template.

    visible_to_volunteers() as well, which is not belt and braces: every row
    here links to the detail page, and that page uses the same predicate. A
    signup an admin entered against an unpublished event would otherwise appear
    with a link that 404s — the failure this pair of predicates was written to
    prevent, arriving from the other end.
    """
    contact = _my_contact(request)
    rows = Participation.objects.none()
    if contact is not None:
        rows = (
            Participation.objects.filter(
                contact=contact,
                event_role__event__in=Event.objects.visible_to_volunteers(),
            )
            .select_related("event_role__event__ministry", "event_role__role")
            .order_by("-event_role__event__start_time")
        )
    return render(request, "events/my_participations.html", {"participations": rows})


@login_required
def participation_cancel(request, pk):
    """Withdraw. Looked up inside "mine", so somebody else's row 404s."""
    contact = _my_contact(request)
    owned = (
        Participation.objects.filter(contact=contact)
        if contact is not None
        else Participation.objects.none()
    )
    participation = get_object_or_404(
        owned.select_related("event_role__event"), pk=pk)
    if request.method == "POST":
        cancel(participation)
        messages.success(request, "Your signup has been cancelled.")
        return redirect("events:my_participations")
    return render(request, "events/participation_cancel.html", {
        "participation": participation,
    })


# --- B10: the ministry admin's pages -------------------------------------


def _managed_event(request, pk):
    """An event this account may manage, or a refusal.

    The lookup is not narrowed to their ministries: for an event that exists,
    "not yours" is the honest answer, and the message explains the scoping so
    that the next person fixes their account instead of deleting the check.
    """
    event = get_object_or_404(Event.objects.select_related("ministry"), pk=pk)
    if not can_manage_event(request.user, event):
        raise PermissionDenied(SCOPED_DENIAL)
    return event


def _scoped_events(request):
    """The events this account may see on the management side, or a refusal.

    Extracted 2026-08-05 because the full report page needs the same answer.
    ⚠️ A second copy of "which events may this account see" is the one thing on
       a report that could go wrong quietly — it is read once and believed, and
       nobody checks a total against a list they are not allowed to see.

    ⚠️ Somebody who is both — a foundation admin who also runs a ministry —
       keeps the managing view of their own ministries rather than the read-only
       view of everything. Losing the ability to publish an event because you
       were also promoted would be a strange way to be rewarded.

    ⚠️ `?scope=all` (2026-08-06) is how that person reaches the other view, and it
       **does not widen anybody's authority**. It is available only to the
       foundation tier, and it answers by handing back an empty `administered` —
       so `can_manage` is False, the page draws itself read-only, and every write
       on it still goes through `_managed_event()`, which asks
       permissions.py about the actual account and not about this parameter.
       A ministry admin who adds it to the URL gets nothing: they are not in the
       tier, so the branch is not taken.

       The 2026-08-05 decision above is not reversed by it. That decision was
       about which view somebody gets **by default**, and the default is
       unchanged; what was missing was any way at all to ask for the other one,
       which left the read-only authority the foundation tier already holds with
       no entrance for half the people who hold it (revisions.md).
    """
    if request.GET.get("scope") == "all" and in_foundation_tier(request.user):
        return (
            Event.objects.all()
            .select_related("ministry", "event_type").order_by("-start_time"),
            set(),
            True,
        )

    administered = ministry_ids_administered_by(request.user)
    # The foundation tier gets the same page over every ministry — read only.
    # ⚠️ Without this it had no entrance at all: it holds no MinistryRole, so
    #    nothing anywhere listed events for it to open. That is the same gap
    #    C0.2 closed five times over — the pages existed and nothing pointed at
    #    them.
    foundation = in_foundation_tier(request.user)
    if not administered and not foundation:
        raise PermissionDenied(SCOPED_DENIAL)

    events = Event.objects.all() if not administered else Event.objects.filter(
        ministry_id__in=administered)
    return (
        events.select_related("ministry", "event_type").order_by("-start_time"),
        administered,
        foundation,
    )


def _looking_foundation_wide(request, administered):
    """Did `_scoped_events()` hand back the whole foundation, read only?

    Asked of the **outcome** (`administered` came back empty) rather than of the
    query string alone, so a ministry admin who types `?scope=all` does not get a
    page that behaves as if the parameter had been honoured. It was not.
    """
    return request.GET.get("scope") == "all" and not administered


def _offered_ministries(administered):
    """What the filter's dropdown may offer this account.

    ⚠️ Interface, not a permission — the queryset is already narrowed. What this
       prevents is a ministry admin being offered every ministry in the
       foundation, picking one, and getting an empty list with nothing saying why.
    """
    if not administered:
        return None
    return Ministry.objects.filter(
        pk__in=administered, is_active=True).order_by("name")


@login_required
def event_manage_list(request):
    """Everything this account administers — including drafts and finished ones.

    The entrance the ministry-admin side never had. event_roles links onward to
    registrations, attendance, the report and the notice page, but the only way
    to reach event_roles was the redirect after creating an event: come back
    tomorrow and there was no route to any of it. event_list is no help, since
    it shows only what is open and upcoming — which excludes drafts, and
    excludes every event whose report anybody would actually want to read.

    2026-08-05 — the same period filter the volunteer lists have, plus a report
    over whatever it selected.

    ⭐ The report describes **exactly the events in the list**: it is handed the
       filtered queryset, not a ministry id. So both tiers run one code path,
       and the panel cannot widen past the page it is drawn on.

    ⚠️ It is computed only when asked for (`?report=1`), not on every filter.
       Thirteen figures are a dozen aggregate queries, and most of the time
       somebody changing a date is only reading the list.
    """
    events, administered, foundation = _scoped_events(request)

    if request.method == "POST":
        # Status is editable straight from the list: publishing an event and
        # closing a finished one are frequent, and both are a single choice.
        # The times are not, and are shown read-only — moving an event obliges
        # somebody to notify the volunteers, so it goes through the edit page,
        # which routes to the notice. Two fields, two different consequences.
        event = _managed_event(request, request.POST.get("event"))
        form = EventStatusForm(request.POST, instance=event)
        if form.is_valid():
            set_status(event, form.cleaned_data["status"])
            messages.success(request, f"“{event.name}” is now {event.get_status_display()}.")
        return redirect("events:event_manage_list")

    period = EventPeriodForm(
        request.GET or None, ministries=_offered_ministries(administered))
    events = period.narrow(events)
    page = _page(request, events, MANAGED_EVENTS_PER_PAGE)
    return render(request, _template(
        request, "events/event_manage_list.html",
        "events/_event_manage_results.html"), {
        "events": page,
        "page": page,
        "total": page.paginator.count,
        "period": period,
        "can_manage": bool(administered),
        # ⚠️ Passed to the filter so it survives a Filter or a Clear. A
        #    method="get" form replaces the whole query string with its own
        #    fields, so without it the foundation-wide page silently narrows back
        #    to the person's own ministries on the first click — and the list
        #    still looks perfectly normal, just shorter.
        "scope_param": "all" if _looking_foundation_wide(request, administered) else None,
        # ⚠️ Present only when asked for, and the template keys the whole panel
        #    off "is it there". `report=1` without it would draw an empty panel
        #    full of zeros, which is a different claim from "not run yet".
        #
        # ⚠️ Built from `events`, never from `page`. The report answers about
        #    the filter, not about which page you happen to be on — a figure
        #    that moved when you clicked Next would mean nothing (D27).
        "report": ministry_report(events) if request.GET.get("report") else None,
        # One unbound form, reused to draw every row's dropdown: the choices are
        # identical, and building one per event would be a form per row for no
        # gain.
        "status_form": EventStatusForm(),
    })


@login_required
def ministry_report_page(request):
    """The whole report, full width, with the event list printed under it.

    The panel beside the management list is capped to the height of that list
    and scrolls (2026-08-05 拍板) — so it needs somewhere to send you rather
    than an expander for every chart. This is that place.

    ⚠️ It shares `_scoped_events()` with the list page rather than repeating the
       scoping. A second copy of "which events may this account see" is the one
       thing on this page that could go wrong quietly: a report is read once and
       believed, and nobody double-checks a total against a list they cannot see.

    ⚠️ No pagination here, on purpose. This is the artefact somebody prints and
       hands to a board — half of it is not an artefact. The print stylesheet
       breaks the list onto its own page.

    "Save as PDF" is the browser's own print dialog (D27). The costs are stated
    there: it is two clicks rather than one, and it is worth it because the PDF
    and the page are then the **same rendering** — there is no second layout to
    keep in step, and swapping in a server-side renderer later changes only who
    rasterises this HTML.
    """
    events, administered, foundation = _scoped_events(request)
    period = EventPeriodForm(
        request.GET or None, ministries=_offered_ministries(administered))
    events = period.narrow(events)
    return render(request, "events/ministry_report.html", {
        "events": events.order_by("start_time", "pk"),
        "total": events.count(),
        "period": period,
        "can_manage": bool(administered),
        "scope": "Every ministry" if not administered else None,
        # Arrived from the panel's "Save as PDF": open the print dialog on load,
        # so that path is one click rather than two.
        "autoprint": bool(request.GET.get("print")),
        "report": ministry_report(events),
    })


@login_required
def event_create(request):
    """P2: publish an event, for a ministry this person actually runs."""
    if not ministry_ids_administered_by(request.user):
        raise PermissionDenied(SCOPED_DENIAL)

    # ⚠️ request.FILES is not optional. Without it the upload is silently
    #    dropped: the form validates, the event saves, and no image arrives.
    form = EventForm(request.POST or None, request.FILES or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        # Checked again, on the submitted value. The narrowed dropdown stops a
        # slip; this stops a forged POST. Two different jobs, both needed.
        if not can_publish_event(request.user, form.cleaned_data["ministry"]):
            raise PermissionDenied(SCOPED_DENIAL)
        event = form.save(commit=False)
        event.owner = _my_contact(request)
        event.save()
        messages.success(request, "Event created. Next, open the roles it needs.")
        return redirect("events:event_update", pk=event.pk)

    return render(request, "events/event_form.html", {"form": form, "event": None})


@login_required
def event_update(request, pk):
    """Change an event — including its time, which is what makes P6 usable.

    Without this page a ministry admin could send "the time has changed" and
    have no way to change it: EventForm was only ever reachable from
    event_create, and the admin site is closed to them by StaffOnlyAdminMiddleware.
    Event.status has the same problem — nothing else can mark an event completed.

    A move goes through services.reschedule() rather than form.save(), so that
    every path that shifts an event runs the same full_clean inside the same
    transaction. Everything else is an ordinary save.
    """
    event = _managed_event(request, pk)
    form = EventForm(request.POST or None, request.FILES or None,
                     instance=event, user=request.user)
    if request.method == "POST" and form.is_valid():
        # Re-checked on the submitted value, exactly as event_create does: the
        # dropdown is narrowed, but a POST can still name any ministry id — and
        # handing an event to a ministry you do not run is the same over-reach
        # as publishing into one.
        if not can_publish_event(request.user, form.cleaned_data["ministry"]):
            raise PermissionDenied(SCOPED_DENIAL)

        # The form answers this, not the view: it depends on what its widgets
        # can express. See EventForm.time_changed for the seconds trap.
        moved = form.time_changed()
        event = form.save(commit=False)
        if moved:
            reschedule(
                event,
                start_time=form.cleaned_data["start_time"],
                end_time=form.cleaned_data["end_time"],
            )
            # Straight to the notice, with the reason already chosen. Whoever
            # moved an event that people signed up for is one click from telling
            # them, instead of having to know that the page exists.
            messages.success(request, "Time changed. Tell the volunteers who signed up.")
            return redirect(
                f"{reverse('events:event_notify', args=[event.pk])}"
                f"?reason={EventNotification.Reason.TIME_CHANGED}"
            )
        event.save()
        messages.success(request, "Event updated.")
        return redirect("events:event_detail", pk=event.pk)

    return render(request, "events/event_form.html",
                  _edit_page_context(event, form=form))


def _edit_page_context(event, *, form=None, role_form=None, user=None):
    """Everything the merged edit page needs, from whichever view got the POST.

    2026-08-04: the event's own form and its list of roles are one page. They
    were two, and the second one had no entrance of its own — you reached
    "Roles" from a redirect after creating the event, and a day later there was
    no way back to it except the URL.

    ⚠️ Built once, here, so `event_update` and `event_roles` cannot render the
       same page from two different contexts. They both post to it, and only
       one of the two forms is bound on any given request — the other has to be
       a fresh one or the page comes back with somebody else's errors on it.
    """
    return {
        "event": event,
        # Always true here: every path into this page goes through
        # _managed_event() first. Passed explicitly rather than left out, so the
        # shared nav does not have to treat "missing" as "false".
        "can_manage": True,
        "form": form if form is not None else EventForm(instance=event, user=user),
        "role_form": role_form if role_form is not None else EventRoleForm(event=event),
        "roles": event.roles.with_signup_counts().select_related("role"),
    }


@login_required
def event_roles(request, pk):
    """P2's second half: which jobs, and how many people each one wants.

    ⚠️ No page of its own any more — a GET here goes to the edit page, which
       renders the same panel. The URL stays because templates, tests and
       anybody's bookmarks point at it, and because the roles form still posts
       here: deleting the route would have been a bigger change than merging
       the pages was.
    """
    event = _managed_event(request, pk)
    if request.method != "POST":
        return redirect("events:event_update", pk=event.pk)

    form = EventRoleForm(request.POST, event=event)
    if form.is_valid():
        form.save()
        messages.success(request, "Role added.")
        # ⭐ The plain-form path is the one that must always work: redirect, so a
        #    refresh cannot post twice. HTMX gets the list back instead — same
        #    write, same message, one fewer full page.
        if not request.headers.get("HX-Request"):
            return redirect("events:event_update", pk=event.pk)
        form = EventRoleForm(event=event)
    elif not request.headers.get("HX-Request"):
        # Errors have to survive, so this one renders rather than redirects —
        # and it renders the merged page, because that is the only page these
        # fields now live on.
        return render(request, "events/event_form.html",
                      _edit_page_context(event, role_form=form, user=request.user))

    return render(request, _template(
        request, "events/event_form.html", "events/_event_roles_swap.html"),
        _edit_page_context(event, role_form=form, user=request.user))


@login_required
def role_delete(request, pk):
    """Remove a job opened by mistake."""
    role = get_object_or_404(EventRole.objects.select_related("event__ministry"), pk=pk)
    if not can_manage_event(request.user, role.event):
        raise PermissionDenied(SCOPED_DENIAL)
    if request.method == "POST":
        role.delete()
        messages.success(request, "Role removed.")
        if request.headers.get("HX-Request"):
            # Same panel event_roles renders, so the two can never disagree
            # about what is in the list.
            return render(request, "events/_event_roles_swap.html",
                          _edit_page_context(role.event, user=request.user))
    return redirect("events:event_update", pk=role.event_id)


@login_required
def event_registrations(request, pk):
    """P4's first half: who signed up, by role — and where an identity is corrected.

    ⚠️ This page used to be read-only and asked only the read check. It now
       carries one write (D38's correction), so it asks **two** questions, the
       same split the attendance page already makes: the foundation tier may
       read any event's signups, only the ministry's own admin may change
       anything on them. Not drawing the control is interface and keeps nobody
       out — a POST arriving from anywhere at all looks identical here.

    ⚠️ The correction is one row at a time and there is deliberately no bulk
       version (D38 section 4's only 🔴). A button that reclassifies thirty
       people at once takes the evidential value of the column away in a single
       click, and that value is the entire reason the column has a
       "who said so" beside it.
    """
    event = get_object_or_404(Event.objects.select_related("ministry"), pk=pk)
    if not can_view_event_records(request.user, event):
        raise PermissionDenied(SCOPED_DENIAL)
    can_manage = can_manage_event(request.user, event)

    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied(SCOPED_DENIAL)
        participation = get_object_or_404(
            Participation.objects.filter(event_role__event=event),
            pk=request.POST.get("participation"),
        )
        # ⚠️ Judged here, not trusted from the form: the question applies to a
        #    set of people and a POST can name anybody. Asked through the same
        #    service the form and the backfill ask, so there is one answer.
        if participation.contact_id in contacts_asked_about_serving(event):
            value = request.POST.get("served_as")
            if value in Participation.ServedAs.values:
                set_served_as(
                    participation, value,
                    declared_by=Participation.DeclaredBy.ADMIN,
                )
                messages.success(
                    request,
                    f"Recorded. {participation.contact} will see on their signups "
                    "page that an admin set this.",
                )
        return redirect("events:event_registrations", pk=event.pk)

    roles = event.roles.with_signup_counts().select_related("role").prefetch_related(
        Prefetch(
            "participations",
            queryset=Participation.objects.select_related("contact").order_by("contact"),
        )
    )
    return render(request, "events/event_registrations.html", {
        "event": event,
        # Drives the shared event nav: Edit and Notify are drawn only for
        # somebody who can actually open them.
        "can_manage": can_manage, "roles": roles,
        # ⚠️ One query for the whole page, not one per row. The identity
        #    question applies to the ministry's own people and to nobody else,
        #    and an outside volunteer's row must not offer a control that would
        #    be refused — D38 section 5's "the cost falls only on the people it
        #    is genuinely ambiguous for".
        "asked_about_serving": contacts_asked_about_serving(event),
        "served_as_choices": Participation.ServedAs.choices,
    })


@login_required
def event_attendance(request, pk):
    """P4's second half: sign people in and out, or enter hours from paper.

    The minors and their emergency numbers are shown here because this is the
    page somebody has open when an ankle gets twisted. That is dialling a
    number on the spot; it is not the same thing as reaching a guardian before
    the event, which goes through consent_email / consent_phone (B11).
    """
    # ⭐ Two different questions, asked separately (2026-08-05). The foundation
    #    tier may read this page for any event; only the ministry's own admin
    #    may change anything on it.
    #
    # ⚠️ The POST check is the boundary. Not drawing the buttons is interface,
    #    and interface keeps nobody out — a form posted from anywhere at all
    #    arrives at this view with the same shape.
    event = get_object_or_404(Event.objects.select_related("ministry"), pk=pk)
    if not can_view_event_records(request.user, event):
        raise PermissionDenied(SCOPED_DENIAL)
    can_manage = can_manage_event(request.user, event)

    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied(SCOPED_DENIAL)
        participation = get_object_or_404(
            Participation.objects.filter(event_role__event=event),
            pk=request.POST.get("participation"),
        )
        action = request.POST.get("action")
        if action == "check_in":
            check_in(participation)
        elif action == "check_out":
            check_out(participation)
        elif action == "absent":
            # ⚠️ The refusal is shown, not swallowed. mark_absent() declines when
            #    the row already carries hours or a check-in, and a button that
            #    quietly does nothing reads as a broken page — the person clicks
            #    it again, then goes looking for the row somewhere else.
            try:
                mark_absent(participation)
            except TurnedUp as error:
                messages.error(request, "; ".join(
                    message for messages_ in error.message_dict.values()
                    for message in messages_
                ))
        elif action == "hours":
            hours_form = HoursForm(request.POST)
            if hours_form.is_valid():
                # The paper-sheet path: no timestamps, just a number. Same
                # field, because there is only one authoritative value.
                record_hours(participation, hours_form.cleaned_data["hours"])
        elif action == "undo":
            # ⚠️ Its own action, never folded into "absent". "I clicked the
            #    wrong row" and "they did not come" are different facts, and
            #    one button doing both would put every mis-click into the
            #    no-show rate. See services.undo_attendance.
            undo_attendance(participation)
        elif action == "clear_hours":
            # ⚠️ Empty is not zero. Until this existed, an hours figure typed by
            #    mistake could only be corrected to a wrong-but-plausible 0.
            clear_hours(participation)
        # ⭐ HTMX swaps just this person's row; the plain form path redirects, as
        #    it always did. This is the page the rule was written for — checking
        #    forty people in one at a time reloads the whole table forty times.
        if request.headers.get("HX-Request"):
            participation.refresh_from_db()
            return render(request, "events/_attendance_row_swap.html", {
                "row": participation,
                "hours_form": HoursForm(),
                "scheduled_hours": scheduled_hours(event),
                "can_manage": True,
            })
        return redirect("events:event_attendance", pk=event.pk)

    rows = (
        Participation.objects.filter(event_role__event=event)
        .notifiable()
        .select_related("contact", "event_role__role")
        .prefetch_related("contact__emergency_contacts")
        .order_by("event_role__role__name", "contact")
    )
    return render(request, "events/event_attendance.html", {
        "event": event,
        "participations": rows,
        "hours_form": HoursForm(),
        "can_manage": can_manage,
        # What the box starts at for somebody with no hours yet. Computed in
        # services, never here — this is date arithmetic, and there is a grep
        # guard on views doing any (D18).
        "scheduled_hours": scheduled_hours(event),
    })


@login_required
def event_report(request, pk):
    """R3–R8 for one event. Every number arrives from services.py.

    ⚠️ Reads, so it asks the read check — not `_managed_event()`, which is the
       write gate this page used to go through. The foundation tier may read
       any event's report without being able to touch the event.
    """
    event = get_object_or_404(Event.objects.select_related("ministry"), pk=pk)
    if not can_view_event_records(request.user, event):
        raise PermissionDenied(SCOPED_DENIAL)
    return render(request, "events/event_report.html", {
        "event": event,
        "can_manage": can_manage_event(request.user, event),
        "summary": event_summary(event),
        "staff": ministry_staff_participation(event),
    })


@login_required
def event_notify(request, pk):
    """P6: tell everybody signed up that the event changed.

    can_manage_event(), the same check as the attendance page — putting a
    message in front of everybody who signed up is a write, not a read.

    GET is the preview, and the three groups on it are the point: who is being
    told directly, whose *guardian* is being told instead, and who cannot be
    reached at all. That third group is the one thing about this page that
    could fail silently, so it is shown even when it is empty.
    """
    event = _managed_event(request, pk)
    recipients, unreachable = resolve_recipients(event)

    if request.method == "POST":
        form = NotifyForm(request.POST)
        if form.is_valid():
            notification = notify_event_change(
                event,
                reason=form.cleaned_data["reason"],
                message=form.cleaned_data["message"],
                sent_by=request.user,
            )
            # ⚠️ Counted off the record that was just written, not off the
            #    recipients resolved above: those two numbers are the same only
            #    when every message was accepted, and the interesting day is the
            #    one where they are not.
            told = notification.recipients.count()
            failed = notification.failed.count()
            if failed:
                messages.warning(
                    request,
                    f"Notified {told}; {failed} could not be sent and "
                    f"{len(unreachable)} could not be reached. "
                    "The names are under “Already notified”, below.",
                )
            else:
                messages.success(
                    request,
                    f"Notified {told}; {len(unreachable)} could not be reached.",
                )
            return redirect("events:event_notify", pk=event.pk)
    else:
        reason = request.GET.get("reason") or EventNotification.Reason.TIME_CHANGED
        form = NotifyForm(initial={
            "reason": reason,
            "message": default_message(event, reason),
        })

    return render(request, "events/event_notify.html", {
        "event": event,
        # Always true: this view is gated on can_manage_event above.
        "can_manage": True,
        "form": form,
        "recipients": [r for r in recipients if not r.is_guardian],
        "guardian_recipients": [r for r in recipients if r.is_guardian],
        "unreachable": unreachable,
        # "Last notified 5 minutes ago" is the only thing standing between a
        # shaky connection and two identical notices (D22, cost 3).
        # prefetch: the template names whoever a notice failed to reach, and
        # without this that is one query per notice plus one per name.
        "previous": event.notifications.prefetch_related(
            "failed__contact")[:5],
    })


# --- D28: the QR check-in ------------------------------------------------
#
# ⭐ The whole point of this pair of views is that "you were standing in front
#    of the screen" and "you are logged in as Maria" are two separate questions.
#    The scan answers the first inside 90 seconds and hands the answer to the
#    session; the login answers the second at whatever speed a phone keyboard
#    allows. Merging them back into one view — the obvious simplification —
#    reintroduces the failure D28 was written to remove: every volunteer's first
#    ever check-in ends with an expired token and a walk back to the iPad.


def checkin_scan(request, token):
    """The URL inside the QR code. Verifies presence, then gets out of the way.

    ⚠️ Deliberately **not** @login_required, and that is not a hole: it writes
       nothing. It turns a token into a session credential and redirects, and
       the credential names an event and a direction, not a person. Who that
       person is gets decided by the login the redirect leads to.

    ⚠️ A GET, and it must therefore stay free of writes to the database. Link
       previewers in messaging apps, browser prefetch and corporate URL scanners
       all fetch this address without a human touching it — the original design
       recorded attendance here, which means forwarding the link into any chat
       window would check somebody in.
    """
    try:
        event_id, mode = verify_checkin_token(token)
    except InvalidCheckInToken as error:
        return render(request, "events/checkin_refused.html", {
            "reason": "; ".join(error.messages),
        }, status=400)

    request.session[CHECKIN_CREDENTIAL_KEY] = issue_credential(event_id, mode)
    return redirect("events:checkin_confirm")


@login_required
@ratelimit(key="user", rate="30/m", method="POST", block=False)
def checkin_confirm(request):
    """What the volunteer actually taps. One row, one write, safe to repeat.

    ⚠️ The credential is read from the session, never from the form. Putting the
       token in a hidden field is the tempting shortcut, and it quietly undoes
       the split above: the 90-second window would then have to survive the
       login, which is the thing it was moved out of the way of.
    """
    contact = _my_contact(request)
    try:
        event_id, mode = read_credential(request.session.get(CHECKIN_CREDENTIAL_KEY))
    except CredentialExpired as error:
        return render(request, "events/checkin_refused.html", {
            "reason": "; ".join(error.messages),
        }, status=400)

    event = get_object_or_404(
        Event.objects.visible_to_volunteers().select_related("ministry"), pk=event_id)
    targets = scan_targets(contact, event, mode) if contact else None

    if targets is None or not targets.any_signup:
        return render(request, "events/checkin_refused.html", {
            "event": event,
            "reason": "You are not signed up for this event.",
            # ⚠️ A link, never a signup. Creating the row here would walk past
            #    sign_up()'s two gates, and the state on the other side of them
            #    is a minor recorded as present with nobody to call.
            "action_url": reverse("events:event_signup", args=[event.pk]),
            "action_label": "Sign up for this event",
        }, status=400)

    if request.method == "POST":
        if getattr(request, "limited", False):
            return render(request, "events/checkin_refused.html", {
                "event": event,
                "reason": "That was a lot of taps. Wait a moment and try again.",
            }, status=429)
        chosen = request.POST.get("participation")
        # Membership of these lists is the authorisation: both are already
        # narrowed to this contact and this event.
        #
        # ⚠️ `done` is in the set on purpose, and leaving it out was a real bug
        #    caught by its test. The commonest second POST comes from a page
        #    that was already open when the first one succeeded — a slow network
        #    and an impatient thumb — and by then the row has moved from pending
        #    to done. Accepting only pending answers that person with a 404,
        #    which reads as "the site is broken" at the exact moment their
        #    check-in has in fact worked. apply_scan() reports it as changed=False
        #    and the page says "you already checked in at 9:03".
        #
        # ⚠️ `needs_check_in` stays out. Those rows would also be a no-op, but
        #    the honest answer for them is "check in first", not "you already
        #    checked out".
        allowed = {str(row.pk): row for row in targets.pending + targets.done}
        if chosen not in allowed:
            raise Http404
        try:
            participation, changed = apply_scan(
                chosen, contact=contact, event_id=event.pk, mode=mode)
        except ConsentRequired as error:
            # ⚠️ Shown, not swallowed, and not a 500. The person this refusal
            #    concerns is standing in a hall holding a phone, and the fix is
            #    on their profile page — so say which fix.
            return render(request, "events/checkin_refused.html", {
                "event": event,
                "reason": "; ".join(
                    message for group in error.message_dict.values() for message in group
                ),
                "action_url": reverse("accounts:profile"),
                "action_label": "Go to my profile",
            }, status=400)
        del request.session[CHECKIN_CREDENTIAL_KEY]
        messages.success(request, checkin_result_message(participation, mode, changed))
        return redirect("events:my_participations")

    return render(request, "events/checkin_confirm.html", {
        "event": event,
        "mode": mode,
        "checking_in": mode == CHECK_IN,
        "targets": targets,
        # One row is the ordinary case and gets no question; several is rare and
        # is the only case where anybody is asked anything (D28 五).
        "only": targets.pending[0] if len(targets.pending) == 1 else None,
    })


@login_required
def checkin_display(request, pk):
    """The page that lives on the iPad. Draws nothing itself — the JS does.

    Read-only as far as the database is concerned, so the permission is the
    manage one purely because of what it grants access to: whoever can open this
    page can mint check-in codes for everybody at the event.
    """
    event = _managed_event(request, pk)
    return render(request, "events/checkin_display.html", {
        "event": event,
        "can_manage": True,
        # ⚠️ Computed here, once, at page load — never re-derived in the
        #    browser. An iPad that flipped from Check in to Check out on its own
        #    halfway through would turn the queue in front of it into check-outs
        #    with nothing on screen saying so. From here on the admin decides.
        "default_mode": default_checkin_mode(event),
        "closed_message": window_message(event),
        "token_url": reverse("events:checkin_token", args=[event.pk]),
    })


@login_required
@ratelimit(key="user", rate="60/m", block=False)
def checkin_token(request, pk):
    """A fresh code for the display. JSON, and the one endpoint that must not leak.

    ⚠️ This permission check **is** the scheme. Without it any signed-in
       volunteer fetches a live token from their sofa and books themselves in;
       every rotating-code measure above it becomes decoration. It is the first
       statement in the function for that reason.
    """
    event = _managed_event(request, pk)
    if getattr(request, "limited", False):
        return JsonResponse({"error": "Too many requests."}, status=429)
    mode = request.GET.get("mode")
    if mode not in MODES:
        raise Http404
    if not window_is_open(event):
        return JsonResponse({"error": window_message(event)}, status=409)
    token, expires_at = issue_with_expiry(event.pk, mode)
    return JsonResponse({
        # ⚠️ The whole URL is assembled here and the browser only draws it.
        #    A script that built this address from parts would be a second
        #    definition of the route, and the QR is the one place where being
        #    subtly wrong produces a code that scans perfectly and goes nowhere.
        "url": request.build_absolute_uri(
            reverse("events:checkin_scan", args=[token])),
        "expires_at": expires_at,
    })
