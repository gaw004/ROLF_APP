"""Register, log in, log out. Thin shells over accounts/services.py."""

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy

from org.permissions import SCOPED_DENIAL

from .forms import EmergencyContactForm, ProfileForm, RegistrationForm
from .services import register_account


def register(request):
    """P1: one new account and one new Contact, in one transaction."""
    if request.user.is_authenticated:
        return redirect("events:event_list")

    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = register_account(**form.cleaned_data)
            login(request, user)
            return redirect("events:event_list")
    else:
        form = RegistrationForm()

    return render(request, "accounts/register.html", {"form": form})


class VolunteerLoginView(LoginView):
    """Django's own view; only the template and the destination are ours."""

    template_name = "accounts/login.html"
    redirect_authenticated_user = True

    def get_success_url(self):
        return self.get_redirect_url() or reverse_lazy("events:event_list")


class VolunteerLogoutView(LogoutView):
    next_page = reverse_lazy("events:event_list")


@login_required
def profile(request):
    """The volunteer's own details, and their emergency contacts.

    Three POST actions on one page, because they are one subject: who you are
    and who to call about you. Splitting them across pages would mean three
    URLs for six fields.

    ⚠️ Everything is scoped to request.user.contact and never to a submitted
       id. The emergency-contact rows are looked up *inside* that person's own
       set, so another volunteer's row can only 404 — the same rule
       participation_cancel follows, and the reason neither needs a permission
       check of its own.
    """
    contact = getattr(request.user, "contact", None)
    if contact is None:
        # A superuser has no Contact by design (D12); there is nothing here for
        # them to edit, and pretending otherwise would create a stray Contact.
        raise PermissionDenied(SCOPED_DENIAL)

    form = ProfileForm(instance=contact)
    kin_form = EmergencyContactForm(person=contact)

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "remove_kin":
            kin = get_object_or_404(
                contact.emergency_contacts, pk=request.POST.get("kin"))
            kin.delete()
            messages.success(request, "Emergency contact removed.")
            return redirect("accounts:profile")

        if action == "add_kin":
            kin_form = EmergencyContactForm(request.POST, person=contact)
            if kin_form.is_valid():
                kin_form.save()
                messages.success(request, "Emergency contact added.")
                return redirect("accounts:profile")
        else:
            form = ProfileForm(request.POST, instance=contact)
            if form.is_valid():
                form.save()
                messages.success(request, "Your details have been saved.")
                return redirect("accounts:profile")

    return render(request, "accounts/profile.html", {
        "form": form,
        "kin_form": kin_form,
        "kin": contact.emergency_contacts.select_related("relationship_type"),
        # Shown as words, not inferred from a blank box: "we do not know" and
        # "you are an adult" lead to different signup flows, and the person
        # should be able to see which one they are in.
        "is_minor": contact.is_minor,
    })
