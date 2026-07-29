import datetime

from django.conf import settings
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.test import TestCase, override_settings
from django.urls import path, reverse

from .forms import ContactAdminForm
from .models import Contact, Language, Relationship, RelationshipType


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


class ContactNameConstraintTests(TestCase):
    """The name rule at the database level (goal.md D9 / D14).

    Contact.clean() only runs from ModelForms and explicit full_clean() calls —
    save() never invokes it. These tests take the paths that used to slip past
    the rule entirely.
    """

    def test_create_bypassing_full_clean_still_cannot_break_the_name_rule(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Contact.objects.create(contact_type=Contact.ContactType.INDIVIDUAL)

    def test_organization_without_a_name_is_rejected_at_the_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Contact.objects.create(contact_type=Contact.ContactType.ORGANIZATION)

    def test_bulk_create_cannot_break_the_name_rule_either(self):
        # bulk_create skips save() and therefore every Python-level hook. It is
        # also what a data import would use, which is exactly when bad rows arrive.
        with self.assertRaises(IntegrityError), transaction.atomic():
            Contact.objects.bulk_create([
                Contact(contact_type=Contact.ContactType.INDIVIDUAL, legal_last_name="Ok"),
                Contact(contact_type=Contact.ContactType.INDIVIDUAL),
            ])


class RelationshipConstraintTests(TestCase):
    """The three Relationship constraints, plus the field-level errors that pair
    with them (goal.md D14)."""

    def setUp(self):
        self.alice = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL, legal_last_name="Alice")
        self.bob = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL, legal_last_name="Bob")
        self.parent_of = RelationshipType.objects.create(
            name_a_to_b="parent of", name_b_to_a="child of")

    def test_cannot_relate_a_contact_to_itself(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Relationship.objects.create(
                contact_a=self.alice, contact_b=self.alice,
                relationship_type=self.parent_of,
            )

    def test_self_reference_error_points_at_the_contact_field(self):
        # This is what the clean() layer buys us: the admin marks contact_b
        # rather than dropping a database message at the top of the form.
        relationship = Relationship(
            contact_a=self.alice, contact_b=self.alice,
            relationship_type=self.parent_of,
        )
        with self.assertRaises(ValidationError) as caught:
            relationship.full_clean()
        self.assertIn("contact_b", caught.exception.message_dict)

    def test_cannot_store_the_same_relationship_twice(self):
        # Both rows have start_date=None, which is the case that only holds
        # because of nulls_distinct=False — Postgres would otherwise consider
        # two NULLs distinct and let the duplicate through.
        Relationship.objects.create(
            contact_a=self.alice, contact_b=self.bob,
            relationship_type=self.parent_of,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Relationship.objects.create(
                contact_a=self.alice, contact_b=self.bob,
                relationship_type=self.parent_of,
            )

    def test_end_date_cannot_be_before_start_date(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Relationship.objects.create(
                contact_a=self.alice, contact_b=self.bob,
                relationship_type=self.parent_of,
                start_date=datetime.date(2023, 1, 1),
                end_date=datetime.date(2020, 1, 1),
            )

    def test_an_open_ended_relationship_is_still_allowed(self):
        # The date constraint must not reject the ordinary case of a
        # relationship that has started and has not ended.
        relationship = Relationship.objects.create(
            contact_a=self.alice, contact_b=self.bob,
            relationship_type=self.parent_of,
            start_date=datetime.date(2020, 1, 1),
        )
        self.assertIsNone(relationship.end_date)


class ConstraintFieldErrorTests(TestCase):
    """Every constraint, submitted violated, lands on its mapped field (goal.md D14).

    This is the second half of the D14 machinery. The core guard checks that
    each constraint *has* a code and a mapping; this one checks the mapping
    actually fires, because CheckConstraint.validate() skips silently when the
    expression raises FieldError — and a constraint that never validates at form
    time surfaces as an IntegrityError 500 rather than a red box on a field.

    It lives in contact/tests.py rather than core/tests.py because only the app
    knows what violating data looks like; core would have to import every model
    to build it, which is the import direction D17 forbids.
    """

    def setUp(self):
        self.alice = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL, legal_last_name="Alice")
        self.bob = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL, legal_last_name="Bob")
        self.parent_of = RelationshipType.objects.create(
            name_a_to_b="parent of", name_b_to_a="child of")

    def assertFieldError(self, instance, field):
        with self.assertRaises(ValidationError) as caught:
            instance.full_clean()
        self.assertIn(field, caught.exception.message_dict)
        return caught.exception.message_dict[field]

    def test_individual_without_a_last_name_points_at_legal_last_name(self):
        messages = self.assertFieldError(
            Contact(contact_type=Contact.ContactType.INDIVIDUAL), "legal_last_name")
        # The sentence comes from violation_error_message, not from a second
        # copy of the rule written out in clean().
        self.assertIn("An individual needs a legal last name.", messages)

    def test_organization_without_a_name_points_at_organization_name(self):
        messages = self.assertFieldError(
            Contact(contact_type=Contact.ContactType.ORGANIZATION), "organization_name")
        self.assertIn("An organization needs an organization name.", messages)

    def test_an_unknown_contact_type_is_rejected_by_the_database(self):
        # This one cannot be checked at form level: the choices validation in
        # clean_fields() already flags contact_type, so full_clean() excludes
        # the field and skips the constraint. The constraint still matters —
        # it is what stops bulk_create and psql from writing a third type.
        with self.assertRaises(IntegrityError), transaction.atomic():
            Contact.objects.bulk_create([
                Contact(contact_type="household", legal_last_name="Nguyen"),
            ])

    def test_a_self_relationship_points_at_contact_b(self):
        messages = self.assertFieldError(
            Relationship(contact_a=self.alice, contact_b=self.alice,
                         relationship_type=self.parent_of),
            "contact_b",
        )
        self.assertIn("A contact cannot be related to themselves.", messages)

    def test_a_duplicate_relationship_points_at_contact_b(self):
        Relationship.objects.create(
            contact_a=self.alice, contact_b=self.bob, relationship_type=self.parent_of)
        messages = self.assertFieldError(
            Relationship(contact_a=self.alice, contact_b=self.bob,
                         relationship_type=self.parent_of),
            "contact_b",
        )
        self.assertIn("This relationship has already been recorded.", messages)

    def test_an_end_date_before_the_start_date_points_at_end_date(self):
        messages = self.assertFieldError(
            Relationship(contact_a=self.alice, contact_b=self.bob,
                         relationship_type=self.parent_of,
                         start_date=datetime.date(2023, 1, 1),
                         end_date=datetime.date(2020, 1, 1)),
            "end_date",
        )
        self.assertIn("The end date cannot be before the start date.", messages)


class ContactHistoryTests(TestCase):
    """Audit trail: what changed, and who changed it (goal.md Phase A)."""

    def setUp(self):
        self.contact = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL,
            legal_last_name="Nguyen",
        )

    def test_editing_a_contact_records_the_previous_value(self):
        self.contact.legal_last_name = "Tran"
        self.contact.save()

        history = self.contact.history.all()
        self.assertEqual(history.count(), 2)                  # create + update
        self.assertEqual(history.first().legal_last_name, "Tran")
        self.assertEqual(history.last().legal_last_name, "Nguyen")

    def test_editing_through_the_admin_records_who_changed_it(self):
        # Note this path does NOT go through HistoryRequestMiddleware:
        # SimpleHistoryAdmin sets obj._history_user from request.user itself.
        # The middleware is what covers every *other* request path — see
        # test_saving_during_a_non_admin_request_records_the_user.
        User = get_user_model()
        editor = User.objects.create_superuser(username="editor", password="x")
        self.client.force_login(editor)

        response = self.client.post(
            reverse("admin:contact_contact_change", args=[self.contact.pk]),
            data=self._admin_form_data(legal_last_name="Tran"),
        )
        self.assertEqual(response.status_code, 302, getattr(response, "context_data", None))

        self.contact.refresh_from_db()
        self.assertEqual(self.contact.legal_last_name, "Tran")
        self.assertEqual(self.contact.history.first().history_user, editor)

    def _admin_form_data(self, **overrides):
        data = {
            "contact_type": Contact.ContactType.INDIVIDUAL,
            "legal_first_name": "",
            "legal_last_name": "Nguyen",
            "preferred_name": "",
            "organization_name": "",
            "email": "",
            "phone": "",
            "gender": "",
            "birth_date": "",
            "preferred_language": "",
            "preferred_communication_method": "",
            "address_country": "US",
            "address_street": "",
            "address_city": "",
            "address_state": "",
            "address_postal_code": "",
            "is_active": "on",
            "notes": "",
            "relationships_as_a-TOTAL_FORMS": "0",
            "relationships_as_a-INITIAL_FORMS": "0",
            "relationships_as_a-MIN_NUM_FORMS": "0",
            "relationships_as_a-MAX_NUM_FORMS": "1000",
        }
        data.update(overrides)
        return data


# --- Test-only view + URLconf, used by ContactHistoryMiddlewareTests below ----
# A non-admin request path is the only way to exercise HistoryRequestMiddleware:
# SimpleHistoryAdmin bypasses it by setting _history_user directly.

def _rename_contact_view(request, pk):
    contact = Contact.objects.get(pk=pk)
    contact.legal_last_name = "Tran"
    contact.save()
    return HttpResponse("ok")


urlpatterns = [
    path("admin/", admin.site.urls),
    path("rename/<int:pk>/", _rename_contact_view),
]


@override_settings(ROOT_URLCONF="contact.tests")
class ContactHistoryMiddlewareTests(TestCase):
    """HistoryRequestMiddleware, exercised through the real middleware stack."""

    def setUp(self):
        self.contact = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL,
            legal_last_name="Nguyen",
        )
        self.user = get_user_model().objects.create_user(username="coordinator", password="x")

    def test_saving_during_a_non_admin_request_records_the_user(self):
        # This is what the middleware is for: the HTMX pages of Phase C and any
        # later API save Contacts without going anywhere near SimpleHistoryAdmin.
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(f"/rename/{self.contact.pk}/").status_code, 200)
        self.assertEqual(self.contact.history.first().history_user, self.user)

    def test_the_middleware_is_installed(self):
        # Guards against someone dropping it from MIDDLEWARE: the test above
        # would then fail with a confusing None rather than a clear reason.
        self.assertIn(
            "simple_history.middleware.HistoryRequestMiddleware",
            settings.MIDDLEWARE,
        )
