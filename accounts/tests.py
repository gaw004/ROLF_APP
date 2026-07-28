from django.contrib.auth import get_user_model
from django.test import TestCase

from contact.models import Contact

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
