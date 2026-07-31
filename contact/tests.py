import datetime
import inspect
from pathlib import Path

from django import forms
from django.apps import apps
from django.conf import settings
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.http import HttpResponse
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import path, reverse

from core.timeutils import local_today

from .admin import MinorFilter
from .forms import ContactAdminForm
from .models import (
    Contact, ContactQuerySet, EmergencyContact, Language, RelationshipType,
)
from .services import MergeConflict, merge_contacts


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


class ContactStringTests(TestCase):
    """B4.1: two people with the same name must not stringify identically.

    Every autocomplete built in B5/B6 is made of these strings, which is why
    this had to land before them: two identical options in a dropdown produce a
    silent data error, never an exception.
    """

    def make(self, **kw):
        data = {"contact_type": Contact.ContactType.INDIVIDUAL, "legal_last_name": "王强"}
        data.update(kw)
        return Contact.objects.create(**data)

    def test_two_contacts_with_the_same_name_stringify_differently(self):
        first = self.make(email="qiang1@example.com")
        second = self.make(email="qiang2@example.com")
        self.assertNotEqual(str(first), str(second))

    def test_the_phone_is_used_when_there_is_no_email(self):
        contact = self.make(phone="+14085550101")
        self.assertIn("4085550101", str(contact))

    def test_the_pk_is_the_last_resort(self):
        contact = self.make()
        self.assertEqual(str(contact), f"王强 #{contact.pk}")

    def test_two_organizations_with_the_same_name_stringify_differently(self):
        # The organization branch needs this as much as the person one: two
        # chapters of the same charity are just as easy to confuse.
        first = Contact.objects.create(
            contact_type=Contact.ContactType.ORGANIZATION,
            organization_name="Red Cross", email="north@example.com")
        second = Contact.objects.create(
            contact_type=Contact.ContactType.ORGANIZATION,
            organization_name="Red Cross", email="south@example.com")
        self.assertNotEqual(str(first), str(second))

    def test_duplicate_names_are_allowed(self):
        # Deliberately not a unique constraint: same-name people are a fact, and
        # this domain has no reliable natural key to key off instead.
        self.make()
        self.make()
        self.assertEqual(Contact.objects.filter(legal_last_name="王强").count(), 2)


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


class RelationshipTypeTests(TestCase):
    """code, symmetry, and the two duplicate gaps A7's constraints missed (B2)."""

    def make(self, code="parent_of", name_a_to_b="parent of", name_b_to_a="child of", **kw):
        return RelationshipType.objects.create(
            code=code, name_a_to_b=name_a_to_b, name_b_to_a=name_b_to_a, **kw)

    def test_code_must_be_unique(self):
        self.make()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make(name_a_to_b="guardian of", name_b_to_a="ward of")

    def test_code_is_lowercased_and_stripped_on_save(self):
        # Cosmetic only: uniqueness is the constraint's job now. Kept so stored
        # values are clean and the admin behaves the same way every time.
        self.assertEqual(self.make(code="  Parent_Of  ").code, "parent_of")

    def test_code_cannot_be_changed_once_created(self):
        relationship_type = self.make()
        relationship_type.code = "something_else"
        with self.assertRaises(ValidationError) as caught:
            relationship_type.full_clean()
        self.assertIn("code", caught.exception.message_dict)

    def test_the_admin_freezes_code_on_the_change_page_only(self):
        # editable=False would also block the add form, so immutability is split:
        # the admin makes it read-only when editing, clean() catches everything else.
        model_admin = admin.site._registry[RelationshipType]
        self.assertEqual(model_admin.get_readonly_fields(None, obj=None), [])
        self.assertEqual(model_admin.get_readonly_fields(None, obj=self.make()), ["code"])

    def test_two_types_with_the_same_name_ignoring_case_are_rejected(self):
        # Gap 2. A plain UniqueConstraint would let "Parent of" in beside
        # "parent of", leaving two identical-looking options in the dropdown
        # and the data split between them.
        self.make()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make(code="parent_of_2", name_a_to_b="Parent Of")

    def test_a_forward_name_colliding_with_an_existing_reverse_name_is_rejected(self):
        # Gap 1: "child of" already exists as the reverse of "parent of". Letting
        # the mirror type in splits one vocabulary entry across two rows, and
        # every constraint stays happy — half the emergency contacts then get
        # filed under one and half under the other.
        self.make()
        mirror = RelationshipType(
            code="child_of", name_a_to_b="Child Of", name_b_to_a="parent of")
        with self.assertRaises(ValidationError) as caught:
            mirror.full_clean()
        self.assertIn("name_a_to_b", caught.exception.message_dict)

    # --- The two below must use bulk_create, and that is the entire point ------
    # Going through save() they would both pass while verifying nothing:
    # save() normalises first, so the duplicate never reaches the database.
    # bulk_create is the path a data import takes, and it skips save() entirely.

    def test_bulk_create_cannot_insert_a_code_differing_only_in_case(self):
        self.make(code="food_pantry", name_a_to_b="pantry volunteer at")
        with self.assertRaises(IntegrityError), transaction.atomic():
            RelationshipType.objects.bulk_create([
                RelationshipType(code="Food_Pantry", name_a_to_b="something else"),
            ])

    def test_bulk_create_cannot_insert_a_name_differing_only_in_whitespace(self):
        self.make()
        with self.assertRaises(IntegrityError), transaction.atomic():
            RelationshipType.objects.bulk_create([
                RelationshipType(code="parent_of_2", name_a_to_b="  parent of  "),
            ])

    def test_symmetry_is_an_explicit_flag_not_an_inference(self):
        # Whoever adds the type may well type "spouse of" into both boxes, which
        # is exactly when "name_b_to_a is empty" stops meaning symmetric.
        spouse = self.make(
            code="spouse_of", name_a_to_b="spouse of", name_b_to_a="spouse of",
            is_symmetric=True)
        self.assertTrue(spouse.is_symmetric)
        self.assertFalse(self.make().is_symmetric)

    def test_only_flagged_types_are_offered_for_emergency_contacts(self):
        self.make(code="mother_of", name_a_to_b="mother of", name_b_to_a="child of",
                  usable_as_emergency_contact=True)
        self.make(code="employee_of", name_a_to_b="employee of", name_b_to_a="employer of")
        offered = RelationshipType.objects.filter(usable_as_emergency_contact=True)
        self.assertEqual([t.code for t in offered], ["mother_of"])


class ConstraintFieldErrorTests(TestCase):
    """Every constraint, submitted violated, lands on its mapped field (goal.md D14).

    This is the second half of the D14 machinery. The core guard checks that
    each constraint *has* a code and a mapping; this one checks the mapping
    actually fires, because CheckConstraint.validate() skips silently when the
    expression raises FieldError — and a constraint that never validates at form
    time surfaces as an IntegrityError 500 rather than a red box on a field.

    It lives in contact/tests.py rather than core/tests.py because only the app
    knows what violating data looks like; core would have to import every model
    to build it, which is the import direction D17 forbids. org/tests.py has
    the matching class for its own four models.
    """

    # Every code this class exercises. Kept as a literal so that
    # test_this_class_covers_every_constraint_in_the_app below can name the one
    # you forgot, rather than just failing a count.
    COVERED = {
        "individual_needs_last_name",
        "organization_needs_name",
        "contact_type_unknown",
        "reltype_name_taken",
        "reltype_code_taken",
        "emergency_contact_duplicate",
    }

    def test_this_class_covers_every_constraint_in_the_app(self):
        """Adding a constraint without a case here goes red.

        core/tests.py checks that a mapping exists; nothing but a case in this
        class checks that it fires, so "somebody will remember to add one" is
        exactly the discipline this project keeps replacing with a test.
        """
        live = {
            constraint.violation_error_code
            for model in apps.get_app_config("contact").get_models()
            for constraint in model._meta.constraints
            if getattr(constraint, "violation_error_code", None)
        }
        missing = sorted(live - self.COVERED)
        self.assertEqual(
            missing, [], f"No field-error case for: {missing}")

    def setUp(self):
        self.alice = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL, legal_last_name="Alice")
        self.bob = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL, legal_last_name="Bob")
        self.parent_of = RelationshipType.objects.create(
            code="parent_of", name_a_to_b="parent of", name_b_to_a="child of")

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

    def test_a_duplicate_type_name_points_at_name_a_to_b(self):
        # An expression constraint — Lower(Trim(...)). Those are the ones B1
        # warned could be skipped at form time and only bite as an
        # IntegrityError, so they are the ones most worth a case here.
        messages = self.assertFieldError(
            RelationshipType(code="parent_two", name_a_to_b="  Parent Of  "),
            "name_a_to_b",
        )
        self.assertIn("A relationship type with this name already exists.", messages)

    def test_a_duplicate_type_code_points_at_code(self):
        messages = self.assertFieldError(
            RelationshipType(code="PARENT_OF", name_a_to_b="guardian of"), "code")
        self.assertIn("A relationship type with this code already exists.", messages)

    def test_a_duplicate_emergency_contact_points_at_name(self):
        kin = RelationshipType.objects.create(
            code="mother_of", name_a_to_b="mother of", usable_as_emergency_contact=True)
        EmergencyContact.objects.create(
            person=self.alice, name="王秀英", phone="+14085550101", relationship_type=kin)
        messages = self.assertFieldError(
            EmergencyContact(person=self.alice, name="  王秀英  ",
                             phone="+14085550101", relationship_type=kin),
            "name",
        )
        self.assertIn("This emergency contact is already recorded for them.", messages)


class EmergencyContactTests(TestCase):
    """B4.2: a dedicated table, with name and phone stored as text.

    The point of the whole design is the last test in this class: nothing an
    emergency contact does can put a row in Contact.
    """

    def setUp(self):
        self.ming = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL, legal_last_name="小明")
        self.mother_of = RelationshipType.objects.create(
            code="mother_of", name_a_to_b="母亲", name_b_to_a="子女",
            usable_as_emergency_contact=True)

    def make(self, person=None, name="王秀英", phone="+14085550101", **kw):
        return EmergencyContact.objects.create(
            person=person or self.ming, name=name, phone=phone,
            relationship_type=kw.pop("relationship_type", self.mother_of), **kw)

    def test_an_emergency_contact_without_a_relationship_type_is_rejected(self):
        # "Relationship is required" is just null=False here. Splitting the table
        # out turned what used to need a CheckConstraint into a plain non-null FK.
        with self.assertRaises(IntegrityError), transaction.atomic():
            EmergencyContact.objects.create(
                person=self.ming, name="王秀英", phone="+14085550101",
                relationship_type=None)

    def test_duplicate_emergency_contact_for_the_same_person_is_rejected(self):
        self.make()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make()

    def test_bulk_create_cannot_insert_a_name_differing_only_in_whitespace(self):
        # Normalisation lives in the constraint expression, not save() — and
        # this is the path that proves it, because bulk_create skips save().
        self.make()
        with self.assertRaises(IntegrityError), transaction.atomic():
            EmergencyContact.objects.bulk_create([
                EmergencyContact(person=self.ming, name="  王秀英  ",
                                 phone="+14085550101",
                                 relationship_type=self.mother_of),
            ])

    def test_one_person_can_have_two_different_emergency_contacts(self):
        # No artificial "at most one" rule: that cap was a side effect of the old
        # self-FK's shape, never a requirement.
        self.make()
        self.make(name="王大明", phone="+14085550102")
        self.assertEqual(self.ming.emergency_contacts.count(), 2)

    def test_the_same_person_can_be_listed_by_two_different_contacts(self):
        # And this is the accepted cost: two rows, two copies of the number, and
        # nothing tying them together.
        sibling = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL, legal_last_name="小红")
        self.make()
        self.make(person=sibling)
        self.assertEqual(EmergencyContact.objects.filter(name="王秀英").count(), 2)

    def test_deleting_a_contact_deletes_their_emergency_contacts(self):
        # CASCADE: emergency contacts are attached data with no life of their own.
        self.make()
        self.ming.delete()
        self.assertEqual(EmergencyContact.objects.count(), 0)

    def test_only_flagged_relationship_types_are_offered(self):
        employee_of = RelationshipType.objects.create(
            code="employee_of", name_a_to_b="员工", name_b_to_a="雇主")
        field = EmergencyContact._meta.get_field("relationship_type")
        offered = RelationshipType.objects.complex_filter(field.get_limit_choices_to())
        self.assertIn(self.mother_of, offered)
        self.assertNotIn(employee_of, offered)

    def test_recording_an_emergency_contact_adds_no_contact_row(self):
        # The verification point of the sixth revision, and the reason the table
        # exists at all: a neighbour listed here never lands in the table that
        # every list, export and mailing starts from.
        before = Contact.objects.count()
        self.make()
        self.assertEqual(Contact.objects.count(), before)

    def test_contact_has_no_is_reference_only_field_and_no_emergency_contact_fk(self):
        field_names = {f.name for f in Contact._meta.get_fields()}
        self.assertNotIn("is_reference_only", field_names)
        self.assertNotIn("emergency_contact", field_names)


class DuplicateDetectionTests(TestCase):
    """B4.3: the rule is same normalised name AND same phone. Three boundaries."""

    def make(self, last_name="王强", first_name="", phone="+14085550101", **kw):
        return Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL,
            legal_last_name=last_name, legal_first_name=first_name,
            phone=phone, **kw)

    def find(self, last_name="王强", first_name="", phone="+14085550101", **kw):
        return Contact.find_exact_duplicates(
            last_name=last_name, first_name=first_name, phone=phone, **kw)

    def test_same_name_same_phone_is_a_match(self):
        existing = self.make()
        self.assertIn(existing, self.find())

    def test_same_name_different_phone_is_not_a_match(self):
        # A genuine namesake. Missing this one is correct behaviour.
        self.make()
        self.assertFalse(self.find(phone="+14085550999").exists())

    def test_same_phone_different_name_is_not_a_match(self):
        # A family sharing one line. Also correct to miss.
        self.make()
        self.assertFalse(self.find(last_name="李明").exists())

    def test_name_comparison_ignores_case_and_extra_spaces(self):
        self.make(last_name="Wang", first_name="Qiang")
        self.assertTrue(self.find(last_name=" wang ", first_name="QIANG").exists())

    def test_a_contact_is_never_its_own_duplicate(self):
        existing = self.make()
        self.assertFalse(self.find(exclude_pk=existing.pk).exists())

    def test_no_phone_means_no_match(self):
        # Without a phone there is no second half to the rule, so nothing can
        # match — and the hint never discloses a name the user has not typed.
        self.make(phone="")
        self.assertFalse(self.find(phone="").exists())

    def test_find_same_name_ignores_the_phone_entirely(self):
        self.make()
        namesakes = Contact.find_same_name(last_name="王强", first_name="")
        self.assertEqual(namesakes.count(), 1)


class DuplicateInterceptionTests(TestCase):
    """B4.3b: same name warns, same name AND phone blocks until force_save."""

    def setUp(self):
        self.existing = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL,
            legal_last_name="王强", phone="+14085550101")

    def form_data(self, **overrides):
        data = {
            "contact_type": Contact.ContactType.INDIVIDUAL,
            "legal_last_name": "王强",
            "phone": "+14085550101",
            "address_country": "US",
        }
        data.update(overrides)
        return data

    def test_same_name_same_phone_blocks_saving_until_force_save(self):
        blocked = ContactAdminForm(data=self.form_data())
        self.assertFalse(blocked.is_valid())
        self.assertIn("force_save", blocked.errors)

        forced = ContactAdminForm(data=self.form_data(force_save="on"))
        self.assertTrue(forced.is_valid(), forced.errors)
        self.assertIsNotNone(forced.save().pk)

    def test_same_name_different_phone_only_warns(self):
        # The hard block must never hang off the name alone: 王强 / 李明 / 陈伟
        # repeat constantly here, and a box that pops up twenty times a day gets
        # ticked reflexively — the block stops working and costs two clicks.
        form = ContactAdminForm(data=self.form_data(phone="+14085550999"))
        self.assertTrue(form.is_valid(), form.errors)

    def test_the_checkbox_stays_hidden_until_there_is_something_to_confirm(self):
        clean_form = ContactAdminForm(data=self.form_data(phone="+14085550999"))
        self.assertIsInstance(
            clean_form.fields["force_save"].widget, forms.HiddenInput)

        hit_form = ContactAdminForm(data=self.form_data())
        self.assertNotIsInstance(
            hit_form.fields["force_save"].widget, forms.HiddenInput)

    def test_the_checkbox_stays_visible_when_another_field_is_also_wrong(self):
        # The reason widget visibility is decided in __init__ from the submitted
        # data instead of inside clean(): on a second submission carrying some
        # other error, the checkbox would revert to hidden and the user would be
        # left thinking they had never ticked it.
        form = ContactAdminForm(data=self.form_data(email="not-an-email"))
        self.assertFalse(form.is_valid())
        self.assertIn("email", form.errors)
        self.assertNotIsInstance(
            form.fields["force_save"].widget, forms.HiddenInput)

    def test_editing_the_existing_contact_is_not_blocked_by_itself(self):
        form = ContactAdminForm(data=self.form_data(), instance=self.existing)
        self.assertTrue(form.is_valid(), form.errors)

    def test_a_locally_formatted_phone_is_matched_after_normalisation(self):
        # (408) 555-0101 and +14085550101 are the same number; phonenumber_field
        # normalises on the way in, which is why no similarity scoring is needed.
        form = ContactAdminForm(data=self.form_data(phone="(408) 555-0101"))
        self.assertFalse(form.is_valid())
        self.assertIn("force_save", form.errors)

    def test_force_save_is_not_a_database_column(self):
        field_names = {f.name for f in Contact._meta.get_fields()}
        self.assertNotIn("force_save", field_names)


class MergeContactsTests(TestCase):
    """B4.4: repoint everything, refuse rather than guess, leave a trail."""

    def setUp(self):
        self.keep = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL,
            legal_last_name="王强", phone="+14085550101")
        self.drop = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL,
            legal_last_name="王强", phone="+14085550101",
            email="qiang@example.com", address_city="Santa Clara")
        self.other = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL, legal_last_name="李梅")
        self.parent_of = RelationshipType.objects.create(
            code="parent_of", name_a_to_b="父亲", name_b_to_a="儿子",
            usable_as_emergency_contact=True)

    def test_merge_moves_every_reverse_relation(self):
        """Nothing may be left pointing at the retired record.

        Rather than listing the foreign keys it expects, this walks
        Contact._meta.related_objects and demands that each one was either moved
        or is on an explicit skip list. Add a new FK to Contact without deciding
        what merging does with it, and this test goes red on the spot — which a
        hand-written list of assertions would not.
        """
        EmergencyContact.objects.create(
            person=self.drop, name="王秀英", phone="+14085550188",
            relationship_type=self.parent_of)
        get_user_model().objects.create_user(
            username="dropuser", password="x", contact=self.drop)

        merge_contacts(self.keep, self.drop)

        skipped = []
        for relation in Contact._meta.related_objects:
            model = relation.related_model
            if model.__name__.startswith("Historical"):
                skipped.append(model.__name__)
                continue
            left_behind = model._base_manager.filter(
                **{relation.field.name: self.drop}).count()
            self.assertEqual(
                left_behind, 0,
                f"{model._meta.label} still points at the retired contact")
        # The skip list is only ever the history tables: those record what
        # happened at the time and must not be rewritten.
        self.assertTrue(all(name.startswith("Historical") for name in skipped))

    def test_merge_refuses_when_both_contacts_have_a_user(self):
        User = get_user_model()
        User.objects.create_user(username="keepuser", password="x", contact=self.keep)
        User.objects.create_user(username="dropuser", password="x", contact=self.drop)
        with self.assertRaises(MergeConflict):
            merge_contacts(self.keep, self.drop)

    def test_merge_refuses_on_a_unique_constraint_clash(self):
        # Both list 王秀英 on the same number: repointing would produce two
        # identical rows, which emergencycontact_unique_per_person refuses.
        # Reported, not guessed at.
        EmergencyContact.objects.create(
            person=self.keep, name="王秀英", phone="+14085550188",
            relationship_type=self.parent_of)
        EmergencyContact.objects.create(
            person=self.drop, name="王秀英", phone="+14085550188",
            relationship_type=self.parent_of)
        with self.assertRaises(MergeConflict):
            merge_contacts(self.keep, self.drop)

    def test_a_refused_merge_changes_nothing(self):
        User = get_user_model()
        User.objects.create_user(username="keepuser", password="x", contact=self.keep)
        User.objects.create_user(username="dropuser", password="x", contact=self.drop)
        EmergencyContact.objects.create(
            person=self.drop, name="王秀英", phone="+14085550188",
            relationship_type=self.parent_of)
        with self.assertRaises(MergeConflict):
            merge_contacts(self.keep, self.drop)
        # Partial merges are the one outcome worse than refusing.
        self.assertEqual(self.drop.emergency_contacts.count(), 1)
        self.drop.refresh_from_db()
        self.assertTrue(self.drop.is_active)

    def test_the_kept_record_wins_and_only_blanks_are_filled_in(self):
        self.keep.address_city = "San Jose"
        self.keep.save()
        merge_contacts(self.keep, self.drop)
        self.keep.refresh_from_db()
        self.assertEqual(self.keep.address_city, "San Jose")      # keep wins
        self.assertEqual(self.keep.email, "qiang@example.com")    # blank filled

    def test_the_retired_record_is_deactivated_not_deleted(self):
        merge_contacts(self.keep, self.drop)
        self.drop.refresh_from_db()
        self.assertFalse(self.drop.is_active)
        self.assertTrue(Contact.objects.filter(pk=self.drop.pk).exists())

    def test_the_merge_leaves_a_note_a_human_can_read(self):
        merge_contacts(self.keep, self.drop, actor="gabrielle")
        self.keep.refresh_from_db()
        self.assertIn(f"已合并 #{self.drop.pk}", self.keep.notes)
        self.assertIn("gabrielle", self.keep.notes)

    def test_merging_a_contact_into_itself_is_refused(self):
        with self.assertRaises(MergeConflict):
            merge_contacts(self.keep, self.keep)

    def test_possible_duplicates_finds_both_sides_of_the_pair(self):
        found = Contact.objects.possible_duplicates()
        self.assertIn(self.keep, found)
        self.assertIn(self.drop, found)
        self.assertNotIn(self.other, found)


class MergePageTests(TestCase):
    """The project's second self-written page."""

    def setUp(self):
        self.keep = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL,
            legal_last_name="王强", phone="+14085550101")
        self.drop = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL,
            legal_last_name="王强", phone="+14085550101")
        self.url = f"{reverse('contact:contact_merge')}?keep={self.keep.pk}&drop={self.drop.pk}"

    def login(self):
        self.client.force_login(
            get_user_model().objects.create_superuser(username="staff", password="x"))

    def test_the_merge_page_requires_a_staff_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_a_get_on_the_merge_page_does_not_change_anything(self):
        # A confirmation step that quietly acted would be worse than no
        # confirmation step at all.
        self.login()
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.drop.refresh_from_db()
        self.assertTrue(self.drop.is_active)

    def test_posting_performs_the_merge_and_redirects_to_the_kept_record(self):
        self.login()
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(str(self.keep.pk), response["Location"])
        self.drop.refresh_from_db()
        self.assertFalse(self.drop.is_active)

    def test_a_missing_contact_is_a_404(self):
        self.login()
        merge = reverse("contact:contact_merge")
        self.assertEqual(self.client.get(merge).status_code, 404)
        self.assertEqual(
            self.client.get(f"{merge}?keep={self.keep.pk}&drop=999999").status_code, 404)

    def test_the_page_does_not_extend_any_admin_template(self):
        # The whole reason this is not an admin action: inheriting
        # admin/base_site.html is the layer that breaks on upgrade and is thrown
        # away when the front end arrives.
        template = (Path(settings.BASE_DIR)
                    / "contact/templates/contact/merge_confirm.html").read_text()
        self.assertNotIn("admin/base_site.html", template)


class ChangelistCostTests(TestCase):
    """The merge column must not cost a query per row.

    It did: merge_link called find_exact_duplicates() for every row rendered,
    so the most-visited page in the admin ran one extra query per contact —
    a hundred of them on a default page. The count is compared at two sizes
    rather than pinned to a number, so the test survives Django changing its
    own baseline but still fails the moment the cost goes per-row again.
    """

    def setUp(self):
        self.client.force_login(
            get_user_model().objects.create_superuser(username="staff", password="x"))
        self.url = reverse("admin:contact_contact_changelist")

    def populate(self, count):
        Contact.objects.all().delete()
        Contact.objects.bulk_create([
            Contact(contact_type=Contact.ContactType.INDIVIDUAL,
                    legal_last_name=f"Name{number}", phone=f"+1408555{number:04d}")
            for number in range(count)
        ])

    def queries_to_render(self, count):
        self.populate(count)
        with CaptureQueriesContext(connection) as captured:
            self.assertEqual(self.client.get(self.url).status_code, 200)
        return len(captured)

    def test_the_changelist_cost_does_not_grow_with_the_number_of_rows(self):
        self.assertEqual(self.queries_to_render(5), self.queries_to_render(25))

    def test_both_sides_of_a_duplicate_pair_offer_a_merge(self):
        # The pairing is cyclic within a group, so nobody is left without a
        # partner — a row with no link would be a duplicate you cannot merge.
        self.populate(3)
        Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL,
            legal_last_name="Name0", phone="+14085550000")
        page = self.client.get(self.url).content.decode()
        self.assertEqual(page.count("合并掉"), 2)


class MinorTests(TestCase):
    """B4.5: three states, one threshold, and a clock you can inject."""

    def make(self, birth_date, last_name="小明"):
        return Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL,
            legal_last_name=last_name, birth_date=birth_date)

    def test_is_minor_returns_none_when_the_birth_date_is_unknown(self):
        # Not False. Folding unknown into "adult" is how a minor disappears from
        # the parent-notification list without anything going wrong visibly.
        self.assertIsNone(self.make(None).is_minor)

    def test_is_minor_on_the_eighteenth_birthday(self):
        today = local_today()
        eighteen_today = self.make(ContactQuerySet.majority_threshold(today))
        day_short = self.make(
            ContactQuerySet.majority_threshold(today) + datetime.timedelta(days=1))
        self.assertFalse(eighteen_today.is_minor)   # 18 today counts as an adult
        self.assertTrue(day_short.is_minor)

    def test_the_threshold_handles_the_29th_of_february(self):
        # 2028-02-29 minus 18 years is 2010-02-29, which does not exist. Falling
        # back to the 28th keeps somebody born on 1 March 2010 a minor, which
        # they are — 17, by a day.
        leap_day = datetime.date(2028, 2, 29)
        self.assertEqual(
            ContactQuerySet.majority_threshold(leap_day), datetime.date(2010, 2, 28))

    def test_minors_adults_and_unknown_partition_the_whole_table(self):
        today = local_today()
        self.make(today - datetime.timedelta(days=365 * 10), "小孩")
        self.make(today - datetime.timedelta(days=365 * 40), "大人")
        self.make(None, "未知")
        minors = set(Contact.objects.minors())
        adults = set(Contact.objects.adults())
        unknown = set(Contact.objects.birth_date_unknown())
        # No overlap, and between them they account for every row: nobody with a
        # missing birth date can fall through all three.
        self.assertEqual(minors & adults, set())
        self.assertEqual(minors & unknown, set())
        self.assertEqual(adults & unknown, set())
        self.assertEqual(minors | adults | unknown, set(Contact.objects.all()))

    def test_minors_accepts_an_explicit_date(self):
        # Same injectable clock as .active() and .vacant() (D16), which buys
        # "who was still a minor last March" for nothing.
        born = datetime.date(2010, 6, 1)
        contact = self.make(born)
        self.assertIn(contact, Contact.objects.minors(on=datetime.date(2020, 1, 1)))
        self.assertNotIn(contact, Contact.objects.minors(on=datetime.date(2030, 1, 1)))

    def test_the_admin_filter_does_no_date_arithmetic_of_its_own(self):
        # D18: the threshold, the leap year and the timezone rule are written
        # once on the QuerySet, so Phase C reuses them rather than recomputing.
        source = inspect.getsource(MinorFilter.queryset)
        for spelling in ["timedelta", "AGE_OF_MAJORITY", "replace(year"]:
            self.assertNotIn(spelling, source)

    def test_the_filter_offers_an_unknown_option(self):
        options = [value for value, _ in MinorFilter(None, {}, Contact, None).lookups(None, None)]
        self.assertIn("unknown", options)

    def test_contact_has_no_age_field(self):
        # An age goes stale, and nothing tells you it has.
        self.assertNotIn("age", {f.name for f in Contact._meta.get_fields()})


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
            # One management form per inline: emergency contacts (B4.2) and
            # the tenures that org/admin.py hangs on this page (B5). Adding an
            # inline anywhere breaks every admin POST test until its four keys
            # land here — the symptom is a 200 with "ManagementForm data is
            # missing".
            "assignments-TOTAL_FORMS": "0",
            "assignments-INITIAL_FORMS": "0",
            "assignments-MIN_NUM_FORMS": "0",
            "assignments-MAX_NUM_FORMS": "1000",
            "emergency_contacts-TOTAL_FORMS": "0",
            "emergency_contacts-INITIAL_FORMS": "0",
            "emergency_contacts-MIN_NUM_FORMS": "0",
            "emergency_contacts-MAX_NUM_FORMS": "1000",
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
