import datetime
import json
import re
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.core import mail
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse, reverse_lazy

from django.contrib.staticfiles import finders

from contact.admin import ContactAdmin
from contact.forms import ContactAdminForm
from contact.models import Contact, EmergencyContact, Language, RelationshipType
from core.ratelimit import client_ip
from core.timeutils import local_now

from .models import EmailVerification
from .services import mark_email_verified, register_account, send_verification_code

User = get_user_model()


class CustomUserModelTests(TestCase):
    """The custom User model and its optional link to Contact (see goal.md D12)."""

    def test_auth_user_model_points_at_accounts_user(self):
        # Pins down that AUTH_USER_MODEL is actually wired up. Swapping it once
        # the table holds real accounts is the migration everyone dreads, so a
        # settings typo needs to fail here rather than months from now.
        self.assertEqual(User._meta.label, "accounts.User")

    def test_superuser_can_be_created_without_a_contact(self):
        # A superuser is a technical account matching no real person. This is
        # the case D12 requires `contact` to be nullable for.
        user = User.objects.create_superuser(email="root@example.com", password="x")
        self.assertIsNone(user.contact)

    def test_user_can_be_linked_to_a_contact(self):
        contact = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL,
            legal_first_name="Ping",
            legal_last_name="Nguyen",
        )
        user = User.objects.create_user(email="anguyen@example.com", password="x", contact=contact)
        self.assertEqual(user.contact, contact)
        self.assertEqual(contact.user, user)   # related_name resolves in reverse


class RegistrationTests(TestCase):
    """P1: one new account, one new Contact — and neither half on its own."""

    def test_registering_creates_both_a_user_and_a_contact(self):
        user = register_account(
            email="lisi@example.com", password="a-good-long-password",
            legal_last_name="李", legal_first_name="四",
        )
        self.assertIsNotNone(user.contact)
        self.assertEqual(user.contact.legal_last_name, "李")
        self.assertEqual(Contact.objects.count(), 1)

    def test_a_failed_registration_leaves_neither(self):
        # One transaction. Half a registration — a User with no Contact — is
        # worse than none: they can log in, every page reading user.contact
        # fails for them, and nothing in the data says why.
        User.objects.create(email="taken@example.com")
        with self.assertRaises(IntegrityError), transaction.atomic():
            register_account(
                email="taken@example.com", password="a-good-long-password",
                legal_last_name="王", legal_first_name="Ping")
        self.assertEqual(Contact.objects.count(), 0)

    def test_a_new_account_is_not_staff(self):
        user = register_account(
            email="lisi@example.com", password="a-good-long-password",
            legal_last_name="李", legal_first_name="Ping")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(user.groups.count(), 0)

    def test_a_volunteer_account_gets_403_on_admin(self):
        """D21's first requirement: refused, not redirected to a login form.

        Django's own behaviour is a 302 to the admin login page, which then
        tells an already-signed-in volunteer to enter a staff password — a lie
        and a loop. This test was originally written around that redirect while
        still being named after a 403, which made it a test whose name did not
        match what it checked; core.middleware.StaffOnlyAdminMiddleware is what
        makes the name true.
        """
        register_account(email="lisi@example.com", password="a-good-long-password",
                         legal_last_name="李", legal_first_name="Ping")
        self.client.login(email="lisi@example.com", password="a-good-long-password")
        for path in ["/admin/", "/admin/events/event/"]:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 403)

    def test_an_anonymous_visitor_is_still_sent_to_the_admin_login(self):
        # Deliberately untouched: they may be staff who have not signed in yet,
        # and Django's redirect is the right answer for them. The rule is about
        # accounts that are signed in and still not staff.
        self.assertEqual(self.client.get("/admin/").status_code, 302)

    def test_a_staff_account_still_reaches_the_admin(self):
        # The other side of the guard: it must refuse volunteers without
        # refusing the people the admin is for.
        User.objects.create_superuser(email="root@example.com", password="a-good-long-password")
        self.client.login(email="root@example.com", password="a-good-long-password")
        self.assertEqual(self.client.get("/admin/").status_code, 200)

    def test_logging_out_is_never_refused(self):
        # Somebody whose staff flag was removed mid-session must still be able
        # to leave a site that refuses them every other page.
        register_account(email="lisi@example.com", password="a-good-long-password",
                         legal_last_name="李", legal_first_name="Ping")
        self.client.login(email="lisi@example.com", password="a-good-long-password")
        self.assertNotEqual(self.client.post("/admin/logout/").status_code, 403)

    def test_user_contact_may_still_be_null(self):
        # P1 is a rule about this flow, not about the column. A rule with a
        # legitimate exception (technical accounts) does not belong in a
        # constraint, because a constraint admits no exceptions — D9's own test,
        # applied against itself.
        self.assertTrue(User._meta.get_field("contact").null)
        User.objects.create_superuser(email="root@example.com", password="x")

    def test_registration_does_not_hard_block_on_a_duplicate_name_and_phone(self):
        # ContactForm's hard block is for a member of staff typing somebody in.
        # On self-service it would tell a real volunteer they already exist
        # while giving them no way in. Register, and let merge_contacts() sort
        # duplicates out afterwards.
        Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL,
            legal_last_name="王", legal_first_name="强", phone="+14085550101",
        )
        user = register_account(
            email="wangqiang@example.com", password="a-good-long-password",
            legal_last_name="王", legal_first_name="强", phone="+14085550101",
        )
        self.assertEqual(Contact.objects.filter(legal_last_name="王").count(), 2)
        self.assertIsNotNone(user.contact)


class RegistrationPageTests(TestCase):
    def test_the_registration_page_creates_an_account_and_asks_for_the_code(self):
        """🔴 2026-08-19：注册**不再直接登录**。

        这条测试原来叫 `..._and_logs_in`，断言的是注册完就有 session。
        现在账号建好了、但没人登进去 —— 中间隔着一封信。改的是流程本身，
        不是这条断言写错了。
        """
        response = self.client.post(reverse("accounts:register"), {
            "email": "lisi@example.com",
            "password": "a-good-long-password",
            "legal_last_name": "李",
            "legal_first_name": "四",
        })
        self.assertRedirects(response, reverse("accounts:verify_email"))
        self.assertTrue(User.objects.filter(email="lisi@example.com").exists())
        self.assertIsNone(self.client.session.get("_auth_user_id"))

    def test_a_weak_password_is_refused_and_nothing_is_created(self):
        response = self.client.post(reverse("accounts:register"), {
            "email": "lisi@example.com", "password": "123", "legal_last_name": "李",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.count(), 0)
        self.assertEqual(Contact.objects.count(), 0)

    def test_an_absurdly_long_email_is_a_form_error_not_a_500(self):
        # ⚠️ This was a 500 before core/limits.py: forms.EmailField carries no
        #    max_length of its own, so a 400-character address passed validation
        #    and died at the INSERT against varchar(254). The failure looked like
        #    a database problem on the most ordinary page in the project.
        response = self.client.post(reverse("accounts:register"), {
            "email": "l" * 300 + "@example.com",
            "password": "a-good-long-password", "legal_last_name": "李",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.count(), 0)
        self.assertEqual(Contact.objects.count(), 0)

    def test_an_absurdly_long_password_is_refused_before_it_is_hashed(self):
        # Not about storage — a hash is a fixed 78 bytes whatever went in. It is
        # about the hashing itself, which is deliberately slow: a megabyte of
        # "password" is CPU spent per request before anything is stored.
        response = self.client.post(reverse("accounts:register"), {
            "email": "lisi@example.com", "password": "x" * 5000,
            "legal_last_name": "李",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.count(), 0)

    def test_a_duplicate_email_is_a_form_error_not_a_500(self):
        # The address is the login name, so registering twice with it is the
        # duplicate case. Checked case-insensitively, matching the constraint:
        # an exact check here would pass the form and then fail at the INSERT.
        User.objects.create_user(email="lisi@example.com", password="x")
        response = self.client.post(reverse("accounts:register"), {
            "email": "LISI@example.com",
            "password": "a-good-long-password", "legal_last_name": "李",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Contact.objects.count(), 0)


class EmailIsTheLoginNameTests(TestCase):
    """The address is the login name and there is no username (2026-08-06).

    ⚠️ Half of these guard against a **silent** outcome rather than a crash. An
       account whose stored address differs in case from what its owner types is
       an account that reports "wrong password" forever; two accounts that differ
       only in case are one inbox with two logins and no way to tell which you
       are in. Neither raises anything.
    """

    def test_there_is_no_username_field_left(self):
        # `username = None` removes the column rather than leaving a unique NOT
        # NULL field that every insert has to invent a value for.
        self.assertFalse(any(f.name == "username" for f in User._meta.get_fields()))
        self.assertEqual(User.USERNAME_FIELD, "email")

    def test_the_manager_lowercases_the_whole_address(self):
        # ⚠️ Not what BaseUserManager.normalize_email does — it lowercases the
        #    domain only, leaving the local part as typed.
        user = User.objects.create_user(email="Mei@Example.COM", password="x")
        self.assertEqual(user.email, "mei@example.com")

    def test_a_mixed_case_address_still_logs_in(self):
        User.objects.create_user(email="mei@example.com", password="a-good-long-password")
        self.assertTrue(
            self.client.login(email="MEI@Example.com", password="a-good-long-password"))

    def test_two_accounts_cannot_differ_only_in_case(self):
        # Written straight through the model, which is the path the manager's
        # lowercasing does not cover: a shell session, a fixture, a migration.
        # The constraint is what holds there.
        User.objects.create_user(email="mei@example.com", password="x")
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create(email="MEI@EXAMPLE.COM")

    def test_the_login_page_asks_for_an_email(self):
        # Django builds AuthenticationForm's label from USERNAME_FIELD's
        # verbose_name, so this follows from the model — but a page that still
        # says "Username" is a page nobody can get past.
        response = self.client.get(reverse("accounts:login"))
        self.assertContains(response, "Email address")
        self.assertNotContains(response, "Username")

    def test_the_register_page_no_longer_asks_for_a_username(self):
        response = self.client.get(reverse("accounts:register"))
        self.assertNotContains(response, "Username")

    def test_createsuperuser_needs_only_an_address(self):
        # `createsuperuser` calls create_superuser() with the USERNAME_FIELD, so
        # a manager whose signature still said `username` would fail here with a
        # TypeError from inside Django rather than anywhere in our own code.
        call_command("createsuperuser", interactive=False, email="root@example.com",
                     verbosity=0)
        self.assertTrue(User.objects.get(email="root@example.com").is_superuser)

    def test_the_top_bar_shows_a_name_and_not_the_login_address(self):
        # ⭐ The whole reason display_name() exists. The address in the header of
        #    every page is somebody's private address on whatever screen is
        #    behind them.
        user = register_account(
            email="mei@example.com", password="a-good-long-password",
            legal_last_name="Mei", legal_first_name="Xiu",
        )
        self.client.force_login(user)
        response = self.client.get(reverse("events:event_list"))
        self.assertContains(response, "Xiu Mei")
        self.assertNotContains(response, "mei@example.com")

    def test_a_technical_account_falls_back_to_its_address(self):
        # A superuser has no Contact by design (D12), so there is no name to
        # show. Falling through to the address beats showing nothing at all.
        root = User.objects.create_superuser(email="root@example.com", password="x")
        self.assertEqual(root.display_name(), "root@example.com")


class GooglePrefillTests(TestCase):
    """"Continue with Google" fills three boxes in. It is not a way to log in.

    ⚠️ The verification call is patched throughout: the real one fetches Google's
       signing certificates over the network, and a unit test that needs the
       internet goes red on a train. `accounts.google.verify_id_token` is one thin
       function with no logic precisely so that patching it replaces Google and
       nothing else.
    """

    CLIENT_ID = "1234.apps.googleusercontent.com"
    CLAIMS = {"email": "Mei@Example.com", "given_name": "Xiu", "family_name": "Mei"}

    def setUp(self):
        self.url = reverse("accounts:register_with_google")

    @override_settings(GOOGLE_OAUTH_CLIENT_ID=CLIENT_ID)
    def test_a_valid_credential_fills_the_three_boxes(self):
        with mock.patch("accounts.google.verify_id_token", return_value=self.CLAIMS):
            response = self.client.post(self.url, {"credential": "a.b.c"})
        self.assertEqual(response.status_code, 200)
        initial = response.context["form"].initial
        self.assertEqual(initial["email"], "mei@example.com")
        self.assertEqual(initial["legal_first_name"], "Xiu")
        self.assertEqual(initial["legal_last_name"], "Mei")

    @override_settings(GOOGLE_OAUTH_CLIENT_ID=CLIENT_ID)
    def test_it_creates_nothing_at_all(self):
        # ⭐ The claim the whole design rests on. Google says who somebody is;
        #    only the ordinary registration form makes an account.
        with mock.patch("accounts.google.verify_id_token", return_value=self.CLAIMS):
            self.client.post(self.url, {"credential": "a.b.c"})
        self.assertEqual(User.objects.count(), 0)
        self.assertEqual(Contact.objects.count(), 0)
        self.assertIsNone(self.client.session.get("_auth_user_id"))

    @override_settings(GOOGLE_OAUTH_CLIENT_ID=CLIENT_ID)
    def test_the_prefilled_form_is_not_pre_validated(self):
        # ⚠️ `initial`, not `data`. Binding it would greet somebody with
        #    "This field is required" under a password box they have not reached.
        with mock.patch("accounts.google.verify_id_token", return_value=self.CLAIMS):
            response = self.client.post(self.url, {"credential": "a.b.c"})
        self.assertFalse(response.context["form"].is_bound)
        self.assertNotContains(response, "This field is required")

    @override_settings(GOOGLE_OAUTH_CLIENT_ID=CLIENT_ID)
    def test_a_forged_credential_gives_an_empty_form_not_an_error(self):
        with mock.patch("accounts.google.verify_id_token",
                        side_effect=ValueError("bad signature")):
            response = self.client.post(self.url, {"credential": "forged"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["form"].initial, {})
        self.assertEqual(User.objects.count(), 0)

    @override_settings(GOOGLE_OAUTH_CLIENT_ID=CLIENT_ID)
    def test_a_transport_failure_is_not_a_500(self):
        # ⚠️ The broad `except` in identity_from(). Google being slow must not
        #    take the registration page down — the boxes just stay empty.
        with mock.patch("accounts.google.verify_id_token",
                        side_effect=OSError("connection reset")):
            response = self.client.post(self.url, {"credential": "a.b.c"})
        self.assertEqual(response.status_code, 200)

    @override_settings(GOOGLE_OAUTH_CLIENT_ID="")
    def test_with_no_client_id_the_button_is_not_drawn(self):
        # ⚠️ Not drawn rather than drawn-and-broken: a control that fails when
        #    pressed is what C0.5 spent a whole step removing.
        response = self.client.get(reverse("accounts:register"))
        self.assertNotContains(response, "accounts.google.com/gsi/client")
        self.assertNotContains(response, "Continue with Google")

    @override_settings(GOOGLE_OAUTH_CLIENT_ID=CLIENT_ID)
    def test_with_a_client_id_the_button_and_its_own_form_are_drawn(self):
        response = self.client.get(reverse("accounts:register"))
        self.assertContains(response, "accounts.google.com/gsi/client")
        self.assertContains(response, self.CLIENT_ID)
        # Our own form, with our own CSRF token — see the view for why Google's
        # documented login_uri pattern is not used.
        self.assertContains(response, "google-prefill-form")
        self.assertContains(response, "csrfmiddlewaretoken")

    @override_settings(GOOGLE_OAUTH_CLIENT_ID="")
    def test_it_verifies_nothing_when_unconfigured(self):
        # No client id means no audience to check the token against, and checking
        # a token against nothing is worse than not checking it.
        with mock.patch("accounts.google.verify_id_token") as verify:
            self.client.post(self.url, {"credential": "a.b.c"})
        verify.assert_not_called()

    def test_a_get_just_goes_to_the_registration_page(self):
        self.assertRedirects(
            self.client.get(self.url), reverse("accounts:register"))

    @override_settings(GOOGLE_OAUTH_CLIENT_ID=CLIENT_ID)
    def test_the_audience_is_passed_to_googles_verifier(self):
        # ⭐ Without an audience, a token issued to **somebody else's** Google
        #    application verifies here perfectly well. Patched at Google's own
        #    function rather than at ours, so what is asserted is the argument
        #    that actually reaches the library.
        from accounts.google import verify_id_token

        with mock.patch("google.oauth2.id_token.verify_oauth2_token") as verify:
            verify_id_token("a.b.c")
        self.assertEqual(verify.call_args.args[2], self.CLIENT_ID)


class RegistrationRateLimitTests(TestCase):
    """Registration is the only write an anonymous stranger can do, twice over.

    ⚠️ The rate is overridden down to something small in these tests. Overriding
       it is only possible because core/ratelimit.py reads the setting per
       request — a plain string on the decorator is read once at import, and
       `override_settings` would have appeared to work while changing nothing.
    """

    URL = reverse("accounts:register")

    def payload(self, email):
        return {"email": email, "password": "a-good-long-password",
                "legal_last_name": "李", "legal_first_name": "四"}

    def attempt(self, email, ip=None):
        """One registration attempt as a stranger.

        ⚠️ Logs out first, and that is not tidiness. Registering signs you in,
           and a signed-in request is exempt from these limits by design (see
           core/ratelimit.py) — so without the logout every attempt after the
           first was exempt, and the first draft of these tests passed nothing
           while looking like it tested the limit.
        """
        self.client.logout()
        extra = {"REMOTE_ADDR": ip} if ip else {}
        return self.client.post(self.URL, self.payload(email), **extra)

    @override_settings(REGISTRATION_RATELIMIT_PER_IP="2/h")
    def test_one_machine_is_cut_off_after_its_allowance(self):
        for n in range(2):
            self.attempt(f"a{n}@example.com")
        self.assertEqual(self.attempt("a2@example.com").status_code, 429)

    @override_settings(REGISTRATION_RATELIMIT_PER_IP="2/h")
    def test_a_refused_attempt_writes_nothing_at_all(self):
        # ⭐ The assertion that makes this a limit rather than a message: refused
        #    has to mean no User and no Contact, or the page is decoration.
        for n in range(2):
            self.attempt(f"a{n}@example.com")
        self.attempt("blocked@example.com")
        self.assertFalse(User.objects.filter(email="blocked@example.com").exists())
        self.assertEqual(Contact.objects.count(), 2)

    @override_settings(REGISTRATION_RATELIMIT_PER_IP="1/h",
                       REGISTRATION_RATELIMIT_SITE="1/h")
    def test_somebody_signed_in_cannot_spend_the_allowance(self):
        # ⭐ A denial of service from inside, and cheap: one account, then POST to
        #    /register/ in a loop until nobody else in the world can register.
        #    Signed-in requests are skipped entirely, counting included.
        self.client.force_login(
            User.objects.create_user(email="member@example.com", password="x"))
        for n in range(5):
            self.client.post(self.URL, self.payload(f"junk{n}@example.com"))
        # A stranger's allowance is untouched.
        self.assertEqual(self.attempt("mei@example.com").status_code, 302)

    @override_settings(REGISTRATION_RATELIMIT_PER_IP="1/h")
    def test_reloading_the_form_does_not_use_up_the_allowance(self):
        # method="POST" on the decorator. Counting GETs would mean somebody who
        # reloads the page twice can no longer register at all.
        for _ in range(5):
            self.assertEqual(self.client.get(self.URL).status_code, 200)
        # ⚠️ 落点是验证页而不是活动列表（2026-08-19 起注册不再直接登录）。
        self.assertRedirects(
            self.attempt("mei@example.com"), reverse("accounts:verify_email"))

    @override_settings(REGISTRATION_RATELIMIT_PER_IP="100/h",
                       REGISTRATION_RATELIMIT_SITE="2/h")
    def test_a_thousand_machines_asking_once_each_are_still_counted(self):
        # ⭐ The attack the per-IP limit cannot see. Every request here comes
        #    from a different address and is inside the per-IP allowance.
        for n in range(2):
            self.attempt(f"a{n}@example.com", ip=f"203.0.113.{n}")
        self.assertEqual(
            self.attempt("a2@example.com", ip="203.0.113.99").status_code, 429)

    @override_settings(REGISTRATION_RATELIMIT_PER_IP="1/h")
    def test_two_different_machines_do_not_share_one_allowance(self):
        # The other direction, and the one that matters for real volunteers: a
        # limit that pooled everybody would refuse the second person to sign up.
        self.attempt("a@example.com", ip="203.0.113.1")
        response = self.attempt("b@example.com", ip="203.0.113.2")
        self.assertRedirects(response, reverse("accounts:verify_email"))


class ClientIpTests(TestCase):
    """Which address the limit counts against. Both branches, because both fail quietly.

    ⚠️ Trusting X-Forwarded-For with nothing in front of the app is a rate limit
       that any caller can walk around by inventing a header — it looks like it
       is working right up until somebody tries. Not trusting it *behind* a proxy
       puts every visitor on earth in one bucket. Neither raises anything, so
       neither would be noticed without these.
    """

    def request_with(self, **meta):
        return RequestFactory().post("/register/", **meta)

    @override_settings(TRUST_PROXY_CLIENT_IP=False)
    def test_without_a_proxy_the_header_is_ignored(self):
        request = self.request_with(
            REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR="1.2.3.4")
        self.assertEqual(client_ip(request), "10.0.0.1")

    @override_settings(TRUST_PROXY_CLIENT_IP=True)
    def test_behind_a_proxy_the_last_hop_wins_not_the_first(self):
        # ⭐ "1.2.3.4" is whatever the caller typed; "10.0.0.9" is what our own
        #    proxy appended. Believing the first entry is an unlimited limiter.
        request = self.request_with(
            REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR="1.2.3.4, 10.0.0.9")
        self.assertEqual(client_ip(request), "10.0.0.9")

    @override_settings(TRUST_PROXY_CLIENT_IP=True)
    def test_a_missing_header_falls_back_rather_than_failing(self):
        # Health checks and anything reaching the app directly carry no header,
        # and an exception here would be a 500 on the registration page.
        self.assertEqual(client_ip(self.request_with(REMOTE_ADDR="10.0.0.1")), "10.0.0.1")


class UserAdminTests(TestCase):
    """The user admin, rebuilt around `email` when `username` was dropped.

    ⚠️ `search_fields` is why this class exists rather than trusting
       `manage.py check`. A field name there that the model does not have passes
       every system check and raises only when a person types something into the
       search box — checked by experiment, not assumed.
    """

    def setUp(self):
        self.client.force_login(
            User.objects.create_superuser(email="root@example.com", password="x"))
        self.target = User.objects.create_user(email="mei@example.com", password="x")

    def test_the_changelist_the_add_form_and_the_change_form_all_render(self):
        for name, args in [("changelist", []), ("add", []), ("change", [self.target.pk])]:
            with self.subTest(page=name):
                response = self.client.get(reverse(f"admin:accounts_user_{name}", args=args))
                self.assertEqual(response.status_code, 200)

    def test_searching_the_changelist_works(self):
        response = self.client.get(
            reverse("admin:accounts_user_changelist"), {"q": "mei@example.com"})
        self.assertContains(response, "mei@example.com")


class ProfileEmailIsTheLoginTests(TestCase):
    """Changing your address on the profile page changes what you log in with.

    ⚠️ This was a real gap before 2026-08-06, and a silent one: the profile page
       wrote `Contact.email` only, so somebody who corrected a mistyped address
       saw the new one on the page while password resets and login went on using
       the old one. Nothing anywhere reported the difference.
    """

    def setUp(self):
        self.user = register_account(
            email="wrong@example.com", password="a-good-long-password",
            legal_last_name="Mei", legal_first_name="Ping",
        )
        self.client.force_login(self.user)
        self.url = reverse("accounts:profile")

    def details(self, **overrides):
        fields = {"action": "save", "legal_last_name": "Mei",
                  "legal_first_name": "Ping", "email": "wrong@example.com"}
        fields.update(overrides)
        return fields

    def test_correcting_the_address_moves_the_login_with_it(self):
        self.client.post(self.url, self.details(email="right@example.com"))
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "right@example.com")
        self.assertEqual(self.user.contact.email, "right@example.com")
        # And it is the address that now logs in, which is the point.
        self.client.logout()
        self.assertTrue(
            self.client.login(email="right@example.com",
                              password="a-good-long-password"))

    def test_an_address_another_account_holds_is_a_form_error(self):
        User.objects.create_user(email="taken@example.com", password="x")
        response = self.client.post(self.url, self.details(email="TAKEN@example.com"))
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "wrong@example.com")

    def test_saving_without_touching_the_address_is_not_a_clash_with_yourself(self):
        # ⚠️ The exclude() in clean_email. Without it, saving the page at all
        #    reports that the address is taken — by the person saving it.
        response = self.client.post(self.url, self.details(legal_first_name="Xiu"))
        self.assertRedirects(response, self.url)


class PasswordChangeTests(TestCase):
    """Changing your own password, from the modal and from its own page.

    ⚠️ The two paths are one view on purpose, so most of these do not care which
       one they exercise — except where the difference **is** the behaviour:
       HX-Redirect on success, and the form fragment on failure.
    """

    OLD = "a-good-long-password"
    NEW = "another-good-long-password"

    def setUp(self):
        self.user = register_account(
            email="mei@example.com", password=self.OLD,
            legal_last_name="Mei", legal_first_name="Ping")
        self.client.force_login(self.user)
        self.url = reverse("accounts:password_change")

    def payload(self, **overrides):
        fields = {"old_password": self.OLD,
                  "new_password1": self.NEW, "new_password2": self.NEW}
        fields.update(overrides)
        return fields

    def test_the_password_actually_changes(self):
        self.client.post(self.url, self.payload())
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.NEW))

    def test_the_wrong_current_password_changes_nothing(self):
        response = self.client.post(self.url, self.payload(old_password="not-it"))
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.OLD))

    def test_two_different_new_passwords_are_refused(self):
        response = self.client.post(
            self.url, self.payload(new_password2="something-else-entirely"))
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.OLD))

    def test_a_weak_new_password_is_refused(self):
        # Django's configured validators, so the rules are the ones in settings
        # rather than a second opinion.
        self.client.post(self.url, self.payload(new_password1="123", new_password2="123"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.OLD))

    def test_changing_it_does_not_sign_you_out(self):
        # ⭐ update_session_auth_hash. Without it Django rotates the session hash
        #    and the very next click is the login page — which looks exactly like
        #    the change having failed, so people change it again.
        self.client.post(self.url, self.payload())
        response = self.client.get(reverse("accounts:profile"))
        self.assertEqual(response.status_code, 200)

    def test_the_htmx_path_answers_a_named_redirect_on_success(self):
        # ⚠️ Not a 302. HTMX follows a redirect itself and would swap a whole
        #    page into the modal.
        response = self.client.post(
            self.url, self.payload(), headers={"HX-Request": "true"})
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response["HX-Redirect"], reverse("accounts:profile"))

    def test_the_htmx_path_answers_the_form_again_on_failure(self):
        # The fragment, not the whole page — it is swapped into the open modal,
        # so a full page here would put a second navigation bar inside a dialog.
        response = self.client.post(
            self.url, self.payload(old_password="not-it"),
            headers={"HX-Request": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "<!doctype html>")
        self.assertContains(response, "Current password")

    def test_the_plain_page_stands_on_its_own(self):
        # ⭐ D24's progressive-enhancement rule. The profile page's button is a
        #    link to this address, so with no JavaScript this page **is** the
        #    feature — it cannot be a placeholder.
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        for label in ["Current password", "New password", "Confirm new password"]:
            self.assertContains(response, label)

    def test_it_needs_a_login(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertRedirects(response, f"{reverse('accounts:login')}?next={self.url}")

    def test_an_absurdly_long_new_password_is_refused_before_hashing(self):
        response = self.client.post(
            self.url, self.payload(new_password1="x" * 5000, new_password2="x" * 5000))
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.OLD))


class LoginInformationCardTests(TestCase):
    """The Login information section on the profile page."""

    def setUp(self):
        self.user = register_account(
            email="mei@example.com", password="a-good-long-password",
            legal_last_name="Mei", legal_first_name="Ping")
        self.client.force_login(self.user)

    def test_it_shows_the_login_address_and_a_way_to_change_the_password(self):
        response = self.client.get(reverse("accounts:profile"))
        self.assertContains(response, "Login Information")
        self.assertContains(response, "mei@example.com")
        self.assertContains(response, reverse("accounts:password_change"))

    def test_the_change_password_control_is_a_real_link(self):
        # ⚠️ Not a button with an Alpine handler. Without JavaScript an
        #    x-on:click button is a control that does nothing at all, and this is
        #    the only route to a password change.
        response = self.client.get(reverse("accounts:profile"))
        self.assertContains(
            response, f'href="{reverse("accounts:password_change")}"')


class PasswordRevealTests(TestCase):
    """Every password box on the site gets the same show/hide control.

    ⚠️ Checked on all three pages rather than one, because the whole point of
       putting it in `field.html` was that there is one implementation. A test
       that only looked at the change-password page would pass just as happily if
       login and register had been left behind.
    """

    def test_every_password_box_has_a_reveal_control(self):
        # login and register turn a signed-in visitor away, and password_change
        # turns a stranger away — so the pages are fetched in the state each of
        # them is actually used in.
        pages = [("accounts:login", False), ("accounts:register", False),
                 ("accounts:password_change", True)]
        user = register_account(
            email="mei@example.com", password="a-good-long-password",
            legal_last_name="Mei", legal_first_name="Ping")
        for name, signed_in in pages:
            with self.subTest(page=name):
                if signed_in:
                    self.client.force_login(user)
                else:
                    self.client.logout()
                response = self.client.get(reverse(name))
                self.assertContains(response, "field-password-toggle")
                self.assertContains(response, "Show password")

    def test_the_reveal_button_never_submits_the_form(self):
        # ⚠️ A <button> defaults to type="submit". On the login page that would
        #    mean "show me what I typed" tries to log in with half a password.
        response = self.client.get(reverse("accounts:login"))
        html = response.content.decode()
        toggle = html[html.index("field-password-toggle") - 400:
                      html.index("field-password-toggle")]
        self.assertIn('type="button"', toggle)


class ProfilePageTests(TestCase):
    """C0.2.5: the volunteer can correct their own details.

    Every field here was a dead end before this page. The birth date is the
    expensive one: left blank at registration, Contact.is_minor returns None,
    the cautious branch applies, and that person is asked for guardian consent
    at every signup forever — with no way to fix it short of a staff account.
    """

    def setUp(self):
        self.user = register_account(
            email="mei@example.com", password="a-good-long-password",
            legal_last_name="Mei", legal_first_name="Ping",
        )
        self.client.force_login(self.user)
        self.url = reverse("accounts:profile")

    def contact(self):
        self.user.contact.refresh_from_db()
        return self.user.contact

    def details(self, **overrides):
        fields = {
            "action": "save",
            "legal_first_name": "Ping",
            "legal_last_name": "Mei",
            "email": "mei@example.com",
            "phone": "",
            "birth_date": "",
            "preferred_communication_method": "",
            "address_street": "",
            "address_city": "",
            "address_state": "",
            "address_postal_code": "",
            "address_country": "",
        }
        fields.update(overrides)
        return fields

    def test_filling_in_a_birth_date_ends_the_consent_prompt(self):
        # The whole reason this page is a Phase B gap rather than a nicety.
        self.assertIsNone(self.contact().is_minor)
        self.client.post(self.url, self.details(birth_date="1990-05-04"))
        self.assertIs(self.contact().is_minor, False)

    def test_the_email_cannot_be_cleared(self):
        # An account that can log in has to be recoverable (password reset) and
        # tellable (signup confirmations, event changes). Enforced on the forms
        # that make and edit accounts, not by a constraint: the same table holds
        # walk-ins a coordinator wrote down and organisations, and neither has
        # an inbox. See ProfileForm.__init__.
        response = self.client.post(self.url, self.details(email=""))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.contact().email, "mei@example.com")

    def test_the_address_is_optional_in_full(self):
        # Partial is not wrong. Refusing it would only teach people to invent
        # the boxes they cannot fill.
        self.client.post(self.url, self.details(address_city="San Jose"))
        self.assertEqual(self.contact().address_city, "San Jose")
        self.assertEqual(self.contact().address_street, "")

    def test_a_wrong_email_can_be_corrected_by_its_owner(self):
        # Otherwise the fix for "I never got the notice" is a staff account.
        self.client.post(self.url, self.details(email="right@example.com"))
        self.assertEqual(self.contact().email, "right@example.com")

    def test_a_minor_may_freely_raise_their_own_birth_date(self):
        # ⚠️ Asserted because it was decided, not because it is safe: a minor
        #    can walk past the consent gate this way. Decided 2026-07-31; the
        #    mitigation is that Contact carries simple-history, so the change
        #    is on the record. If this test ever has to be inverted, the place
        #    to look is phase-c.md's known-gaps table.
        self.client.post(self.url, self.details(birth_date="2015-01-01"))
        self.assertIs(self.contact().is_minor, True)
        self.client.post(self.url, self.details(birth_date="1990-01-01"))
        self.assertIs(self.contact().is_minor, False)

    def test_the_change_is_on_the_record(self):
        # The only thing standing behind the decision above.
        self.client.post(self.url, self.details(birth_date="1990-01-01"))
        self.assertGreaterEqual(self.contact().history.count(), 2)

    def test_an_emergency_contact_can_be_added_and_removed(self):
        relationship = RelationshipType.objects.get(code="parent")
        self.client.post(self.url, {
            "action": "add_kin", "name": "Wang Xiuying",
            "phone": "+14085550101", "email": "wang@example.com",
            "relationship_type": relationship.pk,
        })
        kin = self.contact().emergency_contacts.get()
        self.assertEqual(kin.name, "Wang Xiuying")

        self.client.post(self.url, {"action": "remove_kin", "kin": kin.pk})
        self.assertEqual(self.contact().emergency_contacts.count(), 0)

    def test_the_relationship_dropdown_is_not_empty(self):
        # It is a required FK. Before contact/0004 seeded the vocabulary this
        # page could be opened but never submitted.
        response = self.client.get(self.url)
        choices = response.context["kin_form"].fields["relationship_type"].queryset
        self.assertGreater(choices.count(), 0)

    def test_somebody_elses_emergency_contact_cannot_be_removed(self):
        other = register_account(
            email="other@example.com", password="a-good-long-password",
            legal_last_name="Other", legal_first_name="Ping",
        )
        theirs = EmergencyContact.objects.create(
            person=other.contact, name="Theirs", phone="+14085550102",
            relationship_type=RelationshipType.objects.get(code="parent"),
        )
        response = self.client.post(self.url, {"action": "remove_kin", "kin": theirs.pk})
        self.assertEqual(response.status_code, 404)
        self.assertTrue(EmergencyContact.objects.filter(pk=theirs.pk).exists())

    def test_the_page_needs_a_login(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_a_superuser_has_nothing_to_edit_here(self):
        # No Contact by design (D12). Rendering the form would create a stray one.
        root = get_user_model().objects.create_superuser(email="root@example.com", password="x")
        self.client.force_login(root)
        self.assertEqual(self.client.get(self.url).status_code, 403)


class ProfileLanguageAndKinEmailTests(TestCase):
    """2026-08-05 feedback: two fields the volunteer could not reach.

    `preferred_language` has been on Contact since the data-core phase, narrowed
    to living languages — it was simply never offered to the person it describes,
    so only a staff account could set it. No migration was needed for it; the
    field existed and the form did not list it.
    """

    def setUp(self):
        self.user = register_account(
            email="lang@example.com", password="a-good-long-password",
            legal_last_name="Lang", legal_first_name="Ping")
        self.client.force_login(self.user)
        self.url = reverse("accounts:profile")

    def test_preferred_language_is_offered_and_saved(self):
        english = Language.objects.get(code="eng")
        response = self.client.post(self.url, {
            "action": "save", "legal_last_name": "Lang", "legal_first_name": "Ping",
            "email": "lang@example.com", "preferred_language": english.pk,
        })
        self.assertEqual(response.status_code, 302)
        self.user.contact.refresh_from_db()
        self.assertEqual(self.user.contact.preferred_language, english)

    def test_only_living_languages_are_offered(self):
        # Same narrowing the admin form already had: Latin is in the table, not
        # in the dropdown.
        choices = self.client.get(self.url).context["form"].fields[
            "preferred_language"].queryset
        self.assertFalse(choices.filter(code="lat").exists())

    def test_an_emergency_contact_without_an_email_is_refused(self):
        """⚠️ Required, and refused at the form rather than silently stored blank.

        The cost of this rule is in phase-c.md's known gaps: a guardian who left
        only a phone number cannot be recorded at all.
        """
        relationship = RelationshipType.objects.get(code="parent")
        self.client.post(self.url, {
            "action": "add_kin", "name": "No Email",
            "phone": "+14085550103", "relationship_type": relationship.pk,
        })
        self.assertEqual(self.user.contact.emergency_contacts.count(), 0)


class RequiredNameTests(TestCase):
    """两个名字都必填 —— 而这条守卫真正钉的是「删掉 last name 整个网站就蹦了」。

    起因是三处口径各不相同：数据库自 D9 起就要求个人有 last name，Register 跟着
    要了，My profile 两个都没要。于是在 My profile 里清空姓氏，表单一声不吭地
    通过，然后在 INSERT 上炸成 IntegrityError —— 一个 500，而页面上没有任何
    一行字说哪里错了。

    🔴 而且 Django 自己的约束校验**接不住它**：ModelForm 会跳过任何提到了表单
       没有渲染的字段的约束，而这三条 Contact 约束都提到 `contact_type`，
       ProfileForm 刻意不提供那一格。admin 没事只是因为 ContactAdminForm 是
       `fields = "__all__"`。所以拦住这个 500 的只有 `required = True` 那两行。
    """

    def setUp(self):
        self.user = register_account(
            email="mei@example.com", password="a-good-long-password",
            legal_first_name="Ping", legal_last_name="Mei")
        self.client.force_login(self.user)
        self.url = reverse("accounts:profile")

    def details(self, **overrides):
        fields = {"action": "save", "legal_first_name": "Ping",
                  "legal_last_name": "Mei", "email": "mei@example.com"}
        fields.update(overrides)
        return fields

    def contact(self):
        self.user.contact.refresh_from_db()
        return self.user.contact

    def test_clearing_the_last_name_is_a_form_error_and_not_a_500(self):
        # ⭐ 报上来的那个崩溃本身。
        response = self.client.post(self.url, self.details(legal_last_name=""))
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "legal_last_name",
                             "This field is required.")
        self.assertEqual(self.contact().legal_last_name, "Mei")

    def test_clearing_the_first_name_is_a_form_error_too(self):
        response = self.client.post(self.url, self.details(legal_first_name=""))
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "legal_first_name",
                             "This field is required.")
        self.assertEqual(self.contact().legal_first_name, "Ping")

    def test_the_database_would_have_refused_it_anyway(self):
        """约束是那条规则**唯一**的一份声明（D14），表单只是把它提前说出来。

        ⚠️ 这条不是重复上面两条：上面两条钉的是「用户看到一句话」，这一条钉的是
           「就算绕过表单也写不进去」—— 脚本、bulk_create、psql 都走这里。
        """
        for field in ("legal_first_name", "legal_last_name"):
            with self.subTest(field=field), \
                    self.assertRaises(IntegrityError), transaction.atomic():
                Contact.objects.create(**{
                    "contact_type": Contact.ContactType.INDIVIDUAL,
                    "legal_first_name": "Ping", "legal_last_name": "Mei",
                    field: "",
                })

    def test_both_boxes_are_marked_required_on_the_profile_page(self):
        # 红色的 * 由 core/components/field.html 按 `required` 画，所以这里只要
        # 判 `required` 就够 —— 星号的样式归那个组件，它自己有守卫。
        form = self.client.get(self.url).context["form"]
        self.assertTrue(form.fields["legal_first_name"].required)
        self.assertTrue(form.fields["legal_last_name"].required)

    def test_both_boxes_are_marked_required_on_the_register_page(self):
        self.client.logout()
        form = self.client.get(reverse("accounts:register")).context["form"]
        self.assertTrue(form.fields["legal_first_name"].required)
        self.assertTrue(form.fields["legal_last_name"].required)

    def test_the_red_star_actually_reaches_the_page(self):
        """⚠️ 一条端到端的，因为「必填」和「看得出必填」是两件事。

        `required` 为真而模板没画星号，是一个在表单层完全测不出来的缺陷。
        """
        html = self.client.get(self.url).content.decode()
        for label in ("First name", "Last name"):
            with self.subTest(label=label):
                self.assertRegex(html, label + r'<span class="text-danger-fg[^"]*"[^>]*> \*</span>')

    def test_registering_without_a_first_name_is_refused(self):
        self.client.logout()
        response = self.client.post(reverse("accounts:register"), {
            "email": "new@example.com", "password": "a-good-long-password",
            "legal_last_name": "李",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context["form"], "legal_first_name",
                             "This field is required.")
        self.assertFalse(User.objects.filter(email="new@example.com").exists())


class DuplicateEmergencyContactTests(TestCase):
    """同一个紧急联系人加第二次 —— 也是一个 500，同一个成因（2026-08-19）。

    `emergencycontact_unique_per_person` 横跨 (person, name, phone)，而 `person`
    是在 `__init__` 里挂到 instance 上的、不是表单渲染的字段 —— 于是 Django 把
    整条约束跳过了。表现：连点两下 Add，或者一年后又把妈妈写了一遍，页面 500。
    """

    def setUp(self):
        self.user = register_account(
            email="mei@example.com", password="a-good-long-password",
            legal_first_name="Ping", legal_last_name="Mei")
        self.client.force_login(self.user)
        self.url = reverse("accounts:profile")
        self.parent = RelationshipType.objects.get(code="parent")

    def add(self):
        return self.client.post(self.url, {
            "action": "add_kin", "name": "Mum", "phone": "+14085550101",
            "email": "mum@example.com", "relationship_type": self.parent.pk,
        })

    def test_adding_the_same_person_twice_is_a_sentence_and_not_a_500(self):
        self.add()
        response = self.add()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(EmergencyContact.objects.count(), 1)
        # 文案来自约束的 violation_error_message，不是这里第二次写下的规则；
        # 它落在 name 那一格靠 CONSTRAINT_FIELD["emergency_contact_duplicate"]。
        self.assertContains(response, "This emergency contact is already recorded for them.")


class ProfileAddressStateTests(TestCase):
    """先填 country，是 US 时 state 变成下拉 —— 和 Contact admin 一样（2026-08-19）。

    用户报的原话是「my profile 应该像 django admin 的 contact 一样」。所以这里
    做的**不是**再实现一遍，而是让两页读同一个文件、同一份州清单。
    """

    def setUp(self):
        self.user = register_account(
            email="mei@example.com", password="a-good-long-password",
            legal_first_name="Ping", legal_last_name="Mei")
        self.client.force_login(self.user)
        self.page = self.client.get(reverse("accounts:profile"))

    def test_country_is_asked_before_the_rest_of_the_address(self):
        """⚠️ 顺序本身就是功能：它是下一格依赖的那一格。

        排在最后的话，人会先把州名打进一个文本框，然后眼看着它在底下变成下拉；
        或者更常见 —— 整个地址填完了，压根不知道有个下拉。
        """
        order = list(self.page.context["form"].fields)
        address = [name for name in order if name.startswith("address_")]
        self.assertEqual(address[0], "address_country")
        self.assertLess(order.index("address_country"), order.index("address_state"))

    def test_the_state_box_carries_the_list_the_picker_needs(self):
        widget = self.page.context["form"].fields["address_state"].widget
        states = json.loads(widget.attrs["data-us-states"])
        self.assertIn(["CA", "California"], states)
        # 50 个州 + DC + 属地 + 军邮 —— 不在这里写死数量，写死的是「不是空的」
        # 和「确实是从 localflavor 来的」。
        self.assertGreater(len(states), 50)

    def test_both_pages_are_handed_the_same_list(self):
        """一份清单，两个表单。抄一份的代价是 localflavor 加了个属地之后，
        没人看的那一份先过期，而两页都不会报错。
        """
        admin_widget = ContactAdminForm().fields["address_state"].widget
        self.assertEqual(admin_widget.attrs["data-us-states"],
                         self.page.context["form"].fields["address_state"].widget.attrs["data-us-states"])

    def test_the_page_loads_the_same_script_the_admin_does(self):
        self.assertContains(self.page, "contact/address_state_toggle.js")

    def test_the_state_is_still_free_text_and_still_optional(self):
        """⚠️ 下拉是**增强**，不是校验规则。

        列是自由文本，所以非美国地址写得下「Ontario」；而且它不必填 ——
        只想改个手机号的人不该被一个不相干的必填项拦住。
        """
        form = self.page.context["form"]
        self.assertFalse(form.fields["address_state"].required)
        response = self.client.post(reverse("accounts:profile"), {
            "action": "save", "legal_first_name": "Ping", "legal_last_name": "Mei",
            "email": "mei@example.com", "address_country": "CA",
            "address_state": "Ontario",
        })
        self.assertEqual(response.status_code, 302)
        self.user.contact.refresh_from_db()
        self.assertEqual(self.user.contact.address_state, "Ontario")

    def test_the_script_is_not_an_admin_asset_any_more(self):
        """它现在两页共用，所以它不在 admin/ 下面 —— 而 admin 也得跟着改路径。

        ⚠️ 这条钉的是**两边都指着同一个文件**。少改一处的表现是 admin 的州下拉
           静默消失（404 的 <script> 不报任何东西给用户），而 My profile 完全正常。
        """
        self.assertIsNotNone(finders.find("contact/address_state_toggle.js"))
        self.assertIsNone(finders.find("contact/admin/address_state_toggle.js"))
        self.assertIn("contact/address_state_toggle.js", ContactAdmin.Media.js)


class EmailVerificationFlowTests(TestCase):
    """注册之后要去邮箱收验证码（2026-08-19）。

    在这之前，注册就是「填完表单 → 建号 → 当场登录」，地址是什么就是什么。
    两件事因此一直是可能的，而且都不报错：打错一个字母的人从此收不到任何东西
    （重置链接、报名确认、活动改期），而故意填别人地址的人让这个系统去给陌生人
    发信。

    口径是最严的那一档（2026-08-19 拍板）：**验过才能登录**。
    """

    URL = reverse_lazy("accounts:register")

    def register(self, **overrides):
        payload = {"email": "lisi@example.com", "password": "a-good-long-password",
                   "legal_last_name": "李", "legal_first_name": "四"}
        payload.update(overrides)
        return self.client.post(self.URL, payload)

    def code_from_the_email(self):
        """把信里那六位数抠出来 —— 从**信本身**，不从数据库。

        ⚠️ 这一条很要紧：库里存的是哈希，所以任何「直接读一下码」的写法都必须
           去读那封信。测试因此走的是真人走的那条路，而不是一条只有测试能走的。
        """
        self.assertTrue(mail.outbox, "没有发出验证码邮件")
        found = re.search(r"\b(\d{6})\b", mail.outbox[-1].body)
        self.assertIsNotNone(found, mail.outbox[-1].body)
        return found.group(1)

    def test_registering_creates_the_account_but_signs_nobody_in(self):
        response = self.register()
        self.assertRedirects(response, reverse("accounts:verify_email"))
        user = User.objects.get(email="lisi@example.com")
        self.assertFalse(user.email_verified)
        self.assertIsNone(self.client.session.get("_auth_user_id"))

    def test_the_code_arrives_by_email_and_is_not_in_the_subject(self):
        self.register()
        code = self.code_from_the_email()
        # ⚠️ 手机锁屏会把主题整条显示出来 —— 码写进主题等于贴在任何路过的人
        #    都看得见的屏幕上。
        self.assertNotIn(code, mail.outbox[-1].subject)
        self.assertEqual(mail.outbox[-1].to, ["lisi@example.com"])

    def test_typing_the_code_confirms_the_address_and_signs_them_in(self):
        self.register()
        response = self.client.post(reverse("accounts:verify_email"),
                                    {"code": self.code_from_the_email()})
        self.assertRedirects(response, reverse("events:event_list"))
        user = User.objects.get(email="lisi@example.com")
        self.assertTrue(user.email_verified)
        self.assertEqual(self.client.session.get("_auth_user_id"), str(user.pk))

    def test_the_stored_code_is_not_the_code(self):
        """⚠️ 库里存哈希。六位数只有一百万种，所以这不是护城河 ——

        真正的防线是 15 分钟有效期加 5 次上限。它买到的是：备份、psql、
        带着一行数据的报错页，都不会顺手交给别人一个还能用的码。
        """
        self.register()
        code = self.code_from_the_email()
        row = EmailVerification.objects.get()
        self.assertNotIn(code, row.code_hash)
        self.assertTrue(check_password(code, row.code_hash))

    def test_a_wrong_code_is_refused_and_costs_a_try(self):
        self.register()
        response = self.client.post(reverse("accounts:verify_email"), {"code": "000000"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "That code is wrong")
        self.assertEqual(EmailVerification.objects.get().attempts, 1)
        self.assertFalse(User.objects.get(email="lisi@example.com").email_verified)

    def test_a_code_stops_working_after_too_many_wrong_guesses(self):
        """⭐ 六位数是一百万种，5 次尝试不是难点 —— 难点是有效期。

        这条上限挡住的是「慢慢猜一千次」，那正是光有有效期挡不住的形状。
        """
        self.register()
        code = self.code_from_the_email()
        for _ in range(EmailVerification.MAX_ATTEMPTS):
            self.client.post(reverse("accounts:verify_email"), {"code": "000000"})
        # 连**对的**码都不行了 —— 这才叫这一码作废，而不是「再猜一次要等等」。
        response = self.client.post(reverse("accounts:verify_email"), {"code": code})
        self.assertContains(response, "expired or has already been used")
        self.assertFalse(User.objects.get(email="lisi@example.com").email_verified)

    def test_an_expired_code_is_refused(self):
        self.register()
        code = self.code_from_the_email()
        EmailVerification.objects.update(expires_at=local_now() - datetime.timedelta(seconds=1))
        response = self.client.post(reverse("accounts:verify_email"), {"code": code})
        self.assertContains(response, "expired or has already been used")

    def test_a_code_works_once(self):
        self.register()
        code = self.code_from_the_email()
        self.client.post(reverse("accounts:verify_email"), {"code": code})
        self.client.logout()
        # 再拿同一个码走一遍：连页面都进不去了（没有待验证的账号）。
        response = self.client.post(reverse("accounts:verify_email"), {"code": code})
        self.assertRedirects(response, reverse("accounts:login"))

    def test_asking_for_a_new_code_kills_the_old_one(self):
        """⚠️ 两个活的码意味着**旧那封信还有效**。

        于是「我怀疑有人看到了第一封信，所以重发一次」这个动作什么也没换来。

        ⚠️ 代价说清楚：重发之后再去照着第一封信打字，那**算一次错猜**（比的是
           当前那个码），而不是「这个码作废了」。分辨得出来的话要拿输入去和
           所有作废的行逐个哈希比一遍 —— 为了一句更好的提示，给每次输入加上
           一串哈希运算。不划算，所以没做，写在这里免得以后当成 bug 修。
        """
        self.register()
        first = self.code_from_the_email()
        EmailVerification.objects.update(
            created_at=local_now() - EmailVerification.RESEND_AFTER)
        self.client.post(reverse("accounts:resend_verification"))
        self.assertEqual(len(mail.outbox), 2)
        response = self.client.post(reverse("accounts:verify_email"), {"code": first})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.get(email="lisi@example.com").email_verified)
        # 而新的那个码照常好用。
        self.assertRedirects(
            self.client.post(reverse("accounts:verify_email"),
                             {"code": self.code_from_the_email()}),
            reverse("events:event_list"))

    def test_the_resend_button_is_rationed(self):
        # 被限住的表现是**不发信**，不是一句好话。
        self.register()
        self.client.post(reverse("accounts:resend_verification"))
        self.assertEqual(len(mail.outbox), 1)

    def test_resending_is_post_only(self):
        # ⚠️ 一个 GET 就能发信的端点，离「被链接预览自己触发一次」只差一次预取。
        self.register()
        self.assertEqual(
            self.client.get(reverse("accounts:resend_verification")).status_code, 405)

    def test_the_page_shows_the_address_it_sent_to(self):
        # 收不到信最可能的原因就是地址打错了，而看不到地址的人没法发现自己写了 .con。
        self.register(email="typo@example.con")
        self.assertContains(self.client.get(reverse("accounts:verify_email")),
                            "typo@example.con")

    def test_the_page_is_not_reachable_without_a_pending_account(self):
        # ⚠️ 谁在等验证码由 session 说，不由 URL 说 —— URL 上带 user id 的话，
        #    那就是一个谁都能替别人打开的页面。
        self.assertRedirects(self.client.get(reverse("accounts:verify_email")),
                             reverse("accounts:login"))

    def test_the_code_only_proves_the_address_it_was_sent_to(self):
        """⚠️ 码验的是**那封信寄到的那个地址**，不是「这个账号当前的地址」。

        中途改了地址，那个码就什么也没证明 —— 拿它去比 `user.email` 的写法
        会悄悄说它证明了。
        """
        self.register()
        code = self.code_from_the_email()
        User.objects.filter(email="lisi@example.com").update(email="other@example.com")
        response = self.client.post(reverse("accounts:verify_email"), {"code": code})
        self.assertContains(response, "no longer applies")
        self.assertFalse(User.objects.get(email="other@example.com").email_verified)

    def test_spaces_and_dashes_in_a_pasted_code_are_forgiven(self):
        # 人从邮件客户端里粘出来的东西经常带空格。拒绝它只教会人「这个框很挑」。
        self.register()
        code = self.code_from_the_email()
        response = self.client.post(reverse("accounts:verify_email"),
                                    {"code": f"{code[:3]} {code[3:]}"})
        self.assertRedirects(response, reverse("events:event_list"))

    def test_a_code_that_starts_with_zero_survives_the_round_trip(self):
        """⚠️ 前导零。码当成整数走一圈的话，「004821」会变成 4821，

        然后照着信打字的人被告知打错了 —— 而信上写的就是那个。
        """
        user = register_account(email="zero@example.com", password="a-good-long-password",
                                legal_first_name="Ping", legal_last_name="Mei")
        with mock.patch("accounts.services._new_code", return_value="004821"):
            send_verification_code(user)
        self.assertIn("004821", mail.outbox[-1].body)
        session = self.client.session
        session["pending_verification_user"] = user.pk
        session.save()
        response = self.client.post(reverse("accounts:verify_email"), {"code": "004821"})
        self.assertRedirects(response, reverse("events:event_list"))


class UnverifiedLoginTests(TestCase):
    """没验证的账号登不进来 —— 但也不能被关在门外没有路（2026-08-19）。"""

    def setUp(self):
        self.user = register_account(
            email="mei@example.com", password="a-good-long-password",
            legal_first_name="Ping", legal_last_name="Mei")

    def login(self):
        return self.client.post(reverse("accounts:login"), {
            "username": "mei@example.com", "password": "a-good-long-password"})

    def test_an_unverified_account_cannot_log_in(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "has not been confirmed")
        self.assertIsNone(self.client.session.get("_auth_user_id"))

    def test_being_refused_leaves_a_way_forward(self):
        """⚠️ 被拦下的是一个真人：密码是对的，只是没开过那封信。

        所以拦下的同时要把他标进 session，让验证页认得他 —— 否则「关掉标签页
        的人」在这个最严的流程里根本没有门。
        """
        self.login()
        page = self.client.get(reverse("accounts:verify_email"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "mei@example.com")

    def test_a_verified_account_logs_in_as_before(self):
        mark_email_verified(self.user)
        self.assertRedirects(self.login(), reverse("events:event_list"))

    def test_the_wrong_password_is_still_just_the_wrong_password(self):
        """🔴 这条钉的是那个检查**放在哪一层**。

        如果「没验证」在密码之前判，登录框就成了一个查询工具：随便打一个地址，
        看它回「没验证」还是「用户名或密码不对」，就能问出这个地址有没有注册过。
        `confirm_login_allowed` 跑在密码验过之后，所以问不出来。
        """
        response = self.client.post(reverse("accounts:login"), {
            "username": "mei@example.com", "password": "wrong-password"})
        self.assertNotContains(response, "has not been confirmed")

    def test_a_password_reset_also_confirms_the_address(self):
        """⚠️ 点开重置链接**证明的是同一件事**：这个人读到了寄到那儿的信。

        不认这一份证明的话，「注册了没验证 → 忘了密码 → 重置」的人会拿着
        从那个收件箱里点出来的链接，设完新密码，然后在登录框被拦下。
        """
        self.client.post(reverse("accounts:password_reset"), {"email": "mei@example.com"})
        link = re.search(r"https?://[^/]+(/password-reset/[^\s]+)", mail.outbox[-1].body)
        self.assertIsNotNone(link, mail.outbox[-1].body)
        follow = self.client.get(link.group(1), follow=True)
        self.client.post(follow.redirect_chain[-1][0] if follow.redirect_chain else link.group(1),
                         {"new_password1": "another-good-password",
                          "new_password2": "another-good-password"})
        self.user.refresh_from_db()
        self.assertTrue(self.user.email_verified)


class GoogleRegistrationVerificationTests(TestCase):
    """Google 已经验过的地址不必再验一次 —— 但只有它自己那个地址（2026-08-19）。"""

    IDENTITY = {"email": "mei@example.com", "legal_first_name": "Ping",
                "legal_last_name": "Mei", "email_verified": True}

    def prefill(self, identity=None):
        with mock.patch("accounts.views.identity_from",
                        return_value=dict(identity or self.IDENTITY)):
            return self.client.post(reverse("accounts:register_with_google"),
                                    {"credential": "any"})

    def register(self, email="mei@example.com"):
        return self.client.post(reverse("accounts:register"), {
            "email": email, "password": "a-good-long-password",
            "legal_last_name": "Mei", "legal_first_name": "Ping"})

    def test_a_google_verified_address_skips_the_code(self):
        self.prefill()
        response = self.register()
        self.assertRedirects(response, reverse("events:event_list"))
        self.assertEqual(mail.outbox, [])
        self.assertTrue(User.objects.get(email="mei@example.com").email_verified)

    def test_typing_a_different_address_over_it_does_not(self):
        """🔴 地址那一格是**可以改的**。

        Google 用一个账号登进来、然后把别人的地址打上去 —— 如果这里认的是
        「这次注册来自 Google」而不是「Google 认的就是这个地址」，那任何人
        都能拿任何一个 Google 账号去注册别人的邮箱。
        """
        self.prefill()
        response = self.register(email="somebody-else@example.com")
        self.assertRedirects(response, reverse("accounts:verify_email"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertFalse(User.objects.get(email="somebody-else@example.com").email_verified)

    def test_google_saying_the_address_is_unverified_is_believed(self):
        # ⚠️ Google 账号也可以挂着一个没验过的地址。
        self.prefill({**self.IDENTITY, "email_verified": False})
        self.assertRedirects(self.register(), reverse("accounts:verify_email"))
        self.assertEqual(len(mail.outbox), 1)

    def test_the_claim_is_not_taken_from_the_browser(self):
        """⚠️ 「这个地址已验证」放在 **session** 里，不放在表单的隐藏字段里。

        隐藏字段是浏览器发回来的值 —— 那等于给了任何人一个「我已验证」的勾。
        这条钉的是：不经过 Google 那一步、自己 POST 一个字段，是不管用的。
        """
        response = self.client.post(reverse("accounts:register"), {
            "email": "mei@example.com", "password": "a-good-long-password",
            "legal_last_name": "Mei", "legal_first_name": "Ping",
            "email_verified": "true", "google_verified_email": "mei@example.com",
        })
        self.assertRedirects(response, reverse("accounts:verify_email"))
        self.assertFalse(User.objects.get(email="mei@example.com").email_verified)


class SuperuserVerificationTests(TestCase):
    """createsuperuser 在一个收不到信的终端上跑。"""

    def test_a_superuser_is_verified_by_construction(self):
        # ⚠️ 不这样的话，一个刚部署好的站点的唯一管理员登不进自己的站 ——
        #    而那不是一条安全性质。能跑这个命令的人已经拿着数据库了。
        user = User.objects.create_superuser(email="root@example.com", password="x")
        self.assertTrue(user.email_verified)

    def test_an_ordinary_new_account_is_not(self):
        user = register_account(email="mei@example.com", password="a-good-long-password",
                                legal_first_name="Ping", legal_last_name="Mei")
        self.assertFalse(user.email_verified)


class PasswordResetTests(TestCase):
    """C3.2. Django's flow, this project's templates, and one limit.

    ⚠️ What is worth asserting here is **not** that Django's token works — that
       is Django's own test suite. It is the three decisions layered on top:
       who the flow refuses to serve, what it refuses to reveal, and that a
       refused request sends no mail. Every one of those, done wrong, looks
       exactly like a working page.
    """

    ASK = reverse("accounts:password_reset")

    def setUp(self):
        mail.outbox = []
        self.user = User.objects.create_user(
            email="mei@example.invalid", password="the-old-password-1")

    def ask_for(self, email, ip=None):
        extra = {"REMOTE_ADDR": ip} if ip else {}
        return self.client.post(self.ASK, {"email": email}, **extra)

    def link_from_the_email(self):
        found = re.search(r"https?://[^\s]+", mail.outbox[0].body)
        self.assertIsNotNone(found, "the message carries no link at all")
        return found.group(0).split("testserver", 1)[1]

    # --- the walk ---------------------------------------------------------

    def test_a_registered_volunteer_can_get_back_in(self):
        # ⭐ End to end, because the parts are only worth anything joined up:
        #    ask → receive → follow → set → log in with the new one.
        self.ask_for("mei@example.invalid")
        self.assertEqual(len(mail.outbox), 1)

        confirm = self.client.get(self.link_from_the_email(), follow=True)
        self.assertTrue(confirm.context["validlink"])
        self.client.post(confirm.request["PATH_INFO"], {
            "new_password1": "a-brand-new-password-42",
            "new_password2": "a-brand-new-password-42"})

        self.assertTrue(self.client.login(
            email="mei@example.invalid", password="a-brand-new-password-42"))

    def test_the_link_stops_working_once_it_is_used(self):
        # ⚠️ Asserted rather than assumed: a reset link sits in an inbox for
        #    ever, and an inbox is not a safe place. Django invalidates it by
        #    hashing the old password into the token, which is the sort of
        #    thing that keeps working right up until somebody "simplifies" it.
        self.ask_for("mei@example.invalid")
        link = self.link_from_the_email()
        confirm = self.client.get(link, follow=True)
        self.client.post(confirm.request["PATH_INFO"], {
            "new_password1": "a-brand-new-password-42",
            "new_password2": "a-brand-new-password-42"})

        again = self.client.get(link, follow=True)
        self.assertFalse(again.context["validlink"])
        self.assertContains(again, "That Link No Longer Works")

    # --- what it will not say ---------------------------------------------

    def test_an_unknown_address_gets_the_same_page_and_no_mail(self):
        # ⭐ The page must not become a way to ask "is this person a volunteer
        #    here". This database holds minors.
        known = self.ask_for("mei@example.invalid")
        mail.outbox = []
        unknown = self.ask_for("nobody@example.invalid")
        self.assertEqual(unknown.status_code, known.status_code)
        self.assertEqual(unknown["Location"], known["Location"])
        self.assertEqual(mail.outbox, [])

    def test_an_admin_entered_contact_with_no_account_gets_nothing(self):
        # The documented gap, pinned so that it stays the documented gap:
        # somebody entered from a paper list has no password to reset.
        Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL,
            legal_first_name="Ping",
            legal_last_name="Paper", email="paper@example.invalid")
        self.ask_for("paper@example.invalid")
        self.assertEqual(mail.outbox, [])

    def test_the_message_carries_an_address_and_a_link_and_no_names(self):
        # Same rule as the event notifications (D22): what leaves this database
        # through a third party is an address and an announcement.
        self.user.contact = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL,
            legal_first_name="Mei", legal_last_name="Chen")
        self.user.save(update_fields=["contact"])
        self.ask_for("mei@example.invalid")
        body = mail.outbox[0].body
        self.assertIn("mei@example.invalid", body)
        for name in ("Mei", "Chen"):
            with self.subTest(name=name):
                self.assertNotIn(name, body)

    # --- the limit --------------------------------------------------------

    @override_settings(PASSWORD_RESET_RATELIMIT_PER_IP="2/h")
    def test_one_machine_is_cut_off_after_its_allowance(self):
        for _ in range(2):
            self.ask_for("mei@example.invalid")
        self.assertEqual(self.ask_for("mei@example.invalid").status_code, 429)

    @override_settings(PASSWORD_RESET_RATELIMIT_PER_IP="2/h")
    def test_a_refused_request_sends_nothing(self):
        # ⭐ The assertion that makes this a limit rather than a message. What
        #    is being protected is the day's mail allowance, so a 429 that
        #    still sends is worth nothing at all.
        for _ in range(2):
            self.ask_for("mei@example.invalid")
        mail.outbox = []
        self.assertEqual(self.ask_for("mei@example.invalid").status_code, 429)
        self.assertEqual(mail.outbox, [])

    @override_settings(PASSWORD_RESET_RATELIMIT_PER_IP="50/h",
                       PASSWORD_RESET_RATELIMIT_SITE="2/h")
    def test_many_machines_asking_once_each_are_counted_together(self):
        # ⚠️ The per-IP bucket cannot see this shape, and against a shared daily
        #    allowance it is the shape that actually empties it.
        for n in range(2):
            self.ask_for("mei@example.invalid", ip=f"203.0.113.{n}")
        self.assertEqual(
            self.ask_for("mei@example.invalid", ip="203.0.113.9").status_code, 429)

    # --- the way in -------------------------------------------------------

    def test_the_login_page_offers_the_way_out_of_being_locked_out(self):
        # ⚠️ A reset flow nobody can find is a reset flow nobody uses, and the
        #    person who needs it is standing at a form that just refused them.
        page = self.client.get(reverse("accounts:login"))
        self.assertContains(page, self.ASK)
