import datetime

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Value
from django.db.models.functions import Coalesce, Greatest, Least, Lower, Trim
from phonenumber_field.modelfields import PhoneNumberField
from django_countries.fields import CountryField
from simple_history.models import HistoricalRecords

from core.constraints import ConstraintErrorFieldMixin
from core.models import TimeStampedModel
from core.querysets import DateRangeMixin, DateRangeQuerySet


class Language(models.Model):
    """One ISO 639-3 language.

    Seeded from pycountry (~7,900 rows) by a data migration, and editable in the
    admin afterwards. We keep our own table rather than using django-languages-plus
    because that table is keyed on the 2-letter ISO 639-1 code, which excludes
    Mandarin (cmn), Cantonese (yue), Hmong and many others.
    """

    class Type(models.TextChoices):
        LIVING = "L", "Living"
        EXTINCT = "E", "Extinct"
        HISTORICAL = "H", "Historical"
        CONSTRUCTED = "C", "Constructed"
        SPECIAL = "S", "Special"

    code = models.CharField(
        max_length=3, primary_key=True, verbose_name="ISO 639-3 code",
    )
    name = models.CharField(max_length=150, help_text="Official ISO 639-3 name.")
    display_name = models.CharField(
        max_length=150, db_index=True,
        help_text="Name shown in dropdowns. Defaults to the ISO name.",
    )
    alt_names = models.CharField(
        max_length=255, blank=True,
        help_text="Other names this language can be found by, comma separated. "
                  "Searched but not displayed.",
    )
    language_type = models.CharField(
        max_length=1, choices=Type.choices, default=Type.LIVING,
    )
    # Higher ranks sort first, so the languages we serve most often sit at the top
    # of the dropdown ahead of the alphabetical list.
    pin_rank = models.PositiveSmallIntegerField(
        default=0, help_text="Higher numbers sort to the top of the list. 0 = unpinned.",
    )

    class Meta:
        ordering = ["-pin_rank", "display_name"]

    def __str__(self):
        return self.display_name


class Contact(ConstraintErrorFieldMixin, TimeStampedModel):
    """A person OR an organization. contact_type distinguishes them."""

    # --- Type: individual vs organization (the CiviCRM approach) ---
    class ContactType(models.TextChoices):
        INDIVIDUAL = "individual", "Individual"
        ORGANIZATION = "organization", "Organization"

    contact_type = models.CharField(
        max_length=20,
        choices=ContactType.choices,
        default=ContactType.INDIVIDUAL,
        db_index=True,
    )

    # --- Name fields ---
    legal_first_name = models.CharField(max_length=100, blank=True)
    legal_last_name = models.CharField(max_length=100, blank=True, db_index=True)
    preferred_name = models.CharField(max_length=100, blank=True)
    organization_name = models.CharField(
        max_length=200, blank=True,
        help_text="Used when contact_type is organization.",
    )
    # Which name fields apply to which contact type. The admin hides the others
    # (contact_type_toggle.js) and save() clears them.
    NAME_FIELDS = {
        ContactType.INDIVIDUAL: ["legal_first_name", "legal_last_name", "preferred_name"],
        ContactType.ORGANIZATION: ["organization_name"],
    }

    # --- Contact info ---
    email = models.EmailField(blank=True, db_index=True)
    # Stores in E.164 international format (+1..., +44...); region lets users type local US numbers.
    phone = PhoneNumberField(blank=True, region="US")

    # --- Demographics ---
    class Gender(models.TextChoices):
        FEMALE = "female", "Female"
        MALE = "male", "Male"
        OTHER = "other", "Other"
        UNSPECIFIED = "unspecified", "Prefer not to say"

    gender = models.CharField(
        max_length=20, choices=Gender.choices, blank=True,
    )
    birth_date = models.DateField(null=True, blank=True)

    # Full ISO 639-3 language list; see the Language model above. The dropdown is
    # narrowed to living languages — the extinct and historical ones are still in
    # the table (and editable in the Language admin), just not offered here.
    # PROTECT: don't allow deleting a language that contacts still reference.
    preferred_language = models.ForeignKey(
        Language,
        on_delete=models.PROTECT,
        null=True, blank=True,
        limit_choices_to={"language_type": Language.Type.LIVING},
        related_name="+",   # no reverse accessor needed from Language back to Contact
    )

    class CommunicationMethod(models.TextChoices):
        EMAIL = "email", "Email"
        PHONE = "phone", "Phone"
        SMS = "sms", "SMS"
        MAIL = "mail", "Postal mail"

    preferred_communication_method = models.CharField(
        max_length=20, choices=CommunicationMethod.choices, blank=True,
    )

    # --- Structured address (single address, Google-Places-ready later) ---
    address_street = models.CharField(max_length=255, blank=True)
    address_city = models.CharField(max_length=100, blank=True)
    # Free text so non-US addresses keep their province/region. For US addresses the
    # admin swaps in the 50-state dropdown (address_state_toggle.js), which stores
    # the usual 2-letter abbreviation.
    address_state = models.CharField(
        max_length=100, blank=True, verbose_name="state / province / region",
    )
    address_postal_code = models.CharField(max_length=20, blank=True)
    # Full ISO 3166 country dropdown, provided by django-countries.
    address_country = CountryField(blank=True, default="US")

    # --- Status & bookkeeping ---
    is_active = models.BooleanField(default=True, db_index=True)
    notes = models.TextField(blank=True)
    # created_at / updated_at come from TimeStampedModel

    # Who changed what, and when. Dictionary tables like Language do not need
    # this; Assignment (Phase B) and Contribution (Phase C) must have it.
    history = HistoricalRecords()

    class Meta:
        ordering = ["legal_last_name", "legal_first_name"]
        constraints = [
            # The one and only statement of these rules (goal.md D9 / D14). They
            # hold for every write path — objects.create(), bulk_create(),
            # queryset.update(), psql. Do NOT restate them in clean(): the admin
            # gets field-level errors from violation_error_code below, via
            # core.constraints.CONSTRAINT_FIELD.
            #
            # ⚠️ THREE constraints, not the one this used to be. A single
            # constraint said "an individual needs a last name OR an organization
            # needs a name", which is two rules with two different offending
            # fields — and a code maps to exactly one field, so the organization
            # case landed on legal_last_name. One constraint per rule is what
            # makes the mapping possible at all. The third one preserves what the
            # old OR-form enforced by accident: an unknown contact_type failed it
            # too, and splitting would have silently dropped that.
            #
            # The values have to be written out as literals: a nested class body
            # cannot see the enclosing class's namespace, so ContactType.INDIVIDUAL
            # here would raise NameError. Keep them in step with ContactType above.
            models.CheckConstraint(
                condition=(
                    ~models.Q(contact_type="individual") | ~models.Q(legal_last_name="")
                ),
                name="contact_individual_has_a_last_name",
                violation_error_message="An individual needs a legal last name.",
                violation_error_code="individual_needs_last_name",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(contact_type="organization") | ~models.Q(organization_name="")
                ),
                name="contact_organization_has_a_name",
                violation_error_message="An organization needs an organization name.",
                violation_error_code="organization_needs_name",
            ),
            models.CheckConstraint(
                condition=models.Q(contact_type__in=["individual", "organization"]),
                name="contact_type_is_known",
                violation_error_message="Unknown contact type.",
                violation_error_code="contact_type_unknown",
            ),
        ]

    def save(self, *args, **kwargs):
        """Blank out the name fields that don't apply to this contact type.

        Keeps the record unambiguous: an organization never carries a leftover
        first name from before the type was switched.
        """
        for contact_type, fields in self.NAME_FIELDS.items():
            if contact_type != self.contact_type:
                for field in fields:
                    setattr(self, field, "")
        super().save(*args, **kwargs)

    def __str__(self):
        if self.contact_type == self.ContactType.ORGANIZATION:
            return self.organization_name or "(unnamed organization)"
        full = f"{self.legal_first_name} {self.legal_last_name}".strip()
        return self.preferred_name or full or self.email or f"Contact #{self.pk}"


class RelationshipType(ConstraintErrorFieldMixin, models.Model):
    """A dictionary of relationship kinds: 'volunteer at', 'parent of', 'spouse of'.

    Note 'manages' / 'managed by' are no longer used for the org chart — reporting
    lines hang off Position.reports_to (goal.md D6 / D11).
    """

    # Code is what the code matches on, forever. Display names are editable in the
    # admin, which means filter(name_a_to_b="parent of") stops finding anything the
    # day somebody renames one — silently, with no error. See goal.md D5 / D6.
    code = models.SlugField(
        max_length=50,
        help_text="Stable identifier used by code. Lowercase, cannot be changed later.",
    )

    # Label as seen from A -> B, e.g. "parent of"
    name_a_to_b = models.CharField(max_length=100)
    # Reverse label B -> A, e.g. "child of". Empty for symmetric types.
    name_b_to_a = models.CharField(max_length=100, blank=True)
    description = models.CharField(max_length=255, blank=True)

    # Marks 'spouse of' / 'sibling of' explicitly rather than inferring symmetry
    # from an empty name_b_to_a: whoever enters the type may well type "spouse of"
    # into both boxes, and then the inference is simply wrong. See goal.md D15.
    is_symmetric = models.BooleanField(
        default=False,
        help_text="The relationship reads the same in both directions (spouse, sibling).",
    )

    # Emergency contacts reuse this vocabulary (goal.md D6). This boolean keeps
    # 'employee of' and friends out of that dropdown via limit_choices_to — the
    # same trick Contact.preferred_language already uses for living languages.
    usable_as_emergency_contact = models.BooleanField(
        default=False,
        help_text="Offer this type when recording an emergency contact.",
    )

    class Meta:
        ordering = ["name_a_to_b"]
        constraints = [
            # Lower(): a plain UniqueConstraint would happily take "Parent of"
            # alongside "parent of", which also contradicts the case-insensitive
            # comparison clean() does below.
            # Trim() : relying on save() to strip means bulk_create can still
            #          insert " parent of". See goal.md D9「归一化通则」.
            models.UniqueConstraint(
                Lower(Trim("name_a_to_b")),
                name="relationshiptype_name_a_to_b_ci_unique",
                violation_error_message="A relationship type with this name already exists.",
                violation_error_code="reltype_name_taken",
            ),
            # Lower("code") rather than unique=True on the field, for the same
            # reason: save() lowercases, and bulk_create does not call save(), so
            # Food_Pantry and food_pantry would both go in.
            models.UniqueConstraint(
                Lower("code"),
                name="relationshiptype_code_ci_unique",
                violation_error_message="A relationship type with this code already exists.",
                violation_error_code="reltype_code_taken",
            ),
        ]

    def save(self, *args, **kwargs):
        # None of this carries correctness any more — the expression constraints
        # above do. It is here so the stored values are clean and the admin
        # behaves consistently. See goal.md D9「归一化通则」.
        self.code = self.code.strip().lower()
        self.name_a_to_b = " ".join(self.name_a_to_b.split())
        self.name_b_to_a = " ".join(self.name_b_to_a.split())
        super().save(*args, **kwargs)

    def clean(self):
        """The two rules no constraint can express, because both look at other rows.

        ⚠️ This is the form layer's only interception for these two, not a
        presentation hint — bulk_create walks straight past it. A known and
        stated imperfection, see goal.md D14.

        1. Gap 1: a new type's forward name collides with an existing type's
           reverse name. The root cause is that the reverse type row should never
           exist at all — "child of" is already the name_b_to_a of "parent of".
           No reverse type, no reverse relationship rows: the defence belongs at
           the type level, not on every relationship.
        2. code is immutable once created. editable=False only stops ModelForms;
           this compares against the value in the database.
        """
        super().clean()
        errors = {}

        forward = " ".join((self.name_a_to_b or "").split()).casefold()
        if forward:
            clash = (
                RelationshipType.objects.exclude(pk=self.pk)
                .filter(name_b_to_a__iexact=forward)
                .first()
            )
            if clash:
                errors["name_a_to_b"] = (
                    f'"{self.name_a_to_b}" is already the reverse label of '
                    f'"{clash.name_a_to_b}". Use that type instead of adding its mirror.'
                )

        if self.pk:
            previous = RelationshipType.objects.filter(pk=self.pk).values_list(
                "code", flat=True).first()
            if previous is not None and previous != (self.code or "").strip().lower():
                errors["code"] = (
                    f'Code cannot be changed once created (it is "{previous}"). '
                    "Code is what the rest of the system matches on."
                )

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.name_a_to_b


class Relationship(ConstraintErrorFieldMixin, DateRangeMixin, TimeStampedModel):
    """Connects two contacts with a typed, dated relationship.

    Example rows:
      (Alice, RedCross, 'volunteer at')
      (Bob, Alice, 'manages')
      (Carol, Ming, 'parent of')
    """

    contact_a = models.ForeignKey(
        Contact,
        on_delete=models.CASCADE,
        related_name="relationships_as_a",
    )
    contact_b = models.ForeignKey(
        Contact,
        on_delete=models.CASCADE,
        related_name="relationships_as_b",
    )
    relationship_type = models.ForeignKey(
        RelationshipType,
        on_delete=models.PROTECT,
        related_name="relationships",
    )

    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    # No is_active: it and end_date were the same fact recorded twice, and the
    # pair could contradict each other (is_active=True with end_date in 2020).
    # Whether a relationship is in effect is derived — see .active() below.
    # created_at / updated_at come from TimeStampedModel

    objects = models.Manager.from_queryset(DateRangeQuerySet)()

    class Meta:
        indexes = [
            models.Index(fields=["contact_a", "relationship_type"]),
            models.Index(fields=["contact_b", "relationship_type"]),
        ]
        # These three constraints are the only statement of their rules
        # (goal.md D14). Nothing restates them in clean(); the admin gets
        # field-level errors from violation_error_code, via
        # core.constraints.CONSTRAINT_FIELD.
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(contact_a=models.F("contact_b")),
                name="relationship_no_self_reference",
                violation_error_message="A contact cannot be related to themselves.",
                violation_error_code="relationship_self_reference",
            ),
            # Replaces A7's (contact_a, contact_b, type, start_date) — not
            # alongside it. The unordered version is strictly stronger: it covers
            # every case the old one did, plus the mirrored row A7 could not see
            # ((王强,李梅,spouse) and (李梅,王强,spouse) are different column
            # values, so the old constraint stayed quiet).
            #
            # No condition on it: once the mirror *type* is impossible (gap 1,
            # RelationshipType.clean()), one pair holding one type in two
            # directions is wrong for every type, not just symmetric ones —
            # (小明, 王强, parent of) says 小明 is the parent, and the two cannot
            # both be true.
            #
            # Coalesce rather than nulls_distinct=False: whether an expression
            # UniqueConstraint can carry nulls_distinct is untested, and
            # date.min is exactly equivalent — two rows with no start date still
            # collide — without depending on that unknown.
            models.UniqueConstraint(
                Least("contact_a", "contact_b"),
                Greatest("contact_a", "contact_b"),
                "relationship_type",
                Coalesce("start_date", Value(datetime.date.min)),
                name="relationship_unique_unordered_pair",
                violation_error_message="This relationship has already been recorded.",
                violation_error_code="relationship_already_recorded",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(end_date__isnull=True)
                    | models.Q(start_date__isnull=True)
                    | models.Q(end_date__gte=models.F("start_date"))
                ),
                name="relationship_end_date_not_before_start_date",
                violation_error_message="The end date cannot be before the start date.",
                violation_error_code="relationship_end_before_start",
            ),
        ]

    def save(self, *args, **kwargs):
        # Symmetric types (spouse, sibling) always store the lower id as
        # contact_a, so the row has one settled reading.
        #
        # ⚠️ Cosmetic only — duplicates are refused by
        #    relationship_unique_unordered_pair above, which bulk_create cannot
        #    dodge. And asymmetric types must never be swapped: their direction
        #    carries the meaning, so swapping reverses the sentence.
        if self.relationship_type_id and self.relationship_type.is_symmetric:
            if (self.contact_a_id and self.contact_b_id
                    and self.contact_a_id > self.contact_b_id):
                self.contact_a_id, self.contact_b_id = self.contact_b_id, self.contact_a_id
        super().save(*args, **kwargs)

    def label_from(self, side):
        """How this relationship reads from one side: 'parent of' / 'child of'.

        Symmetric types fall back to the forward label, because their reverse
        one is empty by design — 'spouse of' backwards is still 'spouse of'.
        """
        if side == "contact_a" or self.relationship_type.is_symmetric:
            return self.relationship_type.name_a_to_b
        return self.relationship_type.name_b_to_a or self.relationship_type.name_a_to_b

    def __str__(self):
        return f"{self.contact_a} — {self.relationship_type} → {self.contact_b}"