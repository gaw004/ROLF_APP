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

from typing import NamedTuple

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
from core.querysets import in_effect_on
from core.timeutils import day_start, local_day, local_now, local_today
from org.models import Assignment, Ministry, Position


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

    A catch-all row must always exist for each half of the axis (see CATCH_ALL
    below): Participation.event_role is not nullable, so "no particular job"
    and "no particular service" each need somewhere to land.

    ⚠️ There was only one until 2026-08-26, and it was a helping one — so a
       beneficiary with no particular service had nowhere correct to go. The
       argument for leaving it that way was "a ministry admin can add one in a
       single click", which answers the wrong question: it makes every
       foundation discover a system-level gap for themselves, and then invent
       the same fix. See 06-roadmap.md L1.6.

    ⚠️ Both names carry their half in brackets, and that is the point of the
       pair rather than decoration. Left as a bare "General participant" beside
       "General participant (attending)", the first one's half is **invisible**
       — readable only by knowing that no bracket means helping, which is a
       rule nobody is ever told. D27's line: what is missing and what is not
       counted must not look the same.
    """

    class Nature(models.TextChoices):
        """Are they here to give, or to receive? A property of the job itself.

        D10's test decides where this lives: an ESL seat is somewhere a service
        is received no matter who sits in it, and lifting is somewhere time is
        given no matter who does it. So it belongs to the *kind* of job, not to
        one event's decision to open it — which is why it is here and EventRole
        is untouched. The payoff is that it cannot be set inconsistently
        between two events. See participants.md L1.

        ⚠️ TextChoices rather than a column the foundation fills in, because
           code branches on it (the identity question, the refusal to record
           hours, and two denominators on the report). D5's test, and it does
           not contradict this being a dictionary table: the foundation owns the
           rows, the code owns which of the two halves a row is in.

        ⚠️ One word each, with the explanation kept beside them in
           NATURE_EXPLANATIONS rather than inside the label — the same split
           SERVED_AS_EXPLANATIONS makes, for the same reason. A label carrying
           its own gloss reads well on the form that asks and badly in the
           table cell that reports, and the version of this that trimmed the
           gloss back off in a template would stop trimming, silently, the day
           somebody reworded it.
        """

        HELPING = "helping", "Helping"
        ATTENDING = "attending", "Attending"

    # ⚠️ Kept under its old name, and it is still the helping one's code. It is
    #    frozen in three migrations (0003 seeds it, 0005 and 0015 renamed the
    #    row) and can never change — ImmutableCodeMixin. What moved on
    #    2026-08-26 is that "the catch-all" is now ambiguous, so read CATCH_ALL
    #    rather than this constant unless you specifically mean the helping one.
    GENERAL_CODE = "general"
    ATTENDING_GENERAL_CODE = "general-attending"

    #: The row a signup lands on when nobody picked a specific job or service.
    #: One per half of the axis, keyed by it, so no caller can ask for "the"
    #: catch-all without saying which.
    #:
    #: ⚠️ The two codes are not symmetrical, and that is not sloppiness: the
    #:    helping one predates the axis and its code cannot be renamed, so
    #:    matching it (to "general-helping") is simply not available.
    CATCH_ALL = {
        Nature.HELPING: (GENERAL_CODE, "General participant (helping)"),
        Nature.ATTENDING: (ATTENDING_GENERAL_CODE, "General participant (attending)"),
    }

    code = models.SlugField(
        max_length=50,
        help_text="Stable identifier used by code. Lowercase, cannot be changed later.",
    )
    name = models.CharField(max_length=100)
    # ⚠️ A default is right here, and it is worth writing down why — because the
    #    same round's `served_as` forbids one (D38 section 9). The two are
    #    opposites: a default identity would vouch for a claim nobody made,
    #    while `helping` is a fact already true of every row in the database
    #    today. A default that restates the present is not an assumption.
    nature = models.CharField(
        max_length=20,
        choices=Nature.choices,
        default=Nature.HELPING,
        verbose_name="What somebody in this role is doing",
        # ⚠️ The first sentence is the definition participants.md section 9
        #    names as the mitigation for a known gap, not politeness: in this
        #    sector "participant" is usually read as "the person being served",
        #    and this is the first screen where the two halves are named side by
        #    side. Without it the foundation reads the word narrowly and then
        #    goes looking for another one to cover the people who came to help.
        help_text="Everybody at an event is a participant — this says which "
                  "kind. Lifting, interpreting and the welcome desk are "
                  "helping; an ESL seat or a food parcel is attending.",
    )
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
    def seed_catch_all(cls, nature):
        """The catch-all role for one half of the axis, created if it is missing.

        ⚠️ Takes the half rather than defaulting to helping (it was
           `seed_general()` with no argument until 2026-08-26). A default here
           would let a caller ask for "the" catch-all and silently get the
           helping one — which is exactly the bug this pair exists to fix, just
           moved from the data into the code.

        ⚠️ `defaults` covers the name **and** the nature, so a row created here
           is complete. Creating it without the nature would give it the field's
           default — helping — and the attending catch-all would be born as a
           helping one, which nothing would report.
        """
        code, name = cls.CATCH_ALL[nature]
        role, _ = cls.objects.get_or_create(
            code=code, defaults={"name": name, "nature": nature},
        )
        return role

    def clean(self):
        """`code` is immutable, and `nature` becomes immutable once it is used.

        The second half is new (2026-08-21). Flipping `nature` after people have
        signed up rewrites what their rows mean: an attending signup carries
        `served_as=not_applicable` and is counted under "people served", and
        both of those were written under what this column said at the time. A
        dictionary row is cheap — add another one instead.

        ⚠️ A hint layer, not a rule, and D14 asks for that to be said plainly:
           `ParticipationRole.objects.update(nature=…)` walks straight past
           this. It cannot become a CheckConstraint either, for the same reason
           the L2×L3 invariant cannot — the test is in another table (does any
           Participation point at me), and a check constraint cannot see it.

        ⚠️ The admin needs no `readonly_fields` here, unlike `served_as`
           (D38 section 4). That one had to be frozen because the guard greps
           source and the admin form exists without anybody writing code; this
           rule lives in `clean()`, which the admin's ModelForm calls. So the
           column stays editable — fixing a role opened under the wrong kind,
           before anybody has used it, is exactly what should be allowed.
        """
        super().clean()
        error = self.code_change_error()
        if error:
            raise ValidationError({"code": error})
        if self.pk is None:
            return
        was = (type(self).objects.filter(pk=self.pk)
               .values_list("nature", flat=True).first())
        if was is None or was == self.nature:
            return
        # Participation is declared further down this module; a name looked up
        # inside a method resolves at call time, so this is fine here and would
        # not be in the class body.
        if Participation.objects.filter(event_role__role_id=self.pk).exists():
            raise ValidationError({"nature": (
                "People have already signed up through this role, and their "
                "records were written under what it says now. Add a new role "
                "instead — a dictionary row is cheap."
            )})

    def __str__(self):
        return self.name


#: The other half of each Nature option, for when somebody is being *asked*
#: rather than shown a value: "Attending — they receive a service". Beside the
#: enum rather than in a form or a template, so the term and its gloss cannot
#: drift apart. Same shape and same reason as SERVED_AS_EXPLANATIONS below.
NATURE_EXPLANATIONS = {
    ParticipationRole.Nature.HELPING: "they give their time",
    ParticipationRole.Nature.ATTENDING: "they receive a service",
}


# --- Who counts as one of the foundation's own, on a given day ---------------
#
# ⚠️ These live here rather than in services.py (where they were until
#    2026-08-26) because EventQuerySet.for_audience() below needs them, and
#    services.py already imports this module — the other direction would be a
#    circular import. Moving them keeps one definition and keeps the dependency
#    pointing one way, services → models.


def on_the_books_q(on):
    """"Counts as one of the foundation's own on this day", as a Q over Assignment.

    The predicate itself, extracted 2026-08-21 so the three shapes below are
    three callers rather than three copies. `on` is a date, or a database
    expression naming one (see on_the_books_exists).

    ⚠️ Existence, never identity: a person may hold several posts at once
       (D32's invariant is about there being one structure, not one row), so
       every caller asks whether *a* qualifying tenure exists.
    """
    return (
        models.Q(position__kind=Position.Kind.STAFF)
        & models.Q(position__is_active=True)
        & in_effect_on(on=on)
    )


def on_the_books_exists(*, contact_ref, day_ref):
    """The same predicate as a correlated subquery, for a set of rows at once.

    Where _on_the_books() answers for one event's day, this answers for a
    queryset whose rows each carry their own day — the report counting people
    served across a month of events, and the audience filter in batch two.

    ⚠️ `day_ref` is an OuterRef onto a day the **outer** query annotated with
       core.timeutils.local_day(). It cannot be a TruncDate over an OuterRef
       here: TruncDate reads its operand's output_field while resolving, and a
       ResolvedOuterRef has none — that raises AttributeError outright. Found by
       running it (2026-08-21); see 06-roadmap.md L1.4.

       Annotating outside also puts the timezone conversion at the call site,
       where a reader can see which column is being turned into a local day.
    """
    return models.Exists(
        Assignment.objects.filter(models.Q(contact_id=contact_ref) & on_the_books_q(day_ref))
    )


class Audience(models.Model):
    """Who this is for: outsiders, all staff, or the staff of named ministries.

    L3 on an Event ("who can see it") and L2 on an EventRole ("who can sign up
    for it") are the same three questions, so they are the same three columns,
    declared once. See participants.md L2/L3 and 06-roadmap.md L2.1.

    ⚠️ Three tick-boxes rather than one three-valued column, and the reason is
       a sentence the enum could not say: "the food pantry **and** the tax
       clinic, nobody else". Joint training and cross-ministry outings are real,
       and an enum with a "this ministry" tier cannot express two of them.

    ⚠️ `visible_to_outsiders` is **not** the widest setting. It means only the
       people with no current post — a food handout for the people it serves,
       which staff should not be filling up. "Everyone" is this plus
       `visible_to_all_staff`, which is why the form offers an "Everyone" tick
       that stores those two rather than a third value of its own: one state,
       one spelling.

    ⚠️ `visible_to_all_staff` **contains** every ministry, so the two must not
       both be set — see `refuse_redundant_audience()`. They agree today and
       stop agreeing the moment a new ministry is created, which is exactly the
       kind of drift a second spelling produces.
    """

    class Spec(NamedTuple):
        """One audience, lifted out of wherever it came from.

        🔴 Every rule below takes these rather than a model instance, and that
           is not tidiness. Narrowing an event means comparing the **submitted**
           audience against its roles — read off the instance, `ministries`
           would be the row already in the database, which is the trap L2.1
           records in full: a check that reads right and validates last week.

        ⚠️ A NamedTuple, so it still unpacks like the plain tuple it replaced.
        """

        outsiders: bool
        all_staff: bool
        ministries: frozenset

        @classmethod
        def of(cls, instance):
            """The audience a **saved** row currently has. Never for validation.

            ⚠️ Only safe where the row is not the one being edited — the roles
               of an event whose own audience is being narrowed, for instance.
            """
            return cls(
                outsiders=instance.visible_to_outsiders,
                all_staff=instance.visible_to_all_staff,
                ministries=frozenset(
                    instance.visible_to_ministries.values_list("pk", flat=True)),
            )

        def __str__(self):
            """For error messages: what this audience says, in the page's words."""
            parts = []
            if self.outsiders:
                parts.append("people with no current post")
            if self.all_staff:
                parts.append("everybody on the books")
            if self.ministries:
                parts.append(
                    ", ".join(Ministry.objects.filter(pk__in=self.ministries)
                              .order_by("name").values_list("name", flat=True)))
            return " + ".join(parts) or "nobody"

    class Meta:
        abstract = True

    #: The three, named once. Every form and the admin build their field list
    #: from this, so no screen can quietly offer two of the three.
    AUDIENCE_FIELDS = (
        "visible_to_outsiders", "visible_to_all_staff", "visible_to_ministries",
    )

    visible_to_outsiders = models.BooleanField(
        default=False,
        verbose_name="People with no current post",
        help_text="Outside volunteers and the people the foundation serves.",
    )
    visible_to_all_staff = models.BooleanField(
        default=False,
        verbose_name="Everybody on the books",
        help_text="Anyone holding a post on the day — paid or not.",
    )
    visible_to_ministries = models.ManyToManyField(
        Ministry,
        blank=True,
        # ⚠️ A real reverse name, not "+". `for_audience()` asks the question
        #    from the Assignment end — "does this person hold a post in a
        #    ministry this event is open to" — and with the reverse disabled
        #    that lookup does not exist (FieldError, verified). The alternative
        #    is a subquery over the through table with two nested OuterRefs,
        #    which gives the same answer and reads far worse.
        #
        # ⚠️ Ministry therefore has two entrances that must not be confused:
        #    `ministry.events` (the ones it runs) and `ministry.event_audience`
        #    (the ones it can see). Different questions, similar names.
        related_name="%(class)s_audience",
        # A retired ministry must not be offered — same trick as
        # Participation.consent_relationship's limit_choices_to.
        limit_choices_to={"is_active": True},
        verbose_name="Only these ministries' staff",
    )

    @property
    def audience_is_empty(self):
        """Nobody at all. ⚠️ Costs a query for the M2M — do not call it per row."""
        return not (
            self.visible_to_outsiders
            or self.visible_to_all_staff
            or self.visible_to_ministries.exists()
        )


# --- The two rules an audience has to obey on its own -----------------------
#
# 🔴 Neither of these can live in Model.clean(), and that is a mechanic rather
#    than a preference. A ManyToMany is written **after** save(); full_clean()
#    runs **before** it. On a new object the field cannot even be read —
#
#        ValueError: 'Event' instance needs to have a primary key value
#                    before this relationship can be used
#
#    — and on an existing one it reads the row already in the database, not the
#    values being submitted. That second case is the dangerous one: a check that
#    looks right and validates last week's data.
#
#    So both are plain functions taking loose values, called from:
#      · the ModelForms      — the real gate; they hold the submitted M2M
#      · services            — for anything not coming through a form
#      · the admin's own form — ⚠️ without one, the admin has no check at all
#
# ⚠️ And there is no database constraint behind either, which D14 asks to be
#    said rather than implied. "At least one" has a third disjunct living in
#    another table (are there any ministry rows?), which a CheckConstraint
#    cannot see; and the weaker version a constraint *could* express — "one of
#    the two booleans" — is simply wrong, because an event ticked for two
#    ministries and nothing else is perfectly legal. bulk_create walks past
#    both of these.


#: What "nobody ticked" means on each of the two tables. One function, two
#: sentences — the failure looks different from each side, and a message that
#: covers both ends up describing neither.
EMPTY_AUDIENCE_MESSAGE = {
    "event": (
        "Say who this is for. Something published that nobody can see is a "
        "draft, and there is already a status for that."
    ),
    "role": (
        "Say who may sign up for this. A role nobody can take looks exactly "
        "like one that is full, or one somebody forgot to finish."
    ),
}


def refuse_empty_audience(*, outsiders, all_staff, ministries, on="event"):
    """Nobody ticked. Raises ValidationError; returns nothing.

    ⚠️ Both tables use this, and the role side was missing until 2026-08-26 —
       so a hand-made POST could create a role nobody at all could sign up for,
       indistinguishable on the page from a full one or an unfinished one
       (D27: what is missing and what is not counted must not look the same).

    ⚠️ Takes loose values rather than an instance, because the only layer that
       can see the **submitted** M2M is the form (see the module note on
       refuse_redundant_audience below).
    """
    if outsiders or all_staff or ministries:
        return
    raise ValidationError(EMPTY_AUDIENCE_MESSAGE[on])


def refuse_redundant_audience(*, all_staff, ministries):
    """"Everybody on the books" plus a named ministry — one state, two spellings.

    ⚠️ The form greys the ministries out once the box is ticked, and greying is
       interface: it keeps nobody out. This is the half that does.

    Refused rather than quietly normalised: "everybody on the books" and "these
    four ministries" mean the same thing today and stop meaning it the moment a
    fifth ministry is created — so which one was meant is a question only the
    person submitting can answer.
    """
    if all_staff and ministries:
        raise ValidationError(
            "“Everybody on the books” already includes every ministry — untick "
            "it, or untick the ministries."
        )


def refuse_wider_than_event(*, event, role):
    """A role may not be open to anybody the event itself is closed to.

    Requirement 7 read from the other side: seeing an event and being able to
    take one of its jobs are different questions, but they are not independent
    — somebody who cannot see the event must not be able to sign up for a job
    inside it. participants.md's L2×L3 invariant.

    Three comparisons, and they are **not the same kind of comparison** — which
    is what the old three-tier enum hid behind a single "is this wider than
    that". Written as one clever expression they come apart on the third:

      · outsiders / all-staff are booleans — implication, one each
      · ministries is a set — containment
      · and "all staff" sits **above** every ministry in that containment,
        while being a boolean rather than a set

    ⚠️ The converse of the second is deliberately refused: an event ticked for
       every ministry does **not** satisfy a role ticked for all-staff. The two
       agree today and stop agreeing the moment a ministry is created, which is
       the same reason refuse_redundant_audience() exists. It is nearly
       impossible to trigger with two ministries in the database — and that is
       exactly why it is written down rather than left to be noticed.

    ⚠️ Not a CheckConstraint, and D14 asks for that to be said rather than
       implied: the fields are on two tables plus a join table, which no check
       can see. bulk_create walks past it, and what lies on the other side is
       somebody signed up for an event they cannot see.

       ⚠️ That state is **not** backstopped by sign_up(). The signup path asks
          whether a *person* may take a *role* (services.eligible), which is a
          different question with different inputs — having it also re-check
          this invariant would give one rule two implementations. Stated
          because "have sign_up() check it too" sounds obviously right.
    """
    if role.outsiders and not event.outsiders:
        raise ValidationError(
            "This event is not open to people with no current post, so a role "
            "inside it cannot be either."
        )
    if role.all_staff and not event.all_staff:
        raise ValidationError(
            "This event is not open to everybody on the books, so a role "
            "inside it cannot be either. (Ticking every ministry is not the "
            "same thing — a ministry added later would be covered by one and "
            "not the other.)"
        )
    # A ministry-specific role is fine if the event covers all staff, and
    # otherwise only if the event names at least those ministries.
    if role.ministries and not event.all_staff:
        beyond = role.ministries - event.ministries
        if beyond:
            names = ", ".join(
                Ministry.objects.filter(pk__in=beyond).order_by("name")
                .values_list("name", flat=True))
            raise ValidationError(
                f"This event is not open to {names}, so a role inside it "
                f"cannot be."
            )


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

    def for_audience(self, contact):
        """Narrow to what this person may see. L3.

        ⚠️ A **second** predicate, always written beside visible_to_participants()
           and never folded into it. That one answers "is it published", this one
           answers "is it for them", and until 2026-08-26 the second question had
           no answer anywhere: every signed-in account saw every published event
           (participants.md section 1).

        The three branches are the three kinds of tick, and each is judged on
        **the day of the event** — the same clock L2's eligibility uses. Two
        different clocks would produce "visible but not signable on the day" and
        "signable on the day but invisible today", and neither has an
        explanation a person would accept.

        ⚠️ All three ask whether *a* qualifying tenure **exists**, never which
           one it is: somebody may hold posts in two ministries at once (D32's
           invariant is about there being one structure, not one row), and an
           event ticked for either is one they can see.

        🔴 The ministry branch is an Exists, never a join. Written as
           `filter(visible_to_ministries__in=…)` an event ticked for two
           ministries comes back **twice** for somebody on the books in both —
           verified, and it corrupts paging and every count downstream while
           looking on the page like a row that got listed twice.

        ⚠️ `contact is None` is not an error and not a special case: an account
           with no Contact cannot hold a post, so it *is* an outsider. That
           includes every superuser (D12 keeps User.contact nullable because a
           superuser matches no real person) — so a superuser does not see
           staff-only events, which is correct and reads like a bug the first
           time somebody meets it. Do not "fix" it by exempting them; that would
           open a hole straight through this whole layer, exactly as
           org/permissions.py says about its own checks.
        """
        outsiders = Q(visible_to_outsiders=True)
        if contact is None:
            return self.filter(outsiders)

        on_the_books = Assignment.objects.filter(
            on_the_books_q(models.OuterRef("event_day")), contact_id=contact.pk)
        # ⚠️ The reverse name on the M2M is what lets this be one Exists rather
        #    than a subquery over the through table with two nested OuterRefs.
        #    See the field's own comment.
        in_a_ticked_ministry = on_the_books.filter(
            position__ministry__event_audience=models.OuterRef("pk"))

        dated = self.annotate(event_day=local_day("start_time"))
        return dated.filter(
            # ⚠️ `~Exists`, checked against `exclude(Exists(...))` on both a
            #    staff member and a genuine outsider — the two agree. Testing it
            #    with a staff member alone returns nothing either way and proves
            #    nothing, which is how the first attempt at this went.
            (outsiders & ~models.Exists(on_the_books))
            | (Q(visible_to_all_staff=True) & models.Exists(on_the_books))
            | models.Exists(in_a_ticked_ministry)
        )

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


class Event(Audience, ConstraintErrorFieldMixin, TimeStampedModel):
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
    #
    # ⚠️ `m2m_fields` is not optional here. simple-history does **not** track a
    #    ManyToMany unless it is named, so without this the two audience
    #    booleans would appear in the history and the list of ministries would
    #    not — half a record of who could see this, which reads as a whole one.
    #    Who an event was published to is the same kind of promise as when and
    #    where it was, which is the reason this table has history at all.
    history = HistoricalRecords(m2m_fields=["visible_to_ministries"])

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


class EventRole(Audience, ConstraintErrorFieldMixin, TimeStampedModel):
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
    # ⚠️ m2m_fields for the same reason as Event's; see there.
    history = HistoricalRecords(m2m_fields=["visible_to_ministries"])

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


#: "This signup is on a place somebody attends." Written once, read in both
#: directions by the two predicates below — so the pair cannot come to disagree
#: about which rows they are talking about.
ON_AN_ATTENDING_ROLE = models.Q(
    event_role__role__nature=ParticipationRole.Nature.ATTENDING)


class ParticipationQuerySet(models.QuerySet):
    def recording_hours(self):
        """The rows where hours are a question at all — L4's rule, as a filter.

        ⚠️ This and `Participation.records_hours` below are two implementations
           of one rule, and they sit deliberately close together for the reason
           core/querysets.py gives about active() and is_currently_active():
           "change one, change the other" should be a glance rather than a
           promise. One row asks the property; a page of rows asks this, in one
           query instead of one per row.
        """
        return self.exclude(ON_AN_ATTENDING_ROLE)

    def attending(self):
        """The other side: rows where somebody was receiving a service.

        The exact complement of recording_hours() today, because `nature` has
        two values — and named for the axis rather than for the consequence,
        because that is the question the report asks of it ("how many people
        did we serve", not "whose hours are missing").

        ⚠️ Unlike EventRole's is_full / is_short, these two really are
           complements, so they share one Q above rather than each spelling the
           condition. Two spellings of one filter is how the pair would end up
           disagreeing about a row.
        """
        return self.filter(ON_AN_ATTENDING_ROLE)

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

        ⚠️ Two of these three are identities. The third is not — read its
           comment before treating them as a set of three.
        """

        VOLUNTEER = "volunteer", "Volunteering"
        WORK = "work", "Scheduled work"
        # 🔴 Not a third identity: "this question does not arise on this row".
        #
        #    A role people *attend* (ParticipationRole.Nature.ATTENDING) records
        #    no hours, so whose time it was is not asked and not stored. The
        #    obvious place to put that is blank — and blank is taken. It means
        #    one thing already, "this row predates D38 and the backfill could
        #    not prove anything about it" (migration 0014), and two different
        #    facts sharing one absence is how a column stops being evidence.
        #
        # ⚠️ Never offered to anybody. It is not in SERVED_AS_EXPLANATIONS, and
        #    askable_served_as() is built from that dict rather than from these
        #    choices, so it cannot reach a dropdown by anybody forgetting to
        #    exclude it. Written only by services.set_served_as(), with
        #    declared_by left empty: nobody claimed this, the structure did.
        NOT_APPLICABLE = "not_applicable", "Not applicable"

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
            # L1/L4: a place somebody attends does not record hours.
            #
            # ⭐ The rule this project could not previously express. "Attending
            #    roles record no hours" reads across two tables — the hours are
            #    here, the nature of the role is on ParticipationRole — and a
            #    CheckConstraint cannot see another table, the same corner D19
            #    put Participation.event in. Storing `not_applicable` on the row
            #    moves the test onto this row, so the rule becomes something the
            #    database enforces on every write path rather than something
            #    services.py asks nicely (D9: if it can be a constraint, it is).
            #
            # ⚠️ Zero is refused along with everything else, deliberately. Zero
            #    hours is a statement — "they came and did none" — and this row
            #    is saying something different: hours are not a question here.
            #    The constraint above it does allow 0, because it is about a
            #    different thing (you cannot have hours without attending).
            #
            # ⚠️ What it does not cover, stated rather than implied (D14): a
            #    bulk_create that writes a blank served_as against an attending
            #    role walks straight past this, because the row never says
            #    not_applicable. Keeping that from happening is
            #    services.sign_up()'s job, and it is a hint layer.
            models.CheckConstraint(
                condition=(
                    ~models.Q(served_as="not_applicable")
                    | models.Q(hours__isnull=True)
                ),
                name="participation_no_hours_when_not_applicable",
                violation_error_message="A place somebody attends does not record "
                                        "hours — they were not giving their time.",
                violation_error_code="participation_hours_when_not_applicable",
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
    def records_hours(self):
        """False on a place somebody attends: the event side records no hours.

        ⭐ The one spelling of L4's rule. Everything that has to know — the two
           refusals in services.py, the hours box on the attendance page, the
           identity line on somebody's own signups page — asks this.

        ⚠️ It reads the **role**, not this row's `served_as`, and that is the
           whole point. `not_applicable` is the recorded *consequence* of being
           on an attending role, so a row that never had it written (a
           bulk_create, an importer, anything older than this rule) would slip
           past a served_as test and collect hours — and the CheckConstraint
           would not catch it either, because that row does not claim to be
           not_applicable. So the service layer asks the question at its source
           and is deliberately the wider of the two checks; the constraint is
           the backstop for rows that did get the value. Two vantage points on
           one rule, not two rules.

        ⚠️ Reads through event_role.role, so anything rendering this per row
           must select_related("event_role__role") — the attendance page and
           the signups page both do. Same trap EventRole.is_full sidesteps by
           being an annotation; this one is a boolean off a dictionary row
           rather than a count, so a property is the honest shape for it.
        """
        return self.event_role.role.nature != ParticipationRole.Nature.ATTENDING

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
#:
#: ⚠️ Its keys are also **the whole list of identities a human may be offered**.
#:    NOT_APPLICABLE has no wording here because nobody is ever asked about it,
#:    and askable_served_as() below reads this dict rather than the enum — so
#:    a value with no way of being asked cannot appear in a dropdown by
#:    somebody forgetting to filter it out. The opposite construction (all the
#:    choices, minus the ones we exclude) makes every future value offerable by
#:    default, which is the wrong default for a column that is evidence.
SERVED_AS_EXPLANATIONS = {
    Participation.ServedAs.VOLUNTEER: "my own time",
    Participation.ServedAs.WORK: "counts as my work time",
}


def askable_served_as():
    """The identities somebody may be offered, as (value, label) pairs.

    Exactly the keys of SERVED_AS_EXPLANATIONS, in their order: a value with no
    wording for asking about it is not a value anybody is asked about. Both the
    signup form and the correction control on the signups page read this, so
    neither of them holds its own idea of which options exist.
    """
    return [
        (value, Participation.ServedAs(value).label)
        for value in SERVED_AS_EXPLANATIONS
    ]


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
