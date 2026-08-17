"""Register, log in, log out. Thin shells over accounts/services.py."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import (
    LoginView,
    LogoutView,
    PasswordResetCompleteView,
    PasswordResetConfirmView,
    PasswordResetDoneView,
    PasswordResetView,
)
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit

from core.ratelimit import (
    password_reset_rate_per_ip,
    password_reset_rate_site,
    registration_rate_per_ip,
    registration_rate_site,
    site_wide,
)
from org.permissions import SCOPED_DENIAL

from .google import identity_from
from .forms import (
    EmergencyContactForm,
    ProfileForm,
    RegistrationForm,
    VolunteerPasswordChangeForm,
    VolunteerSetPasswordForm,
)
from .services import register_account


@ratelimit(key="ip", rate=registration_rate_per_ip, method="POST", block=False)
@ratelimit(key=site_wide, rate=registration_rate_site, method="POST", block=False)
def register(request):
    """P1: one new account and one new Contact, in one transaction.

    ⚠️ Two limits, and they answer different attacks. The per-IP one stops one
       machine asking a thousand times; the site-wide one stops a thousand
       machines asking once each, which the first cannot see at all.

    ⚠️ `block=False` rather than letting the decorator raise. The default raises
       `Ratelimited`, which is a bare 403 — on the page whose visitors have no
       account and therefore no way to ask anybody what happened. This way the
       refusal is a page that says what happened and what to do instead.

    ⚠️ `method="POST"` on both: reading the form is not the thing being limited,
       and counting GETs would let somebody exhaust their own allowance by
       reloading the page.
    """
    if request.user.is_authenticated:
        return redirect("events:event_list")

    if getattr(request, "limited", False):
        # ⚠️ Before the form is even built, so a refused attempt writes nothing
        #    and validates nothing. 429 rather than 403: this is "not now",
        #    not "not you", and the difference is what a caller has to know to
        #    behave correctly.
        return render(request, "accounts/registration_rate_limited.html", status=429)

    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = register_account(**form.cleaned_data)
            login(request, user)
            return redirect("events:event_list")
    else:
        form = RegistrationForm()

    return render(request, "accounts/register.html", _register_context(form))


def _register_context(form):
    """What register.html needs. Two views render it, so this is written once."""
    return {
        "form": form,
        # Read here rather than from a context processor: it belongs to one page,
        # and putting it on every request would mean every page in the site
        # carries a Google client id in its context for no reason.
        "google_client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
    }


def register_with_google(request):
    """Fill the registration form in from a Google sign-in. Fills; grants nothing.

    ⚠️ It renders the registration page with three boxes prefilled and **does not
       create anything**. The person still picks a password and presses Register,
       which goes through the ordinary path above — limits, duplicate check and
       all. There is no branch anywhere that treats a Google token as a login.

    ⚠️ An ordinary Django POST with a CSRF token, not Google's `login_uri`
       redirect. Google's own pattern posts its form straight from their iframe,
       which carries no CSRF token of ours — so following it would mean marking
       this endpoint csrf_exempt and hand-rolling the double-submit check instead.
       The button's callback fills a hidden field in one of our own forms and
       submits it, so Django's protection is untouched and there is nothing new
       to get right.

    ⚠️ A bad or missing credential renders the **empty form**, not an error. The
       page's job is to let somebody register; "we could not read that" leaves
       them exactly where they would have been without the button.
    """
    if request.user.is_authenticated:
        return redirect("events:event_list")
    if request.method != "POST":
        return redirect("accounts:register")

    identity = identity_from(request.POST.get("credential", ""))
    if identity is None:
        messages.info(
            request,
            "We could not read that Google sign-in, so nothing was filled in. "
            "You can still register by filling the form in yourself.")
        return render(request, "accounts/register.html", _register_context(RegistrationForm()))

    # ⚠️ `initial`, not `data`. Binding it would run validation on a form the
    #    person has not filled in yet — so they would arrive at their own
    #    registration page with "This field is required" under the password box,
    #    having done nothing wrong.
    return render(request, "accounts/register.html",
                  _register_context(RegistrationForm(initial=identity)))


class VolunteerLoginView(LoginView):
    """Django's own view; only the template and the destination are ours."""

    template_name = "accounts/login.html"
    redirect_authenticated_user = True

    def get_success_url(self):
        return self.get_redirect_url() or reverse_lazy("events:event_list")


class VolunteerLogoutView(LogoutView):
    next_page = reverse_lazy("events:event_list")


# --- C3.2 · Password reset ---------------------------------------------------
#
# Django's own four views, given this project's templates and one limit. The
# flow is not reimplemented: the token, its expiry, the single use and the
# careful refusal to say whether an address exists are all Django's, and they
# are the parts that are easy to get subtly wrong.
#
# ⚠️ It serves accounts that were **registered**, which is the whole population
#    that can log in. A Contact entered by an admin from a paper list has no
#    account and no password, and Django's own form skips exactly those (it
#    looks for users with a usable password). That is the documented gap in
#    phase-c.md, not a bug to fix here.


@method_decorator(
    ratelimit(key="ip", rate=password_reset_rate_per_ip, method="POST", block=False),
    name="post")
@method_decorator(
    ratelimit(key=site_wide, rate=password_reset_rate_site, method="POST", block=False),
    name="post")
class VolunteerPasswordResetView(PasswordResetView):
    """Ask for a link. ⚠️ Limited, and not for the reason it looks like.

    The link goes to the address on file, so hammering this page reveals
    nothing and unlocks nothing. What it does is make this application send
    mail to any address a stranger types — spending an allowance shared with
    every notification and every reset a real person needs, and collecting spam
    complaints against the domain while it does. Neither of those two failures
    produces an error anywhere.
    """

    template_name = "accounts/password_reset.html"
    email_template_name = "accounts/password_reset_email.txt"
    subject_template_name = "accounts/password_reset_subject.txt"
    success_url = reverse_lazy("accounts:password_reset_done")

    def post(self, request, *args, **kwargs):
        if getattr(request, "limited", False):
            # ⚠️ Before the form is validated, so a refused attempt sends
            #    nothing at all. 429 rather than 403 for the same reason as
            #    registration: this is "not now", not "not you".
            return render(request, "accounts/password_reset_rate_limited.html",
                          status=429)
        return super().post(request, *args, **kwargs)


class VolunteerPasswordResetDoneView(PasswordResetDoneView):
    template_name = "accounts/password_reset_done.html"


class VolunteerPasswordResetConfirmView(PasswordResetConfirmView):
    """Set the new password. The link's validity is checked by Django."""

    template_name = "accounts/password_reset_confirm.html"
    # Capped, unlike SetPasswordForm — see the form's docstring.
    form_class = VolunteerSetPasswordForm
    success_url = reverse_lazy("accounts:password_reset_complete")


class VolunteerPasswordResetCompleteView(PasswordResetCompleteView):
    template_name = "accounts/password_reset_complete.html"


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

    form = ProfileForm(instance=contact, user=request.user)
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
            form = ProfileForm(request.POST, instance=contact, user=request.user)
            if form.is_valid():
                form.save()
                messages.success(request, "Your details have been saved.")
                return redirect("accounts:profile")

    return render(request, "accounts/profile.html", {
        "form": form,
        "kin_form": kin_form,
        # Unbound, and it is the modal's copy. The bound one lives on
        # password_change() — the page never validates a password itself.
        "password_form": VolunteerPasswordChangeForm(user=request.user),
        "kin": contact.emergency_contacts.select_related("relationship_type"),
        # Shown as words, not inferred from a blank box: "we do not know" and
        # "you are an adult" lead to different signup flows, and the person
        # should be able to see which one they are in.
        "is_minor": contact.is_minor,
    })


@login_required
def password_change(request):
    """Change your own password. Old password required.

    ⚠️ Two ways in, one implementation. The profile page opens this as a modal
       (HTMX), and the same URL is a whole page for anybody without JavaScript —
       which is not a hypothetical here, because the modal's button is a plain
       link to this address (see core/components/modal.html). D24's
       progressive-enhancement rule: every write has a complete server-side path.

    ⚠️ On success the HTMX path answers **HX-Redirect**, not a fragment. HTMX
       follows a 302 itself and would swap a whole page into the modal, which is
       why the redirect has to be named. Sending the browser to the profile page
       is also what makes the success message appear through the ordinary
       messages framework rather than needing an out-of-band swap — the same
       page and the same message on both paths, with nothing to keep in step.

    ⚠️ Not a `PasswordChangeView`. The class-based view brings its own
       success_url, its own template names and `PasswordChangeDoneView`, and the
       one thing it does that matters — update_session_auth_hash — is one line.
       Changing your password must not log you out; without that line Django
       rotates the session hash and the next click is the login page, which
       looks exactly like the change having failed.
    """
    if request.method == "POST":
        form = VolunteerPasswordChangeForm(user=request.user, data=request.POST)
        if form.is_valid():
            form.save()
            update_session_auth_hash(request, form.user)
            messages.success(request, "Your password has been changed.")
            if request.headers.get("HX-Request"):
                response = HttpResponse(status=204)
                response["HX-Redirect"] = reverse("accounts:profile")
                return response
            return redirect("accounts:profile")
    else:
        form = VolunteerPasswordChangeForm(user=request.user)

    # ⚠️ The invalid POST comes back as the form fragment, which HTMX swaps in
    #    place — so the modal stays open, with the error under the box that
    #    caused it. Its Alpine state lives outside the swapped element, which is
    #    what makes that work.
    template = ("accounts/_password_form.html" if request.headers.get("HX-Request")
                else "accounts/password_change.html")
    return render(request, template, {"password_form": form})
