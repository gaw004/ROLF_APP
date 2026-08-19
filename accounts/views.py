"""Register, log in, log out. Thin shells over accounts/services.py."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login, update_session_auth_hash
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
from django.views.decorators.http import require_POST
from django.urls import reverse, reverse_lazy
from django.utils.decorators import method_decorator
from django_ratelimit.decorators import ratelimit

from core.ratelimit import (
    password_reset_rate_per_ip,
    password_reset_rate_site,
    registration_rate_per_ip,
    registration_rate_site,
    site_wide,
    verification_rate_per_ip,
)
from org.permissions import SCOPED_DENIAL

from .google import identity_from
from .forms import (
    EmergencyContactForm,
    ProfileForm,
    RegistrationForm,
    VerificationCodeForm,
    VolunteerAuthenticationForm,
    VolunteerPasswordChangeForm,
    VolunteerSetPasswordForm,
)
from .services import (
    VerificationError,
    check_verification_code,
    mark_email_verified,
    register_account,
    seconds_until_resend,
    send_verification_code,
)


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
            # 🔴 **No login() here any more** (2026-08-19). The account exists;
            #    nobody is signed in until the address has been proved. That is
            #    the strict shape of this flow, decided the same day: an
            #    unconfirmed address is one this application cannot reach, and
            #    "you are in, but we may never be able to write to you" is the
            #    state that produces a volunteer who never hears about the
            #    event they signed up for.
            if _google_verified(request, user.email):
                # The one address we do not have to ask about: Google already
                # proved it, and the person did not change the box afterwards.
                mark_email_verified(user)
                login(request, user)
                return redirect("events:event_list")
            send_verification_code(user)
            request.session[PENDING_SESSION_KEY] = user.pk
            return redirect("accounts:verify_email")
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

    # What Google is willing to vouch for, kept for the POST that follows
    # (2026-08-19). Registration reads it back and skips the emailed code — but
    # only if the address is still the one Google named, because the box is
    # editable and somebody may type a different address over it.
    #
    # ⚠️ In the **session**, not in a hidden field on the form. A hidden field
    #    is a value the browser sends back, so "this address is verified" would
    #    be a checkbox anybody could tick with a POST.
    #
    # ⚠️ Only when Google says `email_verified`. A Google account can exist with
    #    an unproved address, and this is exactly the branch where that
    #    distinction stops being academic — see accounts/google.py, whose old
    #    note said the flag was ignored because nothing here confirmed addresses
    #    at all. Now something does.
    if identity.pop("email_verified", False):
        request.session[GOOGLE_VERIFIED_SESSION_KEY] = identity.get("email", "")
    else:
        request.session.pop(GOOGLE_VERIFIED_SESSION_KEY, None)

    # ⚠️ `initial`, not `data`. Binding it would run validation on a form the
    #    person has not filled in yet — so they would arrive at their own
    #    registration page with "This field is required" under the password box,
    #    having done nothing wrong.
    return render(request, "accounts/register.html",
                  _register_context(RegistrationForm(initial=identity)))


# --- C3.x · Confirming the email address (2026-08-19) -------------------------
#
# Two views and one session key. The session key is what makes this reachable at
# all: the person is **not logged in** — that is the whole point — so "which
# account is waiting on a code" has to be carried somewhere, and a URL carrying
# a user id would be a page anybody could open for anybody.

#: Named once, in the form, and imported here. Three places read it.
PENDING_SESSION_KEY = VolunteerAuthenticationForm.PENDING_SESSION_KEY
#: The address Google vouched for during a prefill, if it did.
GOOGLE_VERIFIED_SESSION_KEY = "google_verified_email"


def _google_verified(request, email):
    """Did Google prove *this* address during this registration?

    ⚠️ The comparison matters as much as the flag. The address box is editable
       after a Google prefill, so somebody can sign in with Google and then type
       a different address over it — and skipping the code there would let
       anybody with any Google account register somebody else's address.
    """
    vouched = request.session.pop(GOOGLE_VERIFIED_SESSION_KEY, None)
    return bool(vouched) and vouched.strip().lower() == (email or "").strip().lower()


def _pending_user(request):
    """The account waiting on a code, or None."""
    pk = request.session.get(PENDING_SESSION_KEY)
    if not pk:
        return None
    user = get_user_model().objects.filter(pk=pk).first()
    if user is None or user.email_verified:
        # Already done, or the row is gone. Either way there is nothing to wait
        # for, and leaving the key in place would strand the next visit here.
        request.session.pop(PENDING_SESSION_KEY, None)
        return None
    return user


@ratelimit(key="ip", rate=verification_rate_per_ip, method="POST", block=False)
def verify_email(request):
    """Type the six digits. On success the person is logged in — not before.

    ⚠️ Limited per IP, and what it protects is the guessing: six digits is a
       million-wide space, and MAX_ATTEMPTS only ends *one* code. Without a
       limit here somebody could resend and guess, resend and guess.

    ⚠️ A GET with no pending account is a redirect to the login page, not a 404.
       The person who lands here is either done already or arrived from a stale
       tab, and both of those want the same thing next.
    """
    if request.user.is_authenticated:
        return redirect("events:event_list")

    user = _pending_user(request)
    if user is None:
        return redirect("accounts:login")

    form = VerificationCodeForm(request.POST or None)
    if request.method == "POST":
        if getattr(request, "limited", False):
            # Before the code is even read, so a refused attempt costs the
            # person nothing — no attempt spent, no row touched. 429 for the
            # same reason registration uses it: "not now", not "not you".
            return render(request, "accounts/verify_email.html",
                          _verify_context(request, user, form,
                                          rate_limited=True), status=429)
        if form.is_valid():
            try:
                check_verification_code(user, form.cleaned_data["code"])
            except VerificationError as refusal:
                form.add_error("code", str(refusal))
            else:
                request.session.pop(PENDING_SESSION_KEY, None)
                login(request, user)
                messages.success(
                    request, "Your email address is confirmed. Welcome.")
                return redirect("events:event_list")

    return render(request, "accounts/verify_email.html",
                  _verify_context(request, user, form))


def _verify_context(request, user, form, rate_limited=False):
    return {
        "form": form,
        # ⚠️ The address is shown back. A typo is the most likely reason no mail
        #    arrived, and somebody staring at "we sent you a code" with no
        #    address on the page has no way to notice they typed .con.
        "email": user.email,
        "resend_in": seconds_until_resend(user),
        "rate_limited": rate_limited,
    }


@require_POST
@ratelimit(key="ip", rate=verification_rate_per_ip, method="POST", block=False)
def resend_verification(request):
    """Send another code, at most one per cooldown.

    ⚠️ POST only. A resend sends mail, and a GET that sends mail is one prefetch
       or one link-preview away from sending itself.

    ⚠️ Two limits doing different jobs, and neither replaces the other: the
        per-account cooldown stops one person's held-down button becoming the
        mail bill, and the per-IP limit stops one machine walking through
        accounts to make *us* mail other people.
    """
    user = _pending_user(request)
    if user is None:
        return redirect("accounts:login")
    if getattr(request, "limited", False):
        messages.error(request, "Too many attempts just now. Try again shortly.")
        return redirect("accounts:verify_email")

    wait = seconds_until_resend(user)
    if wait > 0:
        messages.info(
            request, f"A code was just sent. You can ask for another in {wait} seconds.")
    else:
        send_verification_code(user)
        messages.success(request, f"A new code is on its way to {user.email}.")
    return redirect("accounts:verify_email")


class VolunteerLoginView(LoginView):
    """Django's own view; the template, the destination and one refusal are ours.

    ⚠️ The refusal (an unconfirmed address cannot log in) lives in the **form**,
       not here — see VolunteerAuthenticationForm for why it has to run after
       the password check rather than before it.
    """

    template_name = "accounts/login.html"
    authentication_form = VolunteerAuthenticationForm
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
    """Set the new password. The link's validity is checked by Django.

    ⚠️ Following this link also confirms the address (2026-08-19), because it
       **proves exactly the same thing** the six-digit code does: somebody read
       mail sent there. Without this, a person who registered, never opened the
       confirmation mail, and later used "forgot password" would set a new
       password and still be refused at the login box — holding a link from
       that very inbox. Two proofs of one fact, one of them not counted.
    """

    template_name = "accounts/password_reset_confirm.html"
    # Capped, unlike SetPasswordForm — see the form's docstring.
    form_class = VolunteerSetPasswordForm
    success_url = reverse_lazy("accounts:password_reset_complete")

    def form_valid(self, form):
        # ⚠️ After the password is actually set, not before: `self.user` is
        #    populated by Django's dispatch, but the link is only spent here.
        response = super().form_valid(form)
        mark_email_verified(self.user)
        return response


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
