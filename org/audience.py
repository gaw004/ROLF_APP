"""Who a row is for, and who counts as one of the foundation's own.

Three tick-boxes — outsiders, everybody on the books, named ministries — plus
the one query that reads them. L3 asks it of an Event ("may they see this"), L2
of an EventRole ("may they sign up for this"), and a Notice asks the same three
of a message with no occasion behind it at all.

⚠️ This lives in `org` rather than beside the first table that used it, and
   the reason is what the three ticks are **made of**: Ministry, Position,
   Assignment — all of them org. Nothing here mentions an event, and the
   containment rule that genuinely does ("a role cannot be wider than its
   event") deliberately stayed behind in events/models.py. Moved 2026-08-31,
   when the third table wanted it and `notices` importing from `events` for a
   reason unrelated to events would have been the wrong dependency to draw.
   Only the arithmetic is general; the sentences are not.
"""

from typing import NamedTuple

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from core.querysets import in_effect_on
from core.timeutils import local_day, local_today
from org.models import Assignment, Ministry, Position


# --- Who counts as one of the foundation's own, on a given day ---------------
#
# ⚠️ These have moved twice, and the second move is why they are not in
#    events/models.py any more. They started in events/services.py, went to
#    events/models.py on 2026-08-26 (for_audience() below needs them, and
#    services already imports models — the other direction would be circular),
#    and came here on 2026-08-31 with the rest of the audience machinery. The
#    predicate is written entirely in org vocabulary — Position, Assignment —
#    so this is where it always belonged; events was simply the first caller.
#
# ⚠️ events/services.py imports both back out. That is one definition and one
#    direction (events → org), which is the property both moves were after.


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

        ⚠️ **No `__str__`, deliberately** (deleted 2026-08-27). There was one,
           documented "for error messages", and no error message ever called
           it — so its three phrases sat beside the ones in
           refuse_wider_than_event() that people actually read, free to drift
           apart with nothing to notice. Same reasoning that deleted
           EventQuerySet's upcoming()/past(): an unused thing has nothing
           checking it and reads to the next person as a supported way of
           doing things.

           🔴 And it was worse than merely unused: it ran a **query** for the
              ministry names. A `__str__` that reaches the database fires
              wherever anything is printed — a log line, a debugger, a
              template that renders the value, the repr of a list of them —
              which is a bad property for a value object and an invisible one.
              The NamedTuple's own repr shows the raw fields, which is what a
              person debugging this actually wants.

           If a page ever needs to *say* an audience in words (L2.4's role
           list, L2.6's event page), take the phrases from where they already
           have readers rather than writing a second set here, and keep the
           database out of them.
        """

        outsiders: bool
        all_staff: bool
        ministries: frozenset

        @classmethod
        def of(cls, instance):
            """The audience a **saved** row currently has. Never for validation.

            ⚠️ Only safe where the row is not the one being edited — the roles
               of an event whose own audience is being narrowed, for instance.

            ⚠️ `.all()`, not `.values_list("pk")`, and the difference is a query
               per call (2026-08-27). `values_list` builds a fresh queryset and
               therefore **ignores prefetch_related**, so the one caller that
               reads this in a loop — refuse_narrowing_below_the_roles(), over
               every role on the event — paid one query per role however it
               fetched them. Written this way, prefetching at the call site
               works; written the other way it silently does nothing, which is
               the worse of the two failures because the fix looks applied.

               Ministry is a table of tens of rows, so pulling whole objects
               where only the pks are wanted costs nothing measurable, and the
               alternatives that keep values_list all involve the caller
               assembling the sets itself.
            """
            return cls(
                outsiders=instance.visible_to_outsiders,
                all_staff=instance.visible_to_all_staff,
                ministries=frozenset(
                    ministry.pk for ministry in instance.visible_to_ministries.all()),
            )

    class Meta:
        abstract = True

    #: Which side of the pair this table is, for the messages and rules that
    #: differ between them. ⚠️ On the model because the model is the thing that
    #: knows: it was decided by `isinstance(row, EventRole)` in
    #: refuse_bad_audience() and by a constant on the forms, so one question had
    #: two mechanisms and a third audience-bearing table (batch three's
    #: sessions) would have fallen silently into the event branch of one and the
    #: role branch of the other. Subclasses override it; the forms read it too.
    AUDIENCE_ON = "event"

    #: Which column decides **which day** "on the books" is judged on, for the
    #: audience filter. `start_time` here; a table hanging off an event says so
    #: through its own path to it. ⚠️ Beside AUDIENCE_ON for the reason written
    #: above it: the model is the thing that knows. It lived on the queryset for
    #: a day, which split "what a table with an audience must declare" across
    #: two class hierarchies that had to agree with nothing checking it —
    #: see AudienceIsWiredUpTests.
    #:
    #: ⭐ **None means "judge it today"**, and that is the whole of what makes a
    #:    table with no occasion behind it able to carry an audience at all
    #:    (2026-08-31, for Notice). A dated row is judged on its own day; an
    #:    undated one has no day of its own to be judged on, so the only honest
    #:    answer is the day somebody is looking.
    #:
    #:    ⚠️ Not `starts_showing` or any other stand-in, and the case that
    #:       decides it is real: a notice that went up last month, ticked for
    #:       the food pantry, read by somebody who joined the food pantry
    #:       yesterday. Judged on the day it went up he was an outsider and it
    #:       vanishes; judged today he is staff and he sees it. The second is
    #:       what anybody would expect, and there is no reading of the first
    #:       that is defensible.
    AUDIENCE_DAY = "start_time"

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
        """Nobody at all. ⚠️ Costs a query for the M2M — do not call it per row.

        ⚠️ Never a choice somebody made — it is the state
           `refuse_empty_audience()` exists to forbid, and the state a row
           created straight through the ORM lands in, because three columns
           whose defaults are False/False/none add up to nobody. So filling one
           in is a repair rather than an overwrite, which is what lets the seed
           and the fixtures ask this before handing the row to
           services.set_audience(), rather than overwriting an answer somebody
           gave.
        """
        # ⚠️ `.all()`, not `.exists()`, for the reason Spec.of() writes out just
        #    below: `exists()` builds a fresh queryset and therefore ignores
        #    prefetch_related. One query either way today — but the day a caller
        #    prefetches a page of rows, the fix would look applied and do
        #    nothing, which is the worse of the two failures.
        return not (
            self.visible_to_outsiders
            or self.visible_to_all_staff
            or bool(self.visible_to_ministries.all())
        )

    def apply_audience(self, spec):
        """Write this row's audience. ⚠️ **Checks nothing** — call it through
        services.set_audience(), which is the door that does.

        Split from the checking on purpose, and the split is why it is safe for
        this to be so blunt: the rules read a Spec and compare it against other
        rows, and both of those live where a Spec's fields may be read (see
        AudienceContainmentGuardTests). Putting the refusals in here instead
        would make every fixture and every migration that ever needs to write
        three columns pay for them.

        ⚠️ The row must already be saved. A ManyToMany cannot be written before
           either end has a primary key — the same mechanic that keeps these
           rules out of Model.clean() (see the module note below).

        ⚠️ `updated_at` is listed explicitly: it is auto_now, and update_fields
           silently leaves out any column not named. Same list-it-or-lose-it
           trap as services.set_served_as().
        """
        self.visible_to_outsiders = spec.outsiders
        self.visible_to_all_staff = spec.all_staff
        self.save(update_fields=[
            "visible_to_outsiders", "visible_to_all_staff", "updated_at",
        ])
        self.visible_to_ministries.set(spec.ministries)


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
#      · the ModelForms      — the gate for every door a person walks through;
#                              they hold the submitted M2M
#      · the admin's own form — ⚠️ without one, the admin has no check at all
#      · services.set_audience() — the gate for the doors nobody walks through:
#                              the seed, the fixtures, an importer, a script.
#                              ⚠️ This line was here from the start and was
#                              **false** until 2026-08-27: services called none
#                              of them, so bulk_create and every script went
#                              past all three. A comment promising a lock is
#                              worse than an unlocked door, because it stops
#                              anybody looking.
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
    # ⚠️ Its own sentence rather than borrowing the event one, even though both
    #    are about something published (2026-08-31). "There is already a status
    #    for that" is advice a notice can act on and an event cannot act on in
    #    the same way, and the thing at stake differs: an unseen event is a
    #    missed occasion, an unseen notice is a person who did not find out.
    "notice": (
        "Say who needs to know this. A notice nobody can see is one nobody "
        "will be told, and it will look like it went out."
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


#: The same fault named after whichever box the person actually ticked. One
#: function, two sentences — the same split EMPTY_AUDIENCE_MESSAGE makes, and
#: for the same reason: "“Everybody on the books” already includes every
#: ministry" is bewildering to somebody who ticked "Everyone" and never saw
#: that phrase.
REDUNDANT_AUDIENCE_MESSAGE = {
    "all_staff": (
        "“Everybody on the books” already includes every ministry — untick it, "
        "or untick the ministries."
    ),
    "everyone": (
        "“Everyone” already includes every ministry — untick it, or untick the "
        "ministries."
    ),
}


def refuse_redundant_audience(*, all_staff, ministries, covered_by="all_staff"):
    """"Everybody on the books" plus a named ministry — one state, two spellings.

    ⚠️ The form greys the ministries out once the box is ticked, and greying is
       interface: it keeps nobody out. This is the half that does.

    Refused rather than quietly normalised: "everybody on the books" and "these
    four ministries" mean the same thing today and stop meaning it the moment a
    fifth ministry is created — so which one was meant is a question only the
    person submitting can answer.

    ⚠️ `covered_by` names the box that made the ministries redundant, because
       the "Everyone" tick sets all-staff on the person's behalf — so the
       refusal has to talk about the box they ticked, not the one it implies.
    """
    if all_staff and ministries:
        raise ValidationError(REDUNDANT_AUDIENCE_MESSAGE[covered_by])


class AudienceQuerySetMixin:
    """`for_audience()`, for every table that carries an audience.

    ⭐ **One implementation of the three branches, not one per table.** L3 asks
       it of an Event ("may they see this") and L2 of an EventRole ("may they
       sign up for this"), and those are the same three ticks read the same
       way — participants.md's whole point in making them the same three
       columns. Written out twice, the copy that drifts is the outsider branch:
       "open to outsiders" is not the widest setting, it **excludes** staff, and
       this repository has already been bitten by that reading once — seed_demo
       ticked outsiders alone and hid the entire demo from the foundation's own
       people (events/tests.py, make_event).

       ⚠️ And nothing would catch the second copy. AudienceContainmentGuardTests
          watches Audience.Spec's attribute names; a hand-written second version
          reads the model fields and never touches them.

    Two things differ between the tables, and only two, and **neither is
    declared here**:

    · `Audience.AUDIENCE_DAY` — the column holding the moment "on the books" is
      judged at. The event's own start, its event's start, or **None** for a
      table with no occasion behind it, which is judged today. It sits on the
      model beside AUDIENCE_ON, so a table declares everything about its
      audience in one place;
    · the reverse name of the ministries M2M, derived from the model name
      because the field derives it the same way (`related_name=
      "%(class)s_audience"`). One string, one derivation, no constant to keep
      in step.
    """

    def for_audience(self, contact):
        """Narrow to what this person may see. L3 on events, L2 on roles.

        ⚠️ A **second** predicate, always written beside visible_to_participants()
           and never folded into it. That one answers "is it published", this one
           answers "is it for them", and until 2026-08-26 the second question had
           no answer anywhere: every signed-in account saw every published event
           (participants.md section 1).

        The three branches are the three kinds of tick, and each is judged on
        **the day of the event** — the same clock on both tables. Two different
        clocks would produce "visible but not signable on the day" and "signable
        on the day but invisible today", and neither has an explanation a person
        would accept.

        ⚠️ All three ask whether *a* qualifying tenure **exists**, never which
           one it is: somebody may hold posts in two ministries at once (D32's
           invariant is about there being one structure, not one row), and an
           event ticked for either is one they can see — and a role ticked for
           either is one they can sign up for.

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
            on_the_books_q(models.OuterRef("audience_day")), contact_id=contact.pk)
        # ⚠️ The reverse name on the M2M is what lets this be one Exists rather
        #    than a subquery over the through table with two nested OuterRefs.
        #    See the field's own comment.
        in_a_ticked_ministry = on_the_books.filter(**{
            f"position__ministry__{self.model._meta.model_name}_audience":
                models.OuterRef("pk"),
        })

        # ⚠️ `AUDIENCE_DAY = None` means the table has no day of its own, so the
        #    tenure is judged today — see the attribute's own note for the case
        #    that decides it. The annotation is still made either way, because
        #    the OuterRef above has to have something to point at.
        day = self.model.AUDIENCE_DAY
        dated = self.annotate(audience_day=(
            local_day(day) if day
            else models.Value(local_today(), output_field=models.DateField())))
        return dated.filter(
            # ⚠️ `~Exists`, checked against `exclude(Exists(...))` on both a
            #    staff member and a genuine outsider — the two agree. Testing it
            #    with a staff member alone returns nothing either way and proves
            #    nothing, which is how the first attempt at this went.
            (outsiders & ~models.Exists(on_the_books))
            | (Q(visible_to_all_staff=True) & models.Exists(on_the_books))
            | models.Exists(in_a_ticked_ministry)
        )
