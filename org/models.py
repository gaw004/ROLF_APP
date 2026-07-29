"""The organisation chart: ministries, employment types, positions, tenures.

A Position is a box on the chart, not a person. It exists whether or not
anybody is in it — that is the whole point of the table, and the reason the
reporting lines hang off it rather than off a tenure record. See goal.md D11.

Assignment is the other half: who is in which box, and between which dates.
"""

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower
from simple_history.models import HistoricalRecords

from contact.models import Contact
from core.constraints import ConstraintErrorFieldMixin
from core.models import ImmutableCodeMixin, TimeStampedModel
from core.querysets import DateRangeMixin, DateRangeQuerySet
from core.timeutils import local_today


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


class PositionQuerySet(models.QuerySet):
    def vacant(self, on=None):
        """Posts that still exist but have nobody serving in them on `on`.

        This query is the first reason the table was split out at all, so it
        ships with it rather than "later".

        Three parts, none of them optional:

        1. is_active=True — a post that was abolished is not a vacancy, and
           mixing the two turns the hiring list into fiction.
        2. `on` is a parameter, like .active() — D16's second layer, and it
           throws in "which posts were empty last June" for free.
        3. active(), not serving(): somebody on leave still holds the post, so
           the box is not open for applications.
        """
        on = on or local_today()
        return self.filter(is_active=True).exclude(
            pk__in=Assignment.objects.active(on=on).values("position_id")
        )


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

    objects = models.Manager.from_queryset(PositionQuerySet)()

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


class AssignmentQuerySet(DateRangeQuerySet):
    def serving(self, on=None):
        """In their term AND able to serve today. The duty roster.

        ⚠️ status may only ever be ANDed with the dates, never used on its own.
           On its own it is exactly the disease Relationship.is_active had:
           two independent dimensions collapsed into one, so status=active on a
           tenure that ended in 2020 reads as "currently serving".

        The roster of who belongs to a team is active(), not this — somebody on
        leave is still a member of the team.
        """
        return self.active(on).filter(status=Assignment.Status.ACTIVE)


class Assignment(ConstraintErrorFieldMixin, DateRangeMixin, TimeStampedModel):
    """One person's tenure in one position, between two dates.

    Six fields. No kind / title / ministry / is_leader / reports_to — all of
    those describe the box, so they live on Position, and replacing whoever is
    in the box therefore touches this row alone.

    status and the term are two dimensions at right angles, not is_active in a
    hat:

    - there is no is_active — it would let is_active=True sit next to
      end_date=2020, and something would believe it;
    - status only ever describes where somebody stands *inside* the term.
      Ending is said by end_date and by nothing else;
    - leave is never recorded by cutting end_date short. That would falsify the
      agreed dates and miscount the length of service, leaving the real dates
      only in the history table. Avoiding that is the entire reason this field
      exists.

    No "status must agree with the dates" constraint: it would have to name
    today inside a CheckConstraint, which is not an immutable expression and
    Postgres refuses it. It is also unnecessary — a stale status=on_leave on an
    expired term is inert, because serving() ANDs the dates first and the
    person is already out.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Serving"
        ON_LEAVE = "on_leave", "On leave"
        SUSPENDED = "suspended", "Suspended"
        # ⚠️ Never add "ended". Ending is what end_date says; a second place to
        #    say it means two answers to the same question, one of them stale.

    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name="assignments")
    position = models.ForeignKey(
        Position,
        # PROTECT. With CASCADE, deleting one box would take the service
        # history of everybody who ever held it with it.
        on_delete=models.PROTECT,
        related_name="assignments",
    )
    employment_type = models.ForeignKey(
        EmploymentType, on_delete=models.PROTECT, null=True, blank=True, related_name="+",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE,
        help_text="Where they stand within the tenure. Ending it is the end date's job.",
    )
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)

    history = HistoricalRecords()

    objects = models.Manager.from_queryset(AssignmentQuerySet)()

    class Meta:
        ordering = ["-start_date", "contact"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(end_date__isnull=True)
                    | models.Q(start_date__isnull=True)
                    | models.Q(end_date__gte=models.F("start_date"))
                ),
                name="assignment_end_date_not_before_start_date",
                violation_error_message="The end date cannot be before the start date.",
                violation_error_code="assignment_end_before_start",
            ),
            # Short, and that is the news. The old version was
            # (contact, ministry, kind, title, start_date), with a paragraph
            # arguing why title had to be in it — splitting Position out made
            # that whole argument moot, because two jobs are two positions.
            # Worth remembering: a constraint that keeps growing columns is
            # usually a model that has not been split yet.
            #
            # nulls_distinct=False is not optional. start_date is nullable and
            # often left empty, and Postgres treats NULL != NULL, so without it
            # the constraint would wave through any number of duplicates with
            # no start date. A7's lesson.
            models.UniqueConstraint(
                fields=["contact", "position", "start_date"],
                name="assignment_unique_tenure",
                nulls_distinct=False,
                violation_error_message="This person already has a tenure in this position "
                                        "starting on that date.",
                violation_error_code="assignment_duplicate_tenure",
            ),
        ]
        # position + status + end_date covers serving() in one index; active()
        # uses the leftmost column and is happy with the same one.
        indexes = [models.Index(fields=["position", "status", "end_date"])]

    def clean(self):
        """The one rule that spans two tables, and therefore cannot be a constraint.

        employment_type only means anything for a paid post, but kind lives on
        Position and employment_type lives here — a CheckConstraint cannot see
        across the join. So it is a hint, and D14 says to record it as a hint
        rather than dress it up: bulk_create walks past this.
        """
        super().clean()
        if (
            self.employment_type_id
            and self.position_id
            and self.position.kind != Position.Kind.EMPLOYEE
        ):
            raise ValidationError({
                "employment_type": (
                    "Employment type only applies to employee positions "
                    f"(this one is {self.position.get_kind_display()})."
                )
            })

    def __str__(self):
        return f"{self.contact} — {self.position}"
