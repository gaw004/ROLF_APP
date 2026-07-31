"""Register, log in, log out. Thin shells over accounts/services.py."""

from django.contrib.auth import login
from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import redirect, render
from django.urls import reverse_lazy

from .forms import RegistrationForm
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
