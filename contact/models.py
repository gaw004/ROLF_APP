from django.core.exceptions import ValidationError
from django.db import models
from phonenumber_field.modelfields import PhoneNumberField
from django_countries.fields import CountryField
from simple_history.models import HistoricalRecords

from core.models import TimeStampedModel


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


class Contact(TimeStampedModel):
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
            # ⚠️ PAIRED WITH Contact.clean() — see goal.md D14.
            # This constraint is what actually enforces the rule: it holds for
            # every write path, including objects.create(), bulk_create() and
            # psql. clean() states the same rule again purely so the admin can
            # attach the error to the offending field. CHANGE ONE, CHANGE BOTH.
            #
            # The values have to be written out as literals: a nested class body
            # cannot see the enclosing class's namespace, so ContactType.INDIVIDUAL
            # here would raise NameError. Keep them in step with ContactType above.
            models.CheckConstraint(
                condition=(
                    (models.Q(contact_type="individual") & ~models.Q(legal_last_name=""))
                    | (models.Q(contact_type="organization") & ~models.Q(organization_name=""))
                ),
                name="contact_name_matches_type",
                violation_error_message=(
                    "An individual needs a legal last name; "
                    "an organization needs an organization name."
                ),
            ),
        ]

    def clean(self):
        """Require the name that matches the contact type.

        ⚠️ PAIRED WITH the `contact_name_matches_type` constraint in Meta above
        — see goal.md D14. The constraint is the enforcement; this method only
        exists to point at the field that is wrong. CHANGE ONE, CHANGE BOTH.
        """
        super().clean()
        if self.contact_type == self.ContactType.ORGANIZATION:
            if not self.organization_name:
                raise ValidationError({
                    "organization_name": "An organization needs an organization name.",
                })
        elif self.contact_type == self.ContactType.INDIVIDUAL:
            if not self.legal_last_name:
                raise ValidationError({
                    "legal_last_name": "An individual needs a legal last name.",
                })

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


class RelationshipType(models.Model):
    """A dictionary of relationship kinds: 'volunteer at', 'manages', 'parent of'."""

    # Label as seen from A -> B, e.g. "manages"
    name_a_to_b = models.CharField(max_length=100)
    # Reverse label B -> A, e.g. "managed by". Optional but useful for display.
    name_b_to_a = models.CharField(max_length=100, blank=True)
    description = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return self.name_a_to_b


class Relationship(TimeStampedModel):
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
    is_active = models.BooleanField(default=True, db_index=True)
    # created_at / updated_at come from TimeStampedModel

    class Meta:
        indexes = [
            models.Index(fields=["contact_a", "relationship_type"]),
            models.Index(fields=["contact_b", "relationship_type"]),
        ]
        # ⚠️ ALL THREE ARE PAIRED WITH Relationship.clean() — see goal.md D14.
        # These constraints are the enforcement; clean() restates them only to
        # attach errors to the right field in the admin. CHANGE ONE, CHANGE BOTH.
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(contact_a=models.F("contact_b")),
                name="relationship_no_self_reference",
                violation_error_message="A contact cannot be related to themselves.",
            ),
            models.UniqueConstraint(
                fields=["contact_a", "contact_b", "relationship_type", "start_date"],
                name="relationship_unique_per_type_and_start",
                # Without this, Postgres treats NULL != NULL and the same
                # relationship with no start date could be stored any number of
                # times — which is the most likely duplicate, not the rarest.
                # Needs PG 15+; we are on 18.
                nulls_distinct=False,
                violation_error_message="This relationship has already been recorded.",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(end_date__isnull=True)
                    | models.Q(start_date__isnull=True)
                    | models.Q(end_date__gte=models.F("start_date"))
                ),
                name="relationship_end_date_not_before_start_date",
                violation_error_message="The end date cannot be before the start date.",
            ),
        ]

    def clean(self):
        """Restate the Meta constraints as field-level errors for the admin.

        ⚠️ PAIRED WITH the constraints in Meta above — see goal.md D14. The
        database is what enforces these; this method only decides which field
        turns red. CHANGE ONE, CHANGE BOTH.

        The duplicate rule is deliberately not repeated here: it has no single
        offending field to point at, and Django's own constraint validation
        already reports it. See the constraint named
        `relationship_unique_per_type_and_start`.
        """
        super().clean()
        # relationship_no_self_reference
        if self.contact_a_id and self.contact_a_id == self.contact_b_id:
            raise ValidationError({
                "contact_b": "A contact cannot be related to themselves.",
            })
        # relationship_end_date_not_before_start_date
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({
                "end_date": "The end date cannot be before the start date.",
            })

    def __str__(self):
        return f"{self.contact_a} — {self.relationship_type} → {self.contact_b}"