"""The organisation chart: ministries, employment types, positions, tenures.

A Position is a box on the chart, not a person. It exists whether or not
anybody is in it — that is the whole point of the table, and the reason the
reporting lines hang off it rather than off a tenure record. See goal.md D11.

Assignment is the other half: who is in which box, and between which dates.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower
from simple_history.models import HistoricalRecords

from contact.models import Contact
from core.constraints import ConstraintErrorFieldMixin
from core.limits import LONG_TEXT
from core.models import ImmutableCodeMixin, TimeStampedModel
from core.querysets import DateRangeMixin, DateRangeQuerySet, in_effect_on


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
    description = models.TextField(blank=True, max_length=LONG_TEXT)
    # Optional, and optional is the whole design (2026-08-05). Where a ministry
    # has a page of its own, its name becomes a link on the event page; where it
    # does not, the name is plain text and nothing is drawn.
    #
    # ⚠️ The alternative considered and rejected was a placeholder link — an
    #    <a href="#"> now, a real address later. A link that does nothing does
    #    not read as "not written yet"; it reads as a broken site. C0.5 spent a
    #    whole step on exactly that failure, and two other places in this
    #    codebase already refuse to draw a link until its destination exists.
    #
    # ⚠️ URLField's validation runs in full_clean(), which the admin calls and
    #    bulk_create does not (D9's standing caveat). The scheme whitelist is
    #    what keeps `javascript:` out of an href, so a row written by a bulk
    #    path is a row nobody validated.
    website = models.URLField(
        blank=True,
        help_text="Optional. If this ministry has a page of its own, events "
                  "will link to it from the ministry's name.",
    )
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


def _has_a_holder(on=None):
    """EXISTS(somebody holds this post on `on`), correlated to the outer row.

    EXISTS rather than the pk__in subquery this started as: Postgres can stop
    at the first matching tenure instead of materialising every held position
    id, it uses the index on Assignment.position, and it has none of NOT IN's
    behaviour around nulls. Both are database-side; this one is the better plan.

    active(), not serving() — somebody on leave still holds the post, so the
    box is not open for applications.
    """
    return models.Exists(
        Assignment.objects.active(on=on).filter(position=models.OuterRef("pk"))
    )


class PositionQuerySet(models.QuerySet):
    """Staffing questions, all of them answered by the database.

    The three states below partition the table — a post is vacant, occupied or
    retired, never two of them and never none. That is the fix for a filter
    that used to define "occupied" as "not vacant" and therefore swept every
    retired post into it.
    """

    def with_headcounts(self, on=None):
        """Adds holder_count / serving_count as real SQL columns.

        An annotation, not a property, and the difference is the whole point:
        a column can be sorted on, filtered on, paginated and serialised
        straight into an API response, and it costs one query for any number
        of rows. A property can do none of that and costs a query per row.
        The front end that replaces the admin gets the count as an ordinary
        field — nothing extra to write.

        Two counts, because the project already distinguishes them:
          holder_count  — who occupies the post (the roster; on leave counts)
          serving_count — who is actually available today (the duty roster)
        """
        held = in_effect_on(on, prefix="assignments__")
        return self.annotate(
            holder_count=models.Count("assignments", filter=held, distinct=True),
            serving_count=models.Count(
                "assignments",
                filter=held & models.Q(assignments__status=Assignment.Status.ACTIVE),
                distinct=True,
            ),
        )

    def vacant(self, on=None):
        """Posts that still exist and have nobody in them on `on`.

        The first reason this table was split out of Assignment, so it ships
        with the table rather than "later": a vacancy is the absence of a row,
        and absence is not something the Assignment table can be asked about.

        is_active=True is not optional — an abolished post is not a vacancy,
        and mixing the two turns the hiring list into fiction.
        """
        return self.filter(is_active=True).filter(~_has_a_holder(on))

    def occupied(self, on=None):
        """Posts that still exist and have somebody in them on `on`.

        Deliberately NOT "everything that is not vacant". Defined that way, a
        retired post — which is not a vacancy either — silently reports itself
        as staffed, and the org chart claims people who left years ago.
        """
        return self.filter(is_active=True).filter(_has_a_holder(on))

    def retired(self):
        """Posts that have been abolished. The third state, and it has to be
        visible: folded into either of the others it becomes a lie about the
        organisation's shape."""
        return self.filter(is_active=False)


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
        """Is there a box for this person on the chart at all, and whose kind.

        Two values, not three. "Employee" and "Volunteer" used to live here and
        carried two independent facts at once — whether somebody has a standing
        post, and whether the post is paid. The foundation's own sentence is
        what broke it: "they are all volunteers, but they work like employees —
        fixed meeting times, a fixed post each; I want them filed as employees
        but written as unpaid." That is one axis saying two things. See D32.

        ⚠️ Never add a third value for a pay arrangement. That is what
           `compensation` below is for, and folding it back in here is the exact
           mistake this split was made to undo.
        """

        STAFF = "staff", "Staff"
        BOARD = "board", "Board member"

    class Compensation(models.TextChoices):
        """Whether the post is paid. Unpaid staff are still staff.

        A TextChoices and not a dictionary table, which is the opposite call
        from EmploymentType next door — D5's own rule is that code branching on
        the values makes it an enum. This one is branched on by the printable
        volunteer-hours figure (D38 section 7) and by the FLSA prompt (D38
        section 8).

        ⚠️ `stipend` groups with `paid`, and that is a policy choice this
           project made on the foundation's behalf, not a legal fact: 29 CFR
           553.106's "a nominal fee does not defeat volunteer status" covers
           **public agencies only**. See D32 section 2 — the note there is what stops
           the next person from reading this line as settled law.
        """

        PAID = "paid", "Paid"
        UNPAID = "unpaid", "Unpaid"
        STIPEND = "stipend", "Stipend"

    code = models.SlugField(
        max_length=50,
        help_text="Stable identifier used by code. Lowercase, cannot be changed later.",
    )
    name = models.CharField(max_length=100, help_text='e.g. "Program Director".')
    # ⚠️ default=STAFF, where it used to be VOLUNTEER — the old default is not
    #    one of the values any more. A new box is a staff post until somebody
    #    says otherwise; board seats are the rare, deliberate ones.
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.STAFF)
    # ⚠️ default=UNPAID, and the direction is the safe one: a new vacancy that
    #    is wrongly unpaid gets one question asked when somebody hires for it,
    #    where the other way round invents a batch of salaried posts on a
    #    report. Note this is the *opposite* of the same round's `served_as`,
    #    which is forbidden a default — and the two are not inconsistent:
    #    compensation is a property of the box (every post has some pay status),
    #    served_as is a statement somebody makes (nobody may have made it).
    compensation = models.CharField(
        max_length=20,
        choices=Compensation.choices,
        default=Compensation.UNPAID,
        help_text="Whether this post is paid. Unpaid staff are still staff.",
    )
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
    description = models.TextField(blank=True, max_length=LONG_TEXT)

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
           On its own it is the is_active-beside-the-dates disease: two
           independent dimensions collapsed into one, so status=active on a
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

    ⚠️ There is no clean() any more, and its absence is the decision. It held
       one rule — "employment_type only means anything on kind=employee" — and
       both halves of that sentence are gone: `kind` no longer has an
       `employee` value (D32), and how much of a week somebody gives is now
       `fte`'s answer, not a full-time/part-time label's (D37 section 1). What
       is left for employment_type is what it literally says, the shape of the
       engagement, and that is as true of an unpaid post as a paid one — a
       volunteer coordinator can perfectly well be "part-time".

       Deleted rather than rewritten against `compensation`, and not left as a
       clean() that only calls super(): an empty override reads to the next
       person as something that lost its body by accident.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Serving"
        ON_LEAVE = "on_leave", "On leave"
        SUSPENDED = "suspended", "Suspended"
        # ⚠️ Never add "ended". Ending is what end_date says; a second place to
        #    say it means two answers to the same question, one of them stale.

    contact = models.ForeignKey(
        Contact,
        # PROTECT. Assignment carries simple-history and is the only support for
        # R8 ("which employees of the running ministry took part") — deleting a
        # person must not silently take their whole service history with it.
        # Same reason as MinistryRole.contact: a record that has to leave a
        # trace cannot hang off a CASCADE. Retire the person with is_active
        # instead; that is what it is for (goal.md D18, no soft deletes).
        on_delete=models.PROTECT,
        related_name="assignments",
    )
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

    def __str__(self):
        return f"{self.contact} — {self.position}"


class MinistryRole(ConstraintErrorFieldMixin, DateRangeMixin, TimeStampedModel):
    """Who has what authority over which ministry.

    Django's permissions are app_label.codename and are global: granting
    events.add_event grants it for *every* ministry, while P2 and P4 ask for
    "the food pantry's admin may publish for the food pantry, and see the food
    pantry's signups". There is no object-level scope to hang that on, so the
    scope becomes a table. See goal.md D20.

    ⚠️ Not Position(is_leader=True) in disguise. Holding a post in the
       organisation and being able to operate the system are different
       questions — accounts/models.py has said so since D12. Conflated, granting
       authority would mean inventing a post, revoking it would mean editing the
       org chart, and somebody with authority but no post (an auditor, a
       stand-in) would have nowhere to live.

    ⚠️ Nor a Django Group. That is what the global tier is for, and P5 — "who
       may appoint a ministry's admin" — is genuinely global, so it is a Group.
       The test: does the sentence contain "of some ministry"? Then it belongs
       here.
    """

    class Role(models.TextChoices):
        ADMIN = "admin", "Ministry admin"
        # Reserved. Nothing branches on it in this phase and permissions.py
        # answers for ADMIN only — the requirement asked for one tier. Adding a
        # second means editing this enum *and* the judgements, together.
        COORDINATOR = "coordinator", "Coordinator"

    contact = models.ForeignKey(
        Contact,
        # PROTECT: a grant is something to be answerable for, so deleting a
        # person must not quietly erase the record that they had it.
        on_delete=models.PROTECT,
        related_name="ministry_roles",
    )
    ministry = models.ForeignKey(
        Ministry,
        # PROTECT, matching contact above. This was CASCADE, on the reasoning
        # that authority over a ministry that no longer exists is meaningless —
        # but that sentence reads just as well about the person, and the person
        # side is PROTECT because grants have to leave a trace. Two foreign keys
        # on one table justified in contradictory ways means one of them was
        # after the fact. Ministry also has is_active, so it is retired rather
        # than deleted, and this table carries simple-history.
        on_delete=models.PROTECT,
        related_name="roles",
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.ADMIN)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        # SET_NULL, never CASCADE: deleting the account of whoever granted this
        # would otherwise revoke a batch of people's authority in one go. NULL
        # reads as "the granting account is gone", which beats the row vanishing.
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="+",
    )

    history = HistoricalRecords()

    # Grants start and end, exactly like tenures — so they reuse .active() and
    # there is no second definition of "in effect" anywhere.
    objects = models.Manager.from_queryset(DateRangeQuerySet)()

    class Meta:
        ordering = ["ministry__name", "contact"]
        constraints = [
            # nulls_distinct=False for A7's reason: start_date is nullable and
            # is routinely left empty, and Postgres treats NULL != NULL, so
            # without it the constraint waves through any number of duplicates.
            models.UniqueConstraint(
                fields=["contact", "ministry", "role", "start_date"],
                name="ministryrole_unique_grant",
                nulls_distinct=False,
                violation_error_message="They already have that role in this ministry "
                                        "from that date.",
                violation_error_code="ministryrole_duplicate_grant",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(end_date__isnull=True)
                    | models.Q(start_date__isnull=True)
                    | models.Q(end_date__gte=models.F("start_date"))
                ),
                name="ministryrole_end_date_not_before_start_date",
                violation_error_message="The end date cannot be before the start date.",
                violation_error_code="ministryrole_end_before_start",
            ),
        ]
        # Every permission check in the system runs this lookup ("which
        # ministries does this person administer"), which makes it the most
        # frequently executed query we have — at least once per protected
        # request.
        indexes = [models.Index(fields=["contact", "end_date"])]

    def __str__(self):
        return f"{self.contact} — {self.get_role_display()}（{self.ministry.name}）"
