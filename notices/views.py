"""The board, and the pages a ministry's admins put things on it from.

Same three rules as events/views.py and gallery/views.py: visibility decided in
the query, the permission check first and delegated to org.permissions, no
arithmetic here.

🔴 **Every read of this table goes through `for_audience()`**, and that is not a
   convention — `NoticesAreNarrowedGuardTests` in core/tests.py refuses to let
   this file mention `Notice.objects` without it. Forgetting is silent and it is
   the worst failure this table has: a notice meant for one ministry's staff,
   drawn on the home page of every outside volunteer, with nothing raising and
   nobody able to tell.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render

from core.pagination import page_of
from org.permissions import (
    SCOPED_DENIAL,
    can_manage_notice,
    can_publish_notice,
    can_reach_notice_manage,
    in_foundation_tier,
    ministry_ids_administered_by,
)

from .forms import NoticeForm
from .models import Notice
from .services import publish, take_down

#: How many past notices the reading page offers. ⚠️ A cap rather than paging,
#: and it stayed one on 2026-09-03 when everything else on these two pages got a
#: paginator (用户拍板): "what did that say about the cheques again" is answered
#: by the most recent handful, and an archive with a paginator under a board is a
#: filing cabinet bolted to a noticeboard. What is on the board **now** is a
#: different question, and that list is the one that got paged — see below.
PAST_SHOWN = 20

#: How many notices are on one page of the board (2026-09-03).
#:
#: ⚠️ 20, matching `events.views.EVENTS_PER_PAGE` rather than a number of its
#:    own: both are the same page for the same person — "what is there for me
#:    right now" — and two reading lists that scroll to different lengths is a
#:    difference somebody has to explain.
#:
#: ⚠️ It is a **safety net, not an expectation**. 用户说得很清楚：同时挂在板上的
#:    公告不该有很多。所以在正常规模下这个翻页器一次都不会画出来
#:    （`pagination.html` 只在装不下时才画），而它存在是因为「不该很多」不是
#:    「不可能很多」—— 没有它的那一版是**完全无上限**的。
BOARD_PER_PAGE = 20

#: How many rows on one page of the manage list (2026-09-03, 50 → 20 改于
#: 2026-09-09).
#:
#: ⚠️ 20, still matching `events.views.MANAGED_EVENTS_PER_PAGE` — 那条「照抄活动
#:    那边」的关系没变，变的是被照抄的那个数。2026-09-09 拍板四个列表统一 20：
#:    「admin 的 event manage 和 notice manage 翻页都要 50 行才行，改成和 event
#:    一样的条数」。
#:
#: ⚠️ 原来这里写的是「它和阅读列表一样有理由更长 —— 它装着草稿、定时的、
#:    以及所有已经撤下来的」。那条理由**随这次决定作废**，不是留着当注脚：
#:    留着的话，下一个人读到的是一段主张 50 的论证和一个写着 20 的赋值。
#:
#: ⚠️ 字面量而不是 `from events.views import ...`：`notices` 不能依赖 `events`
#:    （D41 第四节，也是 `page_of` 当初搬去 `core/pagination.py` 的理由）。
#:    两个数要一起改，靠的是这条注释和 revisions.md，不是 import。
#:
#: 🔴 The account this was missing for is the foundation tier: `_mine_to_manage()`
#:    hands it `Notice.objects.all()` — every ministry, since the first day, past
#:    ones included — on one unbounded page.
MANAGED_NOTICES_PER_PAGE = 20

#: The sentence both admin pages refuse with. ⚠️ Written once — two doors onto
#: the same room that disagree about why it is locked is how a refusal starts
#: sounding like a bug.
NOT_A_NOTICE_ADMIN = ("Notices are put up by a ministry's admins and by the "
                      "foundation.")


def _my_contact(request):
    return getattr(request.user, "contact", None)


def _mine_to_manage(request):
    """The notices this account may edit, and nothing else.

    ⚠️ Narrowed in the query rather than filtered in the template — the same
       rule the rest of the project follows, because a template that filters
       still fetched the rows.
    """
    if in_foundation_tier(request.user):
        return Notice.objects.all()
    return Notice.objects.filter(
        ministry_id__in=ministry_ids_administered_by(request.user))


@login_required
def notice_list(request):
    """What is on the board for you, and what was on it lately.

    ⚠️ Two lists rather than one, and the split is what makes a required
       `stops_showing` honest (decision 4). A notice coming down is it leaving
       the board, never it being lost — "we no longer take cheques" is still
       here after its thirty days, one section lower.
    """
    contact = _my_contact(request)
    current = Notice.objects.showing().for_audience(contact).select_related("ministry")
    past = (Notice.objects.past().for_audience(contact)
            .select_related("ministry")[:PAST_SHOWN])
    # ⚠️ 翻的是**上面那一列**，下面 `past` 那一列照旧是截断（见 PAST_SHOWN）。
    #    一页上两个翻页器的话，`?page=` 到底指哪一列就得看参数名才知道 ——
    #    而这一页上「板上的」和「已经下架的」是两个不同的问题，不是一列的两截。
    page = page_of(request, current, BOARD_PER_PAGE)
    return render(request, "notices/notice_list.html", {
        # ⚠️ `page` 本身就是可迭代的（Django 的 Page 对象），所以模板那边一个字
        #    都不用改；`page` 另外传一份，是给翻页器用的。
        "notices": page,
        "page": page,
        "past": list(past),
        "may_publish": can_reach_notice_manage(request.user),
    })


@login_required
def notice_manage_list(request):
    """Everything this account is answerable for, drafts included.

    ⚠️ Deliberately **not** narrowed by `for_audience()`, and it is the one page
       in this app that is not. An admin publishing a notice for one ministry's
       staff is very often not in that ministry — they would otherwise be unable
       to see the thing they just wrote. What scopes this page is
       `_mine_to_manage()`, which asks the other question: not "is it for you"
       but "are you answerable for it".
    """
    if not can_reach_notice_manage(request.user):
        raise PermissionDenied(NOT_A_NOTICE_ADMIN)
    rows = _mine_to_manage(request).select_related("ministry", "owner")
    # ⚠️ `page_of()` 补的那个 `-pk` 结尾是这里的**必需品**，不是保险：没有一个
    #    唯一列收尾，同一分钟上板的两条公告在第 1 页和第 2 页之间的先后是未定义
    #    的，表现是一行出现两次、或者一行凭空消失，而没有任何东西会报错。
    #    ⚠️ `Notice.Meta.ordering` 已经是 `["-starts_showing", "-id"]`，
    #       而 `page_of()` 读得到它 —— 读不到的那一版会把整个排序换成 `-pk`，
    #       理由写在 core/pagination.py 的 `ordering_for()` 上。
    page = page_of(request, rows, MANAGED_NOTICES_PER_PAGE)
    return render(request, "notices/notice_manage_list.html",
                  {"notices": page, "page": page})


@login_required
def notice_create(request):
    if not can_reach_notice_manage(request.user):
        raise PermissionDenied(NOT_A_NOTICE_ADMIN)
    form = NoticeForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        # ⚠️ Checked again on the **submitted** ministry, not only on the
        #    dropdown that offered it. The dropdown stops a slip; a POST can
        #    name any id. Two different jobs, both needed — the same pair
        #    events.views.event_create makes.
        if not can_publish_notice(request.user, form.cleaned_data["ministry"]):
            raise PermissionDenied(SCOPED_DENIAL)
        notice = form.save(commit=False)
        notice.owner = _my_contact(request)
        notice.save()
        form.save_m2m()
        messages.success(request, "Notice saved.")
        return redirect("notices:notice_manage_list")
    return render(request, "notices/notice_form.html",
                  {"form": form, "notice": None})


@login_required
def notice_update(request, pk):
    notice = get_object_or_404(Notice.objects.select_related("ministry"), pk=pk)
    if not can_manage_notice(request.user, notice):
        raise PermissionDenied(SCOPED_DENIAL)
    form = NoticeForm(request.POST or None, instance=notice, user=request.user)
    if request.method == "POST" and form.is_valid():
        if not can_publish_notice(request.user, form.cleaned_data["ministry"]):
            raise PermissionDenied(SCOPED_DENIAL)
        form.save()
        messages.success(request, "Notice saved.")
        return redirect("notices:notice_manage_list")
    return render(request, "notices/notice_form.html",
                  {"form": form, "notice": notice})


@login_required
def notice_publish(request, pk):
    """A draft, onto the board. POST only.

    ⚠️ The Status dropdown on the form can do this too, and that is not a reason
       to leave this out — it is the reason to keep the two in step. Publishing
       is one click from the list somebody is already looking at; the dropdown
       is for the person who is editing anyway.

    ⚠️ It deliberately does **not** touch `starts_showing`. A notice written on
       Friday to go up on Monday goes up on Monday, and stamping it with now
       would throw that away silently — see services.publish().
    """
    notice = get_object_or_404(Notice.objects.select_related("ministry"), pk=pk)
    if not can_manage_notice(request.user, notice):
        raise PermissionDenied(SCOPED_DENIAL)
    if request.method == "POST":
        publish(notice)
        messages.success(request, "Notice put up.")
    return redirect("notices:notice_manage_list")


@login_required
def notice_take_down(request, pk):
    """Off the board now. POST only; the service decides which of the two cases.

    ⚠️ The button behind this is a full server-side form, not a link — a write
       reached by GET is one a crawler can perform.
    """
    notice = get_object_or_404(Notice.objects.select_related("ministry"), pk=pk)
    if not can_manage_notice(request.user, notice):
        raise PermissionDenied(SCOPED_DENIAL)
    if request.method == "POST":
        take_down(notice)
        messages.success(request, "Notice taken down.")
    return redirect("notices:notice_manage_list")
