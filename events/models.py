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

import datetime
from decimal import Decimal

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
from core.timeutils import day_start, local_date_of, local_now, local_today
# ⚠️ The audience machinery moved to org/audience.py on 2026-08-31 — the three
#    ticks, Spec, for_audience(), and "who counts as on the books". It is
#    written entirely in org vocabulary (Ministry, Position, Assignment) and
#    says nothing about events, and a third table wanted it (Notice).
#
#    What stayed in this file is the half that is genuinely about events:
#    refuse_wider_than_event(), roles_left_behind(), refuse_bad_audience() and
#    their sentences. Only the arithmetic of containment is general — "this
#    event is not open to X, so a role inside it cannot be either" is not.
#
#    ⚠️ Imported, never re-exported. An alias here would be a second entrance
#       to one thing, which is the shape this project keeps deleting.
from org.audience import (
    Audience,
    AudienceQuerySetMixin,
    refuse_empty_audience,
    refuse_redundant_audience,
)
from org.models import Ministry

# ⚠️ One direction only: recurrence.py is a pure module that imports nothing
#    from this app, which is what lets a model validate a rule without the
#    generator and the ORM having to know about each other.
from .recurrence import (BATCH_CEILING, horizon_for,
                         looks_like_a_rule, occasions)


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


#: The same axis said to the person who **is** the participant (2026-09-08).
#:
#: 🔴 Two dictionaries, and the split is the point rather than a duplication.
#:    The one above is spoken by an admin about other people — it appears where
#:    a ministry admin is opening a job and deciding what the people in it will
#:    be doing, and "they" is exactly who they mean. This one appears in the
#:    volunteer's own filter on the events page, where "they" would be talking
#:    about the reader in the third person while asking them to choose. Same
#:    two terms, two audiences, so two phrasings.
#:
#: ⚠️ Kept **here, beside the other one**, precisely because they are so close.
#:    Written into the form instead, the day somebody rewords "receive a
#:    service" they would reword one of the two and leave the other — and
#:    nothing renders differently enough for anyone to notice.
#:
#: ⚠️ Not sentences and not capitalised: both halves get composed into a label
#:    (`Helping (give your time)`), so the punctuation belongs to whoever
#:    composes it.
NATURE_INVITATIONS = {
    ParticipationRole.Nature.HELPING: "give your time",
    ParticipationRole.Nature.ATTENDING: "receive a service",
}


#: The sentence all three containment refusals share. The phrase that fills
#: `%(audience)s` is the half both doors need — the role page prints this whole
#: sentence, the event page composes its own around the phrase.
#:
#: ⚠️ `%(parent)s` is the noun for whatever the role hangs on, and it is a
#:    parameter rather than the word "event" because there is now a second pair
#:    (`EventSeries` × `EventSeriesRole`, L5.4). `EventAudienceFormMixin`'s
#:    docstring predicted this exactly — "there is no third table that sentence
#:    is true of" — and it was right: the first browser pass on the series page
#:    read "This **event** is not open to…" on a page with no event on it.
#:    One word, and a reader who has to work out that it means the series.
TOO_WIDE_STEM = ("This %(parent)s is not open to %(audience)s, so a role "
                 "inside it cannot be either.")

#: What to call the row a role hangs on, per table. Keyed by AUDIENCE_ON beside
#: org.audience's AUDIENCE_HEADING and EMPTY_AUDIENCE_MESSAGE, and for the same
#: reason those two are: a fifth audience-bearing table adds one entry and is
#: finished. ⚠️ Keyed on the **parent's** side, not the role's.
PARENT_NOUN = {"event": "event", "series": "series"}


def _refuse_too_wide(field, audience, *, parent="event", stem=TOO_WIDE_STEM,
                     extra=""):
    """Raise one containment refusal: keyed to a tick, carrying its phrase.

    ⚠️ One constructor rather than three near-identical `raise` statements, and
       the point is `params` rather than the saving. Two readers index
       `params["audience"]` — services.refuse_bad_audience() and
       AudienceFormMixin.refuse_narrowing_below_the_roles() — on nothing but
       trust that every branch remembered to set it. A fourth branch that
       forgot would raise KeyError at both of them, on the validation path.
       Here it is an argument, so it cannot be left out.
    """
    raise ValidationError({field: ValidationError(
        stem + extra, params={"audience": audience, "parent": parent})})


def refuse_wider_than_event(*, event, role, parent="event"):
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

    ⚠️ Raises a **field-keyed** ValidationError (2026-08-27), so the caller can
       put the sentence on the tick it is about rather than on whichever box
       happens to be first. All three used to land on `visible_to_outsiders`:
       a role refused for naming a ministry the event left out reported it
       under "People with no current post", which is the wrong box — and the
       forms' own rule is that an audience error goes on a field precisely so
       nobody has to hunt for which one. Pointing at the wrong one is that
       failure with an extra step.

    ⚠️ First failure only, not all three collected. The caller shows one
       reason, they fix it, and the next appears if there is one — which is the
       behaviour this already had. Written down because the dict shape now
       makes collecting them look easy.

    ⚠️ Every refusal carries `params["audience"]`: the people it is about, as a
       phrase (2026-08-27). The message here is a whole sentence, which suits
       the page editing one role and not the page narrowing an event — that one
       is about a **set** of roles and composes its own sentence, which this
       rule cannot do because it is handed Specs and never sees a role's name.
       So the phrase is the half both sides share, and it is interpolated into
       the sentence below rather than written twice: one spelling for "the
       people this is about", used by whichever page is asking.
    """
    if role.outsiders and not event.outsiders:
        _refuse_too_wide("visible_to_outsiders", Audience.OUTSIDERS_ARE,
                         parent=parent)
    if role.all_staff and not event.all_staff:
        _refuse_too_wide(
            "visible_to_all_staff", Audience.ALL_STAFF_ARE, parent=parent,
            extra=" (Ticking every ministry is not the same thing — a ministry "
                  "added later would be covered by one and not the other.)")
    # A ministry-specific role is fine if the event covers all staff, and
    # otherwise only if the event names at least those ministries.
    #
    # ⚠️ The lookup below is one query, and refuse_narrowing_below_the_roles()
    #    calls this once per role — so a **rejected** narrowing costs one extra
    #    query per role blocked on this branch. Measured 2026-08-27: 12 queries
    #    for 2 roles, 22 for 12. Left alone deliberately, and the number is
    #    written down so that is a decision rather than a thing nobody looked
    #    at. It is bounded by the roles on one event (they all fit on one
    #    screen), it happens only when a submission is being refused, and the
    #    always-on half of this — reading each role's audience — is now a single
    #    prefetch (see Spec.of).
    #
    #    2026-08-27: these names are now **shown on both pages**, so the lookup
    #    buys something on every path that pays for it. Until then the narrowing
    #    page discarded the sentence and read only which field the error was
    #    keyed to, which made this query pure waste on that path.
    if role.ministries and not event.all_staff:
        beyond = role.ministries - event.ministries
        if beyond:
            # ⚠️ The joining ("A", "A and B", "A, B and C") is Django's own
            #    get_text_list, now inside Audience.ministry_staff_are(). A bare
            #    comma join reads as an unfinished sentence here: the role names
            #    in these messages are each in quotes and delimit themselves,
            #    while these sit unquoted inside a phrase. Written by hand until
            #    2026-08-27, with a test, for behaviour the framework ships.
            names = list(Ministry.objects.filter(pk__in=beyond).order_by("name")
                         .values_list("name", flat=True))
            # ⚠️ "staff in X", not the bare name. The other two phrases describe
            #    people and this one has to read the same way in the same slot —
            #    "open to Tax Help, who could no longer see it" says a
            #    department cannot see an event. All three now come from
            #    org.audience so the refusal and the page cannot drift.
            _refuse_too_wide("visible_to_ministries",
                             Audience.ministry_staff_are(names), parent=parent,
                             stem="This %(parent)s is not open to %(audience)s, "
                                  "so a role inside it cannot be.")


#: What the **event** side says when narrowing would strand a role. The role
#: side prints refuse_wider_than_event()'s own sentence instead — see
#: roles_left_behind() for why the two cannot be one message.
NARROWING_MESSAGE = ("Narrowing this %(parent)s would leave %(roles)s open to "
                     "%(audience)s, who could no longer see it.")


def roles_left_behind(event, roles, parent="event"):
    """Yield (field, audience phrase, role name) for every role this narrowing strands.

    ⭐ The one walk over an event's roles, for the two callers that ask the
       question from that side: services.set_audience() (stop at the first) and
       AudienceFormMixin.refuse_narrowing_below_the_roles() (collect them all
       and group by box). Written out at both until 2026-08-27 — the same
       queryset hints, the same digging through the raised error, the same
       sentence stem — which put this diff's own N+1 fix in two places, one of
       which the next tuning would miss.

    ⚠️ The query hints live here and nowhere else. `select_related` for the name
       the message prints, `prefetch_related` for each role's audience: one
       query each for any number of roles, and the second only works because
       Spec.of() reads the relation with `.all()`.

    ⚠️ It yields rather than raising, because the two callers disagree about
       what to do with a fault and that disagreement is the point — a person
       gets every problem at once, spread across the boxes that caused them; a
       script gets stopped at the first.
    """
    for role in roles.select_related("role").prefetch_related(
            "visible_to_ministries"):
        try:
            refuse_wider_than_event(event=event, role=Audience.Spec.of(role),
                                    parent=parent)
        except ValidationError as blocked:
            # ⚠️ One entry, always: refuse_wider_than_event() raises on the
            #    first comparison that fails, which its docstring says outright.
            field, reported = next(iter(blocked.error_dict.items()))
            yield field, reported[0].params["audience"], role.role.name


def audience_beyond(*, event, role):
    """Who can see this event but not this role, as phrases. Requirement 8.

    ⭐ The reverse of refuse_wider_than_event() above, and the reason it exists
       is that requirement 8 makes this state **normal**: one event published
       once, recruiting inside and outside at the same time, each person seeing
       only the roles that are open to them. That is intended — and it is also
       what an oversight looks like, because the two are the same state. The
       only thing that separates them is whether the person publishing meant it,
       so the site's job is to say who it happened to and let them decide.

    ⚠️ **Not** refuse_wider_than_event() with the arguments swapped, and that is
       the trap worth writing down because the swap looks obviously right. On an
       event open to all staff with a role open only to Tax Help, swapping
       answers "everybody on the books" — while Tax Help's own staff can see the
       role perfectly well. It would print a sentence that is simply untrue. The
       comparisons are not symmetrical: `all_staff` sits above every ministry on
       one side of the containment and is a plain boolean on the other, which is
       exactly the asymmetry that function's docstring spends a paragraph on.

    ⚠️ Phrases come from org.audience, the same three the refusals use. A fourth
       spelling of "people with no current post" is how a page and a refusal
       come to describe the same group differently.
    """
    words = []
    if event.outsiders and not role.outsiders:
        words.append(Audience.OUTSIDERS_ARE)
    if event.all_staff and not role.all_staff:
        # ⚠️ Minus whatever the role does cover. A role open to Tax Help is open
        #    to those people, so "everybody on the books" would be false of them
        #    — this is the half the naive swap gets wrong.
        if role.ministries:
            covered = list(
                Ministry.objects.filter(pk__in=role.ministries).order_by("name")
                .values_list("name", flat=True))
            words.append(
                f"{Audience.ALL_STAFF_ARE} except "
                f"{Audience.ministry_staff_are(covered)}")
        else:
            words.append(Audience.ALL_STAFF_ARE)
    elif not event.all_staff:
        # Both sides name ministries, so the difference is the ones only the
        # event names. (When the event covers all staff the branch above has
        # already said everything there is to say about staff.)
        beyond = event.ministries - role.ministries
        if beyond:
            names = list(
                Ministry.objects.filter(pk__in=beyond).order_by("name")
                .values_list("name", flat=True))
            words.append(Audience.ministry_staff_are(names))
    return words


def roles_narrower_than_event(event, roles):
    """Yield (role name, audience phrases) for every role not open to everybody
    who can see the event.

    ⚠️ The same walk as roles_left_behind() above and deliberately beside it:
       same query hints, same Spec.of(), same phrases. What differs is the
       direction and what it is for — that one refuses a save, this one reports
       on one that succeeded.

    ⚠️ Yields rather than returning a sentence, because the two callers word it
       differently: a message shown once at publish time, and a line that sits
       on the event page afterwards.
    """
    event_spec = Audience.Spec.of(event)
    for role in roles.select_related("role").prefetch_related(
            "visible_to_ministries"):
        words = audience_beyond(event=event_spec, role=Audience.Spec.of(role))
        if words:
            yield role.role.name, words


def refuse_bad_audience(*, row, spec):
    """Every rule that applies to `row`, for a caller with no form to run them.

    ⭐ The whole of L2/L3's validity in one call, for the paths that reach the
       database directly: an importer, a script, a data migration, batch three's
       session generator. Until 2026-08-27 the three rules lived on the forms
       alone — which covered every door a **person** can walk through, and none
       of the others. The module note above listed `services` as a place they
       were enforced, and it was not; this is that sentence becoming true.

    Which rules apply depends on **what the row has underneath and above it**,
    which every audience-bearing table declares for itself:

    | row             | PARENT   | CHILDREN | on top of not-empty and not-redundant |
    |-----------------|----------|----------|---------------------------------------|
    | Event           | —        | `roles`  | no role of its own is left wider than it |
    | EventRole       | `event`  | —        | not wider than its event               |
    | EventSeries     | —        | `roles`  | as Event, on the template              |
    | EventSeriesRole | `series` | —        | as EventRole, on the template          |
    | Notice          | —        | —        | nothing else — the two above are all of them |

    ⚠️ Notice's row is an explicit branch below, not the `else` it used to be
       (2026-08-31). The old shape assumed anything that was not a role was an
       event, so the first table with an audience and no children fell into the
       event branch and died on `row.roles` with an AttributeError.

    ⚠️ And the two columns above replaced `AUDIENCE_ON == "role"` plus a
       hard-coded `row.event` (2026-09-10, batch three). That spelling made the
       containment rule reachable only by a table that called its parent
       `event` — and `EventSeriesRole`, which is the same pair one level up,
       calls it `series`. The failure would have been silent in the worst
       direction available: a template pair with no containment check at all,
       generating twelve events with roles open to people who cannot see them.
       See `org.audience.Audience.AUDIENCE_PARENT`.

    ⚠️ Raises on the **first** failure, with the error keyed to the tick it is
       about (refuse_wider_than_event's shape). A caller reaching the database
       directly wants to be stopped, not handed a list — the forms are what
       collect every fault at once, because a person is going to fix them all
       before pressing the button again.

    🔴 This is a **second composition** of the same rules, not a second
       implementation of them: each rule below has exactly one body, in this
       module, and both this and AudienceFormMixin call those bodies. They
       differ only in what they do with a failure — this one stops, the forms
       spread the faults across the boxes that caused them and name the roles
       involved. Unifying them further would mean giving that up, and the
       error presentation is worth more than the symmetry.
       AudienceContainmentTests.test_the_service_and_the_forms_refuse_the_same_three_things
       puts every refusal to both doors so the pair cannot drift — and the walk
       itself is shared (roles_left_behind), so only the grouping is written
       twice.
    """
    refuse_empty_audience(
        outsiders=spec.outsiders, all_staff=spec.all_staff,
        ministries=spec.ministries, on=row.AUDIENCE_ON)
    refuse_redundant_audience(all_staff=spec.all_staff, ministries=spec.ministries)
    if row.AUDIENCE_PARENT:
        # ⚠️ The parent's **saved** audience, which is right here and would be
        #    wrong on the other branch — a role is always added to an event
        #    that already exists, so what is in the database is what the event
        #    is. See AudienceFormMixin.refuse_wider_than_its_event.
        above = getattr(row, row.AUDIENCE_PARENT)
        refuse_wider_than_event(
            event=Audience.Spec.of(above), role=spec,
            parent=PARENT_NOUN.get(above.AUDIENCE_ON, "event"))
        return
    if not row.AUDIENCE_CHILDREN:
        # A table with no parent to be wider than and no children to leave
        # behind — the two rules above are all of them. See the table in the
        # docstring, and Notice.AUDIENCE_CHILDREN.
        return
    if row.pk is None:
        # Nothing to be wider than it yet, and `roles.all()` raises outright on
        # an unsaved instance — the same check EventForm makes, for the same
        # reason.
        return
    noun = PARENT_NOUN.get(row.AUDIENCE_ON, "event")
    for field, audience, name in roles_left_behind(
            spec, getattr(row, row.AUDIENCE_CHILDREN), parent=noun):
        # ⚠️ Re-raised naming the role, because from this side the rule's own
        #    sentence answers the wrong question. "This event is not open to
        #    people with no current post" is true and useless to somebody
        #    narrowing an event: what they need is **which of their roles** is
        #    in the way, and that is the one thing the rule cannot say — it is
        #    handed Specs and never sees a name.
        raise ValidationError({field: ValidationError(
            NARROWING_MESSAGE,
            params={"roles": f"“{name}”", "audience": audience, "parent": noun},
        )})


class Source(models.TextChoices):
    """Where a row came from: a person typed it, or a rule produced it.

    ⚠️ **One definition, three columns.** `Session.source`, `Event.source` and
       — when D2a lands — `Shift.source`. Written out here rather than inside
       any one model because they are not "alike", they are the same question,
       and this project has deleted a second copy of one truth three times.

    Its only reader is `services._drop_generated_after()`, and the distinction
    it has to make is this: rows a rule produced may be dropped and produced
    again when the rule changes, and rows a person added by hand may not.
    Without the column, re-scheduling a course either dares not delete
    anything, or deletes the make-up class somebody added on purpose.

    ⚠️ It sits **above** `Event` rather than beside the tables that use it, and
       that is not a preference: a `choices=`/`default=` in a class body is
       evaluated as the class is built, so an enum defined further down the file
       is a NameError at import. `NOT_COMING` a few hundred lines below carries
       the same note for the same reason — it was moved once already.
    """

    MANUAL = "manual", "Added by hand"
    GENERATED = "generated", "Produced by a rule"


class EventQuerySet(AudienceQuerySetMixin, models.QuerySet):
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
           "Is it for them" is a separate question with a separate answer:
           `for_audience()` in org/audience.py, and AudienceIsAskedGuardTests
           requires the two to appear together. Renamed from
           `visible_to_volunteers` on 2026-08-20 so the name stops implying an
           audience it never had.

        ⚠️ The three lines here until 2026-09-08 said nothing anywhere narrowed
           by who is asking, and that "a real gap rather than a design". True
           when written (participants.md section 1), false from the day L3
           landed — and left standing for a fortnight beside the very function
           that closed it. org/audience.py states the rule this broke: a comment
           promising a lock is worse than an unlocked door, because it stops
           anybody looking. The same holds in reverse — a comment reporting a
           hole that is filled sends the next person to fill it twice.
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

    def with_shortfall(self):
        """Short-of-people first, then soonest. For "where am I needed".

        ⭐ One ordering, defined once, because it is a **judgement** and not a
           sort key: "the event that still needs people beats the event that is
           sooner". Written at a call site it would be an `order_by` somebody
           tweaks; written here it has a name and a reason.

        ⚠️ `is_short` is not restated — it is `EventRole`'s own annotation, the
           same one `understaffed()` filters on and the same one the signups
           page draws its badge from. That rule has a trap in it (`needed_count`
           NULL means "no limit", so such a role is never short), and a second
           copy of a rule with a trap in it does not stay in step.

        ⚠️ `Exists`, never a join to the roles. An event with three short roles
           would otherwise come back three times — the same mistake
           `for_audience()` records about the ministry branch, and it corrupts
           slicing while looking on the page like a row that got listed twice.
        """
        short_role = EventRole.objects.with_signup_counts().filter(
            event=models.OuterRef("pk"), is_short=True)
        return self.annotate(
            needs_people=models.Exists(short_role)
        ).order_by("-needs_people", "start_time")

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

    # ⚠️ The two below are a **partition**, and that is what makes them
    #    different from the `upcoming()` / `past()` pair deleted above. Those
    #    were two independent questions that happened to sit together; these are
    #    the two halves of one question, and `test_the_two_predicates_do_not_
    #    overlap` pins that they neither overlap nor leave a gap. The day a
    #    third shape is added, that test is what refuses to let it belong to
    #    neither.
    #
    # ⚠️ They are not speculative either — `events.admin.ShapeFilter` calls both
    #    from the day they land, which is the only thing the note above actually
    #    asks of a predicate. `/programs/` (L5.8) is the second reader.

    def programs(self):
        """Courses and programs: one event, many meetings, sign up once."""
        return self.filter(shape=Event.Shape.PROGRAM)

    def single_occasions(self):
        """One-off occasions — a Saturday food distribution, and every event
        `EventSeries` generates (L5.4 makes N independent single occasions,
        never one program)."""
        return self.filter(shape=Event.Shape.SINGLE)


class Event(Audience, ConstraintErrorFieldMixin, TimeStampedModel):
    """One occasion: a food distribution on Saturday morning.

    Several shifts are several Events, not one Event plus a shift table: the
    time difference is then carried by each event's own start/end, the work
    difference by EventRole, the hours difference by each Participation. Three
    dimensions, no third table. See phase-b.md「一人一活动多角色」.
    """

    IMAGE_DIR = "event-images"

    class Shape(models.TextChoices):
        """Is this one occasion, or a course people sign up to once? L5.3.

        ⚠️ **Two values here, and two on the publish form — not three.** The
           third —
           recurring events, a weekly occasion each signed up for separately —
           produces N independent events that are every one of them `single`.
           It is something you *do* when creating (L5.4's generator), not
           something an event *is*, and giving it a value here would be the
           fourth cell of decision 16's table: an event you click in week three
           and find you have signed up for all twelve.

           🔴 **And it is not on the publish form yet.** Decision 21 asks for a
              three-way radio there; L5.4 built the generator and the admin
              door, and never went back to `EventForm`. So the foundation's own
              sentence — "可以让 admin **选**…", a choice offered to a
              publisher — is not offered to any publisher today: building a
              series is superuser-only, through the admin. Written down as a
              gap in participants.md §9 with L5.8 as its restart condition,
              because this docstring claimed the opposite for a day and a
              requirements review caught it.

        ⚠️ Why a column at all, rather than asking `sessions.exists()`. Two
           reasons, and they answer **classification**, which is a different
           question from what the schedule and the detail page ask:

             · a list page filters on it, and `Exists` means a subquery on every
               read of the busiest page in the system;
             · **a course with no dates on it yet is still a course.** Deciding
               the shape and then scheduling the meetings is the natural order,
               and `sessions.exists()` answers wrongly for as long as that takes.

           What to *draw* is the other question, and it is keyed on having
           meetings — see `schedule.Occurrence`. Two questions, two tests; they
           do not compete.
        """

        SINGLE = "single", "One occasion"
        PROGRAM = "program", "A course or program — sign up once"

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
    # reason: a sixth status must not be swept in here by default, and the three
    # left out (draft, cancelled, wrapped up) are each left out for a stated
    # reason — the last of them by the set below.
    ENDS_WITH_THE_CLOCK = frozenset({Status.OPEN, Status.FULL})
    # 🔴 The statuses a volunteer is **never** shown, whatever the clock says
    #    (2026-08-28). "Wrapped up" is the admin's bookkeeping word — it means
    #    the follow-up is done, attendance taken, hours recorded — and it is
    #    filled in days after the event, by which time `ENDS_WITH_THE_CLOCK`
    #    would have collapsed it to "Ended" anyway. The only case where it ever
    #    reached a volunteer was an admin who ticked it early, and there the
    #    word answers a question nobody outside that ministry is asking.
    #
    #    ⚠️ Its own set rather than a second condition inside `status_label`:
    #       "which words are ours and which are theirs" is the fact worth
    #       naming, and the day somebody adds `reconciled` this is the line
    #       they have to walk past.
    #
    #    ⚠️ It is **not** removed from `VISIBLE_TO_PARTICIPANTS`. The event
    #       itself stays visible — it happened, people signed up for it, and
    #       their signups are still theirs to read. What is hidden is one word.
    NOT_A_VOLUNTEERS_WORD = frozenset({Status.COMPLETED})

    name = models.CharField(max_length=200)
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

    # L5.3. Default `single`, which is what every event in the database was
    # before this column existed — see migration 0027 for the one kind of row
    # that is not.
    shape = models.CharField(
        max_length=20, choices=Shape.choices, default=Shape.SINGLE,
        verbose_name="What kind of event is this",
        help_text="A course runs over weeks and is signed up to once — its "
                  "start and end are the term's two ends, not one sitting.",
    )
    # Decision 17. Only means anything on a course, and Event.clean() says so.
    #
    # ⚠️ It has a reader from the day it lands: services.sign_up() **refuses** a
    #    chosen set of meetings unless this is ticked. Without that the "pick
    #    which ones" behaviour would be available on every course whatever the
    #    publisher decided, and this would be a switch nothing consults — the
    #    shape this project keeps deleting.
    people_pick_meetings = models.BooleanField(
        default=False,
        verbose_name="People choose which meetings they attend",
        help_text="Leave unticked and signing up covers every meeting. Tick it "
                  "for a group somebody joins for a few weeks of a term.",
    )

    # L5.4. The rule this occasion came out of, and whether a rule made it at
    # all. Both empty/`manual` on an event somebody published by hand, which is
    # every event in the database before this column existed.
    #
    # 🔴 **PROTECT, not SET_NULL**, and D40 section 3 changed its mind about
    #    exactly this once already — on `Shift.generated_from`, the same shape
    #    one table over. `SET_NULL` means "delete the series, keep the occasions
    #    but let them forget where they came from", and a generated, still-in-
    #    the-future event with no series is an **orphan**: every generator
    #    filters by `series=`, so nothing will ever collect it again. It stands
    #    on the list page until somebody deletes it by hand, one row at a time.
    #
    #    ⚠️ PROTECT refuses no legitimate deletion. Undo drops what it may drop
    #       first, and a series with nothing left deletes cleanly; one with
    #       occasions left takes `ended_on` instead and is meant to survive.
    #       Both paths are D40 step ②. **PROTECT only ever stops a bug.**
    series = models.ForeignKey(
        "EventSeries", null=True, blank=True, on_delete=models.PROTECT,
        related_name="occasions")
    # ⚠️ The enum is `Source`, declared once further down this file and shared
    #    with `Session.source` — the two are not "alike", they are the same
    #    question. Its only reader is `services._drop_generated_after()`: rows a
    #    rule made may be dropped and made again, rows a person added may not.
    source = models.CharField(
        max_length=20, choices=Source.choices, default=Source.MANUAL)

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
            # 🔴 **A double-click on Generate built the whole batch twice.**
            #    `generate_occasions()` reads which moments already stand and
            #    then writes; under READ COMMITTED two presses in flight at once
            #    each read an empty set and each insert, so a 52-occasion rule
            #    came out as 104 events — two per evening, no error, and two
            #    green messages both saying 52. Measured.
            #
            #    The press is slow enough to invite the second click: 52
            #    occasions with two roles each is ~2500 queries, seconds on a
            #    real instance. The lock in `generate_occasions()` serialises
            #    the ordinary case; this is what makes the guarantee true rather
            #    than likely, because a lock only holds for code that takes it.
            #
            # ⚠️ **Partial** — `series__isnull=False`. Two hand-made events at
            #    the same moment are a perfectly ordinary thing for a foundation
            #    to run (two food distributions on one Saturday morning), and
            #    this must not start refusing them.
            models.UniqueConstraint(
                fields=["series", "start_time"],
                condition=models.Q(series__isnull=False),
                name="event_one_occasion_per_series_moment",
                violation_error_message=(
                    "This series already has an occasion at that moment."),
                violation_error_code="event_series_moment_taken",
            ),
        ]
        indexes = [
            models.Index(fields=["start_time"]),                 # R1
            models.Index(fields=["ministry", "start_time"]),     # R2
            # P3's volunteer list page — the most-hit query in the system.
            models.Index(fields=["status", "start_time"]),
        ]

    def clean(self):
        """`shape` freezes once it has been used, and one switch needs it set.

        The first half is the rule `ParticipationRole.clean()` already writes
        for `nature`, and for the same reason: turning a course into a one-off
        occasion rewrites what the rows underneath it mean. Twelve meetings and
        a term's worth of register entries stay in the database, and nothing
        reads them any more — the signup path stops opening registers, and the
        hours recorded against those meetings go on existing with no shape of
        event that admits to holding them. Nothing raises.

        🔴 **It freezes one column and nothing else.** Calling a course off is a
           `status` change; it goes through `services.set_status()`, which never
           touches this field. A run that has to stop in week seven must always
           be able to stop — see that function for what happens to the register.

        ⚠️ An event nobody has used yet stays free to change its mind, which is
           the whole point of the column existing: deciding the shape and *then*
           scheduling the meetings is the order this is built for, and the
           minutes in between are exactly when somebody notices they picked the
           wrong one.

        ⚠️ A hint layer, not a rule — the same D14 caveat `nature` carries.
           `Event.objects.update(shape=…)` walks straight past this, and no
           CheckConstraint can replace it: the test is whether another table has
           rows pointing here, and a check constraint cannot see another table.
        """
        super().clean()
        if self.shape != self.Shape.PROGRAM and self.people_pick_meetings:
            raise ValidationError({"people_pick_meetings": (
                "Only a course has meetings to choose between. A one-off "
                "occasion is signed up to once, and that is all of it."
            )})
        if self.pk is None:
            return
        was = (type(self).objects.filter(pk=self.pk)
               .values_list("shape", flat=True).first())
        if was is None or was == self.shape:
            return
        if self.sessions.exists():
            raise ValidationError({"shape": (
                "This run already has meetings scheduled. Remove them first, "
                "or leave the shape as it is — the register and the hours "
                "recorded against those meetings were written under it."
            )})
        if Participation.objects.filter(event_role__event_id=self.pk).exists():
            raise ValidationError({"shape": (
                "People have already signed up for this event, and what their "
                "signups cover was decided by what it says now."
            )})

    @property
    def poster(self):
        """The picture this event shows: its own, else the series' one. L5.4.

        ⭐ **One upload, every occasion** — and no copies. A rule that makes 52
           evenings makes them from one template, and a picture is part of that
           template, so the file lives on the series and every occasion points
           at it. Changing it changes all of them, which is what somebody who
           uploaded one picture for one weekly meeting expects.

        ⚠️ Why not copy the bytes onto each occasion (the obvious answer): it
           is 52 files to change when the poster changes, and nothing would
           carry the change — the same "the template edit never reached the
           occasions" complaint this feature already has to write down for
           name and place. Sharing is the only shape where that complaint
           cannot arise, because there is no stale copy to have.

        🔴 And why not simply store the same **path** on all 52 rows, which
           looks equivalent: `purge_event_image()` deletes the *file*. The
           morning after the first evening ended, the other 51 would be
           pointing at nothing — and that function's own docstring says a row
           pointing at a deleted file is worse than a row with no picture,
           because the page renders a broken image rather than the default.
           See `services.series_with_images_to_purge()` for what replaces it:
           the series' picture goes when its **last** occasion is over, which
           is the same sentence the single-event rule has always said, applied
           to the thing that actually owns the file.

        ⚠️ An occasion may still carry its own picture, and it wins — "this
           week we have a guest" is a real thing to want, and it costs nothing
           to allow. That one is purged on the ordinary single-event schedule,
           because it belongs to that evening alone.

        ⚠️ Read this, never `.image`, on anything that might be generated —
           `PosterIsAskedGuardTests` holds the line. A template reading
           `event.image` directly shows the default logo on every generated
           occasion, which looks exactly like a series nobody gave a picture to.
        """
        if self.image:
            return self.image
        return self.series.image if self.series_id and self.series.image else None

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
           (open, full) collapses to one word once `end_time` has passed,
           because to somebody reading the list the difference between "it
           filled up and then it happened" and "it happened" is not a
           difference: it is over either way, and there is nothing to do.

        🔴 **"Wrapped up" reads as "Ended" whatever the clock says**
           (2026-08-28). It used to sit in `ENDS_WITH_THE_CLOCK`, which almost
           always hid it — an admin ticks it days after the event, and by then
           the clock had collapsed it anyway. What was left was the one case
           where it leaked: ticked *early*, and then a volunteer reads a word
           that answers a question only that ministry is asking (is the
           attendance in? are the hours recorded?). Two words for the same
           fact, one of which is about somebody else's paperwork.

           ⚠️ Which of the two it is stays visible exactly where it is acted
              on: the management list shows the real `status`.
           ⚠️ It is a *label*, not a gate. Nothing about who may see the event,
              or sign up, or read their own signup, changed with this — see
              `NOT_A_VOLUNTEERS_WORD`.

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
        if (self.status in Event.NOT_A_VOLUNTEERS_WORD
                or (self.is_over and self.status in Event.ENDS_WITH_THE_CLOCK)):
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


class EventRoleQuerySet(AudienceQuerySetMixin, models.QuerySet):
    """L2 lives here: `for_audience()` on this table answers "may they sign up".

    ⚠️ The same method as on Event, from the same mixin, and that is the point.
       On an event the answer decides what somebody may **find**; on a role it
       decides what somebody may **join** — and requirement 6 is that on a role
       those are one answer: "in the role layer, seen means signable"
       (participants.md section 3). So a role that is not theirs is filtered
       **out** of the page and out of the form's dropdown, never listed with a
       note saying they cannot have it — requirement 8's own words are that
       internal roles "are only shown to" internal people.
    """

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

    ⚠️ `AUDIENCE_ON = "role"`: the other half of the audience pair, and the
       thing every message and rule that differs between the two reads.

    It exists with nobody signed up, and that is the point: an event that
    opened five roles and filled three has five roles, and the two empty ones
    are what P2 wants to see. Merged into Participation there would be no row
    to represent them — the disease D11 convicted once already on Position.
    """

    AUDIENCE_ON = "role"
    # The event above it; nothing below. `refuse_bad_audience()` reads these to
    # decide that this row goes through refuse_wider_than_event() rather than
    # through the walk over children — see its table.
    AUDIENCE_PARENT = "event"
    AUDIENCE_CHILDREN = None
    # ⚠️ Its event's day, which is the same clock the event's own visibility is
    #    judged on — deliberately. Two clocks would mean "visible today but not
    #    signable on the day", and nobody could explain that to the person it
    #    happened to.
    AUDIENCE_DAY = "event__start_time"

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
        coming, and mailing them about a new time is noise. ⚠️ So are withdrawn
        ones, for exactly the same reason: somebody who stopped coming to a
        course in week six does not need to be told week nine has moved.
        """
        return self.exclude(status__in=NOT_COMING)

    def volunteering(self):
        """The rows on the **volunteering** ledger. D38's own half of D36.

        ⭐ This is the definition of the one hours figure this project is
           allowed to print. D36 concluded there was no printable total —
           `Participation.hours` and `Shift` overlap and may never be added —
           and [D38 section 7] overturned exactly that much of it: once
           `served_as` exists, "hours volunteered" has a definition, and this
           is it.

        🔴 **One ledger, never two.** The whole safety of the figure is that
           this filter cannot accidentally include work time: a caller that
           wants both is asking for the sum D36 forbids, and there is
           deliberately no method here that would give it to them.

        ⚠️ Beside recording_hours() and attending() because they are neighbours
           on the same axis, and the three have to be read together: those two
           split on the *role's* nature (giving vs receiving), this one on the
           *person's* declaration (my own time vs my job). An event can pair any
           of them, which is why neither axis can be derived from the other.
        """
        return self.filter(served_as=Participation.ServedAs.VOLUNTEER)

    def hours_given(self):
        """Sum the hours on these rows. `Decimal("0")` when there are none.

        ⚠️ Only meaningful after `volunteering()` — on its own it would add the
           two ledgers together, which is the one thing D36 forbids. It is a
           separate method rather than folded into the filter because the
           dashboard needs the number while `/me/participations/` needs the
           rows.

        ⚠️ `or Decimal("0")`: `Sum` over no rows is None, and a page that prints
           "None hours volunteered" is the failure this line exists to stop.
           Zero is a true answer for somebody who has not started yet; None is
           not an answer at all.

        🔴 **Both columns since 2026-09-08.** Decision 20 put a second hours
           column on `SessionAttendance`, and on a run `Participation.hours` is
           `None` by design — so an assistant who gave twenty-four hours across
           a spring term read **0 hours volunteered** on their own dashboard.
           The zero defended above is the honest one ("has not started yet");
           this was a different zero wearing it.

        ⚠️ **Two queries, not one Sum over a join**, and not `distinct=True`
           either. Summing across the join multiplies each signup's hours by its
           number of register rows; `distinct=True` then "fixes" that by summing
           distinct *values*, so two evenings of 2.5 collapse into one. Both are
           silently wrong in opposite directions, and the second is worse
           because it looks like the cure. Asked separately, each half sums its
           own rows.

        ⚠️ Still one direction. This adds two halves of **hours given**; what it
           must never take in is hours received (D43), which is not a column at
           all and so cannot arrive here by accident.
        """
        signups = self.aggregate(total=models.Sum("hours"))["total"] or Decimal("0")
        sessions = (
            SessionAttendance.objects.filter(participation__in=self)
            .aggregate(total=models.Sum("hours"))["total"] or Decimal("0")
        )
        return signups + sessions

    def mine(self, contact):
        """This person's signups, narrowed in the query rather than the template.

        ⚠️ `visible_to_participants()` as well, and it is not belt and braces:
           every row here links to the detail page, and that page uses the same
           predicate. A signup an admin entered against an unpublished event
           would otherwise appear with a link that 404s — the failure this pair
           was written to prevent, arriving from the other end.

        ⚠️ Extracted from `views.my_participations` on 2026-09-02 so that the
           dashboard and that page cannot come to different answers about what
           counts as "mine". Two copies of this predicate is one page showing a
           signup the other does not.
        """
        if contact is None:
            return self.none()
        return self.filter(
            contact=contact,
            event_role__event__in=Event.objects.visible_to_participants(),
        )

    def upcoming(self, now=None):
        """Not over yet, soonest first — what "coming up" means.

        ⚠️ The cut is `end_time`, not `start_time`, matching
           `EventQuerySet.open_for_signup()`: something that started an hour ago
           and runs till five is still very much coming up for the person who
           has to be there.
        """
        return self.filter(
            event_role__event__end_time__gt=now or local_now(),
        ).order_by("event_role__event__start_time")


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
        # 🔴 Not a synonym for cancelled, and the difference is a figure the
        #    foundation reports. "Pulled out before it started" and "came for
        #    six weeks and then stopped" are different facts: the second person
        #    **was served**, and folding him into the first erases that from
        #    people_served while the register still proves it happened. The
        #    sector reports the three separately (enrolled / completed /
        #    withdrew) for the same reason.
        #
        # ⚠️ Written by services.cancel(), which picks between the two by asking
        #    whether anything is on the register — never chosen by hand on a
        #    form. The person clicking "withdraw" is doing one thing; which of
        #    the two facts it is depends on what already happened, not on what
        #    they meant.
        WITHDREW = "withdrew", "Withdrew partway"

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


#: The two ways of saying "not coming after all". Named because three rules key
#: on the pair (notifiable, people_served, and the register's own gate), and a
#: literal list in each is how they come to disagree about one of them.
#:
#: ⚠️ Module level rather than on the queryset class, which is defined **above**
#:    Participation — a class attribute there is evaluated before the enum
#:    exists. The methods get away with naming it because they resolve at call
#:    time.
NOT_COMING = (Participation.Status.CANCELLED, Participation.Status.WITHDREW)

#: What a register row may say. Four of Participation's five: `withdrew` is
#: about a whole run and cannot be said of one evening. See SessionAttendance.
PER_MEETING_STATUSES = [
    (value, label) for value, label in Participation.Status.choices
    if value != Participation.Status.WITHDREW
]


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


class Session(ConstraintErrorFieldMixin, TimeStampedModel):
    """One meeting inside a run of something. ⚠️ It is not an `Event`.

    The spring ESL class is **one** `Event` (1 March to 20 June); its twelve
    meetings are twelve `Session` rows underneath it. This is the sentence
    participants.md section 9 says `Event` could not hold — "he is in this
    programme from March to June" — and `Event` has in fact always had the two
    ends of it. What was missing is the moments in between.

    ⚠️ The division of labour with `Event` is hard, and all three have to hold:
       · a `Session` **cannot be signed up for on its own** (the signup hangs on
         the `Event`'s role, once for the whole run — decision 19)
       · a `Session` **has no audience of its own** (L2/L3 live on `Event` and
         `EventRole`, and a run is seen or not seen as one thing)
       · a `Session` **is not in the site's event list or schedule** (`/events/`,
         `/events/schedule/`) — it is not an occasion you browse to, it is one
         meeting of an occasion, and it appears inside that run's own page.
       Break any one of them and the thing being described is an `Event`, and it
       should go through recurring events instead — which produces N independent
       events, each of them `single`.

    ⚠️ The line against `Shift` has not moved either (participants.md section 6):
       a standing post + repeating weekly + the foundation owing him time for it
       is a `Shift`. Somebody taking a course is not at work.
    """

    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="sessions")
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    # ⚠️ Named to match `Event`, which is the window this one subdivides —
    #    `session.start_time` and `event.start_time` are compared against each
    #    other in clean() below. The `_at` suffix means something else in this
    #    codebase (registered_at, checked_in_at, sent_at: the moment an action
    #    happened), and a scheduled window has never used it.
    source = models.CharField(
        max_length=20, choices=Source.choices, default=Source.MANUAL)

    history = HistoricalRecords()

    class Meta:
        # Forward, unlike Event's `-start_time`. The two orders answer two
        # questions: a list of events is read newest-first, and a course is read
        # in the order it is taught. The run's own schedule page draws them in
        # this order.
        ordering = ["start_time", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["event", "start_time"],
                name="session_no_two_meetings_at_once",
                violation_error_message=(
                    "This run already has a meeting starting then."),
                violation_error_code="session_duplicate_start",
            ),
            models.CheckConstraint(
                condition=models.Q(end_time__gte=models.F("start_time")),
                name="session_end_time_not_before_start_time",
                violation_error_message="The end time cannot be before the start time.",
                violation_error_code="session_end_before_start",
            ),
        ]
        # ⚠️ No `indexes`, and that is a decision rather than an omission. The
        #    unique constraint above already builds a composite index on
        #    (event, start_time), which is exactly what both queries want: every
        #    meeting of one run in teaching order, and "when is the next one"
        #    for the my-programs page. A second index on start_time alone would
        #    serve nothing that asks about sessions without saying which run.

    def __str__(self):
        return f"{self.event.name} · {self.start_time:%Y-%m-%d %H:%M}"

    @property
    def duration(self):
        """How long this meeting runs. Derived, never stored — same as Event's.

        ⭐ D43 rests on this property existing. The hours somebody was *given*
           are the lengths of the meetings they attended added up, and this is
           where each length comes from: the run already stores the two ends of
           every meeting, so storing a third number saying how far apart they
           are would be a second truth free to disagree with them.

        ⚠️ Not to be confused with `Event.duration` on a programme, which is the
           whole term (111 days for a spring course). That one is the width of
           the run; this one is the width of one evening of it.
        """
        return self.end_time - self.start_time

    def clean(self):
        """A meeting hangs on a course, and it falls inside that course's dates.

        The two ends of an `Event` say when the run starts and stops, so a
        meeting outside them contradicts the row it hangs on: a spring class
        running to 20 June cannot have a meeting on 1 August. Without this, the
        docstring above — "what was missing is the moments in between" — stops
        being true of the table.

        The first rule is L5.3's, and it closes the gap the shape column would
        otherwise open. Meetings on a **one-off occasion** would be drawn by the
        schedule and listed on the detail page (both key on having meetings,
        which is the right test for what to draw) while `sign_up()` — which asks
        the shape, because it is classifying — opened no register for anybody.
        Meetings that exist, a register permanently empty, and nothing raising.
        So: having meetings implies being a course. The converse stays free, and
        deliberately: a course with no dates on it yet is still a course.

        ⚠️ A hint layer, not a rule, and D14 asks for that to be said plainly:
           `Session.objects.create(...)` and `bulk_create` walk straight past
           it. It cannot become a CheckConstraint either, for the same reason
           the L2×L3 invariant and ParticipationRole's frozen `nature` cannot —
           the test is in another table (the event's own columns), and a check
           constraint cannot see one. The programmatic path that is meant to
           obey it is `services.add_session()`, which calls `full_clean()`.
        """
        super().clean()
        if self.event_id is None or self.start_time is None or self.end_time is None:
            return
        if self.event.shape != Event.Shape.PROGRAM:
            raise ValidationError({"event": (
                f"“{self.event.name}” is a one-off occasion, so it has no "
                "meetings — it is the occasion. Change it to a course first, "
                "then schedule them."
            )})
        # Errors land on a field rather than the form as a whole, and on the end
        # that is actually outside — telling somebody "this is out of range"
        # without saying which end is a second lookup they have to do by hand.
        if self.start_time < self.event.start_time:
            raise ValidationError({"start_time": (
                "This run starts on "
                f"{local_date_of(self.event.start_time):%-d %B %Y}, so a meeting "
                "cannot be before that."
            )})
        if self.end_time > self.event.end_time:
            raise ValidationError({"end_time": (
                "This run ends on "
                f"{local_date_of(self.event.end_time):%-d %B %Y}, so a meeting "
                "cannot run past it."
            )})


class SessionAttendanceQuerySet(models.QuerySet):
    """Two orders, because the two directions read this table differently.

    ⚠️ There is deliberately no `Meta.ordering`, and it is worth saying why
       rather than letting the next person add one. Reading down a person
       (`participation.attendances`) wants teaching order; reading across a
       meeting (`session.attendances`) wants people. One Meta cannot serve both,
       and the one that crosses a relation has a second cost: Django appends
       default ordering fields to the GROUP BY of any `values().annotate()`,
       which is exactly the query L5.7 is about to write over this table. A
       silently regrouped aggregate is the shape this project keeps convicting.
    """

    def in_teaching_order(self):
        """Week one first — the order a course is read in, same as Session."""
        return self.order_by("session__start_time", "id")

    def by_person(self):
        """The register for one meeting, in the order a roster is read."""
        return self.order_by(
            "participation__contact__legal_last_name",
            "participation__contact__legal_first_name",
            "id",
        )

    def attended(self):
        """Rows where they actually turned up.

        The one spelling of "he was there for this one", so the register, the
        hours received and any future rate all mean the same thing by it.
        """
        return self.filter(status=Participation.Status.ATTENDED)


class SessionAttendance(ConstraintErrorFieldMixin, TimeStampedModel):
    """One person at one meeting: were they there, and what did they do.

    Every platform surveyed has this layer under a different name and the same
    shape. Salesforce PMM calls it `ServiceDelivery` (the signup being
    `ServiceParticipant`); Apricot calls it an attendance tracker (the signup
    being an enrolment); ChurchSuite, with "sign up to the sequence" on, rolls
    attendance into a view over time. ⚠️ Five products, no exceptions: **signing
    up once is not attending once**, and the two live on two tables.

    ⚠️ It is **not** a row of `Participation`, and that is decision 19 rather
       than a preference. The columns look alike; the meaning does not.
       `Participation` says "he signed up for this run" and the report counts it
       to get how many people signed up; this table says "he came to week seven"
       and counting it gets sessions attended. Two different numbers — and
       merging them is the disease this project has now diagnosed three times.

    ⚠️ `served_as` is not copied here: the identity ("was this my own time or my
       job") is declared once for the whole run and stays on `Participation`.
       D38 section 5's table asks what somebody's *participation* counts as, and
       for a course "this participation" is the term, not the evening.

    ⚠️ 🔴 That decision has a consequence L5.2's draft did not: the
       `CheckConstraint` refusing hours on a place people attend **cannot come
       with it**. On `Participation` that rule is enforceable only because the
       row itself stores `served_as=not_applicable`, which moves a cross-table
       test onto the row. Without that column there is nothing here for a check
       constraint to look at — the criterion lives two tables away, on
       `ParticipationRole.nature`. So on this table the rule is `clean()` plus
       the service layer and nothing more, and D14 asks for the gap to be
       stated: a bare `create()` writes hours against an ESL seat. There is a
       test named after that so the sentence stays true rather than aspirational.
    """

    participation = models.ForeignKey(
        Participation, on_delete=models.CASCADE, related_name="attendances")
    # ⚠️ `attendances` on both ends, and that is not the collision L5.2's draft
    #    was guarding against. `event.sessions` and a would-be
    #    `participation.sessions` would have been one word for two tables; these
    #    two are one word for **two directions onto the same table**, which is
    #    what a reverse accessor is for. The draft's `related_name="+"` would
    #    have blocked "who came to week seven" — the first query the register
    #    page asks.
    session = models.ForeignKey(
        Session, on_delete=models.CASCADE, related_name="attendances")

    # ⚠️ Participation's enum rather than a second copy of it — but only the
    #    four values that mean something for **one meeting**. What changes
    #    between the two tables is the scale, not the question, and D5's line
    #    about a second truth applies to enums as much as to tables; what does
    #    not carry over is `withdrew`, which is a statement about a whole run
    #    ("he stopped coming") and cannot be made about a single evening.
    #
    # ⚠️ An explicit subset rather than the whole enum, and the reason is the
    #    one askable_served_as() is built on: a value with no meaning here would
    #    otherwise be offered by every form and admin that renders this column,
    #    by nobody remembering to exclude it. Opting values in is the right
    #    default for a column somebody reads as evidence.
    #
    #    How each of the four reads down here:
    #      registered — expected at this meeting, which has not happened yet
    #      attended   — was there
    #      absent     — was expected and did not come. ⚠️ The row **existing**
    #                   is what makes this different from decision 18's "the
    #                   first four weeks are not absences": somebody who joined
    #                   in week five has no rows for weeks one to four at all.
    #      cancelled  — told us in advance they could not make this one
    status = models.CharField(
        max_length=20, choices=PER_MEETING_STATUSES,
        default=Participation.Status.REGISTERED,
    )

    # Decision 20: hours are recorded at the meeting level for a run. Same
    # column type and the same reason as Participation.hours — Decimal, never
    # Float, because hours may end up attached to recognition and floats drift
    # when summed.
    #
    # 🔴 This is hours **given** — an assistant's six evenings out of twelve. It
    #    is not what the people being served received; that number is not stored
    #    anywhere, it is computed from the lengths of the meetings they attended
    #    (D43). Anything summing this column is summing donated time, and adding
    #    the other direction into it produces a figure with no definition.
    hours = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    checked_in_at = models.DateTimeField(null=True, blank=True)
    checked_out_at = models.DateTimeField(null=True, blank=True)
    # ⚠️ blank with deliberately no default, in the same words as
    #    Participation.checked_in_method: empty means "this row predates self
    #    check-in", which is not the same fact as "an admin recorded it", and a
    #    default would back-date a claim onto every row nobody checked.
    checked_in_method = models.CharField(
        max_length=20, choices=Participation.CheckInMethod.choices, blank=True,
    )

    # "Who moved this person from absent to attended, and when" is a question
    # about somebody's record of a course. Session and Participation both keep
    # one; two adjacent tables differing needs a reason, not a default.
    history = HistoricalRecords()

    objects = models.Manager.from_queryset(SessionAttendanceQuerySet)()

    class Meta:
        # ⚠️ No `ordering` — the reason is on SessionAttendanceQuerySet, and it
        #    is a decision rather than an omission.
        #
        # ⚠️ No `indexes` either, same shape as Session's note: the unique
        #    constraint below builds the composite index on
        #    (participation, session), and Django indexes each foreign key on
        #    its own, which is what "who came to this meeting" reads.
        constraints = [
            # Two non-nullable columns, so nulls_distinct is not needed.
            models.UniqueConstraint(
                fields=["participation", "session"],
                name="sessionattendance_duplicate",
                violation_error_message="They are already on the register for "
                                        "this meeting.",
                violation_error_code="sessionattendance_duplicate",
            ),
            models.CheckConstraint(
                condition=models.Q(hours__isnull=True) | models.Q(hours__gte=0),
                name="sessionattendance_hours_not_negative",
                violation_error_message="Hours cannot be negative.",
                violation_error_code="sessionattendance_hours_negative",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="attended")
                    | models.Q(hours__isnull=True)
                    | models.Q(hours=0)
                ),
                name="sessionattendance_hours_only_when_attended",
                violation_error_message="Only somebody recorded as having "
                                        "attended can have hours.",
                violation_error_code="sessionattendance_hours_without_attendance",
            ),
            # ⚠️ The two below are the pair L5.2's draft left out, and leaving
            #    them out is exactly what its own line warned about: copy the
            #    rules one at a time, not "much the same", because a table that
            #    dropped one is looser than the table it copied and nothing says
            #    so. Participation carries both, and this table has all three
            #    columns they read.
            models.CheckConstraint(
                condition=(
                    models.Q(checked_out_at__isnull=True)
                    | models.Q(checked_in_at__isnull=True)
                    | models.Q(checked_out_at__gte=models.F("checked_in_at"))
                ),
                name="sessionattendance_checkout_after_checkin",
                violation_error_message="Check-out cannot be before check-in.",
                violation_error_code="sessionattendance_checkout_before_checkin",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(checked_in_at__isnull=True) | ~models.Q(status="absent")
                ),
                name="sessionattendance_checked_in_is_not_absent",
                violation_error_message="Somebody who checked in cannot be "
                                        "marked absent.",
                violation_error_code="sessionattendance_absent_after_checkin",
            ),
        ]

    def __str__(self):
        return f"{self.participation.contact} — {self.session}"

    @property
    def records_hours(self):
        """False on a place somebody attends — asked through the signup.

        ⚠️ Delegated rather than re-derived. L4's rule has exactly one spelling
           (`Participation.records_hours`) and this layer has to ask the same
           question of the same column, or the two answers are free to drift.

        ⚠️ Reads through participation → event_role → role, so anything
           rendering this per row wants
           `select_related("participation__event_role__role")`. Same trap the
           signup page's version notes, one join deeper.
        """
        return self.participation.records_hours

    @property
    def hours_received(self):
        """How long the foundation's time was spent on them here — or None.

        D43, and the other direction from `hours`: one is time somebody gave,
        this is time somebody was given. It is the length of this meeting, which
        the run already stores. Never a column — the two ends are on the
        `Session` row, and a third number saying how far apart they are would be
        free to disagree with them.

        ⚠️ Not "class hours", and the wording is load-bearing. ESL is the first
           service that ran into this, not its boundary: six sessions of
           financial coaching, an eight-week support group, a job-training
           course and a legal clinic all ask the same question. Salesforce PMM
           names the equivalent column `Quantity` with a configurable unit for
           exactly this reason — it does not assume you are running a class.

        🔴 None on a **helping** row, not the meeting's length. An assistant is
           in the room to give, and nothing is being delivered to him; counting
           his evening here is what made one person holding both a seat and an
           assistant's place report four hours of service for a two-hour class.
           The mirror of `records_hours` above: each direction answers on its
           own half and returns None on the other.

        ⚠️ None rather than 0 for somebody who did not come. "Nothing was
           delivered here" and "he was not on this register" are two facts, and
           D27's rule is that they must not look the same.
        """
        if not self.records_hours:
            # An attending row: this is the half the question is about.
            if self.status != Participation.Status.ATTENDED:
                return None
            return self.session.duration
        return None

    def clean(self):
        """Two rules, both of which read another table, so neither can be a constraint.

        1. The meeting has to belong to the run the person signed up for.
           Without it this table stores "he came to week seven of a course he
           never signed up for" — readable, printable, and wrong. It is the same
           corner `Participation` sidesteps by having no `event` column of its
           own (see its docstring): two foreign keys that must agree about a
           third row. Here neither key can be dropped, so the agreement has to
           be checked instead.
        2. A place somebody attends records no hours (L4). The service layer
           refuses it too; this is the half a ModelForm gets.

        ⚠️ D14, said plainly rather than implied: `SessionAttendance.objects
           .create(...)` and `bulk_create` walk past both. Neither can become a
           CheckConstraint — rule 1 compares two other tables, and rule 2's
           criterion is on `ParticipationRole.nature`, which is two joins away.
           The programmatic path meant to obey them is
           `services.add_attendance()`. Two tests are named after the gap.
        """
        super().clean()
        if self.participation_id is None or self.session_id is None:
            return
        if self.participation.event_role.event_id != self.session.event_id:
            raise ValidationError({"session": (
                f"That meeting belongs to “{self.session.event.name}”, and this "
                f"signup is for “{self.participation.event_role.event.name}”. "
                "A register only holds meetings of the run somebody signed up for."
            )})
        if self.hours is not None and not self.records_hours:
            raise ValidationError({"hours": (
                f"“{self.participation.event_role.role.name}” is a place people "
                "attend, not a job — the event side records no hours for it. "
                "How long the foundation's time was spent on them is a "
                "different number, worked out from the meeting's own two ends."
            )})


class EventSeriesQuerySet(AudienceQuerySetMixin, models.QuerySet):
    def live(self):
        """The batches that have not been undone. D40 section 2.

        ⚠️ Asked of this column and never inferred from "does it still have any
           occasions left". Undo **keeps** the ones people signed up for and the
           ones already past, so "undone" and "half undone" look identical from
           that side — the derived-state-as-authoritative-state disease D40
           records this project having judged four times.
        """
        return self.filter(undone_at__isnull=True)


class EventSeries(Audience, ConstraintErrorFieldMixin, TimeStampedModel):
    """One rule and one template, producing N **separate** events. L5.4.

    The third of the three shapes the foundation asked for, and the one the
    other two could not hold: a Tuesday-evening prayer meeting where **coming this week is
    this week's decision**. Twelve of them are twelve events, each with its own
    signups, its own audience and its own line in the list.

    ⚠️ It is **not** a Programme, and the two words are near-synonyms in
       English, so it is written down flat:

         · `EventSeries` → N `Event` rows, each signed up for separately;
         · a programme   → one `Event` + N `Session` rows, signed up for once.

       Which one is a three-way radio at publish time, and the choice cannot be
       swapped afterwards — that would be a data migration, not a setting.

    ⚠️ **The carrier ruling in deferred.md is void, and this table is why.**
       `Event.parent` was assessed against D15's three tests and passed, the
       second of them being "the relationship carries no attributes of its own".
       A generation rule **is** an attribute — a rule, a start, a duration, an
       audience and a whole table of role templates — and D15 watches this exact
       cell, saying outright that a broken test means the relation is promoted
       to a table. The test was not wrong; the thing being tested changed
       (see deferred.md's own line: what was imagined was "morning / afternoon"
       grouping, and what was asked for was "generate twelve of these").

    ⚠️ Why not "the first occasion doubles as the template" — Google Calendar's
       and CiviCRM's shape. It makes one row both the series and one occasion,
       so deleting it and editing it each have two readings, and third-party
       integrations trip over precisely that. A table of its own also settles
       batch identity for free: one series **is** one bulk action, so
       [D40](../docs/planning/decisions/D40-undo-a-pattern-batch.md)'s
       `PatternBatch` needs no second table on this side.

    ⭐ **The picture lives here and every occasion reads it** (`Event.poster`).
       This was a written-down gap for about an hour — the reasoning being that
       `purge_event_image()` deletes the file once an event is over, so twelve
       occasions sharing a path would break the morning after the first one
       ended. That reasoning was right about sharing a **path** and wrong about
       the conclusion: the fix is to own the file here, and to move the purge
       question to "is the **last** occasion over" — the same sentence single
       events have always had, asked of the row that owns the file. See
       `services.series_with_images_to_purge()`.
    """

    AUDIENCE_ON = "series"
    # The template pair, one level up from Event × EventRole. ⚠️ These two are
    # what make the containment invariant reach this table at all — see
    # refuse_bad_audience()'s table, and Audience.AUDIENCE_PARENT for what the
    # string comparison they replaced could not say.
    AUDIENCE_PARENT = None
    AUDIENCE_CHILDREN = "roles"
    # ⭐ None: a template has no occasion of its own to be judged on. Its
    #    occasions do — and each generated `Event` carries its own start, so it
    #    is judged on its own day like any other event. Same answer Notice
    #    reaches by a different road (Audience.AUDIENCE_DAY).
    AUDIENCE_DAY = None

    name = models.CharField(max_length=200)
    ministry = models.ForeignKey(
        Ministry, on_delete=models.PROTECT, related_name="event_series")
    owner = models.ForeignKey(
        Contact, on_delete=models.PROTECT, related_name="event_series_owned")

    # An RFC 5545 RRULE **without** DTSTART — the two below carry that, so the
    # rule stays a thing you can read on its own. Validated in clean(); expanded
    # by events/recurrence.py, which is the only module that parses it.
    #
    # ⚠️ Capped like every other typed-in text column. TextField's max_length is
    #    a form-level cap rather than a database one (see core/limits.py), which
    #    is all that is wanted: a rule is typed, and a paste of a whole calendar
    #    export should get a sentence under the box rather than a 500.
    rule = models.TextField(
        max_length=SHORT_TEXT,
        verbose_name="How often",
        help_text="A repeat rule, e.g. FREQ=WEEKLY;BYDAY=TU;COUNT=12. It has "
                  "to say when it stops — UNTIL= a date, or COUNT= a number.",
    )
    starts_on = models.DateField(
        verbose_name="First one on",
        help_text="The date of the first occasion. Later ones follow the rule.",
    )
    # 🔴 A **wall-clock** time and a length, not two instants, and the reason is
    #    the one D33 section 2 gives for Shift: "every Tuesday at 19:00" is
    #    19:00 on both sides of a daylight-saving change. Stored as two aware
    #    datetimes and stepped forward by seven days, half the term lands an
    #    hour out — and nothing raises, because the calendar still says Tuesday.
    #    See events/recurrence.py for where the two are put back together.
    start_time = models.TimeField(verbose_name="Starts at")
    #: Shorter than this and it is almost certainly a mistyped duration rather
    #: than a very short meeting. See `clean()`.
    SHORTEST_SENSIBLE = datetime.timedelta(minutes=5)

    duration = models.DurationField(
        verbose_name="Runs for",
        help_text="How long each occasion lasts, written hours:minutes:seconds "
                  "— 2:00:00 for two hours.",
    )

    # ⚠️ These, the name and the audience are a **starting point**: they are
    #    copied onto each occasion as it is made, and occasions already made
    #    keep what they were made with. Changing them here therefore reaches
    #    only what has not been generated yet, which is not what a publisher
    #    expects — measured: rename and move a series, press Generate, and the
    #    evening somebody had signed up for still carries the old name and the
    #    old place beside three carrying the new ones.
    #
    #    🔴 The audience is the sharp one. Narrowing the series to staff-only
    #       does **not** narrow the occasions already published, so a publisher
    #       can believe they have closed something that is still visible to
    #       outsiders. Said in the help text rather than left to be found.
    #
    #    ⚠️ A written-down gap (participants.md §9) rather than fixed here: the
    #       real answer is the "this one / this and following / all of them"
    #       question every calendar asks, and that needs a page to ask it on.
    # ⭐ **Uploaded once, shown on every occasion** — shared, not copied. See
    #    `Event.poster` for why sharing rather than copying, and why sharing a
    #    path without this column would have been the worst of the three.
    #
    # ⚠️ Unlike the three fields below it, this one is **not** copied at
    #    generation: the occasions read it live, so changing it here changes
    #    what all of them show, including the ones already made. That is the
    #    behaviour somebody expects of a picture and not of a name, which is
    #    why they are treated differently and why both are said out loud.
    #
    # ⚠️ Purged when the **last** occasion is over, not the first — the same
    #    rule single events have always had, asked of the row that owns the
    #    file. `services.series_with_images_to_purge()`.
    image = models.ImageField(
        upload_to=Event.IMAGE_DIR, blank=True,
        verbose_name="Picture",
        help_text="Shown on every occasion this makes. Change it here and they "
                  "all change; an occasion with its own picture keeps that one.")
    location = models.CharField(
        max_length=200, blank=True,
        help_text="Copied onto each occasion as it is made. Changing it later "
                  "does not move occasions that already exist.")
    description = models.TextField(
        blank=True, max_length=LONG_TEXT,
        help_text="Copied onto each occasion as it is made. Changing it later "
                  "does not reach occasions that already exist.")
    # Decision 30 (2026-09-10): the occasions are born with whatever this says.
    #
    # ⚠️ Default draft, and the two defaults fail in opposite directions — the
    #    same argument written out on Event.requires_guardian_consent. A typo in
    #    a rule that publishes straight through is twelve wrong events sent to
    #    every outside volunteer at once; a typo in a rule that lands as drafts
    #    is a list somebody scrolls and fixes.
    #: The two states decision 30 reasoned about, and only those. ⚠️ The field
    #: was given `Event.Status.choices` — all five — so `Full`, `Wrapped up` and
    #: `Cancelled` came along unexamined, and none is a coherent thing to be at
    #: birth: the admin could stamp four events a month away as "wrapped up",
    #: ten seconds after creating them, with nothing refusing it. A decision's
    #: implementation must not be wider than its reasoning.
    BIRTH_STATUSES = [
        (Event.Status.DRAFT, "Drafts — nobody sees them until you publish"),
        (Event.Status.OPEN, Event.Status.OPEN.label),
    ]

    status = models.CharField(
        max_length=20, choices=BIRTH_STATUSES, default=Event.Status.DRAFT,
        verbose_name="Publish the occasions as",
    )
    requires_guardian_consent = models.BooleanField(
        default=True,
        verbose_name="Minors need a guardian's consent",
        help_text="Applies to every occasion this makes. Untick only when "
                  "under-18s may sign up on their own, like an adult.",
    )

    # "Stop it from today." D40 step ②: a batch whose occasions cannot all be
    # withdrawn keeps its rule and gets an end date instead of being deleted.
    #
    # ⚠️ A date rather than a flag, for the reason this project applies
    #    everywhere else: an ending is a date, not a deletion, and "when did we
    #    stop running this" is the question somebody asks in March.
    ended_on = models.DateField(
        null=True, blank=True,
        verbose_name="Stopped on",
        help_text="Set when the series was stopped early. No more occasions are "
                  "generated from this date onwards; the ones that already "
                  "happened stay.",
    )
    # 🔴 **When the batch was built, which is not when this row was created.**
    #    The undo window was measured from `created_at`, and decision 30
    #    guarantees the two differ: a series is born `draft` so that somebody
    #    can build it, look at it, and publish it — a workflow meant to take
    #    days. Measured: a series drafted a month ago and generated thirty
    #    seconds ago was refused with "This batch was built more than 7 days
    #    ago", on the one screen whose stated purpose is not saying false
    #    things.
    #
    # ⚠️ Two columns rather than a `PatternBatch` table, and D40 §2's test is
    #    what says so: a series **is** one batch, so the batch's three
    #    properties (who, when, undone) all fit on it. What did not fit is the
    #    assumption that the row's own birthday is one of them.
    generated_at = models.DateTimeField(null=True, blank=True)
    generated_by = models.ForeignKey(
        Contact, null=True, blank=True, on_delete=models.PROTECT,
        related_name="event_series_generated")
    undone_at = models.DateTimeField(null=True, blank=True)
    undone_by = models.ForeignKey(
        Contact, null=True, blank=True, on_delete=models.PROTECT,
        related_name="event_series_undone")

    # ⚠️ m2m_fields for the same reason Event's and EventRole's carry it: who
    #    something was published to is half the record, and simple-history does
    #    not track a ManyToMany unless it is named. Here it decides the audience
    #    of every occasion the rule produced, so the half that would go missing
    #    is the half that answers "why could these twelve be seen by outsiders".
    history = HistoricalRecords(m2m_fields=["visible_to_ministries"])

    objects = models.Manager.from_queryset(EventSeriesQuerySet)()

    class Meta:
        ordering = ["-starts_on", "name"]
        verbose_name_plural = "event series"
        # 🔴 **There is deliberately no `ended_on >= starts_on` check**, and the
        #    draft of this table had one. A test caught it on the first run:
        #    build a batch for next month, have one person sign up, undo it the
        #    same afternoon — the survivor keeps the series alive, `ended_on`
        #    goes to today, and the constraint refused an entirely ordinary act.
        #
        #    ⚠️ The mistake was reading these two columns as a tenure, the shape
        #       `Assignment` and `MinistryRole` have, where the two ends really
        #       do bracket one thing. They do not here: `starts_on` is where the
        #       rule is anchored, and `ended_on` says **stop generating after
        #       this date**. A cut-off earlier than the anchor is a real and
        #       sayable state — "we stopped it before any of it happened" — and
        #       it is what undoing a future batch means.
        #
        #    ⚠️ Written down rather than silently dropped, because "the two
        #       dates must be in order" is the first thing anybody reading this
        #       table will reach for, exactly as this draft did.
        constraints = [
            models.CheckConstraint(
                condition=models.Q(duration__gt=datetime.timedelta()),
                name="eventseries_duration_is_positive",
                violation_error_message=(
                    "Say how long each occasion runs — it has to be more "
                    "than nothing."),
                violation_error_code="eventseries_duration_not_positive",
            ),
        ]

    def __str__(self):
        return f"{self.name} · {self.rule}"

    def clean(self):
        """The rule parses, it stops on its own, and it stops soon enough.

        Three refusals rather than one, because they send the reader to three
        different places: a rule that will not parse is a typo in the syntax, a
        rule with no ending is a decision nobody made, and a rule with five
        thousand occasions is a number somebody meant to be twelve.

        ⚠️ **"It must say when it stops" is the price of not materialising on a
           rolling window**, and it is a deliberate disagreement with
           [D33 section 3](../docs/planning/decisions/D33-work-schedule.md),
           which gave shifts a rolling window plus a weekly cron. The test is
           whether the thing has a natural end: a course has twelve meetings, a
           standing post's rota has none. A rolling window here would ask the
           foundation to maintain a never-ending mechanism for something that
           ends by nature; a forced end date there would make every post's dates
           be retyped once a year. Both sides say this; see D33's own note.

        ⚠️ A hint layer, not a rule — the D14 caveat this file carries in three
           other places. `EventSeries.objects.create(...)` walks past it, and no
           CheckConstraint can replace it: parsing an RRULE is not something a
           database check can do.
        """
        super().clean()
        # ⚠️ **First.** The freeze asks whether this row may change at all;
        #    everything below asks whether the new values agree with each other.
        #    Asked the other way round, somebody editing a frozen rule was told
        #    that their new weekday disagrees with the start date — a complaint
        #    about the merits of a change that was never going to be accepted,
        #    landing on a field they had not touched.
        self._refuse_rewriting_the_rule()
        if (self.duration is not None
                and datetime.timedelta() < self.duration < self.SHORTEST_SENSIBLE):
            # 🔴 `DurationField` reads a bare number as **seconds**, so an admin
            #    typing `2` for "two hours" got fifty-two two-second events and
            #    no error at all — the CheckConstraint only catches zero and
            #    below. The number they meant is three orders of magnitude away
            #    from the one they got, and nothing on the page would have said
            #    so until somebody opened an event.
            raise ValidationError({"duration": (
                f"That comes to {self.duration}. Write it as "
                "hours:minutes:seconds — 2:00:00 for two hours, 0:30:00 for "
                "half an hour."
            )})
        if not self.rule:
            return
        # 🔴 **Readable first, then finite** — and the order was the other way
        #    round, which made the commonest wrong input get the wrong
        #    diagnosis. `has_an_ending()` is a substring test, so plain English
        #    ("every tuesday") contains no `COUNT=` and was told "This rule
        #    never stops. Say when it ends" — advice about a rule that is fine
        #    apart from its ending, given to somebody whose input is not a rule
        #    at all. They add an ending in English and get the same sentence.
        #
        #    ⚠️ `recurrence.has_an_ending()`'s own docstring spells out this
        #       exact hazard in the opposite direction ("把后者说成前者会让人
        #       反复去检查语法") and this code did the mirror image of it.
        if "DTSTART" in self.rule.upper():
            # 🔴 An inline `DTSTART:` **beats** the one the expander passes, so
            #    a pasted calendar block silently overrode both boxes above:
            #    the page said "First one on 22 Sep, starts at 19:00" and the
            #    rule produced four evenings at 09:00. The date half is now
            #    caught by the check further down; the **time** half was not,
            #    and would not have been — nothing compares it.
            #
            # ⚠️ This is what makes `recurrence.py`'s "an RRULE **without**
            #    DTSTART" true rather than aspirational, and it is the other
            #    half of the paste that `UNTIL=…Z` fixes: both are what happens
            #    when somebody copies a real export out of a real calendar.
            raise ValidationError({"rule": (
                "Take the DTSTART line out and keep only the FREQ=… part. When "
                "it starts is the two boxes above this one, so the rule can be "
                "read on its own — and a DTSTART here would quietly win over "
                "what they say."
            )})
        if not looks_like_a_rule(self.rule):
            raise ValidationError({"rule": (
                "That does not look like a repeat rule. It has to be typed in "
                "the calendar format — FREQ=WEEKLY;BYDAY=TU;COUNT=12 means "
                "every Tuesday, twelve times."
            )})
        # ⚠️ **没有结束的规则是合法的**（2026-09-11，L5.9）。这里原来有一条
        #    「This rule never stops. Say when it ends」的拒绝，撤掉了：
        #    生成不再一次把整条规则铺完，而是按一年的窗口滚
        #    （`services.HORIZON_MONTHS`），所以「永远」不再意味着「一次造出
        #    无穷多行」。结束一条无限规则的正路是「即日停止」——
        #    `services.stop_series_today()`，它本来就是系列的出口。
        if self.starts_on is None or self.start_time is None:
            return
        try:
            found = occasions(
                self.rule, starts_on=self.starts_on, start_time=self.start_time)
        except (ValueError, TypeError) as unreadable:
            # ⚠️ dateutil raises both, and which one depends on how the string
            #    is malformed. Caught together because the reader's next move is
            #    the same either way, and the original text is handed on: it
            #    names the part it choked on, which is more than this sentence
            #    can work out.
            raise ValidationError({"rule": (
                f"That is not a repeat rule this understands ({unreadable}). "
                "It looks like FREQ=WEEKLY;BYDAY=TU;COUNT=12."
            )}) from None
        if not found:
            # ⚠️ Two causes, two sentences. `COUNT=0` is finite, parses, and
            #    means "none" — sending that person off to check their start
            #    date against their weekdays is advice about a problem they do
            #    not have.
            if "COUNT=0" in self.rule.upper().replace(" ", ""):
                raise ValidationError({"rule": (
                    "COUNT=0 means no occasions at all. Set it to how many "
                    "times this should run."
                )})
            raise ValidationError({"rule": (
                "That rule produces no occasions at all. Check the first date "
                "against the days it repeats on."
            )})
        if self.pk is None or not self.occasions.exists():
            behind = [m for m in found if local_date_of(m) < local_today()]
            if behind:
                # 🔴 **Occasions in the past are made and cannot be taken back.**
                #    A rule anchored twelve weeks ago generates nine evenings
                #    that already happened — published, with roles, counted by
                #    the ministry report as meetings that ran with nobody there
                #    — and `_collectable_occasions()` refuses to touch anything
                #    that has started, by design. So undo cannot reach them and
                #    the only way out is deleting nine rows by hand. Measured.
                #
                # ⚠️ Refused at the door rather than skipped by the generator,
                #    and that is the choice worth writing down: skipping is
                #    tempting and worse, because "why are there only three"
                #    would then have no answer anywhere on the page.
                #
                # ⚠️ Only while the series has no occasions. Afterwards the
                #    whole point is that the early ones are in the past, and
                #    this would refuse every later save of a healthy series.
                raise ValidationError({"starts_on": (
                    f"This rule falls on {len(behind)} date(s) that have "
                    f"already gone, the first on "
                    f"{local_date_of(found[0]):%-d %B %Y}. Generating would "
                    "create real events for evenings that have already "
                    "happened, and they cannot be undone afterwards — set the "
                    "first date to the next one you actually want."
                )})
        first = local_date_of(found[0])
        if first != self.starts_on:
            # 🔴 The label says "First one on" and the help text says "The date
            #    of the first occasion". Nothing checked it, so a Wednesday
            #    typed against a Tuesday rule was accepted and the first evening
            #    quietly landed six days later — and the undo confirmation
            #    screen, whose entire job is telling the truth about a batch,
            #    printed "first on 17 Sept" for a batch whose first occasion is
            #    the 22nd.
            raise ValidationError({"starts_on": (
                f"This rule's first occasion is "
                f"{first:%-d %B %Y}, not {self.starts_on:%-d %B %Y} — "
                f"{self.starts_on:%A}s are not one of the days it repeats on. "
                "Change one or the other so they agree."
            )})
        # 🔴 **密度，不是长度**（2026-09-11，L5.9 改）。这里原来问的是「整条规则
        #    一共多少场」，超过 52 就拒 —— 而那条拒绝挡住的是真实排法：每周一次
        #    跑一年半是 78 场。现在长度不再是问题，因为生成是**滚动**的：一次只
        #    排一年，明年再按一次。
        #
        #    剩下要挡的是另一件事：一条密到「连一年都装不下」的规则，
        #    一次点击就是几千行。高级框里一条 `FREQ=HOURLY` 是一年 8760 场。
        #
        # ⚠️ 所以判据是**窗口之内**有多少场，不是 `found` 有多少 —— `found` 对
        #    一条没有结束的规则会一直取到 `limit`（十四年的周二），拿它来判会
        #    把每一条无限规则都拒掉。
        #
        # ⚠️ 从 `found` 里筛而不是再展开一次：`found` 已经取到 `BATCH_CEILING + 1`
        #    场，而一条密到会被拒的规则，这么多场根本铺不满一年 —— 筛完仍然
        #    超标。稀疏的规则筛完就剩窗口内那几场。两种情况都答得对。
        window_ends = horizon_for(self.starts_on, local_today())
        within_the_window = [moment for moment in found
                             if local_date_of(moment) <= window_ends]
        if len(within_the_window) > BATCH_CEILING:
            raise ValidationError({"rule": (
                f"That repeats more often than this can build — it would make "
                f"more than {BATCH_CEILING} occasions in a single year, and "
                "every one of them is a real event with its own signups. "
                "Check the rule: this is usually a unit that slipped, such as "
                "hourly where daily was meant."
                # ⚠️ 措辞说的是**太密**，不是太长。原来那句是「more than 52
                #    occasions … build it in shorter runs」，而「分成几段短的」
                #    对一条每小时的规则是完全没用的建议 —— 它再短也是这个密度。
            )})

    #: The columns the generator reads to decide **when** its occasions fall.
    #: Frozen together, because they answer one question between them.
    GENERATION_FIELDS = ("rule", "starts_on", "start_time", "duration")

    def _refuse_rewriting_the_rule(self):
        """Once a rule has produced occasions, it is not edited in place. L5.6.

        🔴 **The decision was already written down and nothing enforced it.**
           06-roadmap L5.6: "改规则只动未来 = 老系列 `ended_on = today` + 新建一个
           系列（Google 的 split）。不做原地改规则重算：原地改会让「这一场当初是
           按哪条规则生成的」没有答案." `services.split_series()` is that path,
           and it has existed since the day this landed — with nothing standing
           in front of the other one.

        What the other one does, measured: move `start_time` from 19:00 to 20:00
        on a batch somebody has signed up for, press Generate. The evenings
        nobody had taken are withdrawn and re-made at 20:00; the evening with a
        volunteer on it **survives at 19:00**, because it may not be dropped —
        and a second event appears at 20:00 on the same night. One meeting, two
        events, a volunteer holding a place on one of them, and both standing on
        the list page. Nothing raises.

        ⚠️ Not fixable by matching occasions per day instead of per instant: the
           duplicate is the symptom, and the disease is that half the batch
           would then be generated under one rule and half under another, with
           the row itself claiming the new one. That is the exact sentence the
           roadmap refuses to give up.

        ⚠️ A hint layer, not a rule — the D14 caveat this file carries in four
           other places. `EventSeries.objects.update(rule=…)` walks past it, and
           no CheckConstraint can express it: the test is whether another table
           has rows pointing here.

        ⚠️ Everything else stays editable, deliberately: the name, the place, the
           description, the audience, the status of occasions still to come. What
           is frozen is only what decides **when** — see GENERATION_FIELDS.

    ⚠️ `split_series()` **has** a door — `EventSeriesAdmin.change_the_rule`,
       added once this refusal was found to be pointing at a control that did
       not exist. It stops this series and hands back an editable copy with no
       occasions yet, so every generation column on the copy is free.
        """
        if self.pk is None or not self.occasions.exists():
            return
        was = (type(self).objects.filter(pk=self.pk)
               .values(*self.GENERATION_FIELDS).first())
        if was is None:
            return
        changed = [name for name in self.GENERATION_FIELDS
                   if was[name] != getattr(self, name)]
        if not changed:
            return
        raise ValidationError({changed[0]: (
            "This rule has already produced occasions, and people may have "
            "signed up for them. Changing when they fall would leave the ones "
            "somebody has taken standing at the old time with new ones beside "
            "them. Stop this series instead, and build the next one — what has "
            "already happened stays either way."
        )})

    @property
    def is_undone(self):
        """Has this batch been withdrawn? D40's list greys these out."""
        return self.undone_at is not None


class EventSeriesRoleQuerySet(AudienceQuerySetMixin, models.QuerySet):
    """Nothing of its own — the mixin is the whole point of it existing.

    ⚠️ **Not** `EventRoleQuerySet`, though this table copies that one column for
       column. Every predicate over there counts signups and asks about room,
       and a template has neither: `with_signup_counts()` would join through an
       `event` this table does not have. Sharing it would be one import that
       reads as reuse and fails at the first call.

    ⚠️ And not "no queryset at all": `AudienceIsWiredUpTests` refuses a table
       that carries an audience and cannot be narrowed by one, because that
       failure is silent — the rows simply go out to everybody. Nobody browses
       a template today, and the guard is right not to take that on trust.
    """


class EventSeriesRole(Audience, ConstraintErrorFieldMixin, TimeStampedModel):
    """A job the rule opens on **every** occasion it produces. L5.4.

    The template for `EventRole`, column for column, and generation turns one of
    these into one real role on each generated event.

    ⚠️ Its audience is the one thing here that is not merely copied forward: it
       has to be no wider than the series' own, or the twelve events it produces
       are twelve breaches of the L2×L3 invariant at once. That check happens
       **here**, at publish time, and not at generation time — see
       `refuse_bad_audience()`, which reaches this table through
       `AUDIENCE_PARENT`. Left to generation it would fail halfway through a
       batch, with some occasions already written.
    """

    AUDIENCE_ON = "series_role"
    AUDIENCE_PARENT = "series"
    AUDIENCE_CHILDREN = None
    # None for the same reason the series' own is: a template has no day. The
    # generated roles are judged on their event's day, exactly as EventRole says.
    AUDIENCE_DAY = None

    series = models.ForeignKey(
        EventSeries, on_delete=models.CASCADE, related_name="roles")
    role = models.ForeignKey(
        ParticipationRole, on_delete=models.PROTECT, related_name="+")
    needed_count = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="How many people this job wants, on each occasion. Leave "
                  "empty for no limit.",
    )
    stop_at_needed_count = models.BooleanField(
        default=True,
        verbose_name="Stop signups at this number",
        help_text="Untick if more people than that are welcome — the number "
                  "then says what you are aiming for, and nobody is refused.",
    )
    notes = models.TextField(blank=True, max_length=SHORT_TEXT)

    # ⚠️ Present for the reason EventRole's is: `needed_count` is a promise
    #    published to volunteers, and here it is that promise made on every
    #    occasion at once. Its absence would leave the template as the one row
    #    in this pair whose changes nothing recorded.
    history = HistoricalRecords(m2m_fields=["visible_to_ministries"])

    objects = models.Manager.from_queryset(EventSeriesRoleQuerySet)()

    class Meta:
        ordering = ["series", "role__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["series", "role"],
                name="eventseriesrole_unique_per_series",
                violation_error_message=(
                    "This series already opens that role on every occasion."),
                violation_error_code="eventseriesrole_duplicate",
            ),
            models.CheckConstraint(
                condition=(models.Q(needed_count__isnull=True)
                           | models.Q(needed_count__gt=0)),
                name="eventseriesrole_needed_count_is_positive",
                violation_error_message="Leave the number empty for no limit; "
                                        "otherwise it has to be at least 1.",
                violation_error_code="eventseriesrole_needed_count_not_positive",
            ),
        ]

    def __str__(self):
        return f"{self.role.name} @ {self.series.name}"


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
