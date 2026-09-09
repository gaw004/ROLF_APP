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
from django.db.models import Prefetch
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import urlencode
from django.utils.text import get_text_list
from django_ratelimit.decorators import ratelimit

from core.pagination import page_holding, page_of
from org.models import Ministry
from org.permissions import (
    SCOPED_DENIAL,
    in_foundation_tier,
    administers_one_of,
    can_manage_event,
    can_publish_event,
    event_access,
    ministry_ids_administered_by,
)

from . import schedule, tokens
from .forms import (
    FILTER_PARAMS,
    EventForm,
    EventPeriodForm,
    EventRoleForm,
    EventStatusForm,
    HoursForm,
    NotifyForm,
    SignUpForm,
)
from .models import (
    SERVED_AS_EXPLANATIONS,
    Event,
    EventNotification,
    EventRole,
    Participation,
    Session,
    askable_served_as,
)
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
    NoHoursHere,
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
    prefillable_hours,
    resolve_recipients,
    signups_asked_about_serving,
    hours_recorded_against,
    audience_gaps,
    signups_left_outside,
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


#: 「你刚才在看的是哪一份列表」—— 筛选卡上的每一格，加上页码（2026-09-08）。
#:
#: ⭐ **一份名单，两个使用方**：通向整页详情的链接把它带走（列表行、日程卡片、
#:    面板右下角那颗圆球），`_back_link()` 再把它带回来。各写一份的话，退回去的
#:    那一页迟早和走的时候不是同一份 —— 而那件事在屏幕上读起来是「筛选自己没了」。
#:
#: ⚠️ 故意**不含 `from`**，尽管日程窗口就叫这个名字。`_back_link()` 把 `from` 当成
#:    来路标记读（`?from=mine` / `?from=manage`），而日程那个隐藏字段的值是一个
#:    `Y-m-d` 日期 —— 带上去只会掉进那张白名单的兜底分支。今天无害，但两个意思
#:    共用一个参数名本来就是一颗雷，别再往上堆。
#:
#: ⚠️ 也不含 `report` / `print` / `from_list` 这类**动作**参数：它们说的是
#:    「这一次请求要干什么」，不是「你在看哪一份列表」。带回去就会让一条返回链接
#:    重新触发一次那个动作。
#:
#: ⚠️ 筛选那几格的名字**不在这里抄第二遍**，从 `EventPeriodForm.FILTER_PARAMS`
#:    取。抄一遍的代价这一批自己付过：加 `nature` 时两个文件都得改，而漏掉这边
#:    不报任何错 —— 表现只是那一格点进活动再返回时自己没了。
#:
#: ⚠️ `panel`（2026-09-09）**不在 `FILTER_PARAMS` 里**，故意的：那个常量是这张
#:    表单的**字段名**（守卫 `test_the_filter_names_are_declared_once` 钉着它和
#:    真正长出来的字段一致），而 `panel` 不是一个框，是「右边正开着什么」。
#:    它属于这一条名单，因为这一条问的是「你刚才在看的是哪一份列表」—— 而那份
#:    列表右边开着一块面板，也是答案的一部分。见 `_open_panel()`。
LIST_STATE = (*FILTER_PARAMS, "page", "panel")


def _list_state(request, page=None, panel=None):
    """筛选串，形如 `?q=food&page=2&panel=7`；一格都没填就是空串。

    ⚠️ 路径永远由 `reverse()` 给，这里只拼**查询串**，而且每个值都过
       `urlencode`。`_back_link()` 那段「绝不把查询串里的东西当 URL 用」仍然成立：
       用户控得住的部分从头到尾都只是查询串里的一个值，不是路径的任何一段。

    ⚠️ `page` 收一个 `Page` 而不是一个数：从日程上点一张卡时，左边那一列会被翻到
       **另一页**（`page_holding` 算出来的），而 `request.GET["page"]` 还停在旧的
       那个。给了就以真实渲染出来的那一页为准。第一页不写进去 —— 一条
       `?page=1` 的链接和不写是同一页，而它会让每一个 URL 都长出一截噪音。

    ⚠️ `panel` 同理，而它的时间差更刁：面板是 HTMX 换进来的，而「地址栏里记下
       这一场」是**浏览器**在同一下里做的（`hx-replace-url`）—— 服务端渲染那一份
       响应时，`request.GET` 里还没有 `panel`。不给这个参数的话，面板里那颗圆球
       的链接就少一截，人从它进整页详情再返回，面板是关着的 —— 也就是这一整批
       要修的那件事，在最主要的那条路上原样复发。
    """
    values = {key: request.GET[key] for key in LIST_STATE
              if request.GET.get(key, "").strip()}
    if page is not None:
        values.pop("page", None)
        if page.number > 1:
            values["page"] = str(page.number)
    if panel is not None:
        values["panel"] = str(panel)
    return f"?{urlencode(values)}" if values else ""


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
    # 🔴 **带着筛选串回去**（2026-09-08），三支都带。在此之前这条链接指向一张
    #    **空筛选**的列表：一个人筛到「食物银行 · 十月」、翻到第 3 页、点开一场
    #    活动，再点这条返回，回到的是全部活动的第 1 页 —— 他刚才做的三件事一起
    #    没了，而页面本身完全正常。适老化那一批把这条路变成了主路（面板里那颗
    #    圆球就通向这一页），所以它必须退得回原处。
    #
    # ⚠️ **不挑分支。** `_list_state()` 是一张白名单，一格没填就返回空串 ——
    #    于是它在 My Signups 上恒为无害（那一页没有筛选卡也不翻页），在管理列表
    #    上恰好正确（同一张筛选卡、同一套翻页，`nature` 那一格不存在因而被忽略）。
    #    只给其中一支带的话，「从这一页返回记得住、从那一页返回记不住」会是下一个
    #    人眼里的 bug，而它没有任何理由可以被读出来。
    state = _list_state(request)
    marker = request.GET.get("from")
    if marker == "mine":
        return reverse("events:my_participations") + state, "My Signups"
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
            return reverse("events:event_manage_list") + state, label
    return reverse("events:event_list") + state, "Events"


#: How many events one page of each list holds (2026-08-05, 改成同一个数
#: 2026-09-09).
#:
#: **One number for every list in the project.** 「admin 的 event manage 和
#: notice manage 翻页都要 50 行才行，改成和 event 一样的条数」—— 也就是
#: `EVENTS_PER_PAGE` 这个数，四个列表从此对齐。
#:
#: ⚠️ 这**推翻了 2026-09-03 同一个人的决定**（那次的原话是「加上翻页，每页 50 条，
#:    跟 events 一样」，见 revisions.md 第五十六节）。当时「跟 events 一样」指的是
#:    管理列表那个 50，这次指的是志愿者列表那个 20 —— 同一句话，两个所指。
#:
#: ⚠️ 那句话**当面问清楚了才改的**（2026-09-09）：「都要 50 行才行」这半句单独读
#:    是「保持 50」，而两个管理列表当时本来就是 50，那样这次改动等于什么都不做。
#:    把两种读法摆出来问过，答复是四个列表都用 20。写在这里是因为下一个人会
#:    重新读到那句话，并且有很大机会得出相反的结论 —— 这条注释就是给那个人的。
#:
#: ⚠️ 这里原来写着一段论证：「两个数，因为行高不同 —— 志愿者列表是带缩略图的卡片，
#:    管理列表是表格行；五十张卡片是很长的一页，五十行表格只有一屏多一点」。
#:    那段话**删掉而不是留着**：论证本身没有错，但它得出的结论已经不是现在的行为，
#:    而一段和代码打架的理由比没有理由更贵 —— 下一个人会以为代码错了。
#:    要翻旧账去 revisions.md，那里记着它是被谁、在哪一天、因为什么换掉的。
#:
#: ⚠️ 写成字面量 20，**不写成 `MANAGED_EVENTS_PER_PAGE = EVENTS_PER_PAGE`**：
#:    别名的意思是「这两个数从此必须相等」，而拍板的是「现在都用 20」。
#:    这四个列表仍然是四页给不同的人看的东西，把它们焊死是这次没有人要求的决定。
#:    `notices` 那两个同理，而且它还多一条理由：它不能 import `events`（D41）。
EVENTS_PER_PAGE = 20
MANAGED_EVENTS_PER_PAGE = 20


# ⚠️ 翻页那两个函数 2026-09-03 搬去了 `core/pagination.py`（`page_of` /
#    `page_holding`）。搬家的理由和 `Audience` 去 `org` 那次一样：`notices`
#    的两页也要翻页，而让它为一件和活动无关的事去 import `events` 是画错的
#    依赖（D41 第四节）。排序那条规矩（结尾必须是唯一列）跟着一起走了，
#    两个函数仍然在同一个文件里 —— 它们必须用一模一样的排序。


def _my_contact(request):
    """The Contact behind the logged-in account, or None.

    None is a normal state — a superuser has no Contact by design — so pages
    cope with it rather than raising, the same rule permissions.py follows.
    """
    return getattr(request.user, "contact", None)


def _volunteer_period(request):
    """志愿者那三页共用的那一张筛选表（2026-09-08 抽出来）。

    🔴 **抽出来的理由不是少打字，是那三处必须一字不差。** 左边的列表、右边的
       日程、以及点开一场活动时顺手翻页的那一次，各自 `EventPeriodForm(...)`
       一遍；而 `audience=` 决定「按角色种类筛」那一格存不存在。少传一处的表现
       是**同一张筛选卡画出两个答案** —— 列表筛过了、日程没有（或者反过来），
       而两边都渲染成功。
       `_visible_events()` 和 `_schedule()` 共用 `period.narrow()` 正是为了防
       这件事，而在此之前构造这一步是唯一还各写各的一环。
       守卫：events.tests.RoleKindFilterTests.test_the_schedule_is_narrowed_too

    ⚠️ 管理列表页和报表页**不走这里**：那两页要传 `ministries=`、而且不传
       `audience=`（于是那一格根本不存在，伪造一个 `?nature=helping` 过去什么
       都不会发生）。它们要答的是另一个问题，共用一个构造器只会让这个函数长出
       一串参数。
    """
    return EventPeriodForm(request.GET or None, audience=_my_contact(request))


# --- B9: the volunteer's own pages --------------------------------------


@login_required
def event_list(request):
    """P3: what is on, from today forward.

    visible_to_participants() + from_today() (2026-08-17). It used to be
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
    contact = _my_contact(request)
    period = _volunteer_period(request)
    return render(request, _template(
        request, "events/event_list.html", "events/_event_list_results.html"), {
        "period": period,
        # 右边那块面板开着没有、开的是哪一场（2026-09-09）。见 `_open_panel()`。
        **_open_panel(request),
        **_listing(request, period, contact),
        # 右边那块日程。⚠️ 它和上面那个 `events` 是**两个不同的集合**，故意的：
        #    列表是分页的二十条，日程是那几天的全部。两边共用的只有筛选。
        **_schedule(request, period, contact),
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


def _open_panel(request):
    """`?panel=<pk>` —— 进这一页的时候右边那块面板就开着，里面是那一场活动。

    🔴 **它存在的理由是「回去时不是同一个页面」**（2026-09-09 实测）。
       从面板里那颗圆球进整页详情，再点那一页的「← Events」，回来的是一张
       **面板关着**的列表：筛选和页码都在（`LIST_STATE` 带回来了），而「右边开着
       什么」从来没有进过 URL，所以服务端答不出来。浏览器的后退键反而是对的 ——
       它走 bfcache，把整页原样端回来 —— 于是同一页上两条「回去」的路给出两种
       结果，而这正是这个项目一直在防的那种不一致。

       修的不是那条返回链接，是**面板状态只活在浏览器内存里**这件事本身。
       进了 URL 之后白拿三件：刷新不丢面板、这一页可以收藏、链接可以发给同事。

    🔴 **拿不到就当没写，不是 404。** 一个过期的 `?panel=` —— 活动删了、改回草稿
       了、或者链接被转给了看不见它的人 —— 不许把**整张列表页**打成 404。那读起来
       是「站坏了」，而实际上只是右边那一块开不出来。
       ⚠️ 吞的是 `Http404`，不是所有异常：`_detail()` 里别的错该照常炸出来。

    ⭐ 权限走 `_detail()`，和整页、和 HTMX 那条面板路径**同一个**。这是第三条通向
       同一块内容的路径，而新开的取数路径正是权限最容易漏掉的地方 —— 草稿在这里
       同样 404（然后被上面那一条吞成「不开面板」，对没权限的人和不存在完全一样）。

    ⚠️ `panel` **不进 `EventPeriodForm`**：它不是一个筛选框，是「右边正开着什么」。
       混进那张表单就会出现在 `FILTER_PARAMS` 里，于是管理列表页也长出一个它根本
       没有面板去开的参数。

    ⚠️ HTMX 那条路上**不做**：片段请求换的是 `#event-results`，面板在那块外面、
       原样待着。照做一遍就是每敲一个筛选字符白取一次活动详情。
    """
    pk = request.GET.get("panel", "")
    # ⚠️ `isdigit()` 不是多余的校验，它挡的是一个 **500**：`get_object_or_404` 只
    #    接得住「查不到」，而 `pk="abc"` 在字段层就抛 `ValueError`（Django 的
    #    「expected a number」），根本走不到 404 那一步。一个手改过 URL 的人、
    #    或者一条被聊天软件截断的链接，会把整张列表页变成一个错误页。
    if not pk.isdigit() or request.headers.get("HX-Request"):
        return {}
    try:
        context = _detail(request, pk)
    except Http404:
        return {}
    return {
        **context,
        # 模板据此知道「这一块要画出来、而且 Alpine 一开始就是开着的」。
        # ⚠️ 单独一个键，不是让模板去判 `event` 在不在：`_detail()` 的上下文里
        #    有十几个键，而整页详情那边也叫 `event` —— 用它当开关，改天谁给这一页
        #    加一个同名变量，面板就会自己打开。
        "panel_open": True,
        # 左边那一行的高亮。⚠️ 和面板是同一件事的两半：高亮的意思只有一个 ——
        #    「右边正开着的是这一场」—— 所以它不能由别处算。
        "picked_pk": context["event"].pk,
        # 面板里那一份详情画的是「在面板里的样子」（不画返回链接、活动名降 h2、
        # 报名按钮就地开表单）。⚠️ 少了它，服务端渲染出来的这一份会和 HTMX 换进来
        # 的那一份**长得不一样**，而两边都渲染成功。
        "in_panel": True,
    }


def _visible_events(period, contact):
    """左边那一列列的是什么：筛完、排好，还没分页。

    ⚠️ `contact` 是 L3（2026-08-26 加）。**两道门，不是一道**：
       `visible_to_participants()` 答「发布了没有」，`for_audience()` 答
       「是不是给他看的」。在此之前第二个问题没有任何地方在答 ——
       任何登录用户看得见任何一场已发布的活动。

    ⚠️ 单独一个函数，因为「第几页」要问它两次（先数出那一场排在第几，再取那
       一页），而两次必须是**同一个查询** —— 各写一遍的话，两份筛选迟早不一样，
       于是「跳到那一页」偶尔跳到相邻的一页，看起来像随机失灵。
    """
    return period.narrow(
        Event.objects.visible_to_participants()
        .for_audience(contact)
        .from_today()
        # ⚠️ 每一行都要问「满了没」来决定那枚标签画不画成链接（2026-08-19）。
        #    不加这个注解的话那是**每行一次查询** —— 一页二十行，在全站被打得
        #    最多的一页上。判据本身没有在这里重写，见 `with_capacity()`。
        .with_capacity()
        .select_related("ministry")
        .order_by("start_time")
    )


def _listing(request, period, contact, page_number=None):
    """左边那一列的上下文。event_list 和日程点开的那一次共用。

    ⚠️ 只返回**模板真的要用的**东西。未分页的那个查询集不在里面 —— 需要它的
       是 `page_holding`，而那是视图的事；塞进上下文就是把一个没人渲染的
       完整集合递给模板，正是本文件第一条规矩防的那件事。
    """
    page = page_of(request, _visible_events(period, contact), EVENTS_PER_PAGE,
                 number=page_number)
    return {
        "events": page,
        "page": page,
        # R1, in the plainest possible form: how many, in the window they asked
        # for. ⚠️ The whole filtered set, not this page — "20 events" under a
        # filter that matched 180 would answer a question nobody asked.
        "total": page.paginator.count,
        # 每一行通向整页详情的那条链接要带上的筛选串（2026-09-08）。
        # ⚠️ 传 `page` 而不是让它去读 `request.GET["page"]` —— 从日程点过来的那一次
        #    请求里，这一列被翻到了另一页，见 `_list_state` 的 docstring。
        "list_state": _list_state(request, page),
    }


def _schedule(request, period, contact):
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
    # ⚠️ `visible_to_participants()`，和列表同一道门 —— 日程不是一条绕过草稿的
    #    旁路。⚠️ 但**不带 `from_today()`**，理由 2026-08-18 换了一个：原来是
    #    「那条按 start_time 切会切掉跨夜的活动」，而它现在按 end_time 切，
    #    不再切掉了。剩下的理由是这一条：日程要的是**和窗口相交**的活动，
    #    而窗口可以翻到下个月 —— 再叠一道「今天起」只会把它自己的下界抄第二遍。
    # ⚠️ `for_audience()` 和左边那一列同一道门。少了它就是「列表里没有、
    #    日程上画着」—— 而那是同一份筛选画出来的两个答案。
    # ⚠️ `prefetch_related("sessions")`：日程把一门课画成它的各讲（schedule.
    #    occurrences），而那要读每一场的 sessions。少了它是一场一次查询，
    #    在系统里最常打开的这一页上，且它不报错 —— 只是变慢。
    events = period.narrow(
        Event.objects.visible_to_participants()
        .for_audience(contact).select_related("ministry")
        .prefetch_related("sessions"))
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
    contact = _my_contact(request)
    period = _volunteer_period(request)
    return render(request, "events/_schedule.html", {
        "period": period,
        # 箭头翻页要顺手把筛选卡里那个隐藏的 `from` 也改掉，否则下一次筛选会
        # 把窗口拽回起点 —— 见 _period_filter.html 里那一段。
        "schedule_partial": True,
        # 日程卡片上那条通向整页详情的 `href` 要带的筛选串。
        #
        # ⚠️ 在这里给，**不放进 `_schedule()`**：那个函数的 docstring 写着它
        #    「只做取数和夹紧」，而且整页那一次的 `list_state` 由 `_listing()` 出
        #    （它知道左边那一列真的停在第几页）。放进 `_schedule()` 就会有两份，
        #    而后铺开的那一份会盖掉带页码修正的那一份。
        "list_state": _list_state(request),
        **_schedule(request, period, contact),
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
    """visible_to_participants(), so a full or finished event still opens.

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

    ⚠️ Keyed on membership of VISIBLE_TO_PARTICIPANTS, never on `== DRAFT`. The
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
        Event.objects.select_related("ministry").prefetch_related("sessions"),
        pk=pk)
    when_headline, when_detail = schedule.when_line(event)
    contact = _my_contact(request)
    preview = event.status not in Event.VISIBLE_TO_PARTICIPANTS
    # L3 (2026-08-26): not for them is the same kind of answer as not published.
    #
    # ⚠️ **404, never 403**, matching the draft branch below it and for the same
    #    reason: a 403 says "there is an event at this id and it is not for you",
    #    which is exactly the sentence a staff-only event exists to withhold.
    #    The foundation tier keeps its back door — it can already open this
    #    event's signups, attendance and report, so making the event page the
    #    one thing it cannot see would be a rule nobody could guess.
    #
    # ⚠️ Asked with `.filter(pk=…).exists()` rather than by re-fetching, so this
    #    stays one extra query and the row above is still the one rendered.
    for_them = Event.objects.filter(pk=pk).for_audience(contact).exists()
    # ⚠️ Asked once and kept: it decides the 404 below **and** how much of the
    #    roles table this person is shown. Two calls would be two answers to one
    #    question the day somebody edits one of them.
    #
    # ⚠️ Both answers in one call, because until L2.4 the read check ran only
    #    when the 404 branch needed it and the write check ran always — asking
    #    each separately now means the grant table is read twice on every render
    #    of the most-hit page in the system. org.permissions composes them; the
    #    view must not, or the two ways in stop being one policy.
    can_manage, may_view_records = event_access(request.user, event)
    # 🔴 The third way in, and it is not a loophole in the rule above — it is
    #    the rule 06-roadmap L2.2 states in as many words: **narrowing takes
    #    away discovery, never a row you already hold.** An audience edited
    #    after the fact, or a post that ended between signing up and the day
    #    itself, must not make somebody's own event page disappear.
    #
    #    Participation.mine() has always had this half right — it deliberately
    #    does not ask for_audience(), and the guard whitelist in core/tests.py
    #    names it with this exact reason. This page did not, so the row stayed
    #    listed on /me/participations/ and in the dashboard's "coming up" and
    #    linked to a 404. Both halves say the same thing from 2026-09-08.
    #
    # ⚠️ Only asked when the audience says no, so the most-hit page in the
    #    system does not pay for a query whose answer it already has.
    #
    # ⚠️ Deliberately **not** extended to `preview`. A draft is not a row
    #    anybody holds yet, and mine() already excludes drafts — so there the
    #    listing and the page agree without this, and adding it would make a
    #    withdrawn draft readable to whoever had signed up before it went back.
    holds_a_signup = (
        contact is not None
        and not for_them
        and Participation.objects.filter(
            event_role__event_id=pk, contact=contact).exists()
    )
    if (preview or not (for_them or holds_a_signup)) and not may_view_records:
        # Deliberately indistinguishable from "no such event" — see above.
        raise Http404("No event matches the given query.")

    # L2 (2026-08-29). Two sets, and they are two because they answer two
    # questions:
    #
    #   · `to_join`  — the places this person may actually take. It is what the
    #     signup form's dropdown is narrowed to, so the Sign up button below is
    #     drawn from the same set the button leads to. Drawn from anything else,
    #     a person with no eligible role gets a button to a required dropdown
    #     with nothing in it.
    #   · `roles`    — what the table shows. The same set, **except** for
    #     somebody who may read this event's records: they see every role.
    #
    # 🔴 That exception breaks "the table and the dropdown are one set", which is
    #    how L2.4 was first written, and it is a decision (2026-08-29) rather
    #    than a slip. A ministry admin who has just opened three roles and finds
    #    two of them missing from the event page has no way to tell that from a
    #    bug, and they can already see all three on the signups page. The cost is
    #    that the page now says two different things to two kinds of viewer, so
    #    the page **says so** — see `sees_every_role` in the template.
    #
    # ⚠️ One spelling of "a page of role rows", narrowed or not. Written out
    #    twice, an annotation or a prefetch added for the table would land on
    #    one and not the other, and the records-reader's rows would quietly stop
    #    matching everybody else's — with the template unable to tell.
    # ⚠️ The prefetch is for the "Open to" column, drawn only for whoever sees
    #    every role — one query for the lot instead of one per row. `.all()` in
    #    audience_in_words is what lets it land; see audience_is_empty for why
    #    that spelling matters.
    rows = (event.roles.with_signup_counts().select_related("role")
            .prefetch_related("visible_to_ministries"))
    to_join = list(rows.for_audience(contact))
    # ⚠️ A second query, and only for this viewer.
    roles = list(rows) if may_view_records else to_join

    mine = Participation.objects.none()
    if contact is not None:
        mine = Participation.objects.filter(
            event_role__event=event, contact=contact,
        ).select_related("event_role__role")
    back_url, back_label = _back_link(request)
    # ⚠️ Read once. It is a **property, not a column** — `Event.is_full` inside
    #    it costs two queries on a row that carries no capacity annotation, and
    #    this one does not (the detail page fetches a single event, not a list).
    #    Two readings of it below would be four queries for one answer.
    accepting_signups = event.accepting_signups
    return {
        "event": event,
        "roles": roles,
        # 「有角色，只是没有一个是给你的」—— 第二种空状态（L2.4）。
        #
        # 🔴 它必须和「还没开任何角色」长得**不一样**（D27）。少了这一格，
        #    别的 ministry 的在编成员打开活动，读到的是 "No roles opened yet."
        #    —— 一句假话，而这一页上没有任何东西能让他看出来。
        # ⚠️ 问的是**这张表**空不空，不是 `to_join` 空不空 —— 看全表的那个人
        #    表不空，这一句就不该亮。
        # ⚠️ 只在表是空的、而且这张表是收窄过的时候才多查一次。看全表的人表空，
        #    就证明活动一个角色都没有 —— 再问一次库，问的是一个已经答过的问题。
        "roles_none_for_you": (
            not roles and not may_view_records and event.roles.exists()),
        # 那句常驻文案的开关：这张表对他是全的，对别人不是。⚠️ 不查库。
        "sees_every_role": may_view_records,
        # 「谁看得见这一场，却看不全它」。⚠️ 只在看全表的人那里算 —— 对一个
        #    普通报名者这句话既无意义也无处置，而它要遍历角色的受众。
        "audience_gaps": audience_gaps(event) if may_view_records else [],
        # 「什么时候」。⚠️ 一门课的两列是**学期的两端**，照单场那样带时分印出来
        #    说的是一句假话（读起来像一场开三个月的活动）。两个值：上面一行是
        #    学期，下面一行是从讲次推出来的节奏。
        "when_headline": when_headline,
        "when_detail": when_detail,
        # 讲次表。⚠️ 只在看得到记录的人那里取 —— 它带着签到屏的入口，而那是
        #    管理动作；对报名者「这门课什么时候上」已经答在 When 那一行上了。
        "sessions": (event.sessions.all() if may_view_records else []),
        "mine": mine,
        # ⚠️ The property, not `status in OPEN_FOR_SIGNUP` (2026-08-19). It asks
        #    the clock as well, exactly as the `open_for_signup()` queryset
        #    behind event_signup does — and the reason it has to is that the two
        #    are the same gate seen from two sides. Reading only the status here
        #    put a Sign up button on events that finished last year, and the
        #    page it led to had already stopped accepting them.
        #
        # ⚠️ `to_join`, never `roles` (L2.4): the button is drawn from what the
        #    person may take, so an admin looking at a full table still only gets
        #    one when a place in it is actually theirs. Otherwise the button
        #    leads to an empty dropdown — a dead end that answers "Select a valid
        #    choice" to somebody who did nothing wrong.
        "can_sign_up": accepting_signups and bool(to_join),
        # ⚠️ 「这场活动在收不收报名」，不是「他能不能报」—— 按钮那一格靠这两个
        #    键分出三种情况，而模板里那一支落在 `can_sign_up` 之后，所以到得了
        #    它的人已经证明了「有得报的活动，但没有一个位子是给他的」。
        #    ⚠️ 不写成第三个键（`nothing_open_to_you = accepting and not to_join`）：
        #       那是同一件事的第二种拼法，两个键要手工保持反向一致，
        #       而写歪一个的表现是两支都不亮，于是人读到「这场活动不收报名了」——
        #       正是这一步要消掉的那句假话。也不让模板去读那个属性：它查库。
        "accepting_signups": accepting_signups,
        "can_manage": can_manage,
        # ⚠️ False on every published event, so the banner is not something a
        #    template has to remember to switch off. It is only ever true for a
        #    viewer who already passed the check above.
        "preview": preview,
        "back_url": back_url,
        "back_label": back_label,
        # 面板里那颗圆球通向的整页 URL 要带上的筛选串（2026-09-08）。
        #
        # ⚠️ 在这里给，而不是只在 `_listing()` 里 —— 面板那两个模板
        #    （`_schedule_detail` / `_schedule_signup`）拿得到的只有这一份上下文，
        #    而报名成功换回详情的那一次**根本不经过 `_listing()`**。
        # ⚠️ `event_detail_panel` 里 `_listing()` 在这之后 `update`，于是那一份
        #    （带页码修正的）赢。这是对的先后：它知道左边那一列真的停在第几页。
        "list_state": _list_state(request),
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
    contact = _my_contact(request)
    period = _volunteer_period(request)
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
        # 🔴 `panel=pk` 得**在这里补上**（2026-09-09）。这一次响应正是「把面板打开
        #    到这一场」，而地址栏里那一下是**浏览器**做的（`hx-replace-url`）——
        #    渲染这一份的时候 `request.GET` 里还没有它。不补的话，面板里那颗圆球
        #    的链接就少一截，人从它进整页详情再返回，面板是关着的：这一整批要修的
        #    那件事，在最主要的那条路上原样复发。实测抓到的，不是推出来的。
        context["list_state"] = _list_state(request, panel=pk)
        return render(request, "events/_schedule_detail.html", context)

    number = page_holding(_visible_events(period, contact), pk, EVENTS_PER_PAGE)
    context.update(_listing(request, period, contact, page_number=number))
    context.update({
        # 左边那一列作为 out-of-band 的第二块跟着回去。
        "results_oob": True,
        # 高亮哪一行。⚠️ 算不出页码时是 None —— 模板据此**不画**高亮，
        #    而不是高亮一个碰巧在第一页的别人。
        "picked_pk": pk if number else None,
        # ⚠️ 同上，而且必须写在 `_listing()` 那一次 update **之后** —— 它也出
        #    `list_state`（带页码修正的那一份），写在前面会被它盖掉。
        "list_state": _list_state(request, context["page"], panel=pk),
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
       算一次分页（`page_holding`）并回送 40KB，而那个标签在下一次筛选、翻页
       或刷新时自然就对了。写下来是因为「点了报名，左边标签没变」看起来像 bug，
       而它是这一行。
    """
    contact = _my_contact(request)
    if contact is None:
        raise PermissionDenied(SCOPED_DENIAL)
    # ⚠️ Two gates, and until 2026-08-26 there was only the first: this view ran
    #    `open_for_signup()` and nothing else, so a staff-only event was signable
    #    by anybody who typed its id. Same 404 as the detail page — an event
    #    somebody may not see must not confirm that it exists.
    event = get_object_or_404(
        Event.objects.open_for_signup().for_audience(contact), pk=pk)

    # ⚠️ 读 header，不读查询串：这一块是不是画在面板里，取决于**谁在问**，
    #    而不是取决于一个可以被贴进地址栏的参数。带 `?in_panel=1` 打开这个
    #    URL 的人会拿到一块没有外壳、没有导航的碎片。
    in_panel = bool(request.headers.get("HX-Request"))

    form = SignUpForm(request.POST or None, event=event, contact=contact)
    # L2.4：一个位子都不是给他的时候，这一页是**没有内容**的 —— 一个必填、
    # 却一个选项都没有的下拉框，提交回来是 "Select a valid choice"，
    # 而那个人什么都没做错。
    #
    # ⚠️ 404，和这个视图上面那道门、和详情页那一条同一个答案：这一页对他不存在。
    #    详情页那颗按钮本来就不画（`nothing_open_to_you`），所以走到这里的是
    #    手敲 URL 或者一张放了很久的旧页面。
    # ⚠️ 判据取自表单自己的 queryset，不另问一次 —— 两处会在某一格上走散，
    #    而走散的表现正是「按钮说能报、页面说没有」。
    if not form.fields["event_role"].queryset.exists():
        raise Http404("No event matches the given query.")
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
        # ⚠️ 手搭的上下文，所以这一份要单独补 —— 这一次 render 不走 `_detail()`。
        #    少了它，面板里报名表单右下角那颗圆球会通向一个**丢掉筛选**的整页，
        #    而同一块面板上的详情那颗不会。两颗球两种行为，正是这一格要防的。
        # ⚠️ 面板里那一档要带 `panel`，理由同 `event_detail_panel`：地址栏里那一下
        #    是浏览器做的，渲染这一份时 `request.GET` 里还没有它。
        #    ⭐ 带回去的是**这场活动的详情**，不是这张填了一半的表单 —— 面板还原
        #       到详情是对的：一份半填的表单被 URL 复活，比丢掉它更让人意外。
        "list_state": _list_state(request, panel=event.pk if in_panel else None),
    })


@login_required
def my_participations(request):
    """Everything this person has signed up for, newest first.

    ⚠️ The predicate moved to `ParticipationQuerySet.mine()` on 2026-09-02,
       when the dashboard needed the same one. Its reasoning went with it —
       including why it also asks `visible_to_participants()` — because a rule
       explained where it is not implemented is one that gets changed in one
       place and read in the other.

    What stays here is this page's own half: **all of it, newest first**. The
    dashboard asks the same question of the same method and then adds
    `.upcoming()`, which is exactly the difference between the two pages.
    """
    rows = (
        Participation.objects.mine(_my_contact(request))
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

    🔴 **One list, and the permission lands on each row** (2026-09-03).

       Until today this page had **two modes**, switched by `?scope=all`:
       without it, the ministries you administer (editable); with it, every
       ministry (read only). Somebody holding both hats saw only their own by
       default, and the other ministries' events were **not on the page at
       all** — not greyed out, not unclickable: absent. He had the right to
       read them and no page listed them for him.

       The user's argument, and it is the better design:

         「既然老张有权看，那就给他啊。他的页面就该显示所有的 ministries，
           因为他是 foundation admin，然后在所有的 events 里，
           只有 Food Pantry 他可以改。」

       So the foundation tier now always sees everything, and what he may
       *change* is decided per row (see `event_manage_list`). The mode switch
       had nothing left to do and went with it.

       ⭐ Notices has worked this way since it was written — `_mine_to_manage()`
          hands the foundation tier `Notice.objects.all()`. Events was the last
          place in the project with two modes.

       ⚠️ This reverses the 2026-08-05 decision that somebody holding both hats
          keeps the managing view of their own ministries by default. It
          reverses it **in the direction that decision was protecting**: he does
          not lose a single write — he gains the rows he was already allowed to
          read. Losing publishing rights by being promoted was the thing to
          avoid, and it still is.

    ⚠️ The foundation tier gets the same page over every ministry. Without this
       it had no entrance at all: it holds no MinistryRole, so nothing anywhere
       listed events for it to open. That is the same gap C0.2 closed five times
       over — the pages existed and nothing pointed at them.
    """
    administered = ministry_ids_administered_by(request.user)
    foundation = in_foundation_tier(request.user)
    if not administered and not foundation:
        raise PermissionDenied(SCOPED_DENIAL)

    events = Event.objects.all() if foundation else Event.objects.filter(
        ministry_id__in=administered)
    return (
        events.select_related("ministry").order_by("-start_time"),
        administered,
        foundation,
    )


def _offered_ministries(administered, showing_all=False):
    """What the filter's dropdown may offer this account.

    ⚠️ Interface, not a permission — the queryset is already narrowed. What this
       prevents is a ministry admin being offered every ministry in the
       foundation, picking one, and getting an empty list with nothing saying why.

    🔴 **`showing_all` comes first** (2026-09-03). The foundation tier now sees
       every ministry's events on one page, so narrowing its dropdown to the
       ministries it *administers* would offer it a filter that cannot reach
       most of the rows in front of it — and for somebody holding both hats that
       set is not empty, so the old `if not administered` guard would not have
       caught it. The symptom would be a filter that silently omits ministries
       whose events are right there on the page.
    """
    if showing_all or not administered:
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

    # Set by the POST branch below, and read twice at the bottom: it decides
    # whether the fragment carries the messages back out of band.
    wrote = False

    if request.method == "POST":
        # Status is editable straight from the list: publishing an event and
        # closing a finished one are frequent, and both are a single choice.
        # The times are not, and are shown read-only — moving an event obliges
        # somebody to notify the volunteers, so it goes through the edit page,
        # which routes to the notice. Two fields, two different consequences.
        #
        # 🔴 2026-08-29: there is no Save button any more — picking a status
        #    *is* the submit (see the form in _event_manage_results.html). Two
        #    things follow, and both are about not throwing away where the
        #    person was:
        #
        #    · The HTMX path does not redirect at all. It falls through and
        #      re-renders the same fragment the filter renders, so the row
        #      updates in place and the page does not move. A redirect would
        #      make htmx follow it and swap a whole page into #event-results.
        #
        #    · The no-JS path (the Save button inside <noscript>) redirects to
        #      `get_full_path()` rather than to the bare list URL. The form's
        #      action carries the current filter and page, so this sends them
        #      back to the page they were looking at. It used to drop all of it
        #      and land on an unfiltered page 1 — which looks perfectly normal,
        #      just not where you were.
        event = _managed_event(request, request.POST.get("event"))
        # 🔴 **改之前的状态要在 `is_valid()` 之前读**（2026-09-03 设计评审第 1 条）。
        #
        #    `EventStatusForm(request.POST, instance=event)` 是一个 ModelForm，
        #    而 `is_valid()` 会走到 `_post_clean()` —— 那一步就把提交上来的值
        #    **写进了 `event.status`**。在它之后读到的是新值，于是那颗 Undo
        #    会「撤销」到刚刚设定的那个状态：一颗按下去什么都不会发生的按钮，
        #    而且它每一次都渲染得完全正常。
        previous = event.status
        form = EventStatusForm(request.POST, instance=event)
        if form.is_valid():
            set_status(event, form.cleaned_data["status"])
            messages.success(request, f"“{event.name}” is now {event.get_status_display()}.")
            # 🔴 **撤销的凭据放 session，不是放上下文**（设计评审第 1 条）。
            #
            #    没有 JS 的那条路是 POST → redirect → GET（下面那句 PRG），
            #    而重定向之后 `wrote` 是假 —— 凭据只放在上下文里的话，
            #    **撤销就成了一个只有 JS 的人才有的功能**，而这正是这个项目
            #    一直拒绝的那种东西（D24）。放 session 就跨得过那次重定向，
            #    这也正是 messages 框架自己的做法。
            #
            # ⚠️ HTMX 那条路不重定向，于是同一次请求里写进去、又在下面读出来 ——
            #    一套机制两条路，不是两套。
            #
            # ⚠️ 只存 pk 和状态值，不存对象：session 是要序列化的，而且真正的
            #    权限判断在撤销那一次 POST 上重做一遍（走同一个
            #    `_managed_event()`），这里存的东西不构成任何授权。
            request.session["undo_status"] = {
                "event": event.pk,
                "status": previous,
                # 🔴 **`previous` 的显示名，不是 `event.get_status_display()`。**
                #    后者读的是刚刚设定的**新**状态 —— 按钮会写着
                #    「Undo — back to Open for signup」，而按下去回到的是 Draft。
                #    一句读起来完全通顺、而且每次都渲染正常的假话。
                "label": Event.Status(previous).label,
            }
        if not request.headers.get("HX-Request"):
            return redirect(request.get_full_path())
        wrote = True

    period = EventPeriodForm(
        request.GET or None,
        ministries=_offered_ministries(administered, showing_all=foundation))
    events = period.narrow(events)
    page = page_of(request, events, MANAGED_EVENTS_PER_PAGE)
    # When 那一格的两行字：开始一行、结束一行（2026-08-29 第二轮）。
    #
    # 🔴 起止时间**回到了表格里**。这一批之前它们被搬进一个鼠标停在活动名上才
    #    弹出的小窗 —— 行是短了，但那个小窗在触屏上根本不装（app.js 判
    #    `(hover: hover)`），于是手机和平板上这一页从此看不到任何具体时刻，
    #    而扫一整页时它也不在屏幕上。改成竖排两行同时拿到「短」和「看得见」。
    #
    # ⚠️ 两行字在**服务端**算好，不是把两个时间戳丢给浏览器自己格式化。
    #    这一页的时区是基金会的（D16），而浏览器的时区是访客的 —— 一个在纽约
    #    的 foundation admin 会看到每一场都晚三个小时，且没有任何东西会报错。
    #    events/schedule.py 里那条「前端不做日期运算」是同一件事。
    #
    # ⚠️ 挂在**这一页的**活动上（`page`，不是 `events`）：整页和 HTMX 片段共用
    #    下面这一次 render，所以两条路都覆盖到；而给整个 queryset 算就是给
    #    翻不到的那几百场也各算一遍。
    for event in page:
        event.when_start, event.when_end = schedule.when_labels(event)
        # 🔴 **逐行的「你能不能改这一行」**（2026-09-03）。
        #
        #    这一页现在一张列表列全部（见 `_scoped_events`），所以「可不可改」
        #    不再是整页的属性。它决定这一行画状态下拉还是一枚标签，
        #    以及 Go to 那一格画三个链接还是六个。
        #
        # ⚠️ **一次集合判断，不是每行一次 `administers()` 查询** ——
        #    `administered` 是上面那一次查询的结果（一个 id 集合）。
        #    每行各问一次的话，50 行就是 50 次查询，而答案完全相同。
        #
        # 🔴 **这只决定「画什么」，不决定「准不准」。** 每一个写操作仍然走
        #    `_managed_event()` → `can_manage_event()`，问的是真实账号和真实
        #    活动。藏起一颗按钮挡不住任何人，真正的拒绝在视图里 ——
        #    `button.html` 的 `disabled` 那段注释写的是同一条。
        event.can_manage = administers_one_of(event.ministry_id, administered)
    return render(request, _template(
        request, "events/event_manage_list.html",
        "events/_event_manage_results.html"), {
        "events": page,
        "page": page,
        "total": page.paginator.count,
        "period": period,
        # 🔴 页面级那个 `can_manage` 2026-09-03 一分为二 —— 它一直在同时回答
        #    两个不同的问题，而一张列表之后这两个问题的答案会不一样：
        #
        #      · `showing_all`  这一页列的是不是全部 ministry（标题、横幅、筛选）
        #      · `can_publish`  这个账号有没有任何 ministry 的发布权（那颗按钮）
        #
        #    而「这一行能不能改」是第三个问题，答案挂在每一行上（见上面循环）。
        "showing_all": foundation,
        "can_publish": bool(administered),
        # 撤销刚才那次状态修改。⚠️ `pop` 而不是 `get`：它是一次性的 ——
        #    留着的话，下一次打开这一页还会看到一颗撤销上上次的按钮，
        #    而那时人已经不记得上上次是什么了。
        #
        # ⚠️ 取出来就渲染，不判「是不是刚刚那一次」：跨重定向的那条路上
        #    「刚刚」本来就横跨两次请求。
        "undo_action": request.session.pop("undo_status", None),
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
        # ⚠️ Only the status write lights this. Filtering and paging render the
        #    same fragment and must NOT carry messages back: including that
        #    partial consumes whatever is sitting unseen in the session, so the
        #    next page someone opens is missing its own notice. The rule and its
        #    mirror image are both written on _messages_oob.html.
        "messages_oob": wrote,
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
    # ⚠️ 和列表页同一个判断（2026-09-03）：列全部时下拉必须能选全部，
    #    否则筛选选不到自己看得见的行。
    period = EventPeriodForm(
        request.GET or None,
        ministries=_offered_ministries(administered, showing_all=foundation))
    events = period.narrow(events)
    return render(request, "events/ministry_report.html", {
        "events": events.order_by("start_time", "pk"),
        "total": events.count(),
        "period": period,
        # ⚠️ `can_manage` 和 `scope` 两个 2026-09-03 删了 —— `ministry_report.html`
        #    **一次都没有读过它们**（grep 过），而 `scope` 那句
        #    `"Every ministry" if not administered else None` 改成一张列表之后
        #    还会说谎：两顶帽子的人拿到的正是全基金会的报表，而它答「不是」。
        #    一个没人读、又不再正确的值，留着只会被下一个人当真。
        "showing_all": foundation,
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
        # ⚠️ **Not optional**, for the same reason request.FILES above is not:
        #    without it the tick is silently dropped. `commit=False` defers the
        #    many-to-many, and `visible_to_ministries` is the only part of an
        #    audience that lives in one — so an event ticked for a ministry and
        #    nothing else stored an audience of nobody, and 404'd for everyone
        #    including the person who had just published it. Missing here and in
        #    event_update until 2026-09-08; notices.views.notice_create had it
        #    from the day it was written.
        form.save_m2m()
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
        # ⚠️ Above the branch, not inside it, because **this view has two
        #    exits** — the reschedule path redirects to the notice page and
        #    never reaches the save below. Safe here: the row already has a pk,
        #    so the through-table writes have something to point at, and they
        #    are independent of the columns saved a few lines down.
        #
        #    See event_create for what its absence cost.
        form.save_m2m()
        if moved:
            reschedule(
                event,
                start_time=form.cleaned_data["start_time"],
                end_time=form.cleaned_data["end_time"],
            )
            # Straight to the notice, with the reason already chosen. Whoever
            # moved an event that people signed up for is one click from telling
            # them, instead of having to know that the page exists.
            messages.success(request, "Time changed. Tell the people who signed up.")
            return redirect(
                f"{reverse('events:event_notify', args=[event.pk])}"
                f"?reason={EventNotification.Reason.TIME_CHANGED}"
            )
        event.save()
        # Narrowing an audience may leave people who already signed up outside
        # it. That is allowed — their signups stand and their event page still
        # opens — but it is silent, and this is the one moment somebody can act
        # on knowing. ⚠️ It states the fact and stops: nothing in this system
        # can withdraw somebody else's signup, so naming an action here would
        # send the reader looking for a control that is not there.
        stranded = signups_left_outside(event)
        if stranded:
            who = ("1 person who signed up is" if stranded == 1
                   else f"{stranded} people who signed up are")
            messages.success(request, (
                f"Event updated. {who} outside the audience you just set — "
                "their signups stand, and they can still open this event."
            ))
        else:
            messages.success(request, "Event updated.")
        _mention_audience_gaps(request, event)
        return redirect("events:event_detail", pk=event.pk)

    return render(request, "events/event_form.html",
                  _edit_page_context(event, form=form))


def _mention_audience_gaps(request, event):
    """Say who can see this event without seeing all of its roles.

    Requirement 8 makes this state ordinary — one event, published once,
    recruiting inside and outside at the same time — so this is **not** a
    warning and must not read as one. It is also exactly what a mistyped
    audience looks like, and the two are the same state: only the person
    publishing knows which it is, and until now nothing told them there was
    anything to know.

    ⚠️ `messages.info`, not `warning`. The events this fires on are mostly
       correct, and a scolding tone on a correct action is how people learn to
       click past a message — which would cost the wrong half.

    ⚠️ Wording, not logic. Which groups and which roles is
       services.audience_gaps(); this turns the pairs into a sentence, and the
       fact that it takes a request is why it lives here and not there.
    """
    for phrase, roles in audience_gaps(event):
        named = get_text_list([f"“{name}”" for name in roles], "and")
        messages.info(request, (
            f"Note: {phrase} can see this event, but not {named}. "
            "That is how one event recruits inside and outside at once — "
            "check it is what you meant."
        ))


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
        # ⚠️ Here as well as on the event's own form, because a role is the
        #    other half of the pair: the gap appears when either side moves, and
        #    this is the side somebody is usually on when it appears.
        _mention_audience_gaps(request, event)
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
        # 🔴 Hours are records, and deleting this row deletes them. Participation
        #    cascades from event_role, so one POST took an attended signup and
        #    the hours somebody had recorded against it — while the confirmation
        #    said only "anyone signed up for it goes with it", which reads as
        #    losing a place in a list, not losing a number that has already been
        #    reported. Refused since 2026-09-08: an ending is a date, not a
        #    deletion, and there is no way back from this one through the site.
        #
        # ⚠️ Only when hours exist. A role opened by mistake, or one people
        #    signed up for and nobody has worked yet, is still deletable — that
        #    is what this page is for, and the confirmation now says how many
        #    signups go with it.
        recorded = hours_recorded_against(role)
        if recorded:
            messages.error(request, (
                f"“{role.role.name}” has {recorded} recorded against it, and "
                "deleting the role would delete those hours too. Close signups "
                "on it instead, or correct the hours first."
            ))
            return redirect("events:event_update", pk=role.event_id)
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
    # ⚠️ One read of the grant table, not two. org.permissions.event_access()
    #    exists for exactly this and says so in its own docstring; three pages
    #    were still asking each question separately until 2026-09-08.
    can_manage, may_view_records = event_access(request.user, event)
    if not may_view_records:
        raise PermissionDenied(SCOPED_DENIAL)

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
        if participation.pk in signups_asked_about_serving(event):
            value = request.POST.get("served_as")
            # ⚠️ Against the identities somebody may be **asked** about, not
            #    against the whole enum. `not_applicable` is a member of it and
            #    means "this question does not arise here" — a POST that named
            #    it would erase a real answer, and no control anywhere offers
            #    it. Not drawing a control keeps nobody out; this line does.
            if value in SERVED_AS_EXPLANATIONS:
                set_served_as(
                    participation, value,
                    declared_by=Participation.DeclaredBy.ADMIN,
                )
                messages.success(
                    request,
                    f"Recorded. {participation.contact} will see on their signups "
                    "page that an admin set this.",
                )
            else:
                # ⚠️ Both refusals say so out loud (2026-09-08). They were
                #    silent redirects: somebody pressed the control, the page
                #    came back identical, and nothing distinguished a refused
                #    write from a successful one — D27's rule that having
                #    nothing and counting nothing must not look alike, applied
                #    to an action instead of a figure.
                messages.error(
                    request, "That is not an identity somebody can be asked "
                             "about, so nothing was recorded.")
        else:
            messages.error(
                request, "This signup is on a place people attend, so it does "
                         "not record an identity — nothing was changed.")
        return redirect("events:event_registrations", pk=event.pk)

    roles = event.roles.with_signup_counts().select_related("role").prefetch_related(
        "visible_to_ministries",
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
        "asked_about_serving": signups_asked_about_serving(event),
        # ⚠️ Not `ServedAs.choices` — see the POST branch above. The dropdown
        #    offers the identities a person can claim, and the enum holds one
        #    more value than that.
        "served_as_choices": askable_served_as(),
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
    can_manage, may_view_records = event_access(request.user, event)
    if not may_view_records:
        raise PermissionDenied(SCOPED_DENIAL)

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
                #
                # ⚠️ Caught, for the same reason mark_absent() is caught twelve
                #    lines up — and it was not, until 2026-08-28: record_hours()
                #    refuses a role people *attend* (L4), and an uncaught
                #    ValidationError out of a view is a **500**, not a message.
                #    The template hides the box on those rows, but hiding a
                #    control keeps nobody out: a page held open while somebody
                #    corrects that role's `nature` posts straight into this.
                try:
                    record_hours(participation, hours_form.cleaned_data["hours"])
                except NoHoursHere as error:
                    messages.error(request, "; ".join(
                        message for messages_ in error.message_dict.values()
                        for message in messages_
                    ))
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
                # ⚠️ The same helper as the full-page render below, not
                #    scheduled_hours(): the swapped row and the page it lands in
                #    must agree about what the box starts at, and the HTMX path
                #    is the one that gets forgotten.
                "scheduled_hours": prefillable_hours(event),
                "can_manage": True,
            })
        return redirect("events:event_attendance", pk=event.pk)

    rows = (
        Participation.objects.filter(event_role__event=event)
        .notifiable()
        # ⚠️ `event_role__role` is load-bearing beyond the role's name in the
        #    heading: every row asks `records_hours`, which reads the role's
        #    nature to decide whether to draw an hours box at all. Without it
        #    that is a query per row, on the page somebody has open while
        #    checking forty people in.
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
        #
        # 🔴 None on a run, so the box starts empty. The prefill assumes
        #    `end − start` is a plausible number of hours for one person, and a
        #    term running March to June makes that **2664.00** — a figure an
        #    admin can enter with one click, that is authoritative the moment it
        #    lands, and that mark_absent() will refuse the row over from then
        #    on. The accepted cost noted below ("a prefilled number looks
        #    exactly like a confirmed one") was written before runs existed.
        #    A run's hours are recorded per meeting anyway (decision 20), so
        #    there is nothing here for the prefill to have been right about.
        "scheduled_hours": prefillable_hours(event),
    })


@login_required
def event_report(request, pk):
    """R3–R8 for one event. Every number arrives from services.py.

    ⚠️ Reads, so it asks the read check — not `_managed_event()`, which is the
       write gate this page used to go through. The foundation tier may read
       any event's report without being able to touch the event.
    """
    event = get_object_or_404(Event.objects.select_related("ministry"), pk=pk)
    can_manage, may_view_records = event_access(request.user, event)
    if not may_view_records:
        raise PermissionDenied(SCOPED_DENIAL)
    return render(request, "events/event_report.html", {
        "event": event,
        "can_manage": can_manage,
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
        kind, target_id, mode = verify_checkin_token(token)
    except InvalidCheckInToken as error:
        return render(request, "events/checkin_refused.html", {
            "reason": "; ".join(error.messages),
        }, status=400)

    request.session[CHECKIN_CREDENTIAL_KEY] = issue_credential(
        target_id, mode, kind=kind)
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
        kind, target_id, mode = read_credential(
            request.session.get(CHECKIN_CREDENTIAL_KEY))
    except CredentialExpired as error:
        return render(request, "events/checkin_refused.html", {
            "reason": "; ".join(error.messages),
        }, status=400)

    # ⚠️ The published gate is asked of the **event** either way: a meeting of
    #    an unpublished run must not be a route that manufactures attendance.
    if kind == tokens.SESSION:
        session = get_object_or_404(
            Session.objects.select_related("event__ministry"), pk=target_id)
        event = get_object_or_404(
            Event.objects.visible_to_participants(), pk=session.event_id)
        target = session
    else:
        session = None
        event = get_object_or_404(
            Event.objects.visible_to_participants().select_related("ministry"),
            pk=target_id)
        target = event
    targets = scan_targets(contact, target, mode) if contact else None

    if targets is None or not targets.any_signup:
        return render(request, "events/checkin_refused.html", {
            # ⚠️ **No `event` here**, and it is the only one of this page's four
            #    refusals that withholds the name. The scan path does not ask
            #    the audience — right, because somebody standing in the hall
            #    with a signup should not be stopped by it (06-roadmap L2.2) —
            #    but that reasoning is about people who **have** a signup, and
            #    this is the branch for people who do not. A forwarded QR code
            #    therefore named a staff-only event to somebody the event page
            #    would 404 for. The template already draws the name only when
            #    it is given one.
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
                chosen, contact=contact, target=target, mode=mode)
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
        messages.success(request, checkin_result_message(
            participation, mode, changed, session=session))
        return redirect("events:my_participations")

    return render(request, "events/checkin_confirm.html", {
        "event": event,
        "session": session,
        "mode": mode,
        "checking_in": mode == CHECK_IN,
        "targets": targets,
        # One row is the ordinary case and gets no question; several is rare and
        # is the only case where anybody is asked anything (D28 五).
        "only": targets.pending[0] if len(targets.pending) == 1 else None,
    })


def _checkin_screen(request, target, token_url):
    """The iPad page, for an event or for one meeting of a run.

    Read-only as far as the database is concerned, so the permission is the
    manage one purely because of what it grants access to: whoever can open this
    page can mint check-in codes for everybody in front of it.
    """
    session = target if isinstance(target, Session) else None
    return render(request, "events/checkin_display.html", {
        "event": session.event if session else target,
        "session": session,
        "can_manage": True,
        # ⚠️ Computed here, once, at page load — never re-derived in the
        #    browser. An iPad that flipped from Check in to Check out on its own
        #    halfway through would turn the queue in front of it into check-outs
        #    with nothing on screen saying so. From here on the admin decides.
        "default_mode": default_checkin_mode(target),
        "closed_message": window_message(target),
        "token_url": token_url,
    })


@login_required
def checkin_display(request, pk):
    """The screen for a single occasion.

    ⚠️ A run does not come through here — its screen is one meeting's
       (`session_checkin_display` below), because a code that named the whole
       term was the same code for all twelve evenings.
    """
    event = _managed_event(request, pk)
    return _checkin_screen(
        request, event, reverse("events:checkin_token", args=[event.pk]))


@login_required
def session_checkin_display(request, pk):
    """The screen for one meeting of a run. ⭐ Which meeting is not inferred.

    The teacher opens the screen for the evening they are teaching, so the code
    on it is that evening's — and two meetings on one day are simply two
    screens. That is why "which meeting is this scan about" is not a question
    this system has to answer from the clock.
    """
    session = get_object_or_404(
        Session.objects.select_related("event__ministry"), pk=pk)
    _managed_event(request, session.event_id)
    return _checkin_screen(
        request, session,
        reverse("events:session_checkin_token", args=[session.pk]))


def _checkin_token(request, target, kind):
    """A fresh code for whichever screen asked. One body, two routes.

    ⚠️ The caller has already made the permission check, and that check **is**
       the scheme: without it any signed-in volunteer fetches a live token from
       their sofa and books themselves in, and every rotating-code measure
       becomes decoration.
    """
    if getattr(request, "limited", False):
        return JsonResponse({"error": "Too many requests."}, status=429)
    mode = request.GET.get("mode")
    if mode not in MODES:
        raise Http404
    if not window_is_open(target):
        return JsonResponse({"error": window_message(target)}, status=409)
    token, expires_at = issue_with_expiry(target.pk, mode, kind=kind)
    return JsonResponse({
        # ⚠️ The whole URL is assembled here and the browser only draws it.
        #    A script that built this address from parts would be a second
        #    definition of the route, and the QR is the one place where being
        #    subtly wrong produces a code that scans perfectly and goes nowhere.
        "url": request.build_absolute_uri(
            reverse("events:checkin_scan", args=[token])),
        "expires_at": expires_at,
    })


@login_required
@ratelimit(key="user", rate="60/m", block=False)
def checkin_token(request, pk):
    """A code for a single occasion's screen."""
    event = _managed_event(request, pk)
    return _checkin_token(request, event, tokens.EVENT)


@login_required
@ratelimit(key="user", rate="60/m", block=False)
def session_checkin_token(request, pk):
    """A code for one meeting's screen. ⚠️ The kind travels inside the signature,
    so a week-one code cannot be replayed against week seven by editing a URL."""
    session = get_object_or_404(Session.objects.select_related("event"), pk=pk)
    _managed_event(request, session.event_id)
    return _checkin_token(request, session, tokens.SESSION)
