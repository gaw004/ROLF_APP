"""The organisation chart's skeleton: ministries, employment types, positions.

A Position is a box on the chart, not a person. It exists whether or not
anybody is in it — that is the whole point of the table, and the reason the
reporting lines hang off it rather than off a tenure record. See goal.md D11.
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower
from simple_history.models import HistoricalRecords

from core.constraints import ConstraintErrorFieldMixin
from core.models import ImmutableCodeMixin, TimeStampedModel


class Ministry(ImmutableCodeMixin, ConstraintErrorFieldMixin, TimeStampedModel):
    """A programme or an administrative function: food pantry, ESL, finance.

    Administrative functions are rows in this table too — there is no separate
    Department model. One table of internal units, or every query has to
    remember to look in both.

    ⚠️ Never a Contact with contact_type=organization. Contact holds people and
       *external* organizations; a ministry is an internal unit of the
       foundation. Mixing the two pollutes the contact list and rubs out the
       inside/outside line that most of the privacy rules stand on.

    No history: confirmed to change about never, and simple_history costs a
    shadow table plus a row per save.
    """

    code = models.SlugField(
        max_length=50,
        help_text="Stable identifier used by code. Lowercase, cannot be changed later.",
    )
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    founded_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "ministries"
        constraints = [
            # Lower("code"), not unique=True on the field: save() lowercases,
            # bulk_create does not call save(), and the foundation's existing
            # data arrives through bulk_create. goal.md D9「归一化通则」.
            models.UniqueConstraint(
                Lower("code"),
                name="ministry_code_ci_unique",
                violation_error_message="A ministry with this code already exists.",
                violation_error_code="ministry_code_taken",
            ),
        ]

    def clean(self):
        super().clean()
        error = self.code_change_error()
        if error:
            raise ValidationError({"code": error})

    def __str__(self):
        return self.name


class EmploymentType(ImmutableCodeMixin, ConstraintErrorFieldMixin, models.Model):
    """Full-time / part-time / contract / intern — whatever the foundation uses.

    A dictionary table rather than TextChoices precisely because the real values
    are still unconfirmed: no code anywhere branches on them, which is D5's test
    for "this belongs in a table, not in an enum". Adding one later is a row in
    the admin, not a migration.
    """

    code = models.SlugField(
        max_length=50,
        help_text="Stable identifier used by code. Lowercase, cannot be changed later.",
    )
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                Lower("code"),
                name="employmenttype_code_ci_unique",
                violation_error_message="An employment type with this code already exists.",
                violation_error_code="employmenttype_code_taken",
            ),
        ]

    def clean(self):
        super().clean()
        error = self.code_change_error()
        if error:
            raise ValidationError({"code": error})

    def __str__(self):
        return self.name


class Position(ImmutableCodeMixin, ConstraintErrorFieldMixin, TimeStampedModel):
    """One box on the org chart. Vacant is a perfectly normal state for it.

    kind / ministry / is_leader / reports_to live here rather than on Assignment
    because a vacancy has to be able to say what it is: a vacant box that cannot
    tell you whether it is a paid post, which ministry it belongs to, or who
    would report to whoever fills it is no use to the person trying to fill it.
    And replacing a post-holder then touches one Assignment row instead of every
    subordinate's. See goal.md D11「第二次修订」.

    A Position is a *kind* of box, not a seat. Three food-pantry volunteers are
    one Position and three Assignments — which is why this table stays at a few
    dozen rows instead of growing one row per person. Hence no "only one active
    assignment per position" constraint: it would block co-holders and handover
    overlaps, both of which are legitimate.

    ⚠️ Never walk the reporting chain by hand — call
       org.services.build_org_tree(). A loop spanning two rows is not something
       a CHECK constraint can catch, so the protection against one and the N+1
       avoidance both live in that single function, and core/tests.py has a
       grep guard watching for a second copy.
    """

    class Kind(models.TextChoices):
        EMPLOYEE = "employee", "Employee"
        VOLUNTEER = "volunteer", "Volunteer"
        BOARD = "board", "Board member"

    code = models.SlugField(
        max_length=50,
        help_text="Stable identifier used by code. Lowercase, cannot be changed later.",
    )
    name = models.CharField(max_length=100, help_text='e.g. "Program Director".')
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.VOLUNTEER)
    ministry = models.ForeignKey(
        Ministry,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="positions",
        help_text="Leave empty for foundation-wide posts such as Executive Director.",
    )
    reports_to = models.ForeignKey(
        "self",
        # PROTECT, and neither of the alternatives. CASCADE is a disaster:
        # deleting one box takes the whole subtree of subordinates with it.
        # SET_NULL is worse than it looks — it silently promotes a whole subtree
        # to the top of the chart, and afterwards nothing shows that anything
        # happened. PROTECT is the only choice that makes somebody notice, by
        # refusing until the subordinates have been re-hung by hand.
        # (The line hung off Assignment in the old design and used SET_NULL,
        # because tenure rows do get deleted. Boxes are retired with is_active.)
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="direct_reports",
    )
    is_leader = models.BooleanField(
        default=False,
        help_text="Read by code when grouping the chart. The title is for humans.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether the post still exists. Nothing to do with anybody holding it.",
    )
    description = models.TextField(blank=True)

    # Org chart changes are exactly the thing somebody asks about a year later.
    history = HistoricalRecords()

    class Meta:
        ordering = ["ministry__name", "-is_leader", "name"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(reports_to=models.F("id")),
                name="position_reports_to_is_not_self",
                violation_error_message="A position cannot report to itself.",
                violation_error_code="position_reports_to_self",
            ),
            models.UniqueConstraint(
                Lower("code"),
                name="position_code_ci_unique",
                violation_error_message="A position with this code already exists.",
                violation_error_code="position_code_taken",
            ),
        ]
        # No unique constraint on name: two ministries may each have a
        # "Coordinator", and that is not a mistake. Same line taken with
        # duplicate people — names are disambiguated on display, not forbidden.
        indexes = [models.Index(fields=["ministry", "kind", "is_active"])]

    def save(self, *args, **kwargs):
        self.name = " ".join(self.name.split())
        super().save(*args, **kwargs)

    def clean(self):
        """Two rules, reported together.

        The loop check is a hint layer and is not dressed up as more than one:
        bulk_create walks straight past clean(), which is why build_org_tree()
        carries protection of its own. See goal.md D14 and「汇报线的环」.
        """
        super().clean()
        # Imported here rather than at module scope: services imports this
        # module, so a top-level import would be a circular one.
        from .services import creates_a_reporting_cycle

        errors = {}
        code_error = self.code_change_error()
        if code_error:
            errors["code"] = code_error
        if creates_a_reporting_cycle(self):
            errors["reports_to"] = (
                "This would put a loop in the reporting lines "
                "(the chain leads back to this position)."
            )
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        # Carries the ministry so that the manager dropdown can tell two
        # ministries' coordinators apart — the same reason Contact.__str__
        # carries an email address.
        return f"{self.name}（{self.ministry.name}）" if self.ministry_id else self.name
