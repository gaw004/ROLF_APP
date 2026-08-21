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
from core.limits import LONG_TEXT, SHORT_TEXT
from core.models import ImmutableCodeMixin, TimeStampedModel
from core.timeutils import day_start, local_now, local_today
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
            code=cls.GENERAL_CODE, defaults={"name": "General participant"},
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
    """Two status predicates, because status is answering two different questions.

    Event.status carries the lifecycle (draft → open → full → wrapped up /
    cancelled) *and* the visibility of the event to volunteers, and those are
    not the same question. Writing visibility as status == OPEN means that the
    moment an event is marked full everybody who
    already signed up loses access to its page — and P6's entire scenario ends
    with a notification saying "click here to cancel", a link that would then
    404, on exactly the events that filled up.

    The fix is not another field. An is_published boolean would be a second
    dimension able to contradict status, which is the bill this project has
    already paid three times.
    """

    def visible_to_participants(self):
        """Everything a signed-in person may open: published, including full and over.

        ⚠️ This filters on **lifecycle status only** — it is not an audience.
           Every signed-in account sees every published event, staff and
           outsiders alike, because nothing anywhere narrows by who is asking.
           That is a real gap rather than a design (participants.md section 1):
           the first staff-only event to go up would appear on every outside
           volunteer's list. Renamed from `visible_to_volunteers` on 2026-08-20
           so the name stops implying an audience it never had.
        """
        return self.filter(status__in=Event.VISIBLE_TO_PARTICIPANTS)

    def open_for_signup(self, now=None):
        """Everything a volunteer may still sign up for.

        🔴 **Two conditions, not one (2026-08-19): the status *and* the clock.**

           `status` is filled in by hand and nothing moves it on when the day
           arrives — so an event that ran last year still says "Open for signup"
           until somebody remembers to go and change it. That is not merely an
           ugly label: this predicate is the only gate on the signup path
           (`event_signup` 404s on it, the detail page's button reads it), so
           for as long as the field said `open`, that finished event was
           genuinely signable. Somebody could put their name down for last
           year's Saturday.

           The fix is not a nightly job flipping the column. A cron leaves a
           window — up to a day wide — in which the page is still wrong, and it
           would have to rewrite every past event's row (and its history) to say
           something the two timestamps beside it already said. The question
           "is it over?" has an exact answer in `end_time`; ask it there.

        ⚠️ The cut is `end_time`, not `start_time` (decided 2026-08-19): an
           event that has begun but not finished is still signable, because
           somebody turning up mid-morning to help is the ordinary case at a
           food distribution, not an anomaly. Same column, same reasoning as
           `from_today()` — one predicate, one column, one question.
        """
        return self.filter(
            status__in=Event.OPEN_FOR_SIGNUP,
            end_time__gt=now or local_now(),
        )

    # ⚠️ `upcoming()` (start_time >= now) and `past()` (end_time < now) lived
    #    here until 2026-08-17. They went with their last callers — the Past
    #    Events page, and event_list's old window — and are **not** kept "in
    #    case the calendar wants one": an unused predicate has nothing checking
    #    it and reads to the next person as a supported way of doing things.
    #    Same reasoning that deleted the old Memories rules; the opposite
    #    mistake is the one R1 recorded, where in_period() sat here with no
    #    caller but the tests and the requirement went unanswered for a month.
    #
    #    ⚠️ 2026-08-18: from_today() below now reads end_time, so it is much
    #       closer to the `past()` those two deleted predicates split badly —
    #       and that is the point. Splitting "is it over" across two predicates
    #       read off two columns is what left a running overnight event falling
    #       between them. One predicate, one column, one question.

    def from_today(self, today=None):
        """Today's events and everything after them — what /events/ is a list of.

        ⚠️ The boundary is **midnight in the foundation's timezone**, not `now`.
           An event that ran this morning is still one of today's: it stays on
           the page until the day rolls over, wearing "Completed". Cutting at
           `now` would make the list drop a row at the instant that event
           ended — and the schedule drawn beside it would be showing today with
           its morning missing.

        🔴 **Read off end_time (2026-08-18), not start_time.** The question this
           answers is "is it still to come, or still going", and end_time is the
           column that knows. One row of behaviour changed: an event that began
           at 22:00 yesterday and ends at 02:00 this morning is **in** — it used
           to drop off the page at midnight while it was still running, and the
           schedule beside the list drew it on today's column the whole time,
           so clicking it led to a row the list did not have.

           ⚠️ The comment this replaced argued that end_time "would keep last
              month's three-day trip on the page for as long as it ran over".
              That conflated two things: reading end_time, and keeping finished
              events. A trip that ended last month has `end_time` in the past,
              so it is out — by this predicate, on the first evaluation. What
              stays is a multi-day event **while it is still running**, which is
              the correct answer to "what is on".

           The cost, stated: such an event sits on the list every day until it
           ends, and because the page orders by start_time it sorts to the
           **top** — above things starting later today. That is deliberate; it
           is the one still in progress.

        ⚠️ So this no longer slices on the same column in_period() does. The two
           are answering different questions and always were: R1 asks "which
           events ran in this window" (start_time, matching how a report counts
           them), this asks "what is on from today". Reading them off one column
           is what made the overnight case wrong.

        ⚠️ The filter column is **not** the indexed one any more (the indexes are
           on start_time, and on (status, start_time)). Left alone deliberately:
           the pilot has tens of events, so there is nothing to optimise yet —
           the same call this file's search filter makes. When there is, the
           index to add is (status, end_time), not one on end_time alone.
        """
        return self.filter(end_time__gt=day_start(today or local_today()))

    def with_capacity(self):
        """`role_count` and `has_open_role`, so `Event.is_full` costs no query.

        ⚠️ Added for the volunteer list (2026-08-19), which asks every row
           whether it is full in order to draw the badge. Without this that is
           one query per row — twenty on a default page, on the most-hit page in
           the system.

        ⚠️ The condition inside is **not** restated here: the subquery filters
           on `EventRoleQuerySet.with_signup_counts()`'s own `is_full`
           annotation. One definition of "full", asked from two directions.
        """
        open_role = (EventRole.objects.with_signup_counts()
                     .filter(event=models.OuterRef("pk"), is_full=False))
        return self.annotate(
            role_count=Count("roles", distinct=True),
            has_open_role=models.Exists(open_role),
        )

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

    IMAGE_DIR = "event-images"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"                      # only this ministry sees it
        OPEN = "open", "Open for signup"              # published, taking signups
        # 🔴 "Full", not "Confirmed" (2026-08-19), and the stored value changed
        #    with the label — see migration 0011.
        #
        #    "Confirmed" was the wrong word, and the comment that used to sit on
        #    this line proves it: it read `# full, no more signups`. In event and
        #    booking English "confirmed" is a statement about *certainty* — a
        #    confirmed booking, a confirmed date, it is definitely going ahead —
        #    not about capacity. So a volunteer read good news off the card and
        #    then found no Sign up button, which reads as a broken page rather
        #    than as "this one is full".
        #
        #    ⚠️ The stored value moved too, rather than relabelling in place.
        #       A column that says `confirmed` under a page that says Full is
        #       the same drift this project keeps paying for: the next person
        #       reads the data, or a log line, and learns a word the interface
        #       does not use.
        #
        #    ⚠️ If signups are ever closed for a reason that is **not** capacity
        #       — an early cutoff, "we have enough people" — this word becomes a
        #       lie and the honest one is "Signups closed". Noted 2026-08-19 as
        #       the boundary of the decision, not as a thing to pre-empt.
        FULL = "full", "Full"                         # full, no more signups
        # ⚠️ "Wrapped up", not "Completed" (2026-08-19). Since the same day,
        #    *whether it is over* is answered by the clock (`is_over`), so this
        #    status had to stop meaning that or stop meaning anything. It now
        #    means the follow-up is done — attendance taken, hours recorded —
        #    which is a thing only a person knows. The time-flavoured word had
        #    to go with it: "Completed" beside a derived "Ended" is two words
        #    for what reads as one fact.
        COMPLETED = "completed", "Wrapped up"
        CANCELLED = "cancelled", "Cancelled"

    # ⚠️ Both sets are listed in full, not spelled as exclude(DRAFT), even
    #    though the two are equivalent today. B5 already paid for defining one
    #    state as the complement of another — Position's third state got swept
    #    into the wrong bucket by exactly that. The test is to list the states
    #    and count them: five here, so a complement is wrong, and the day
    #    somebody adds `postponed` a complement would quietly publish it.
    VISIBLE_TO_PARTICIPANTS = frozenset({
        Status.OPEN, Status.FULL, Status.COMPLETED, Status.CANCELLED,
    })
    # Cancelled events stay in the visible set on purpose: the people who signed
    # up are exactly the ones who need to see that it is off.
    #
    # ⚠️ Membership here is necessary but not sufficient — `open_for_signup()`
    #    and `is_open_for_signup` also ask the clock. The set alone has never
    #    been the whole gate since 2026-08-19; read either of those, not this.
    OPEN_FOR_SIGNUP = frozenset({Status.OPEN})
    # The statuses that stop meaning what they say once `end_time` has passed —
    # see `status_label`. Listed in full rather than as a complement, for B5's
    # reason: a sixth status must not be swept in here by default, and the two
    # left out (draft, cancelled) are each left out for a stated reason.
    ENDS_WITH_THE_CLOCK = frozenset({Status.OPEN, Status.FULL, Status.COMPLETED})

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
    # Capped. Every volunteer-facing list renders this, so an unbounded column
    # is one pasted document away from a page that will not load on a phone.
    # See core/limits.py for which layer actually refuses it.
    description = models.TextField(blank=True, max_length=LONG_TEXT)

    # A picture for the listing. Optional, and short-lived by design.
    #
    # ⚠️ **Deleted once the event is over** — purge_event_images, on a daily
    #    schedule. The picture is gone for good at that point: the past-events
    #    list shows the default logo from then on. That is the requirement, not
    #    an oversight, and it is what keeps this feature from accumulating.
    #
    # ⚠️ A file, never a column of bytes. The backup is a pg_dump, so anything
    #    stored in the database is in every backup forever — the opposite of
    #    what was asked for. See the MEDIA notes in config/settings/base.py.
    #
    # ⚠️ Uploads are re-encoded before they are stored (services.normalise_
    #    event_image): resized, converted to WebP and **stripped of EXIF**.
    #    A phone photo carries GPS coordinates, and an event picture taken at
    #    somebody's home would publish where they live to every signed-in user.
    image = models.ImageField(upload_to=IMAGE_DIR, blank=True)

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

    @property
    def is_over(self):
        """Has it finished? Read off the clock, never off `status`.

        The one place the question is answered, so that the badge, the signup
        gate and any test all mean the same thing by it. `end_time`, matching
        `EventQuerySet.open_for_signup()` and `from_today()` — an event that is
        running right now is not over.
        """
        return self.end_time <= local_now()

    @property
    def is_open_for_signup(self):
        """Is the door open — published, and not over yet?

        The row-level twin of `EventQuerySet.open_for_signup()`, written to
        match that predicate condition for condition. The two being one thought
        in two places is the risk here: if they ever disagree, the signup page
        404s for an event whose button was drawn, or refuses one it offered.

        ⚠️ This does **not** ask whether there is any room left — see
           `accepting_signups` for that, and for why the two are separate.
        """
        return self.status in Event.OPEN_FOR_SIGNUP and not self.is_over

    @property
    def accepting_signups(self):
        """Is the door open **and** is there room? What a volunteer can act on.

        🔴 Two properties rather than one, and the split is deliberate
           (2026-08-19). `is_open_for_signup` has to keep meaning exactly what
           the queryset means, because that queryset is what lets the signup
           page be opened at all — and a full event's signup page **should**
           still open. Landing on "all of these are full" is an answer;
           landing on a 404 reads as a broken site.

           So fullness gates what is *offered* (the Sign up button, the green
           badge that doubles as a link), not what is *reachable*.

        ⚠️ Anything that draws a way in reads this one. Anything that decides
           whether a URL exists reads the other.
        """
        return self.is_open_for_signup and not self.is_full

    @property
    def is_full(self):
        """Is there anywhere left to sign up (2026-08-19)?

        Derived from the roles, never stored — the same decision as `is_over`,
        for the same reason. Writing `full` into `status` when the last place
        goes would mean a cancellation has to write it back, and then two
        writers share one column: the admin who deliberately closed signups
        gets reopened by a volunteer changing their mind.

        ⚠️ **Every** role has to be closed, and there has to be at least one.
           One role with no ceiling means this event can always take somebody,
           so it is not full; an event with no roles at all has nothing to sign
           up for, but "full" is the wrong word for that and it says so by
           being False. Whether a signup is actually possible is the roles'
           question, and it is answered per role in `services.sign_up()`.

        ⚠️ One query, and only when something asks. The volunteer list asks it
           per row — twenty rows, twenty queries — so that list goes through
           `with_capacity()`, which hands the answer down as two annotations.
        """
        annotated = self.__dict__.get("has_open_role")
        if annotated is not None:
            return self.__dict__.get("role_count", 0) > 0 and not annotated
        roles = self.roles.with_signup_counts()
        return roles.exists() and not roles.filter(is_full=False).exists()

    @property
    def status_label(self):
        """What a *volunteer* is told the state is. Not always `status`.

        🔴 **"Ended" beats the stored word once the event is over** (2026-08-19).
           `status` is hand-filled and nothing moves it on, so a finished event
           still reads "Open for signup" — the complaint this property exists to
           answer. Every status that means "this was really going to happen"
           (open, full, wrapped up) collapses to one word once `end_time`
           has passed, because to somebody reading the list the difference
           between "it filled up and then it happened" and "it happened" is not
           a difference: it is over either way, and there is nothing to do.

        ⚠️ `Cancelled` is **not** collapsed. It says something the clock cannot:
           it did not take place. The people who signed up need that word, and
           they need it after the date as much as before it.

        ⚠️ `Draft` is not collapsed either — a volunteer never sees one (it is
           outside `VISIBLE_TO_PARTICIPANTS`), and the ministry admin previewing
           it is asking about publication, not about the clock.

        ⚠️ The management list deliberately does **not** use this: that page is
           where `status` is edited, and showing a word other than the one in
           the dropdown next to it would make the edit look like it failed.
           It shows `get_status_display` plus its own "Ended" marker.
        """
        if self.is_over and self.status in Event.ENDS_WITH_THE_CLOCK:
            return "Ended"
        # ⚠️ Derived fullness reads as the same word as the hand-set status
        #    (2026-08-19). To somebody deciding whether to come, "every place
        #    is taken" and "the organiser closed signups because every place is
        #    taken" are one fact, and the page should not make them read like
        #    two. Which of the two it is stays visible where it matters: the
        #    management list shows the real `status`.
        if self.status == Event.Status.OPEN and self.is_full:
            return Event.Status.FULL.label
        return self.get_status_display()

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
            # "Is this role closed?" — the other half of the same number, added
            # 2026-08-19 with `stop_at_needed_count`.
            #
            # ⚠️ **Three conditions, and none of them is optional.** A role with
            #    no number has no ceiling; a role whose number is a target
            #    (`stop_at_needed_count` off) has no ceiling either; only the
            #    third is about how many people turned up. Written as
            #    `registered_count >= needed_count` alone, every unlimited role
            #    in the system would close itself the moment it hit its target.
            #
            # ⚠️ `is_full` is **not** `~is_short`. They differ on exactly the
            #    rows that matter: a target-only role that has met its number is
            #    neither short nor full. Two questions, two annotations.
            is_full=(
                Q(needed_count__isnull=False)
                & Q(stop_at_needed_count=True)
                & Q(registered_count__gte=F("needed_count"))
            ),
        )

    def with_room(self):
        """Roles somebody can still sign up for."""
        return self.with_signup_counts().filter(is_full=False)

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
        help_text="How many people this job wants. Leave empty for no limit.",
    )
    # 🔴 Is that number a **ceiling**, or a target (2026-08-19)?
    #
    #    One number was being asked to answer two questions, and until this
    #    field existed it only ever answered the first: `needed_count` fed the
    #    "understaffed" reports and **nothing anywhere refused a signup**. A job
    #    wanting five people accepted fifty, and the failure showed up on the
    #    day, in a hall, with forty-five people and nothing for them to do.
    #
    #    The second question is real and it is not always "yes" — it came from
    #    the foundation: *"we need 500, but we would love more"*. That is a
    #    target with no ceiling, and it is a perfectly ordinary way to run a
    #    day. So the number stays one number, and this says whether it stops
    #    anybody.
    #
    # ⚠️ **Default True.** The two defaults fail in opposite ways: ticked, the
    #    failure is somebody who cannot sign up and says so, and an admin who
    #    unticks a box; unticked, the failure is invisible until the morning of
    #    the event. Same reasoning, written out in full, as
    #    Event.requires_guardian_consent.
    #
    # ⚠️ Means nothing when `needed_count` is empty — no number, no ceiling.
    #    Every predicate below checks both, and none of them is written as a
    #    complement of the other.
    stop_at_needed_count = models.BooleanField(
        default=True,
        verbose_name="Stop signups at this number",
        help_text="Untick if more people than that are welcome — the number "
                  "then says what you are aiming for, and nobody is refused.",
    )
    # SHORT_TEXT rather than LONG_TEXT: this is read inside a row of the roles
    # panel, where a screenful in one cell pushes the other roles off the page.
    notes = models.TextField(blank=True, max_length=SHORT_TEXT)

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

    # ⚠️ **There is no `is_full` property here, and that is deliberate.**
    #    `is_full` exists once, as the annotation in with_signup_counts(), and a
    #    property of the same name cannot coexist with it anyway: Django sets
    #    annotations with `setattr`, and a read-only property raises on that —
    #    so the two would not merely duplicate the rule, they would crash every
    #    query that asked for it.
    #
    #    Whoever needs the answer for one row re-reads that row through the
    #    annotation; `services.sign_up()` does exactly that, in one query, and
    #    says why.

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

    class CheckInMethod(models.TextChoices):
        """Who put the attendance on this row: an admin, or the volunteer.

        D28. The QR check-in cannot be made tamper-proof — a live accomplice on
        site can always forward the link — so the mitigation is not to try, but
        to make the fact **visible** on the attendance page. A problem nobody can
        see is a problem nobody handles.
        """

        ADMIN = "admin", "Recorded by an admin"
        SELF_QR = "self_qr", "Self check-in by QR"

    class ServedAs(models.TextChoices):
        """What this person was doing here: their own time, or their job.

        D38. The same fact is read from both sides — "I can tell which of my
        weekends I gave away" and "we can tell which of these good deeds were
        actually shifts" — which is exactly why it is a stored column and not
        something either page works out for itself.

        ⚠️ Never derive this from anything else. Two derivations look right and
           are wrong in both directions: from how the row was created (an
           employee can sign themselves up for work, and an admin can enter a
           genuine volunteer from a paper list), and from whether they had a
           shift that day (most unpaid staff have no roster at all, and a
           Saturday event is nobody's rostered time). D38 sections 1–3 spend a
           section on each.

        ⚠️ The labels are interface text and D38 section 6 is their only home —
           do not invent a second wording here or in a template. The
           explanatory half of each option lives in SERVED_AS_EXPLANATIONS
           below, beside these, so the two halves cannot drift apart.
        """

        VOLUNTEER = "volunteer", "Volunteering"
        WORK = "work", "Scheduled work"

    class DeclaredBy(models.TextChoices):
        """Who said so. The evidence lives in this column, not the one above.

        A paid employee's "volunteering" means one thing when they said it and
        something else entirely when their employer ticked it for them — and
        the second is the shape an inspector looks for first. Reading served_as
        alone cannot tell them apart.

        Same shape and same reason as checked_in_method beside it: not a new
        rule, the existing rule applied to a second fact.
        """

        SELF = "self", "Said by the volunteer"
        ADMIN = "admin", "Set by an admin"

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
    # How the attendance on this row was **first** established. D28.
    #
    # ⚠️ blank, and deliberately **no default**. Empty means "this row predates
    #    self check-in", which is not the same fact as "an admin recorded it".
    #    A default of ADMIN would back-date a claim onto every historical row —
    #    vouching for something nobody checked, the same objection D27 raised
    #    about the no-show rate's denominator.
    #
    # ⚠️ First write wins, and undo_attendance() clears it. The question this
    #    column answers is "did the volunteer fill this row in, or did I?", and
    #    an admin correcting the hours afterwards must not rewrite the answer to
    #    "I did" — the correction is in the history table, this is the origin.
    checked_in_method = models.CharField(
        max_length=20, choices=CheckInMethod.choices, blank=True,
    )
    # --- D38: was this their own time, or their job? ----------------------
    #
    # 🔴 blank, and deliberately **no default** — the same objection as
    #    checked_in_method above, in the same words. A default of "volunteer"
    #    would back-date a claim onto every historical row, vouching for
    #    something nobody checked; and this particular claim is the one the
    #    foundation would be relying on if it ever had to show that its unpaid
    #    hours were genuinely unpaid.
    #
    # ⚠️ Empty means one thing only: the row predates D38 and the backfill
    #    could not prove anything about it (migration 0014). Every row written
    #    since goes through services.set_served_as(), which writes both columns
    #    together or neither.
    #
    # ⚠️ No CheckConstraint tying the two together, and it is worth saying why
    #    because it looks like an obvious one to add: "if served_as is set then
    #    declared_by must be too" is violated by the backfill itself, which
    #    writes `volunteer` with nobody's name on it — that combination is
    #    "provable from the data, claimed by no one", and it is legitimate.
    served_as = models.CharField(
        max_length=20, choices=ServedAs.choices, blank=True,
    )
    served_as_declared_by = models.CharField(
        max_length=20, choices=DeclaredBy.choices, blank=True,
    )

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


#: The second half of each served_as option, for when somebody is being *asked*
#: rather than shown a value: "Volunteering — my own time". Kept here rather
#: than in the template so the term and its gloss cannot drift apart, and
#: because D38 section 6 is the single home for both halves.
#:
#: ⚠️ Both entries must read as equally respectable. A layout or a wording that
#:    makes volunteering the nicer answer turns this column into one everybody
#:    fills in the same way, and then **both** figures are wrong. No test can
#:    watch for that; D38 section 10 records it as review's job.
SERVED_AS_EXPLANATIONS = {
    Participation.ServedAs.VOLUNTEER: "my own time",
    Participation.ServedAs.WORK: "counts as my work time",
}


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
    #
    # ⚠️ NotifyForm is a plain Form, so it carries this cap **as well** — from
    #    the same constant, deliberately. The form is what a person submits
    #    against; this column is what the form's output has to fit in.
    message = models.TextField(max_length=LONG_TEXT)
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
    # Had an address, and it still did not go: the provider refused it, the
    # daily quota ran out, the connection dropped halfway down the list.
    #
    # ⚠️ A third column rather than a second meaning for `unreachable`, and the
    #    difference is not pedantic: "we never had a way to tell this person"
    #    is fixed by asking them for a phone number, and "the mail server said
    #    no at 19:04" is fixed by looking at the provider. Merging them makes
    #    the first question unanswerable forever, because nothing else in this
    #    record remembers which one it was.
    #
    # ⚠️ The three sets are exclusive and together they are everybody who was
    #    signed up at that moment. Anything that lands in none of them is a
    #    person nobody can account for, which is the failure this whole record
    #    exists to prevent.
    failed = models.ManyToManyField(
        Participation, related_name="notifications_failed", blank=True)
    provider_ref = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-sent_at"]
        # "How many times has this event been notified about, and when was the
        # last one" — shown on the confirmation page, which is the only thing
        # standing between a shaky connection and two identical notices.
        indexes = [models.Index(fields=["event", "-sent_at"])]

    def __str__(self):
        return f"{self.event.name} — {self.get_reason_display()} @ {self.sent_at:%Y-%m-%d %H:%M}"
