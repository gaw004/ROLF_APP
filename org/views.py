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

from .forms import GrantForm
from .models import Ministry
from .permissions import can_grant_ministry_admin
from .services import find_grant, grant_ministry_admin, ministry_admins, revoke_ministry_role

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
