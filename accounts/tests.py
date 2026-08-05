from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from contact.models import Contact, EmergencyContact, Language, RelationshipType

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
        """D21's first requirement: refused, not redirected to a login form.

        Django's own behaviour is a 302 to the admin login page, which then
        tells an already-signed-in volunteer to enter a staff password — a lie
        and a loop. This test was originally written around that redirect while
        still being named after a 403, which made it a test whose name did not
        match what it checked; core.middleware.StaffOnlyAdminMiddleware is what
        makes the name true.
        """
        register_account(username="lisi", password="a-good-long-password", legal_last_name="李")
        self.client.login(username="lisi", password="a-good-long-password")
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
        User.objects.create_superuser(username="root", password="a-good-long-password")
        self.client.login(username="root", password="a-good-long-password")
        self.assertEqual(self.client.get("/admin/").status_code, 200)

    def test_logging_out_is_never_refused(self):
        # Somebody whose staff flag was removed mid-session must still be able
        # to leave a site that refuses them every other page.
        register_account(username="lisi", password="a-good-long-password", legal_last_name="李")
        self.client.login(username="lisi", password="a-good-long-password")
        self.assertNotEqual(self.client.post("/admin/logout/").status_code, 403)

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


class ProfilePageTests(TestCase):
    """C0.2.5: the volunteer can correct their own details.

    Every field here was a dead end before this page. The birth date is the
    expensive one: left blank at registration, Contact.is_minor returns None,
    the cautious branch applies, and that person is asked for guardian consent
    at every signup forever — with no way to fix it short of a staff account.
    """

    def setUp(self):
        self.user = register_account(
            username="mei", password="a-good-long-password",
            legal_last_name="Mei", email="mei@example.com",
        )
        self.client.force_login(self.user)
        self.url = reverse("accounts:profile")

    def contact(self):
        self.user.contact.refresh_from_db()
        return self.user.contact

    def details(self, **overrides):
        fields = {
            "action": "save",
            "legal_first_name": "",
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
            username="other", password="a-good-long-password",
            legal_last_name="Other", email="other@example.com",
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
        root = get_user_model().objects.create_superuser(username="root", password="x")
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
            username="lang", password="a-good-long-password",
            legal_last_name="Lang", email="lang@example.com")
        self.client.force_login(self.user)
        self.url = reverse("accounts:profile")

    def test_preferred_language_is_offered_and_saved(self):
        english = Language.objects.get(code="eng")
        response = self.client.post(self.url, {
            "action": "save", "legal_last_name": "Lang",
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
