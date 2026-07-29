import datetime

from django.conf import settings
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.test import TestCase, override_settings
from django.urls import path, reverse

from .forms import ContactAdminForm, RelationshipForm
from .models import (
    Contact, EmergencyContact, Language, Relationship, RelationshipType,
)
from .services import direction_choices


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
        # the mirror type in means the same relationship can be recorded twice,
        # in two directions, and every constraint stays happy — the duplicate
        # then shows up twice on the same contact's page.
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


class RelationshipConstraintTests(TestCase):
    """The three Relationship constraints, plus the field-level errors that pair
    with them (goal.md D14)."""

    def setUp(self):
        self.alice = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL, legal_last_name="Alice")
        self.bob = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL, legal_last_name="Bob")
        self.parent_of = RelationshipType.objects.create(
            code="parent_of", name_a_to_b="parent of", name_b_to_a="child of")

    def test_cannot_relate_a_contact_to_itself(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Relationship.objects.create(
                contact_a=self.alice, contact_b=self.alice,
                relationship_type=self.parent_of,
            )

    def test_self_reference_error_points_at_the_contact_field(self):
        # What the D14 machinery buys us: the admin marks contact_b instead of
        # dropping a database message at the top of the form. The rule itself is
        # stated once, in the constraint — see ConstraintFieldErrorTests below.
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


class RelationshipDirectionTests(TestCase):
    """B3.1b: either side can record the relationship, and it never comes out
    reversed. The tests build the form directly — no browser, no admin — which
    is itself the proof that Phase C can reuse it unchanged."""

    def setUp(self):
        self.ming = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL, legal_last_name="小明")
        self.qiang = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL, legal_last_name="王强")
        self.parent_of = RelationshipType.objects.create(
            code="parent_of", name_a_to_b="父亲", name_b_to_a="儿子")
        self.spouse_of = RelationshipType.objects.create(
            code="spouse_of", name_a_to_b="配偶", is_symmetric=True)

    def choice(self, relationship_type, direction):
        return f"{relationship_type.pk}:{direction}"

    def submit(self, subject, relationship_type, direction, other):
        form = RelationshipForm(
            data={
                "direction_choice": self.choice(relationship_type, direction),
                "other": other.pk,
            },
            subject=subject,
        )
        self.assertTrue(form.is_valid(), form.errors)
        return form.save()

    def test_choosing_the_reverse_reading_puts_the_other_party_in_contact_a(self):
        # Standing on 小明's page and saying "小明 is ___'s son" has to store
        # (王强, 小明, parent of). Under the old rule this could not be recorded
        # here at all: you had to navigate to 王强's page first.
        relationship = self.submit(self.ming, self.parent_of, "rev", self.qiang)
        self.assertEqual(relationship.contact_a, self.qiang)
        self.assertEqual(relationship.contact_b, self.ming)

    def test_choosing_the_forward_reading_puts_the_subject_in_contact_a(self):
        relationship = self.submit(self.qiang, self.parent_of, "fwd", self.ming)
        self.assertEqual(relationship.contact_a, self.qiang)
        self.assertEqual(relationship.contact_b, self.ming)

    def test_a_symmetric_type_appears_only_once_in_the_direction_choices(self):
        values = dict(direction_choices(self.ming))
        self.assertIn(self.choice(self.spouse_of, "fwd"), values)
        self.assertNotIn(self.choice(self.spouse_of, "rev"), values)
        # Asymmetric types offer both readings.
        self.assertIn(self.choice(self.parent_of, "fwd"), values)
        self.assertIn(self.choice(self.parent_of, "rev"), values)

    def test_the_subject_is_not_offered_as_the_other_party(self):
        form = RelationshipForm(subject=self.ming)
        self.assertNotIn(self.ming, form.fields["other"].queryset)

    def test_a_duplicate_is_reported_on_the_form_not_as_an_integrity_error(self):
        # contact_a / contact_b are not fields on this form, so ModelForm would
        # skip every constraint that mentions them — the duplicate would only
        # surface as a 500 at save time. See RelationshipForm._check_constraints.
        self.submit(self.qiang, self.parent_of, "fwd", self.ming)
        form = RelationshipForm(
            data={
                "direction_choice": self.choice(self.parent_of, "fwd"),
                "other": self.ming.pk,
            },
            subject=self.qiang,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("other", form.errors)

    def test_the_mirrored_entry_is_also_reported_on_the_form(self):
        # Same pair, same type, entered from the other side: different columns,
        # same relationship. The unordered-pair constraint sees it.
        self.submit(self.qiang, self.parent_of, "fwd", self.ming)
        form = RelationshipForm(
            data={
                "direction_choice": self.choice(self.parent_of, "rev"),
                "other": self.qiang.pk,
            },
            subject=self.ming,
        )
        self.assertFalse(form.is_valid())


class RelationshipPageTests(TestCase):
    """The project's first non-admin page (B3.1b)."""

    def setUp(self):
        self.ming = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL, legal_last_name="小明")
        self.url = reverse("contact:relationship_add")

    def test_the_relationship_page_requires_a_staff_login(self):
        response = self.client.get(f"{self.url}?subject={self.ming.pk}")
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_the_relationship_page_404s_without_a_valid_subject(self):
        self.client.force_login(
            get_user_model().objects.create_superuser(username="staff", password="x"))
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.get(f"{self.url}?subject=999999").status_code, 404)

    def test_a_staff_member_sees_the_form(self):
        self.client.force_login(
            get_user_model().objects.create_superuser(username="staff", password="x"))
        response = self.client.get(f"{self.url}?subject={self.ming.pk}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["subject"], self.ming)


class RelationshipDisplayTests(TestCase):
    """B3.1: both sides of a relationship are visible, with the right label."""

    def setUp(self):
        self.ming = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL, legal_last_name="小明")
        self.qiang = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL, legal_last_name="王强")
        self.parent_of = RelationshipType.objects.create(
            code="parent_of", name_a_to_b="父亲", name_b_to_a="儿子")

    def test_the_reverse_label_is_shown_on_the_other_contact(self):
        # Recording "王强 父亲 小明" used to leave 王强 invisible on 小明's page:
        # the row was there, the second label was simply never read.
        relationship = Relationship.objects.create(
            contact_a=self.qiang, contact_b=self.ming,
            relationship_type=self.parent_of)
        self.assertEqual(relationship.label_from("contact_a"), "父亲")
        self.assertEqual(relationship.label_from("contact_b"), "儿子")

    def test_a_symmetric_type_falls_back_to_the_forward_label(self):
        spouse_of = RelationshipType.objects.create(
            code="spouse_of", name_a_to_b="配偶", is_symmetric=True)
        relationship = Relationship.objects.create(
            contact_a=self.qiang, contact_b=self.ming, relationship_type=spouse_of)
        self.assertEqual(relationship.label_from("contact_a"), "配偶")
        self.assertEqual(relationship.label_from("contact_b"), "配偶")

    def test_both_inlines_are_read_only(self):
        # Entry moved to /relationships/add/; leaving an editable row here would
        # bring the whole formset apparatus back, and with it the problem that
        # an inline form cannot see whose page it is on.
        model_admin = admin.site._registry[Contact]
        inlines = [
            inline(Contact, admin.site) for inline in model_admin.inlines
            if inline.model is Relationship
        ]
        self.assertEqual([i.fk_name for i in inlines], ["contact_a", "contact_b"])
        for inline in inlines:
            self.assertFalse(inline.has_add_permission(None, None))
            self.assertEqual(list(inline.readonly_fields), list(inline.fields))


class RelationshipSymmetryTests(TestCase):
    """B3.2: the enforcement layer, and the cosmetic normalisation next to it."""

    def setUp(self):
        self.qiang = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL, legal_last_name="王强")
        self.mei = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL, legal_last_name="李梅")
        self.spouse_of = RelationshipType.objects.create(
            code="spouse_of", name_a_to_b="配偶", is_symmetric=True)
        self.parent_of = RelationshipType.objects.create(
            code="parent_of", name_a_to_b="父亲", name_b_to_a="儿子")

    def test_a_symmetric_relationship_is_normalised_to_lowest_id_first(self):
        relationship = Relationship.objects.create(
            contact_a=self.mei, contact_b=self.qiang, relationship_type=self.spouse_of)
        relationship.refresh_from_db()
        self.assertEqual(relationship.contact_a_id, min(self.qiang.pk, self.mei.pk))

    def test_an_asymmetric_relationship_is_never_swapped(self):
        # Direction is the meaning here: swapping turns "王强 is 李梅's father"
        # into the opposite claim.
        relationship = Relationship.objects.create(
            contact_a=self.mei, contact_b=self.qiang, relationship_type=self.parent_of)
        relationship.refresh_from_db()
        self.assertEqual(relationship.contact_a_id, self.mei.pk)

    # --- Enforcement: these must bypass save(), which is the entire point ------
    # Written through save() they would pass while proving nothing: save()
    # normalises the pair before the database ever sees it. bulk_create is the
    # path a data import takes.

    def test_bulk_create_cannot_insert_a_mirrored_symmetric_pair(self):
        Relationship.objects.create(
            contact_a=self.qiang, contact_b=self.mei, relationship_type=self.spouse_of)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Relationship.objects.bulk_create([
                Relationship(contact_a=self.mei, contact_b=self.qiang,
                             relationship_type=self.spouse_of),
            ])

    def test_bulk_create_cannot_insert_a_mirrored_asymmetric_pair(self):
        # The constraint carries no condition, so this is refused too: one pair
        # cannot hold "父亲" in both directions.
        Relationship.objects.create(
            contact_a=self.qiang, contact_b=self.mei, relationship_type=self.parent_of)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Relationship.objects.bulk_create([
                Relationship(contact_a=self.mei, contact_b=self.qiang,
                             relationship_type=self.parent_of),
            ])

    def test_the_same_pair_and_type_can_repeat_with_different_start_dates(self):
        Relationship.objects.create(
            contact_a=self.qiang, contact_b=self.mei, relationship_type=self.spouse_of,
            start_date=datetime.date(2010, 1, 1), end_date=datetime.date(2015, 1, 1))
        second = Relationship.objects.create(
            contact_a=self.qiang, contact_b=self.mei, relationship_type=self.spouse_of,
            start_date=datetime.date(2020, 1, 1))
        self.assertEqual(Relationship.objects.count(), 2)
        self.assertIsNotNone(second.pk)

    def test_the_same_pair_and_type_with_both_start_dates_null_is_rejected(self):
        # What Coalesce(start_date, date.min) buys: two NULLs are equal here,
        # the same thing nulls_distinct=False does for plain field constraints.
        Relationship.objects.create(
            contact_a=self.qiang, contact_b=self.mei, relationship_type=self.spouse_of)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Relationship.objects.bulk_create([
                Relationship(contact_a=self.qiang, contact_b=self.mei,
                             relationship_type=self.spouse_of),
            ])


class RelationshipActiveTests(TestCase):
    """B3.3: is_active is gone; being in effect is derived from the dates."""

    def setUp(self):
        self.qiang = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL, legal_last_name="王强")
        self.mei = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL, legal_last_name="李梅")
        self.spouse_of = RelationshipType.objects.create(
            code="spouse_of", name_a_to_b="配偶", is_symmetric=True)

    def test_relationship_has_no_is_active_field(self):
        # It and end_date were one fact stored twice, and could disagree
        # (is_active=True alongside end_date=2020). Pinning its absence.
        field_names = {f.name for f in Relationship._meta.get_fields()}
        self.assertNotIn("is_active", field_names)

    def test_relationship_active_uses_the_shared_queryset(self):
        current = Relationship.objects.create(
            contact_a=self.qiang, contact_b=self.mei,
            relationship_type=self.spouse_of, start_date=datetime.date(2020, 1, 1))
        self.assertIn(current, Relationship.objects.active())
        self.assertTrue(current.is_currently_active)

    def test_an_ended_relationship_is_not_active(self):
        ended = Relationship.objects.create(
            contact_a=self.qiang, contact_b=self.mei,
            relationship_type=self.spouse_of,
            start_date=datetime.date(2010, 1, 1), end_date=datetime.date(2015, 1, 1))
        self.assertNotIn(ended, Relationship.objects.active())
        self.assertFalse(ended.is_currently_active)


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
            # One management form per inline: emergency contacts (B4.2), plus
            # the relationships read from each side (B3.1).
            "emergency_contacts-TOTAL_FORMS": "0",
            "emergency_contacts-INITIAL_FORMS": "0",
            "emergency_contacts-MIN_NUM_FORMS": "0",
            "emergency_contacts-MAX_NUM_FORMS": "1000",
            "relationships_as_a-TOTAL_FORMS": "0",
            "relationships_as_a-INITIAL_FORMS": "0",
            "relationships_as_a-MIN_NUM_FORMS": "0",
            "relationships_as_a-MAX_NUM_FORMS": "1000",
            "relationships_as_b-TOTAL_FORMS": "0",
            "relationships_as_b-INITIAL_FORMS": "0",
            "relationships_as_b-MIN_NUM_FORMS": "0",
            "relationships_as_b-MAX_NUM_FORMS": "1000",
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
