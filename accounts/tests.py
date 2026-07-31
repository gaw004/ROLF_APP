from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from contact.models import Contact

from .services import register_account

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
        user = User.objects.create_superuser(username="root", password="x")
        self.assertIsNone(user.contact)

    def test_user_can_be_linked_to_a_contact(self):
        contact = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL,
            legal_last_name="Nguyen",
        )
        user = User.objects.create_user(username="anguyen", password="x", contact=contact)
        self.assertEqual(user.contact, contact)
        self.assertEqual(contact.user, user)   # related_name resolves in reverse


class RegistrationTests(TestCase):
    """P1: one new account, one new Contact — and neither half on its own."""

    def test_registering_creates_both_a_user_and_a_contact(self):
        user = register_account(
            username="lisi", password="a-good-long-password",
            email="lisi@example.com", legal_last_name="李", legal_first_name="四",
        )
        self.assertIsNotNone(user.contact)
        self.assertEqual(user.contact.legal_last_name, "李")
        self.assertEqual(Contact.objects.count(), 1)

    def test_a_failed_registration_leaves_neither(self):
        # One transaction. Half a registration — a User with no Contact — is
        # worse than none: they can log in, every page reading user.contact
        # fails for them, and nothing in the data says why.
        User.objects.create(username="taken")
        with self.assertRaises(IntegrityError), transaction.atomic():
            register_account(
                username="taken", password="a-good-long-password", legal_last_name="王")
        self.assertEqual(Contact.objects.count(), 0)

    def test_a_new_account_is_not_staff(self):
        user = register_account(
            username="lisi", password="a-good-long-password", legal_last_name="李")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(user.groups.count(), 0)

    def test_a_volunteer_account_gets_403_on_admin(self):
        # D21's first requirement: not "no link to it", actually refused.
        register_account(username="lisi", password="a-good-long-password", legal_last_name="李")
        self.client.login(username="lisi", password="a-good-long-password")
        response = self.client.get("/admin/", follow=True)
        # Django's admin bounces a non-staff account to its own login page
        # rather than serving anything; either way the index is never rendered.
        self.assertNotContains(response, "Site administration", status_code=200)
        self.assertIn("/admin/login/", response.redirect_chain[-1][0])

    def test_user_contact_may_still_be_null(self):
        # P1 is a rule about this flow, not about the column. A rule with a
        # legitimate exception (technical accounts) does not belong in a
        # constraint, because a constraint admits no exceptions — D9's own test,
        # applied against itself.
        self.assertTrue(User._meta.get_field("contact").null)
        User.objects.create_superuser(username="root", password="x")

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
            username="wangqiang", password="a-good-long-password",
            legal_last_name="王", legal_first_name="强", phone="+14085550101",
        )
        self.assertEqual(Contact.objects.filter(legal_last_name="王").count(), 2)
        self.assertIsNotNone(user.contact)


class RegistrationPageTests(TestCase):
    def test_the_registration_page_creates_an_account_and_logs_in(self):
        response = self.client.post(reverse("accounts:register"), {
            "username": "lisi",
            "email": "lisi@example.com",
            "password": "a-good-long-password",
            "legal_last_name": "李",
            "legal_first_name": "四",
        })
        self.assertRedirects(response, reverse("events:event_list"))
        self.assertTrue(User.objects.filter(username="lisi").exists())
        self.assertEqual(self.client.session.get("_auth_user_id"),
                         str(User.objects.get(username="lisi").pk))

    def test_a_weak_password_is_refused_and_nothing_is_created(self):
        response = self.client.post(reverse("accounts:register"), {
            "username": "lisi", "email": "lisi@example.com",
            "password": "123", "legal_last_name": "李",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.count(), 0)
        self.assertEqual(Contact.objects.count(), 0)

    def test_a_duplicate_username_is_a_form_error_not_a_500(self):
        User.objects.create_user(username="lisi", password="x")
        response = self.client.post(reverse("accounts:register"), {
            "username": "lisi", "email": "lisi@example.com",
            "password": "a-good-long-password", "legal_last_name": "李",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Contact.objects.count(), 0)
