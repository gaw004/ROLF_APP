"""P5: appointing a ministry's admins. A thin shell over org/services.py.

Lives in org rather than events because its subject is a ministry, not an
event (D17: one app, one business domain). Phase C's org chart page lands
here too, next to it.

⚠️ Nothing here queries MinistryRole. permissions.py judges, services.py
   writes, and a view calls one of the two — there is a grep guard on it, and
   this page is the one that first made it go red.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from .forms import AssignmentForm, GrantForm, PositionForm
from .models import Ministry, Position
from .permissions import (
    SCOPED_DENIAL,
    can_define_position_terms,
    can_grant_ministry_admin,
    can_manage_staff_roster,
    can_reach_staff_roster,
    in_foundation_tier,
    ministry_ids_administered_by,
)
from .services import (
    assign,
    confirm_position,
    create_position,
    find_grant,
    grant_ministry_admin,
    end_assignment,
    ministry_admins,
    ministry_roster,
    positions_awaiting_review,
    roster_index,
    revoke_ministry_role,
    update_position,
)

# Said once, so both pages refuse in the same words. The wording points at the
# account rather than at the check, so that the next person fixes their group
# membership instead of deleting the guard.
FOUNDATION_ONLY = (
    "Appointing a ministry's admins is a foundation-wide permission "
    "(the foundation_admin group), not one a ministry admin holds."
)


@login_required
def ministry_list(request):
    """The entrance to P5. Which ministry's admins do you want to manage?

    ministry_admin_page needs a primary key, and until this page existed there
    was nowhere to get one: the grant page appeared in no template, so using P5
    meant knowing a ministry's id and typing the URL. Same permission as the
    page it leads to — offering a link that 403s is worse than offering none.
    """
    if not can_grant_ministry_admin(request.user):
        raise PermissionDenied(FOUNDATION_ONLY)

    return render(request, "org/ministry_list.html", {
        "ministries": Ministry.objects.filter(is_active=True).order_by("name"),
    })


@login_required
def ministry_admin_page(request, pk):
    """Grant and revoke the ministry-admin role for one ministry.

    ⚠️ The check is can_grant_ministry_admin(), which reads the global group and
       never a grant. A ministry admin appointing further ministry admins is
       exactly what would make the tier above meaningless — P5 asks for a level
       the people being appointed do not themselves hold.
    """
    if not can_grant_ministry_admin(request.user):
        raise PermissionDenied(FOUNDATION_ONLY)
    ministry = get_object_or_404(Ministry, pk=pk)

    form = GrantForm(request.POST or None)
    if request.method == "POST":
        if request.POST.get("revoke"):
            grant = find_grant(ministry, request.POST["revoke"])
            if grant is None:
                raise Http404
            # Revoking dates the row; it never deletes it.
            revoke_ministry_role(grant)
            messages.success(request, "Revoked. The grant was dated, not deleted.")
            return redirect("org:ministry_admins", pk=ministry.pk)

        if form.is_valid():
            grant_ministry_admin(
                contact=form.cleaned_data["contact"],
                ministry=ministry,
                start_date=form.cleaned_data["start_date"],
                # From the session, never from the page.
                granted_by=request.user,
            )
            messages.success(request, "Granted.")
            return redirect("org:ministry_admins", pk=ministry.pk)

    return render(request, "org/ministry_admins.html", {
        "ministry": ministry,
        "form": form,
        "grants": ministry_admins(ministry),
    })


# --- 员工名册（Phase D 的 D1.9，2026-09-15 落地） ---------------------------


def _scoped_positions(request):
    """这个账号在名册上看得到的岗位，或者一句拒绝。

    ⭐ **照 `events.views._scoped_events()` 的形状写的**，连「一张列表、权限逐行
       判」那条口径都一样（2026-09-03 那次改口）：foundation tier 看全部，
       ministry admin 看自己那几个 ministry 的。

    🔴 **`Position.ministry` 可空，而那一格空着的意思是「基金会级岗位」** ——
       所以 `filter(ministry_id__in=administered)` 天然把它们排除在 ministry
       admin 之外，**这正是想要的**：Executive Director 不是食物银行的岗位。
       而 foundation tier 拿的是 `all()`，所以它们在他那里。
       ⚠️ 这一条是 D2a.10 给 `on_duty()` 记下的那个坑的另一半 —— 那里的症状是
          「这些人一个页面都进不去，不报错」。这里由收窄的这一处一次答完，
          底下的服务层因此收 queryset 而不是一组 ministry id。

    ⚠️ 返回 queryset 而不是 id，同 D27 的不变量：收 id 的写法要在服务层里再判一次
       权限，而那一处判断迟早和这里走散。
    """
    administered = ministry_ids_administered_by(request.user)
    foundation = in_foundation_tier(request.user)
    if not administered and not foundation:
        raise PermissionDenied(SCOPED_DENIAL)

    positions = Position.objects.all() if foundation else Position.objects.filter(
        ministry_id__in=administered)
    return positions, administered, foundation


#: 基金会级岗位（`Position.ministry` 可空）在地址里的写法。
#:
#: ⚠️ 一个词，不是一个 pk —— 那批岗位**不属于任何 ministry**，所以没有 id 可用。
#:    词形的段排在 `<int:pk>` 前面（见 org/urls.py），所以它撞不上任何一个真实的
#:    ministry。
FOUNDATION_WIDE = "foundation-wide"


@login_required
def staff_roster(request):
    """`/staff/` —— 先选一个 ministry（2026-09-15 用户定的版式）。

    ⭐ **索引页不列任何一个岗位。** 初版是一页列全部，按 ministry 分段 ——
       两个 ministry 时读起来还行，而这个基金会的 ministry 只会变多，
       那一页会长成一堵墙。用户要的是先选一个，点进去只看那一个。

    ⚠️ 门问的是 `can_reach_staff_roster()`，不是「他有没有建过岗位」——
       「你还没建过」和「这一页不归你」不能长一个样（D27）。

    ⚠️ 视图里没有任何算术：四个数在 `services.roster_index()`，两次聚合查询。
       `ViewsAreThinGuardTests` 盯着这件事。
    """
    if not can_reach_staff_roster(request.user):
        raise PermissionDenied(SCOPED_DENIAL)
    positions, _administered, foundation = _scoped_positions(request)

    return render(request, "org/staff_roster.html", {
        "cards": roster_index(positions),
        "foundation_wide": FOUNDATION_WIDE,
        "can_define_terms": foundation,
    })


@login_required
def ministry_roster_page(request, pk=None):
    """`/staff/<pk>/` —— 一个 ministry 的岗位和在任的人。

    ⚠️ `pk=None` 是**基金会级岗位**那一页（`/staff/foundation-wide/`），不是缺参数：
       `Position.ministry` 可空，而那批岗位没有 id 可挂。只有 foundation tier
       到得了 —— 不是靠这里判断，是靠 `_scoped_positions()` 给 ministry admin 的
       queryset 里根本没有那些行，于是下面那个 `exists()` 对他为假、交 404。

    ⚠️ 404 而不是 403：一个不归他管的 ministry 的名册对他**不存在**，
       同活动详情页那道门的口径。
    """
    positions, _administered, foundation = _scoped_positions(request)
    mine = positions.filter(ministry_id=pk)
    if not mine.exists():
        raise Http404("No roster matches the given query.")

    sections = ministry_roster(mine)
    return render(request, "org/staff_ministry.html", {
        # ⚠️ 一个 ministry 只会有一段 —— 但仍然走 `ministry_roster()`，
        #    因为分组那条规矩（五组互斥、空缺排他）只有那一处实现。
        "section": sections[0] if sections else None,
        "ministry": Ministry.objects.filter(pk=pk).first() if pk else None,
        "awaiting": positions_awaiting_review(mine) if foundation else None,
        "can_define_terms": foundation,
        # ⚠️ 「新建岗位」那颗键把 ministry 带过去预选上 —— 人是从这一页点进去的，
        #    再让他在下拉里找一遍自己刚点过的那个 ministry 是一次无谓的操作。
        "new_post_query": f"?ministry={pk}" if pk else "",
    })


@login_required
def position_create(request):
    """建一个岗位。

    🔴 **权限判在 `ministry` 这一格上，而不是判「他是不是某种管理员」。**
       表单的下拉已经收窄过了（`PositionForm.__init__`），但那是界面 ——
       `can_publish_notice()` 那段注释记着这个教训的原话：「检查不是门」，
       而这里是反过来的一半：**下拉不是门**。伪造一个别人 ministry 的 id
       提交上来，挡住它的是下面这一句。

    ⚠️ `ministry` 为空（基金会级岗位）时 `can_manage_staff_roster(user, None)`
       只对 foundation tier 为真 —— 而 ministry admin 的表单里这一格是必填的，
       所以那条路他走不到。两道门，而它们挡的是两种人。
    """
    if not can_reach_staff_roster(request.user):
        raise PermissionDenied(SCOPED_DENIAL)

    form = PositionForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        if not can_manage_staff_roster(request.user, form.cleaned_data.get("ministry")):
            raise PermissionDenied(SCOPED_DENIAL)
        position = create_position(
            form.instance, by_foundation=can_define_position_terms(request.user))
        messages.success(request, f"Created “{position.name}”.")
        return redirect("org:position_detail", pk=position.pk)

    return render(request, "org/position_form.html", {"form": form})


@login_required
def position_detail(request, pk):
    """一个岗位：改它、往里放人、结束谁的任职、以及 foundation tier 的确认。

    ⚠️ 岗位从 `_scoped_positions()` 里取，取不到就是 404 —— 不是 403。
       一个不归他管的岗位对他不存在，同 `event_signup` 那道门的口径
       （「an event somebody may not see must not confirm that it exists」）。

    ⚠️ 四个 POST 分支，形状照 `ministry_admin_page`（那一页也是列表 + 表单 +
       一个按名字分辨的动作）。每一个分支**各自**再问一次权限：隐藏按钮是界面，
       拒绝 POST 才是权限（`can_view_event_records()` 那段注释的原话）。
    """
    positions, _administered, foundation = _scoped_positions(request)
    position = get_object_or_404(
        positions.select_related("ministry", "reports_to"), pk=pk)
    may_manage = can_manage_staff_roster(request.user, position.ministry)

    form = PositionForm(instance=position, user=request.user)
    assign_form = AssignmentForm(position=position)

    if request.method == "POST":
        if not may_manage:
            raise PermissionDenied(SCOPED_DENIAL)

        if request.POST.get("end"):
            # ⚠️ 从**这个岗位自己的**任职里取，而不是按 pk 全库找 —— 一个来自
            #    表单的 pk 不许够得着别的岗位的行，同 `find_grant()` 的作用域。
            tenure = get_object_or_404(position.assignments, pk=request.POST["end"])
            end_assignment(tenure)
            messages.success(request, "Ended. The row was dated, not deleted.")
            return redirect("org:position_detail", pk=position.pk)

        if request.POST.get("confirm"):
            if not can_define_position_terms(request.user):
                raise PermissionDenied(SCOPED_DENIAL)
            confirm_position(position)
            messages.success(request, "Verified. It has left the review list.")
            return redirect("org:position_detail", pk=position.pk)

        if request.POST.get("assign"):
            assign_form = AssignmentForm(request.POST, position=position)
            if assign_form.is_valid():
                assign_form.instance.position = position
                assign(assign_form.instance)
                messages.success(request, "Added to this post.")
                return redirect("org:position_detail", pk=position.pk)
        else:
            form = PositionForm(request.POST, instance=position, user=request.user)
            if form.is_valid():
                # ⚠️ **foundation tier 保存 = 核验通过**（用户 2026-09-15 定的）。
                #    判断传进服务层，不在这里翻那一格 —— 写入路径在
                #    `org/services.py` 收口（D18），而这里是薄壳。
                was_pending = position.needs_foundation_review
                update_position(form.instance, by_foundation=foundation)
                messages.success(
                    request,
                    "Saved and verified." if foundation and was_pending else "Saved.")
                return redirect("org:position_detail", pk=position.pk)

    return render(request, "org/position_detail.html", {
        "position": position,
        "form": form,
        "assign_form": assign_form,
        "holders": position.assignments.active().select_related(
            "contact", "employment_type").order_by("-start_date"),
        "past": position.assignments.exclude(
            pk__in=position.assignments.active().values("pk")
        ).select_related("contact").order_by("-end_date"),
        "may_manage": may_manage,
        "can_define_terms": foundation,
    })
