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

from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms import RelationshipForm
from .models import Contact


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
