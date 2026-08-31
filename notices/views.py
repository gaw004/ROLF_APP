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

#: How many past notices the reading page offers. ⚠️ A cap rather than paging:
#: "what did that say about the cheques again" is answered by the most recent
#: handful, and a board with a paginator is a filing cabinet.
PAST_SHOWN = 20

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
    return render(request, "notices/notice_list.html", {
        "notices": list(current),
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
    return render(request, "notices/notice_manage_list.html", {"notices": rows})


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
