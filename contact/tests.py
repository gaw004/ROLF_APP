from django.core.exceptions import ValidationError
from django.test import TestCase

from .forms import ContactAdminForm
from .models import Contact, Language


class ContactNameByTypeTests(TestCase):
    """Name fields must match the contact type: required if they apply, cleared if not."""

    def test_organization_clears_person_names_on_save(self):
        contact = Contact.objects.create(
            contact_type=Contact.ContactType.ORGANIZATION,
            organization_name="Red Cross",
            legal_first_name="Alice",
            legal_last_name="Nguyen",
            preferred_name="Ali",
        )
        contact.refresh_from_db()
        self.assertEqual(contact.legal_first_name, "")
        self.assertEqual(contact.legal_last_name, "")
        self.assertEqual(contact.preferred_name, "")
        self.assertEqual(contact.organization_name, "Red Cross")

    def test_individual_clears_organization_name_on_save(self):
        contact = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL,
            legal_last_name="Nguyen",
            organization_name="Red Cross",
        )
        contact.refresh_from_db()
        self.assertEqual(contact.organization_name, "")
        self.assertEqual(contact.legal_last_name, "Nguyen")

    def test_switching_type_clears_the_old_names(self):
        contact = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL,
            legal_first_name="Alice",
            legal_last_name="Nguyen",
        )
        contact.contact_type = Contact.ContactType.ORGANIZATION
        contact.organization_name = "Red Cross"
        contact.save()
        contact.refresh_from_db()
        self.assertEqual(contact.legal_first_name, "")
        self.assertEqual(contact.legal_last_name, "")

    def test_organization_requires_organization_name(self):
        with self.assertRaises(ValidationError) as caught:
            Contact(contact_type=Contact.ContactType.ORGANIZATION).full_clean()
        self.assertIn("organization_name", caught.exception.message_dict)

    def test_individual_requires_legal_last_name(self):
        with self.assertRaises(ValidationError) as caught:
            Contact(contact_type=Contact.ContactType.INDIVIDUAL).full_clean()
        self.assertIn("legal_last_name", caught.exception.message_dict)


class ContactAddressStateTests(TestCase):
    """State is free text, so non-US addresses keep their province/region."""

    def _form_data(self, **overrides):
        data = {
            "contact_type": Contact.ContactType.INDIVIDUAL,
            "legal_last_name": "Nguyen",
            "address_country": "CA",
            "address_state": "Ontario",
        }
        data.update(overrides)
        return data

    def test_non_us_state_is_accepted(self):
        form = ContactAdminForm(data=self._form_data())
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().address_state, "Ontario")

    def test_us_state_abbreviation_is_accepted(self):
        form = ContactAdminForm(data=self._form_data(address_country="US", address_state="CA"))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().address_state, "CA")

    def test_form_publishes_us_states_to_the_admin_js(self):
        widget_attrs = ContactAdminForm().fields["address_state"].widget.attrs
        self.assertIn('["CA", "California"]', widget_attrs["data-us-states"])


class LanguageTests(TestCase):
    """The seeded ISO 639-3 list, ordered with the pinned languages first."""

    def test_pinned_languages_come_first_in_order(self):
        top_three = [language.display_name for language in Language.objects.all()[:3]]
        self.assertEqual(top_three, ["English", "Mandarin Chinese", "Cantonese"])

    def test_languages_missing_from_iso_639_1_are_available(self):
        for code in ["cmn", "yue", "hmn", "ase", "prs"]:
            self.assertTrue(Language.objects.filter(code=code).exists(), code)

    def test_only_living_languages_are_offered_as_preferred_language(self):
        choices = ContactAdminForm().fields["preferred_language"].queryset
        self.assertTrue(choices.filter(code="cmn").exists())
        # Latin is historical, Esperanto constructed: in the table, not in the dropdown.
        self.assertFalse(choices.filter(code="lat").exists())
        self.assertFalse(choices.filter(code="epo").exists())
        self.assertEqual(
            choices.exclude(language_type=Language.Type.LIVING).count(), 0
        )
