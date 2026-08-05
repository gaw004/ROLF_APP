"""Events, the roles they open, and who turned up to them.

The shape to hold on to is EventRole. An event opens a set of jobs — five for
lifting, two on the welcome desk, one interpreting — and those jobs exist
whether or not anybody signs up for them. Counting DISTINCT roles on the signup
table instead would report "this event has 3 roles" for an event that opened 5,
without raising anything, and "which job is still short" is precisely the number
P2 asks for. See goal.md D19.

EventRole is to Participation what Position is to Assignment: a box, and the
people in it. The analogy is exact, and this is the second time in this project
that one had to be split out of the other.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Count, F, Q
from django.db.models.functions import Lower
from phonenumber_field.modelfields import PhoneNumberField
from simple_history.models import HistoricalRecords

from contact.models import Contact, RelationshipType
from core.constraints import ConstraintErrorFieldMixin
from core.models import ImmutableCodeMixin, TimeStampedModel
from core.timeutils import local_now
from org.models import Ministry


class EventType(ImmutableCodeMixin, ConstraintErrorFieldMixin, models.Model):
    """Food distribution, tax clinic, ESL class — a dictionary table.

    Same shape as Ministry and EmploymentType, and for D5's reason: no code
    branches on the values, so they belong in a table where the admin can add a
    row, not in an enum where adding one is a migration.
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
            # Lower("code"), never unique=True on the field: bulk_create is a
            # normal write path here and never calls save(), so lowercasing in
            # save() guarantees nothing about what is in the table. goal.md D9.
            models.UniqueConstraint(
                Lower("code"),
                name="eventtype_code_ci_unique",
                violation_error_message="An event type with this code already exists.",
                violation_error_code="eventtype_code_taken",
            ),
        ]

    def clean(self):
        super().clean()
        error = self.code_change_error()
        if error:
            raise ValidationError({"code": error})

    def __str__(self):
        return self.name


class ParticipationRole(ImmutableCodeMixin, ConstraintErrorFieldMixin, models.Model):
    """A job done *inside* one event: welcome desk, lifting, interpreting.

    ⚠️ Not a Position. A Position is a standing box on the org chart
       ("Programme Coordinator") held for months by an Assignment; this is a job
       that exists for one afternoon. D10's test: still true with somebody else
       doing it and no event running → Position. Only meaningful within this one
       event → here. Written down because otherwise somebody files event jobs as
       posts and the org chart fills up with "lifting".

    One row must always exist with code="general": Participation.event_role is
    not nullable, so "no particular job" needs somewhere to land.
    """

    GENERAL_CODE = "general"

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
                name="participationrole_code_ci_unique",
                violation_error_message="A participation role with this code already exists.",
                violation_error_code="participationrole_code_taken",
            ),
        ]

    @classmethod
    def seed_general(cls):
        """The catch-all role, created if it is not there yet."""
        role, _ = cls.objects.get_or_create(
            code=cls.GENERAL_CODE, defaults={"name": "General volunteer"},
        )
        return role

    def clean(self):
        super().clean()
        error = self.code_change_error()
        if error:
            raise ValidationError({"code": error})

    def __str__(self):
        return self.name


class EventQuerySet(models.QuerySet):
    """Two predicates, because status is answering two different questions.

    Event.status carries the lifecycle (draft → open → confirmed → completed /
    cancelled) *and* the visibility of the event to volunteers, and those are
    not the same question. Writing visibility as status == OPEN means that the
    moment an event is marked confirmed ("full, no more signups") everybody who
    already signed up loses access to its page — and P6's entire scenario ends
    with a notification saying "click here to cancel", a link that would then
    404, on exactly the events that filled up.

    The fix is not another field. An is_published boolean would be a second
    dimension able to contradict status, which is the bill this project has
    already paid three times.
    """

    def visible_to_volunteers(self):
        """Everything a volunteer may open: published, including full and over."""
        return self.filter(status__in=Event.VISIBLE_TO_VOLUNTEERS)

    def open_for_signup(self):
        """Everything a volunteer may still sign up for."""
        return self.filter(status__in=Event.OPEN_FOR_SIGNUP)

    def upcoming(self, now=None):
        """Not started yet.

        Here rather than in the view: a view holding date arithmetic is one that
        gets rewritten along with the templates, and there is a grep guard
        saying so.
        """
        return self.filter(start_time__gte=now or local_now())

    def past(self, now=None):
        """Already over — paired with upcoming(), and deliberately not its negation.

        ⚠️ Read off end_time, not start_time. An event that began this morning
           and runs until tonight is neither upcoming nor past, and calling it
           past would file a running event under history while people are still
           checking in. The two methods leave that gap on purpose; "not
           upcoming" would have silently closed it.
        """
        return self.filter(end_time__lt=now or local_now())

    def in_period(self, start, end):
        """R1: the events that ran in a window, half-open [start, end).

        The boundaries arrive already resolved, because where "this month"
        starts depends on the foundation's timezone and that answer belongs to
        core.timeutils (D16), not to whoever is drawing the report.
        """
        return self.filter(start_time__gte=start, start_time__lt=end)


class Event(ConstraintErrorFieldMixin, TimeStampedModel):
    """One occasion: a food distribution on Saturday morning.

    Several shifts are several Events, not one Event plus a shift table: the
    time difference is then carried by each event's own start/end, the work
    difference by EventRole, the hours difference by each Participation. Three
    dimensions, no third table. See phase-b.md「一人一活动多角色」.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"                      # only this ministry sees it
        OPEN = "open", "Open for signup"              # published, taking signups
        CONFIRMED = "confirmed", "Confirmed"          # full, no more signups
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    # ⚠️ Both sets are listed in full, not spelled as exclude(DRAFT), even
    #    though the two are equivalent today. B5 already paid for defining one
    #    state as the complement of another — Position's third state got swept
    #    into the wrong bucket by exactly that. The test is to list the states
    #    and count them: five here, so a complement is wrong, and the day
    #    somebody adds `postponed` a complement would quietly publish it.
    VISIBLE_TO_VOLUNTEERS = frozenset({
        Status.OPEN, Status.CONFIRMED, Status.COMPLETED, Status.CANCELLED,
    })
    # Cancelled events stay in the visible set on purpose: the people who signed
    # up are exactly the ones who need to see that it is off.
    OPEN_FOR_SIGNUP = frozenset({Status.OPEN})

    name = models.CharField(max_length=200)
    event_type = models.ForeignKey(EventType, on_delete=models.PROTECT, related_name="events")
    # Not nullable. R2, R8 and P2 all turn on this column, and an event with no
    # ministry is one nobody owns and nobody has the right to manage.
    ministry = models.ForeignKey(Ministry, on_delete=models.PROTECT, related_name="events")
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    location = models.CharField(max_length=200, blank=True)
    owner = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="events_owned")
    # Whether this event holds minors to the consent rule. Per event, and not a
    # setting, because it genuinely differs: a Saturday food sort with parents
    # in the room is not a weekend away, and one blanket answer would either
    # burden the first or under-protect the second.
    #
    # ⚠️ Default True. A new event is protected until somebody deliberately says
    #    otherwise — the safe direction, because the failure mode of the other
    #    default is a minor signed up with nobody informed, and nothing about
    #    that is visible until the day.
    requires_guardian_consent = models.BooleanField(
        default=True,
        verbose_name="Minors need a guardian's consent",
        help_text="Untick only when under-18s may sign up on their own, like an "
                  "adult. Ticked, they need consent on file and somebody to call.",
    )

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    description = models.TextField(blank=True)

    # Published to the outside world: a change of time or place has to be
    # answerable for afterwards.
    history = HistoricalRecords()

    objects = models.Manager.from_queryset(EventQuerySet)()

    class Meta:
        ordering = ["-start_time"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_time__gte=models.F("start_time")),
                name="event_end_time_not_before_start_time",
                violation_error_message="The end time cannot be before the start time.",
                violation_error_code="event_end_before_start",
            ),
        ]
        indexes = [
            models.Index(fields=["start_time"]),                 # R1
            models.Index(fields=["ministry", "start_time"]),     # R2
            # P3's volunteer list page — the most-hit query in the system.
            models.Index(fields=["status", "start_time"]),
        ]

    @property
    def duration(self):
        """R3. Derived, never stored: two columns already say it."""
        return self.end_time - self.start_time

    def __str__(self):
        # Date and ministry, so two "Food distribution" rows are told apart in
        # a dropdown. Same reason Position.__str__ carries its ministry.
        return f"{self.name}（{self.ministry.name} · {self.start_time:%Y-%m-%d}）"


class EventRoleQuerySet(models.QuerySet):
    def with_signup_counts(self):
        """Adds registered_count / attended_count as real SQL columns.

        An annotation, not a property, for the reason written out on
        PositionQuerySet.with_headcounts(): a column can be sorted, filtered,
        paginated and serialised, and it costs one query for any number of rows.
        A property does none of that and costs a query each.
        """
        return self.annotate(
            registered_count=Count(
                "participations",
                filter=~Q(participations__status=Participation.Status.CANCELLED),
                distinct=True,
            ),
            attended_count=Count(
                "participations",
                filter=Q(participations__status=Participation.Status.ATTENDED),
                distinct=True,
            ),
        ).annotate(
            # "Is this role short?" as a column, so the signups page can flag it
            # without restating the rule.
            #
            # ⚠️ The rule has a trap in it — needed_count NULL means "no limit",
            #    so such a role is never short rather than short by infinity —
            #    and a template writing `registered_count < needed_count` would
            #    be a second copy of it. Copies of a rule with a trap in it do
            #    not stay in step; understaffed() below filters on this same
            #    annotation for exactly that reason.
            is_short=Q(needed_count__isnull=False) & Q(registered_count__lt=F("needed_count")),
        )

    def understaffed(self):
        """Roles with fewer signups than they asked for.

        ⚠️ needed_count NULL means "no limit", so such a role is never short —
           not "short by infinity".
        ⚠️ Roles nobody signed up for have to appear here. That is the whole
           reason this table exists: they have no row in Participation to be
           found through. See goal.md D19.

        Filters on the `is_short` annotation rather than spelling the condition
        out again — one definition, so this and the badge on the signups page
        can never come to different conclusions about the same role.
        """
        return self.with_signup_counts().filter(is_short=True)


class EventRole(ConstraintErrorFieldMixin, TimeStampedModel):
    """This event opened this job, and wants this many people for it.

    It exists with nobody signed up, and that is the point: an event that
    opened five roles and filled three has five roles, and the two empty ones
    are what P2 wants to see. Merged into Participation there would be no row
    to represent them — the disease D11 convicted once already on Position.
    """

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="roles")
    role = models.ForeignKey(ParticipationRole, on_delete=models.PROTECT, related_name="+")
    needed_count = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Leave empty for no limit. Advisory only — signups are never blocked.",
    )
    notes = models.TextField(blank=True)

    # needed_count is a promise published to volunteers ("we need 5 for
    # lifting"), so changing it has to be traceable — same reason as Event.
    history = HistoricalRecords()

    objects = models.Manager.from_queryset(EventRoleQuerySet)()

    class Meta:
        ordering = ["event", "role__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["event", "role"],
                name="eventrole_unique_per_event",
                violation_error_message="This event already has that role open.",
                violation_error_code="eventrole_duplicate",
            ),
            models.CheckConstraint(
                condition=models.Q(needed_count__isnull=True) | models.Q(needed_count__gt=0),
                name="eventrole_needed_count_is_positive",
                violation_error_message="Leave the number empty for no limit; "
                                        "otherwise it has to be at least 1.",
                violation_error_code="eventrole_needed_count_not_positive",
            ),
        ]

    def __str__(self):
        return f"{self.role.name} @ {self.event.name}"


class ParticipationQuerySet(models.QuerySet):
    def notifiable(self):
        """Everyone a change to this event still concerns.

        Cancelled signups are out — that person has already said they are not
        coming, and mailing them about a new time is noise.
        """
        return self.exclude(status=Participation.Status.CANCELLED)


class Participation(ConstraintErrorFieldMixin, TimeStampedModel):
    """One person, one role, one event — and what came of it.

    Signup, attendance and hours are three facts about the same occasion, so
    they are one row.

    There is no `event` column and no `role` column: both live inside
    event_role. Keeping a separate `event` would allow participation.event and
    participation.event_role.event to name two different events, and that is a
    cross-table condition no CheckConstraint can see — the same corner
    Assignment.employment_type is stuck in. D11's line: not two places that may
    record it, one place that does. Queries go through event_role__event.
    """

    class Status(models.TextChoices):
        REGISTERED = "registered", "Registered"
        ATTENDED = "attended", "Attended"
        ABSENT = "absent", "No-show"
        CANCELLED = "cancelled", "Cancelled"

    class ConsentMethod(models.TextChoices):
        # ⚠️ C2.5 改的是**标签**（右边那半，显示给人看的）。左边的 value 一个字
        #    没动，也不许动 —— 它们已经写在库里了，改 value 是一次数据迁移，
        #    而漏掉迁移的表现是旧行的同意方式变成一个不在选项里的值。
        VERBAL = "verbal", "In person"
        PAPER = "paper", "On paper"
        ONLINE = "online", "Online"

    event_role = models.ForeignKey(
        EventRole, on_delete=models.CASCADE, related_name="participations",
    )
    contact = models.ForeignKey(
        Contact,
        # PROTECT: CASCADE would let deleting one person wipe their whole hours
        # history, which is what R6 and R7 are computed from.
        on_delete=models.PROTECT,
        related_name="participations",
    )
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.REGISTERED,
    )

    registered_at = models.DateTimeField(null=True, blank=True)
    checked_in_at = models.DateTimeField(null=True, blank=True)   # P4: did they turn up
    checked_out_at = models.DateTimeField(null=True, blank=True)
    # Decimal, never Float: hours may end up attached to recognition, and floats
    # drift when summed. null=True because signed-up-but-not-yet-happened is not
    # the same fact as turned-up-and-did-zero.
    hours = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    # --- P3: a guardian's consent, for this one event ---------------------
    # Deliberately not a Guardianship table. The requirement is "did a parent
    # agree to *this* event", which is an event record; "who is this child's
    # legal guardian" is a standing relationship. Different shapes — build the
    # one the requirement asks for.
    consent_given_by = models.CharField(max_length=200, blank=True)
    consent_relationship = models.ForeignKey(
        RelationshipType, on_delete=models.PROTECT, null=True, blank=True, related_name="+",
        limit_choices_to={"usable_as_emergency_contact": True},
    )
    consent_at = models.DateTimeField(null=True, blank=True)
    consent_method = models.CharField(max_length=20, choices=ConsentMethod.choices, blank=True)
    # ⚠️ These two are a hard prerequisite for P6, not extra detail.
    #    consent_given_by is a *name*: no delivery address can be resolved from
    #    it, so without these the "notify the guardian" rule resolves nothing
    #    and the people who most need telling all land in unreachable. D22 ①.
    consent_email = models.EmailField(blank=True)
    consent_phone = PhoneNumberField(blank=True, region="US")

    # This table holds the only authoritative value in the system a human may
    # overwrite by hand (hours, for paper sign-in sheets). Whoever turned 3
    # hours into 8 has to be answerable for it afterwards.
    history = HistoricalRecords()

    objects = models.Manager.from_queryset(ParticipationQuerySet)()

    class Meta:
        ordering = ["-registered_at", "contact"]
        constraints = [
            # Two non-nullable columns, so nulls_distinct is not needed here —
            # splitting the table shortened the constraint. Second time in this
            # project; the first was Position.
            models.UniqueConstraint(
                fields=["event_role", "contact"],
                name="participation_unique_per_event_role",
                violation_error_message="They are already signed up for this role.",
                violation_error_code="participation_duplicate",
            ),
            models.CheckConstraint(
                condition=models.Q(hours__isnull=True) | models.Q(hours__gte=0),
                name="participation_hours_not_negative",
                violation_error_message="Hours cannot be negative.",
                violation_error_code="participation_hours_negative",
            ),
            # Without this, "no-show, 5 hours" is storable — the same disease
            # as is_active=True sitting next to end_date=2020.
            models.CheckConstraint(
                condition=(
                    models.Q(status="attended")
                    | models.Q(hours__isnull=True)
                    | models.Q(hours=0)
                ),
                name="participation_hours_only_when_attended",
                violation_error_message="Only somebody recorded as having attended "
                                        "can have hours.",
                violation_error_code="participation_hours_without_attendance",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(checked_out_at__isnull=True)
                    | models.Q(checked_in_at__isnull=True)
                    | models.Q(checked_out_at__gte=models.F("checked_in_at"))
                ),
                name="participation_checkout_after_checkin",
                violation_error_message="Check-out cannot be before check-in.",
                violation_error_code="participation_checkout_before_checkin",
            ),
            # "Did they turn up" may only ever have one answer.
            models.CheckConstraint(
                condition=models.Q(checked_in_at__isnull=True) | ~models.Q(status="absent"),
                name="participation_checked_in_is_not_absent",
                violation_error_message="Somebody who checked in cannot be marked absent.",
                violation_error_code="participation_absent_after_checkin",
            ),
        ]

    @property
    def event(self):
        """Read-only convenience. The column deliberately does not exist."""
        return self.event_role.event

    @property
    def guardian_address(self):
        """(address, channel) for the guardian, or None — D22's rule 2.

        Email first: the default backend is email, and it costs essentially
        nothing where a text message does not.

        ⚠️ 2026-08-05 更正：这段原来写的是「兜底路径（EmergencyContact）只能走
           短信，因为那张表根本没有 email 列」。**那句话不再成立** ——
           EmergencyContact 现在有必填的 email，兜底的顺序由它自己的
           reachable_at 决定，和这里同序。
        """
        if self.consent_email:
            return self.consent_email, "email"
        if self.consent_phone:
            return str(self.consent_phone), "sms"
        return None

    def __str__(self):
        return f"{self.contact} — {self.event_role}"


class EventNotification(ConstraintErrorFieldMixin, TimeStampedModel):
    """One notice sent about one event: what was said, to whom, and who missed it.

    An event can be notified about more than once, and each notice has
    properties of its own (when, why, what it said, who received it). By D15's
    three tests that is a table, not a notified_at column on Event.

    ⚠️ Both M2Ms are snapshots and must never be recomputed. Somebody who could
       not be reached in March may have a phone number today, and recalculating
       would quietly rewrite this record into "everyone was told" — which is
       false. Same rule that makes hours authoritative rather than derived.

    No simple-history on this one: it is already an immutable record of
    something that happened. Editing it would not be a correction, it would be
    a forgery.
    """

    class Reason(models.TextChoices):
        TIME_CHANGED = "time_changed", "Time changed"
        LOCATION_CHANGED = "location_changed", "Location changed"
        CANCELLED = "cancelled", "Event cancelled"
        OTHER = "other", "Other"

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="notifications")
    reason = models.CharField(max_length=30, choices=Reason.choices)
    # A snapshot of the words. Editing the event afterwards must not rewrite
    # what this notice said.
    message = models.TextField()
    sent_at = models.DateTimeField()
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        # SET_NULL: whoever sent this may leave and have their account closed,
        # and the record still has to exist. Anything kept for the record is
        # never hung off a CASCADE.
        on_delete=models.SET_NULL,
        null=True, blank=True, related_name="+",
    )
    recipients = models.ManyToManyField(
        Participation, related_name="notifications", blank=True)
    # ⚠️ Who could not be reached, by name — not a count. A number answers
    #    "how many" once and can never answer "which three", and the only way
    #    back to the names would be to recompute, which the note above forbids.
    #    That is what D22 ② is asking for.
    unreachable = models.ManyToManyField(
        Participation, related_name="notifications_unreachable", blank=True)
    provider_ref = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-sent_at"]
        # "How many times has this event been notified about, and when was the
        # last one" — shown on the confirmation page, which is the only thing
        # standing between a shaky connection and two identical notices.
        indexes = [models.Index(fields=["event", "-sent_at"])]

    def __str__(self):
        return f"{self.event.name} — {self.get_reason_display()} @ {self.sent_at:%Y-%m-%d %H:%M}"
