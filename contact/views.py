"""The contact app's own pages — the first non-admin pages in the project.

Both of them were pushed out of the admin by D18's shape trigger, not by
preference: this one needs to know whose page you came from (an inline form
cannot reach its parent object without the deepest admin plumbing there is),
and the merge page (B4.4) would have to inherit admin templates.

Thin shells on purpose. The logic lives in services.py and models.py, so Phase C
swaps the template for an HTMX fragment and changes nothing else.

⚠️ staff_member_required is imported from admin here, and this is the one file
   allowed to do that — the layering guard in core/tests.py covers models /
   forms / services and deliberately leaves views out. It is an auth decorator,
   not admin plumbing: nothing on this page renders through the admin.
"""

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms import RelationshipForm
from .models import Contact
from .services import MergeConflict, merge_contacts


@staff_member_required
def relationship_create(request):
    """Record a relationship for ?subject=<contact id>, from either direction."""
    # 404 rather than a blank picker: the subject is this form's entire premise,
    # and a page that quietly worked without one would file relationships
    # against nobody.
    subject = get_object_or_404(Contact, pk=request.GET.get("subject") or 0)

    if request.method == "POST":
        form = RelationshipForm(request.POST, subject=subject)
        if form.is_valid():
            form.save()
            return redirect(reverse("admin:contact_contact_change", args=[subject.pk]))
    else:
        form = RelationshipForm(subject=subject)

    return render(request, "contact/relationship_form.html", {
        "form": form,
        "subject": subject,
    })


@staff_member_required
def contact_merge(request):
    """Merge ?drop=<id> into ?keep=<id>, after showing them side by side.

    A plain view rather than an admin action, on purpose (goal.md D18): the
    confirmation step would have to extend admin/base_site.html and the
    "N waiting" count would have to override admin/index.html or subclass
    AdminSite — precisely the layer that breaks on a Django upgrade and is
    thrown away entirely when the front end arrives. Here, Phase C swaps the
    template and keeps the view and merge_contacts() as they are.

    GET is a preview and must have no side effects whatsoever.
    """
    keep = get_object_or_404(Contact, pk=request.GET.get("keep") or 0)
    drop = get_object_or_404(Contact, pk=request.GET.get("drop") or 0)

    if request.method == "POST":
        try:
            merge_contacts(keep, drop, actor=request.user.get_username())
        except MergeConflict as conflict:
            messages.error(request, str(conflict))
        else:
            messages.success(request, f"已把 #{drop.pk} 合并进 #{keep.pk}。")
            return redirect(reverse("admin:contact_contact_change", args=[keep.pk]))

    return render(request, "contact/merge_confirm.html", {
        "keep": keep,
        "drop": drop,
        "fields": _comparable_fields(keep, drop),
        # Shown at the top of this page rather than on the admin index — same
        # information, none of the admin template coupling.
        "waiting": Contact.objects.possible_duplicates().count(),
    })


def _comparable_fields(keep, drop):
    """Side-by-side rows for the confirmation page."""
    interesting = [
        "legal_last_name", "legal_first_name", "preferred_name",
        "organization_name", "email", "phone", "birth_date",
        "address_street", "address_city", "address_state", "notes",
    ]
    rows = []
    for name in interesting:
        field = Contact._meta.get_field(name)
        rows.append({
            "label": field.verbose_name,
            "keep": getattr(keep, name),
            "drop": getattr(drop, name),
        })
    return rows
