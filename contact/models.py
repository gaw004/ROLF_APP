from collections import defaultdict

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower, Trim
from phonenumber_field.modelfields import PhoneNumberField
from django_countries.fields import CountryField
from simple_history.models import HistoricalRecords

from core.constraints import ConstraintErrorFieldMixin
from core.models import ImmutableCodeMixin, TimeStampedModel
from core.timeutils import local_today


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


class ContactQuerySet(models.QuerySet):
    """Derived judgements about contacts, defined once (goal.md D18).

    Every admin filter on Contact calls one of these and does no work of its
    own — that is what keeps the logic alive when admin.py is eventually deleted.
    """

    # The age of majority, in one place. Changing it to 16 is a one-line edit
    # here and nowhere else — which is the reason the filter is not allowed to
    # do this arithmetic itself.
    AGE_OF_MAJORITY = 18

    @classmethod
    def majority_threshold(cls, on=None):
        """Born on or before this date ⇒ already an adult on `on`.

        Subtracting years, not counting days — 18 × 365 is wrong four or five
        times over that span, and wrong by a day never raises anything.

        `on` is a parameter rather than an implicit call to local_today() for the
        same reason .active() takes one (D16): it makes "who was still a minor
        last March" free, and testing the boundary free with it.
        """
        on = on or local_today()
        year = on.year - cls.AGE_OF_MAJORITY
        try:
            return on.replace(year=year)
        except ValueError:
            # 29 February, and the year we land in is not a leap year. Step back
            # to the 28th: somebody born on 1 March that year is still 17 today
            # and must stay on the minor side of the line.
            return on.replace(year=year, day=28)

    def minors(self, on=None):
        return self.filter(birth_date__gt=self.majority_threshold(on))

    def adults(self, on=None):
        return self.filter(birth_date__lte=self.majority_threshold(on))

    def birth_date_unknown(self):
        """Unknown is its own answer, never folded into "adult".

        A minor with no birth date on file would otherwise disappear silently
        from the list of people whose parents need calling — the one mistake
        this feature cannot afford.
        """
        return self.filter(birth_date__isnull=True)

    def possible_duplicates(self):
        """Contacts sharing a normalised name AND a phone number with another row.

        The same rule as Contact.find_exact_duplicates(), asked of the whole
        table at once: that one answers "is this new person already here?", this
        one answers "what is already sitting in here twice?".
        """
        keyed = self.exclude(phone="").annotate(
            key_last=Lower(Trim("legal_last_name")),
            key_first=Lower(Trim("legal_first_name")),
        )
        duplicated_keys = (
            keyed.values("key_last", "key_first", "phone")
            .annotate(row_count=models.Count("id"))
            .filter(row_count__gt=1)
        )
        matches = models.Q(pk__in=[])
        for key in duplicated_keys:
            matches |= models.Q(
                key_last=key["key_last"],
                key_first=key["key_first"],
                phone=key["phone"],
            )
        return keyed.filter(matches)

    def duplicate_partners(self):
        """{pk: the pk of another contact sharing this one's name and phone}.

        One lookup for a whole page. The admin's merge column used to call
        find_exact_duplicates() per row, which is a query per row on the most
        visited page in the project — the same N+1 that list_select_related is
        spelled out to avoid two files away.

        Pairing lives on the queryset for the reason the judgement does (D18):
        admin.py renders this, it does not work it out. It also means the merge
        column and the "Possible duplicates" admin filter now answer from one
        of two that could disagree.
        """
        grouped = defaultdict(list)
        for row in self.possible_duplicates().values(
                "pk", "key_last", "key_first", "phone"):
            grouped[(row["key_last"], row["key_first"], row["phone"])].append(row["pk"])

        partners = {}
        for pks in grouped.values():
            # Each row points at the next in its group and the last back at the
            # first, so every side of a duplicate offers a merge and none is
            # left without a partner.
            for index, pk in enumerate(pks):
                partners[pk] = pks[(index + 1) % len(pks)]
        return partners


class Contact(ConstraintErrorFieldMixin, TimeStampedModel):
    """A person OR an organization. contact_type distinguishes them."""

    objects = models.Manager.from_queryset(ContactQuerySet)()

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
    # Indexed because find_exact_duplicates() filters on it first (B4.3).
    phone = PhoneNumberField(blank=True, region="US", db_index=True)

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

    @property
    def is_minor(self):
        """True / False / None (birth date unknown) — three states, not two.

        birth_date is nullable, and folding unknown into False would quietly
        drop minors with no date on file from the parent-notification list.

        Never store an age: it goes stale, and nothing tells you it has.
        """
        if self.birth_date is None:
            return None
        return self.birth_date > ContactQuerySet.majority_threshold()

    @staticmethod
    def normalise_name(value):
        """Collapse whitespace and case, so " Wang  Qiang " == "wang qiang"."""
        return " ".join((value or "").split()).casefold()

    @classmethod
    def find_exact_duplicates(cls, *, last_name, first_name, phone, exclude_pk=None):
        """Contacts with the same normalised name AND the same phone number.

        One rule, one implementation — the form hint, the admin filter and the
        management command all call this.

        Phone first, because it is stored in E.164 (already normalised) and
        indexed; the handful of rows left are then compared on name in Python,
        which is cheaper than carrying a redundant normalised-name column.

        ⚠️ No phone similarity, ever. These are E.164 strings: +14085550102 and
           +14085550103 are 92% alike and belong to two unrelated people. A
           number has no notion of "close". The formatting differences that do
           matter ((408) 555-0102) were normalised away on the way in.

        The two cases it misses are exactly the two it should: same number,
        different name (a family sharing a line) and same name, different number
        (a genuine namesake).

        Requiring both halves also means the hint never reveals anything the
        person has not already typed — which is why there is no name-only
        autocomplete anywhere near this.
        """
        if not phone:
            return cls.objects.none()
        candidates = cls.objects.filter(phone=phone)
        if exclude_pk:
            candidates = candidates.exclude(pk=exclude_pk)
        target = (cls.normalise_name(last_name), cls.normalise_name(first_name))
        matching = [
            contact.pk for contact in candidates
            if (cls.normalise_name(contact.legal_last_name),
                cls.normalise_name(contact.legal_first_name)) == target
        ]
        return cls.objects.filter(pk__in=matching)

    @classmethod
    def find_same_name(cls, *, last_name, first_name, exclude_pk=None):
        """Contacts with the same name, whatever their phone number.

        A weaker signal on purpose: this one only ever produces a warning. 王强
        / 李明 / 陈伟 sharing a name is routine in the population this foundation
        serves, and a hard block firing twenty times a day trains people to tick
        the box on sight — the block stops working and costs two extra clicks.
        """
        last_name = " ".join((last_name or "").split())
        first_name = " ".join((first_name or "").split())
        if not last_name and not first_name:
            return cls.objects.none()
        found = cls.objects.filter(
            legal_last_name__iexact=last_name, legal_first_name__iexact=first_name)
        return found.exclude(pk=exclude_pk) if exclude_pk else found

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
        """Name plus something that tells two people of the same name apart.

        Two contacts both called 王强 used to stringify identically, and every
        autocomplete in the project is built out of this string: two identical
        options in a dropdown, picking the wrong one raises nothing. That is a
        silent data error — the sign-up, the assignment, the emergency contact
        all land on the wrong person.

        Duplicate names are NOT forbidden by a constraint: they are a legitimate
        fact and this domain has no reliable natural key (an email cannot be
        unique — a family shares one). Disambiguate in the display instead.

        ⚠️ Cost, stated rather than hidden: emails and phone numbers now appear
           in dropdowns and log entries. Acceptable for a small foundation, but
           it is a real disclosure, not a free win.
        """
        if self.contact_type == self.ContactType.ORGANIZATION:
            base = self.organization_name or "(unnamed organization)"
        else:
            full = f"{self.legal_first_name} {self.legal_last_name}".strip()
            base = self.preferred_name or full or "(unnamed contact)"
        # The organization branch gets the same treatment: two chapters of one
        # charity are as easy to confuse as two people.
        if self.email:
            return f"{base} ({self.email})"
        if self.phone:
            return f"{base} ({self.phone})"
        return f"{base} #{self.pk}" if self.pk else base


class RelationshipType(ImmutableCodeMixin, ConstraintErrorFieldMixin, models.Model):
    """A dictionary of relationship kinds: 'parent of', 'spouse of', 'neighbour of'.

    The vocabulary EmergencyContact.relationship_type and
    Participation.consent_relationship both draw on — "who is this person to
    them". Nothing stores person-to-person relationships as rows; reporting
    lines hang off Position.reports_to (goal.md D11).
    """

    # Code is what the code matches on, forever. Display names are editable in the
    # admin, which means filter(name_a_to_b="parent of") stops finding anything the
    # day somebody renames one — silently, with no error. See goal.md D5.
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

    # Emergency contacts draw on this vocabulary (goal.md D15). This boolean keeps
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
        # (`code` itself is lowercased by ImmutableCodeMixin.save().)
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
           exist at all — "child of" is already the name_b_to_a of "parent of",
           and two rows saying the same thing means half the emergency contacts
           get filed under one and half under the other.
        2. code is immutable once created — the shared rule, borrowed from
           ImmutableCodeMixin so Ministry / EmploymentType / Position say it
           the same way.
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

        code_error = self.code_change_error()
        if code_error:
            errors["code"] = code_error

        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.name_a_to_b


class EmergencyContact(ConstraintErrorFieldMixin, TimeStampedModel):
    """Somebody to call about `person`. Name and phone are text, deliberately.

    An emergency contact may be a neighbour or a flatmate — somebody who never
    interacts with the foundation at all. Giving them a Contact row would put
    them in the table every list, export, mailing and statistic starts from, and
    then a single Contact.objects.filter(...) that forgets to exclude them mails
    the volunteer newsletter to a few hundred third parties. That is a privacy
    incident, not a wrong number. See goal.md D15「载体的第四条判据」.

    The costs are known and accepted, not overlooked:
      · 王秀英 with three children volunteering is three rows, three copies of
        her phone number, and nothing linking them;
      · a fourth copy appears if she volunteers herself;
      · "who lists 王秀英 as their emergency contact" cannot be answered.

    ⚠️ Do NOT "tidy" name/phone into a FK to Contact. That is the migration this
       project marked as the painful direction, and it walks the ghost records
       straight back into Contact. See goal.md D15 and the deferral list.
    """

    person = models.ForeignKey(
        Contact, on_delete=models.CASCADE, related_name="emergency_contacts",
    )
    # All four are required. An emergency contact with no phone number is
    # pointless, and one with no relationship does not say who they are.
    #
    # ⚠️ `email` became required on 2026-08-05, and the reason is money rather
    #    than completeness: this address is what P6 falls back to when a minor
    #    has no consent address on file, and until now that fallback could only
    #    send SMS because this table had no email column. Email costs
    #    essentially nothing to send and SMS does not.
    #
    # ⚠️ The cost is real and is recorded in phase-c.md's known gaps: somebody
    #    being entered from a paper sign-in sheet, whose guardian left only a
    #    phone number, **cannot be recorded at all**. If that turns out to
    #    happen in the pilot, the way back is to make this blank again and have
    #    reachable_at() below simply skip the empty case — it already does.
    name = models.CharField(max_length=200)
    phone = PhoneNumberField(region="US")
    email = models.EmailField()
    relationship_type = models.ForeignKey(
        RelationshipType,
        on_delete=models.PROTECT,
        related_name="+",
        limit_choices_to={"usable_as_emergency_contact": True},
        # Spelling the direction out is not optional: leave it implicit and it
        # gets entered backwards. a = the emergency contact, b = this person.
        help_text="Read it as a sentence: “Wang Xiuying is this person's "
                  "parent.” Pick what the emergency contact is to them, "
                  "not the other way round.",
    )

    @property
    def reachable_at(self):
        """(address, channel) for this person, or None — email first, then phone.

        ⚠️ One definition, because P6 reaches an emergency contact from two
           different places (a change of time, and a signup confirmation) and a
           guardian reached by email for one and by SMS for the other is a bug
           nobody would ever file — it just looks like the messages went
           missing.

        The order matches Participation.guardian_address deliberately: whichever
        route a notice takes to a guardian, it prefers the same channel.
        """
        if self.email:
            return self.email, "email"
        if self.phone:
            return str(self.phone), "sms"
        return None

    class Meta:
        # No "one per person" rule. The table supports several by nature; the
        # foundation happens to need one today. The old self-FK design capped it
        # at one as a side effect of its shape, which was never the requirement.
        ordering = ["person", "name"]
        constraints = [
            # Stops the same emergency contact being entered twice on one person.
            # Normalisation goes in the expression, not save() — bulk_create
            # walks past save() (goal.md D9「归一化通则」).
            models.UniqueConstraint(
                "person",
                Lower(Trim("name")),
                "phone",
                name="emergencycontact_unique_per_person",
                violation_error_message="This emergency contact is already recorded for them.",
                violation_error_code="emergency_contact_duplicate",
            ),
        ]

    def save(self, *args, **kwargs):
        # Cosmetic, as everywhere else: the constraint above owns uniqueness.
        self.name = " ".join(self.name.split())
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name}（{self.relationship_type.name_a_to_b}）"
