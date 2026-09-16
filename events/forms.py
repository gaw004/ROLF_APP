"""Forms for the self-service and ministry-admin pages.

Permanent assets: plain django.forms, no admin import (there is a guard), and
every one of them takes its context as an explicit keyword argument rather than
reaching into a request. Phase C's views construct the same classes unchanged.
"""

import datetime

from django import forms
from django.forms.forms import DeclarativeFieldsMetaclass
from django.utils.text import get_text_list
from django.core.exceptions import ValidationError
from django.conf import settings
from django.db.models import Q

from contact.models import Contact, EmergencyContact, RelationshipType
from core.images import decode_complaint_for, is_new_upload
from core.address import postal_code_field, state_field, strip_text
from core.limits import LONG_TEXT, PHONE, SEARCH, SHORT_TEXT
from core.timeutils import day_start
from django.utils.timezone import localtime
from org.audience import Audience
from org.forms import AudienceFormMixin
from org.models import Ministry
from org.permissions import in_foundation_tier, ministry_ids_administered_by

from . import schedule
# ⚠️ The picker below asks `recurrence` to build and read the rule string
#    rather than spelling RRULE here: that module owns the syntax, and a
#    second speller of it is how the preview and the save start disagreeing.
from .recurrence import (
    BATCH_CEILING,
    MAX_INTERVAL,
    MONTHLY,
    ORDINALS,
    WEEKDAYS,
    WEEKLY,
    compose,
    decompose,
    occasions,
)
from .models import (
    NARROWING_MESSAGE,
    PARENT_NOUN,
    NATURE_EXPLANATIONS,
    NATURE_INVITATIONS,
    SERVED_AS_EXPLANATIONS,
    Event,
    EventRole,
    EventSeries,
    EventSeriesRole,
    Participation,
    ParticipationRole,
    Session,
    askable_served_as,
    refuse_wider_than_event,
    roles_left_behind,
)


class RoleChoiceField(forms.ModelChoiceField):
    """The role dropdown, with "— full" on the ones that cannot take anybody.

    ⚠️ Full roles are **listed, not hidden** (2026-08-19). Dropping them would
       leave somebody looking at a dropdown missing the job they came to do,
       with nothing on the page saying why — and an event whose roles are all
       full would show an empty box. Saying "full" answers the question the
       absence would raise.

    ⚠️ This is the label only. The refusal itself is `services.sign_up()`'s, and
       it has to be: this form is not the only door (an admin entering somebody
       from a paper list meets the same rule), and the last place can go between
       this page being drawn and the button being pressed.
    """

    def label_from_instance(self, obj):
        label = super().label_from_instance(obj)
        return f"{label} — full" if obj.is_full else label


class MeetingChoiceField(forms.ModelMultipleChoiceField):
    """The meetings tick-list, labelled by when each one meets.

    ⚠️ A label override and nothing else — same shape and same reason as
       `RoleChoiceField` above. `Session.__str__` is "{event} · {date} {time}",
       which is right in the admin (where a meeting is loose in a table of every
       run's meetings) and wrong here: on one course's own signup page it prints
       that course's name twelve times and pushes the date — the only part being
       chosen between — to the end of each line.

    🔴 These boxes are where somebody **first** sees the individual dates, and
       for a participant they are the only place. The detail page deliberately
       withholds the meetings table from anybody who cannot read the event's
       records (`events.views._detail`): that table carries the check-in screen
       entrances, which are an administrative action, and "when is this course"
       is answered for a participant by the summary on the When line. So a label that reads
       badly here is not untidy, it is somebody ticking boxes blind.
    """

    def label_from_instance(self, obj):
        # ⚠️ Through `schedule.clock()`, not a format string of its own. "7pm"
        #    rather than "07:00 PM" is a decision that page already made, and a
        #    second spelling of a time-of-day is how two screens come to
        #    disagree about what the same meeting says.
        start = localtime(obj.start_time)
        return (f"{start:%a %-d %b} · "
                f"{schedule.clock(start)}–{schedule.clock(localtime(obj.end_time))}")


class SignUpForm(forms.Form):
    """Pick a role, and — for a minor — record the guardian's consent.

    The consent half is shown only when it applies, but it is never the form
    that decides whether consent was required: services.sign_up() judges that,
    because the same rule has to hold for an admin registering somebody from a
    paper list. The form only decides what to draw.
    """

    event_role = RoleChoiceField(queryset=EventRole.objects.none(), label="Role")

    # The short path, and the one most minors will take: their emergency
    # contact is already on file, so re-typing a guardian's name and number at
    # every single signup is asking for the same information twice — and the
    # copy typed in a hurry is the one that will be wrong on the day.
    use_emergency_contact = forms.ModelChoiceField(
        queryset=EmergencyContact.objects.none(), required=False,
        label="Use an emergency contact as the consenting guardian",
        help_text="Pick one and you only need to say how consent was given.",
    )

    consent_given_by = forms.CharField(
        max_length=200, required=False, label="Guardian's name")
    consent_relationship = forms.ModelChoiceField(
        queryset=RelationshipType.objects.filter(usable_as_emergency_contact=True),
        required=False, label="They are the participant's…",
    )
    # ⚠️ At least one of these two. Consent carrying only a *name* satisfies the
    #    paperwork and leaves P6 with no address to send anything to, so the
    #    signup would go in already guaranteed to be unreachable.
    consent_email = forms.EmailField(required=False, label="Guardian's email")
    # ⚠️ max_length is not decoration here: a plain CharField has **no** upper
    #    bound at all, and this one is copied into Participation.consent_phone,
    #    which is varchar(200). Without it an overlong number passes validation
    #    and fails at the INSERT — a 500 on the ordinary signup path.
    consent_phone = forms.CharField(
        required=False, max_length=PHONE, label="Guardian's phone")
    # Declared last because it is asked last: it applies to **both** paths
    # through this form — the emergency-contact shortcut still has to say how
    # consent was given — so it sits after the branch rather than inside it.
    #
    # `required=False` here and switched on in __init__ when consent actually
    # applies. Marking it required at class level would demand it from adults
    # too, for whom the whole section is hidden.
    consent_method = forms.ChoiceField(
        choices=[("", "---------"), *Participation.ConsentMethod.choices],
        required=False, label="How consent was given",
    )
    # D38. Drawn only for somebody the question applies to, and __init__
    # deletes it outright for everybody else — see there.
    #
    # ⚠️ The choices are built from the model's labels plus the gloss beside
    #    them in events/models.py, never typed out here. This wording appears
    #    on four screens and D38 section 6 is its only home; a copy in a form
    #    file is how it comes to say something slightly different in one place.
    # ⚠️ Built from askable_served_as(), **not** from ServedAs.choices. The
    #    enum has a third member (not_applicable) that nobody may be offered,
    #    and iterating the choices here would not merely show it — it would
    #    raise KeyError on the gloss lookup below, in a class body, so the app
    #    would stop importing. That is the good version of this mistake; the
    #    bad version is the two places in views.py that would have shown it.
    served_as = forms.ChoiceField(
        choices=[
            (value, f"{label} — {SERVED_AS_EXPLANATIONS[value]}")
            for value, label in askable_served_as()
        ],
        widget=forms.RadioSelect,
        required=False,
        label="How were you serving this time?",
        # ⚠️ One sentence, and it is doing real work. An event may open both
        #    kinds of role at once, and this question is drawn per form rather
        #    than per option — so somebody picking a place to attend answers it
        #    and the service then ignores the answer. Saying so is cheaper and
        #    steadier than making the question appear and disappear as the
        #    dropdown changes, which would be browser-side state (D24).
        help_text="Only asked about roles where you are giving your time.",
    )

    # Decision 17, and drawn only on a run published as one people choose
    # between — see `ask_sessions` in __init__.
    #
    # ⚠️ Tick-boxes, not a multi-select list: the same reason the audience group
    #    on EventForm is drawn that way. Every option has to be visible at once,
    #    because what is being compared is dates against a calendar somebody
    #    holds in their head.
    #
    # ⚠️ `required=False` here and enforced in `clean_sessions()` instead. The
    #    field only exists on some runs, so a class-level `required=True` would
    #    be a rule about a field that is usually deleted; and the message wanted
    #    is about **this** decision, not "This field is required."
    sessions = MeetingChoiceField(
        queryset=Session.objects.none(), required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Which meetings will you come to?",
        help_text="Tick the ones you can make. You can only pick from the "
                  "meetings that have not happened yet.",
    )

    CONSENT_FIELDS = [
        "consent_given_by", "consent_relationship", "consent_method",
        "consent_email", "consent_phone",
    ]

    def __init__(self, *args, event, contact, **kwargs):
        # event and contact are explicit keyword arguments, not something dug
        # out of a request — that is what lets the tests build this form
        # directly and Phase C reuse it untouched.
        super().__init__(*args, **kwargs)
        self.event = event
        self.contact = contact
        self.fields["event_role"].queryset = (
            # ⚠️ `with_signup_counts()` 是为了那个 "— full" 后缀能问出答案来
            #    （2026-08-19）。不带它的话每个选项各查一次，而这里正好是一个
            #    循环里的每一行。
            #
            # ⚠️ `for_audience()` 是 L2（2026-08-29）。角色**按人过滤掉**，
            #    不是列出来附一句「你报不上」—— 需求 8 原文写的是 internal
            #    roles「只会显示给 internal 的人」，而 participants.md 第三节
            #    那条 🔴 写的是「在角色这一层，看得见 = 报得上」。
            #    ⚠️ 于是它也是那道服务层资格门的**前哨**：手工构造的 POST 在
            #       这里就得到一条普通的 "Select a valid choice"，而不是走到
            #       `sign_up()` 里换一个 500 回来。
            event.roles.with_signup_counts().for_audience(contact)
            .select_related("role").order_by("role__name")
        )
        # Decision 17. The publisher decides whether this question exists at
        # all; until it was drawn, ticking that box on the publish form produced
        # a course somebody could sign up to and end up on the register of
        # nothing — no error, and nowhere to choose.
        #
        # ⚠️ The options come from `_meetings_still_to_come()`, the same
        #    predicate `open_register()` uses to decide which rows to make. Asked
        #    twice with two spellings, the page would offer a meeting the service
        #    then refuses to enrol them in — and the two would drift apart on the
        #    day somebody edits either one.
        from .services import _meetings_still_to_come

        self.ask_sessions = (event.shape == Event.Shape.PROGRAM
                             and event.people_pick_meetings)
        if not self.ask_sessions:
            # ⚠️ Deleted rather than hidden, for the reason spelled out on
            #    `served_as` below: a hidden field posts its value back, and a
            #    signup for an ordinary run must not carry this name at all.
            #    `sign_up()` refuses it regardless; this is so the page is
            #    honest, not so the data is safe.
            del self.fields["sessions"]
        else:
            self.fields["sessions"].queryset = _meetings_still_to_come(event)

        # Asked through services, so the form and the two service-layer gates
        # cannot answer it differently — an event that waives the rule must
        # waive it on the page too, or the boxes are drawn and then ignored.
        from .services import consent_required_for

        # ⚠️ Asked through services, exactly like consent below it: whether the
        #    question applies and what it defaults to are one answer, and this
        #    form is not allowed to work either half out for itself (D38
        #    section 5).
        from .services import default_served_as, is_on_the_books

        # ⚠️ The question is per **role** now (2026-08-21), and this form covers
        #    several roles at once. It is drawn when any role in the dropdown
        #    would ask it; if the role finally chosen turns out to be one that
        #    would not, services.sign_up() discards the answer and records
        #    not_applicable. The field's help text says so on the page — the
        #    alternative, making the question appear and disappear as the
        #    dropdown changes, is browser-side state (D24) for one sentence.
        #
        # ⚠️ "Is this person on the books" is asked once and handed to each
        #    call. Left to default_served_as() it would be a query per role.
        #    The judgement itself does not move: that function is still the only
        #    place that decides, exactly as D38 section 5 requires.
        on_the_books = is_on_the_books(contact, event)
        answers = [
            default_served_as(contact, role, on_the_books=on_the_books)
            for role in self.fields["event_role"].queryset
        ]
        asked = [value for value, ask in answers if ask]
        self.ask_served_as = bool(asked)
        self.served_as_default = asked[0] if asked else ""
        if not self.ask_served_as:
            # ⚠️ Deleted, not hidden — unlike the consent fields below, which
            #    stay as hidden inputs. A hidden field posts its value back,
            #    and an outside volunteer's form must not carry this name at
            #    all. services.sign_up() re-checks regardless; this is so the
            #    page is honest, not so the data is safe.
            del self.fields["served_as"]
        else:
            # ⚠️ Pre-*selected*, not pre-filled: both options are drawn and one
            #    is already chosen. A default that is not shown is a statement
            #    made on somebody's behalf without telling them, which is the
            #    whole of D38 section 4. Compare the hours box on the
            #    attendance page, where a pre-filled number is indistinguishable
            #    from one a human checked.
            self.fields["served_as"].initial = self.served_as_default
            self.fields["served_as"].required = True

        self.needs_consent = consent_required_for(contact, event)
        if self.needs_consent:
            self.fields["use_emergency_contact"].queryset = (
                contact.emergency_contacts.select_related("relationship_type")
            )
            # ⚠️ Genuinely required, not just marked with a star.
            #
            #    services.sign_up() has always refused a signup whose consent
            #    carries no method — so the field was **already** compulsory,
            #    and the form simply did not say so. The cost of that gap is a
            #    whole round trip: submit, get bounced by the service layer,
            #    and read the complaint attached to a different field. Saying
            #    it here puts the error under the box that caused it.
            #
            #    The service-layer check stays. It guards the other callers —
            #    an admin entering somebody from a paper list meets the same
            #    rule, and that path never touches this form.
            self.fields["consent_method"].required = True
        else:
            for name in [*self.CONSENT_FIELDS, "use_emergency_contact"]:
                self.fields[name].widget = forms.HiddenInput()

    def clean_sessions(self):
        """At least one, on a run that asks the question at all.

        🔴 Not `required=True` on the field, and not silence either. Signing up
           for a course and ticking nothing puts somebody on the register of
           **no meetings** — the signup exists, the course looks joined, and
           there is nothing to attend. That is a state this round has already
           shipped once by accident (a run people pick from used to enrol them
           in every meeting that happened to exist; it now enrols them in none),
           and both halves of it are the same mistake: a signup that means
           nothing, arrived at without anybody being told.

        ⚠️ The sentence names the decision rather than the field, because "This
           field is required" answers a question about a form, and the person is
           asking a question about a course.
        """
        chosen = self.cleaned_data.get("sessions")
        if self.ask_sessions and not chosen:
            raise forms.ValidationError(
                "Tick at least one meeting — signing up without choosing any "
                "would put you on the register for none of them."
            )
        return chosen

    def consent(self):
        """The consent kwargs for sign_up(), or None for an adult.

        ⚠️ consent_relationship is a foreign key, so an empty one has to be
           None and never "". Assigning "" to a relation raises ValueError, and
           leaving the relationship blank is both allowed and common — which
           made this a 500 on the ordinary path rather than an exotic one.
        """
        if not self.needs_consent:
            return None

        kin = self.cleaned_data.get("use_emergency_contact")
        if kin is not None:
            # Copied, not referenced. Participation's consent columns are a
            # record of what was agreed on the day it was agreed; pointing at
            # the emergency contact instead would rewrite last March's consent
            # the moment somebody edits their profile. Same rule as hours and
            # as the notification message snapshot.
            return {
                "consent_given_by": kin.name,
                "consent_relationship": kin.relationship_type,
                "consent_method": self.cleaned_data.get("consent_method") or "",
                # ⚠️ 2026-08-05：email 也复制过来了。原来这里写死成 "" ——
                #    那时 EmergencyContact 没有 email 列，所以这条路上的家长
                #    只能收短信。现在它有了，而 P6 优先走 email。
                "consent_email": kin.email,
                "consent_phone": str(kin.phone),
            }

        empty = {"consent_relationship": None}
        return {
            name: self.cleaned_data.get(name) or empty.get(name, "")
            for name in self.CONSENT_FIELDS
        }


class EventAudienceFormMixin(AudienceFormMixin):
    """The half of the audience rules that is about an event and its roles.

    ⭐ Split out of `org.forms.AudienceFormMixin` on 2026-08-31, and the seam is
       worth stating because it is not "generic vs. specific" in the vague
       sense. What travels is a rule an audience obeys **alone** — somebody
       ticked at least one box, and did not tick two boxes that mean the same
       thing. What stays is a rule about a **pair of rows**: a role may not be
       open to people its event is not.

       ⚠️ The arithmetic of that comparison would travel fine. Its sentences
          would not — `TOO_WIDE_STEM` reads "This event is not open to …, so a
          role inside it cannot be either", and there is no third table that
          sentence is true of. A rule whose message has to be reworded per
          table is a rule that belongs to its tables.

    ⚠️ Both halves sit on a mixin rather than on EventForm, because the admin
       edits events *and* roles through one AudienceAdminForm — put either on
       the concrete form and the admin loses that half of the invariant, which
       is the hole L2.1 found for the other two rules.
    """

    def refuse_narrowing_below_the_roles(self, event):
        """L2×L3, asked from the **event's** side: a role must not outlive a narrowing.

        ⚠️ On the mixin rather than on EventForm, because the admin edits events
           through AudienceAdminForm and would otherwise have this half of the
           invariant missing — the same hole L2.1 found for the other two rules.
           EventRoleForm inherits it too and simply has no roles of its own, so
           the loop below runs zero times.

        ⚠️ Names the roles. "That is not allowed" leaves somebody looking at
           five roles with no idea which two are the problem — and the fix is on
           a different page.

        ⚠️ `roles.all()` raises outright on an unsaved instance (verified), and
           a new event has no roles anyway, so the pk check is not defensive
           tidiness.

        ⚠️ The walk itself — the query hints included — is models.roles_left_behind(),
           shared with services.set_audience(). This side groups and prints;
           that side stops at the first. Written out at both until 2026-08-27,
           which had put one N+1 fix in two places.
        """
        if getattr(self.instance, "pk", None) is None:
            return
        # ⚠️ Asked for by name rather than assumed, the same correction
        #    `submitted_event()` below records (2026-09-10). It happens to be
        #    `roles` on all three tables that have children, so this one was
        #    never wrong — but "right by coincidence" is not a property worth
        #    keeping when the declaration is right there. A table whose children
        #    are called anything else would have had this half silently skipped.
        children = self.instance.AUDIENCE_CHILDREN
        if children is None:
            return
        roles = getattr(self.instance, children, None)
        if roles is None:
            return
        # ⚠️ Keyed by the tick **and** the people, not by the tick alone. The
        #    tick decides which box the sentence hangs under; the phrase decides
        #    which sentence, because two roles under one box can be blocked for
        #    different reasons — one naming Tax Help and one naming Youth Work —
        #    and a single sentence covering both would name neither.
        #
        # ⚠️ Grouping on the phrase is also what keeps the ordinary case short.
        #    Three roles blocked by the same ministry are one sentence naming it
        #    once; per-role parentheses would print "(Tax Help)" three times,
        #    which reads as three problems rather than one.
        noun = PARENT_NOUN.get(self.instance.AUDIENCE_ON, "event")
        blocked = {}
        for field, audience, name in roles_left_behind(event, roles, parent=noun):
            blocked.setdefault((field, audience), []).append(name)
        for (field, audience), names in blocked.items():
            self.add_error(field, ValidationError(
                # ⚠️ The stem is models.NARROWING_MESSAGE, shared with the
                #    service door so the two read alike; only the tail differs,
                #    because only this side can offer somewhere to go next.
                NARROWING_MESSAGE + " Narrow %(those)s first.",
                params={
                    "roles": ", ".join(f"“{name}”" for name in names),
                    "audience": audience,
                    # ⚠️ "event" or "series", from the row itself. The sentence
                    #    used to say "event" flat, which is a false noun on the
                    #    series page — see models.TOO_WIDE_STEM.
                    "parent": noun,
                    # One blocked role is the commonest case by a distance, and
                    # "Narrow those roles first" for a single one reads as
                    # though something has been missed.
                    "those": "that role" if len(names) == 1 else "those roles",
                },
            ))

    def submitted_event(self):
        """The row this one may not be wider than, wherever it is coming from.

        Three doors put it in three places and only one of them is the
        instance, which is why this is a method rather than an attribute read:

        · the site's own EventRoleForm sets `instance.event` in __init__;
        · the admin's add page has it in `cleaned_data` (ModelForm only copies
          it onto the instance in `_post_clean`, which runs after clean());
        · an inline under the Event add page has it in `cleaned_data` too, put
          there by Django's InlineForeignKeyField — as the **unsaved** parent.

        ⚠️ Returns None on a form editing a parent rather than a child, which is
           how EventForm inherits this pair and does nothing with it.

        🔴 **The attribute is asked for, never assumed** (2026-09-10). This read
           `"event"` as a literal in both places, so the check was reachable
           only by a table that calls its parent `event`. `EventSeriesRole`
           calls it `series`, and the failure was silent in the worst available
           direction: the admin inline saved a template role open to outsiders
           on a staff-only series, returned 302, and every occasion that rule
           went on to make carried the breach. Found by a test written for this
           page — the same hole the service side had, and the service side was
           fixed first, which is exactly how a second copy of one rule comes to
           be half-mended.
        """
        parent = self.instance.AUDIENCE_PARENT
        if parent is None:
            return None
        submitted = (self.cleaned_data.get(parent)
                     if hasattr(self, "cleaned_data") else None)
        # ⚠️ getattr, because Django raises RelatedObjectDoesNotExist here on an
        #    unset FK — and that is an AttributeError, so this reads as absent
        #    rather than as an error. The 🔴 below is what that cost.
        return submitted if submitted is not None else getattr(
            self.instance, parent, None)

    def refuse_wider_than_its_event(self, role):
        """L2×L3, asked from the **role's** side. The other half of the pair above.

        ⚠️ Also on the mixin, and for the same reason: the admin edits roles
           through AudienceAdminForm. EventForm inherits it and has no event of
           its own to compare against, so it does nothing there.

        ⚠️ `Spec.of(event)` reads the event's saved audience, which is correct
           **here** whenever the event already exists: a role added to a saved
           event is judged against what that event actually is. It would be
           wrong in refuse_narrowing_below_the_roles(), where the event's own
           audience is the thing being changed.

        🔴 Two doors reach this with no saved event behind them, and until
           2026-08-28 **both went through silently** — the failure this project
           keeps convicting, a rule that reads as though it ran:

           · `/admin/events/eventrole/add/`. The event arrives in
             `cleaned_data`, not on the instance, because ModelForm copies the
             FK across in `_post_clean` — *after* clean(). And the read was
             `getattr(self.instance, "event", None)`, where Django raises
             `RelatedObjectDoesNotExist`, a subclass of **AttributeError**, so
             getattr swallowed it and returned None. Reproduced: a role saved
             visible to outsiders on a ministry-only event, 302, no error.
           · The Event add page with inline roles. There the event is the
             unsaved parent, so `submitted_audience` below is the only thing
             that knows what it is going to be — and the other half of the pair
             (refuse_narrowing_below_the_roles) returns early on `pk is None`,
             so with this one silent too the invariant had nobody watching it.
        """
        event = self.submitted_event()
        if event is None:
            return
        # ⚠️ The parent's **submitted** audience when there is one, and only
        #    then the saved row. On the admin's Event *add* page the event does
        #    not exist yet: there is no row to read, and Spec.of() would raise
        #    outright on the unsaved instance's ManyToMany. See
        #    clean_audience(), which is what puts it there.
        spec = getattr(event, "submitted_audience", None)
        if spec is None:
            if getattr(event, "pk", None) is None:
                return
            spec = Audience.Spec.of(event)
        try:
            refuse_wider_than_event(
                event=spec, role=role,
                parent=PARENT_NOUN.get(event.AUDIENCE_ON, "event"))
        except ValidationError as error:
            # ⚠️ `None`, so Django distributes the error by the keys the rule
            #    put in it. Naming a field here would override what the rule
            #    knows and put every refusal back on one box.
            self.add_error(None, error)


class AudienceAdminForm(EventAudienceFormMixin, forms.ModelForm):
    """The admin's form for anything carrying an audience.

    🔴 Without it the admin has **no audience check at all** — the two rules
       cannot live in Model.clean() (a ManyToMany is written after save() while
       full_clean() runs before it; the working is in org/audience.py above
       refuse_empty_audience()), and the admin builds its own forms.

    ⚠️ It lives here rather than in admin.py so that admin.py keeps holding no
       logic of its own (D18) — over there it is one `form = ` line. And it
       reuses the mixin rather than repeating the two calls, so the admin and
       the site can never come to different conclusions about the same event.
    """

    class Meta:
        widgets = {"visible_to_ministries": forms.CheckboxSelectMultiple}

    def clean(self):
        cleaned = super().clean()
        audience = self.clean_audience()
        if audience is not None:
            # ⚠️ Both halves, because the admin edits events **and** roles
            #    through this one form. On a role the loop finds nothing; on an
            #    event it is the only thing standing between a narrowing and a
            #    role left open to people who can no longer see it.
            self.refuse_narrowing_below_the_roles(audience)
            self.refuse_wider_than_its_event(audience)
        return cleaned


#: What a publisher is asked about the thing itself, whichever of the three
#: shapes they picked. ⚠️ The list lives here rather than being typed into two
#: `Meta.fields`, because ten field names written twice is ten chances for the
#: two screens to drift — and this repository's answer to "two forms ask the
#: same questions" has been a mixin since `AudienceFormMixin` (which serves
#: five tables the same way).
SHARED_PUBLISH_FIELDS = (
    "name", "ministry", "location",
    # ⚠️ 紧跟着 `location`，因为它们回答的是同一个问题的两半：房间在哪一栋楼里。
    #    ⚠️ 代价如实说：表单是**逐个字段平铺**渲染的（`form_fields.html`），
    #       所以活动编辑页因此长了四行。分组要动 `drawn_separately` 那套机制，
    #       而那是「几个字段彼此有关系、选了哪一档决定下面问什么」才用的东西 ——
    #       这四个之间没有那种关系，硬套会把一个简单的东西说复杂。
    "address_street", "address_city", "address_state", "address_postal_code",
    "status", "requires_guardian_consent",
    # L3. Right after the lifecycle fields and before the prose, because
    # "who is this for" is a publishing decision rather than a detail.
    *Audience.AUDIENCE_FIELDS,
    "description", "image",
)

#: The three answers to "what are you publishing". ⚠️ Two of them are values of
#: `Event.Shape`; the third is not a value of anything, because recurring
#: events are a **generator** rather than a state an event can be in — see
#: `Event.Shape`'s docstring. It is offered here anyway because decision 21 is
#: about the publisher's question, and to them there are three answers.
PUBLISH_AS_SERIES = "series"


class PublishFormMixin(EventAudienceFormMixin):
    """Everything a publish form asks that does not depend on which shape it is.

    ⭐ Extracted 2026-09-10 so `EventForm` and `EventSeriesForm` can be the same
       screen. They differ in **one block** — when the thing happens — and agree
       on the other ten fields, on the picture pipeline, on which ministries the
       dropdown may offer, and on how the audience starts out.

    ⚠️ `EventForm`'s behaviour is unchanged by the extraction: every line below
       was moved out of it, not rewritten. That is worth stating because a mixin
       that quietly changes the form it was lifted from is the expensive kind.
    """

    #: The fields that answer "when does this happen" — the **only** block the
    #: three-way radio swaps (decision 32). Each shape names its own, because
    #: they are not the same question: an event happens at two moments, a rule
    #: happens every so often for a length.
    WHEN_FIELDS = ()

    #: Does the "when" block still get hand-drawn once the thing exists?
    #: ⚠️ False here and True only on `EventSeriesForm` — see its own note.
    WHEN_BLOCK_ON_EDIT = False

    @property
    def drawn_separately(self):
        """The fields the publish page lays out itself; see `form_fields.html`.

        ⭐ Only when adding. The publish page draws the radio and the "when"
           block as one unit because choosing a shape decides what the block
           asks — a relationship a flat list of fields cannot express. The two
           edit pages (`event_update`, `series_detail`) have no radio and no
           block: there is nothing to choose, the shape is already settled, so
           they render every field flat and this must be empty for them.

        ⚠️ Both halves of that are load-bearing, in opposite directions. Name a
           field here that the page does not draw and it **vanishes** from the
           page. Fail to name one it does draw and it is rendered **twice**,
           which is the quiet one: the second, empty copy wins on submit.
           `NoFieldIsDrawnTwiceTests` pins both, on all three pages.
        """
        if self.instance.pk is None:
            return ("publish_as", *self.WHEN_FIELDS)
        # ⚠️ On an edit page the radio is gone, but the "when" block may still
        #    need hand-drawing: `EventSeriesForm`'s picker is a block whose
        #    boxes depend on each other, and it is the same block on both
        #    pages. `EventForm`'s two moments are flat fields, so it says no.
        return tuple(self.WHEN_FIELDS) if self.WHEN_BLOCK_ON_EDIT else ()

    def clean_image(self):
        """Re-encode the upload, or refuse it.

        ⚠️ 2026-08-05 更正：这段注释原来写的是「大小检查发生在 Pillow 打开文件
           **之前**」。**那是错的** —— Django 的 `ImageField.to_python()` 早在
           `clean_image()` 被调用之前就已经 `Image.open()` + `verify()` 过一遍了。
           一条测试当场抓出来：喂一个超大的假文件，回来的报错是 Django 的
           「不是有效图片」，而不是这里的大小提示。

           所以真正挡住解压炸弹的是 **Pillow 自己的 `MAX_IMAGE_PIXELS`**，不是
           下面这个比较。这里这一条管的是**存储和带宽**：一张 40 MB 的原图能被
           解码，但不该被接收。两件事，别再把它们写成一件。

        ⚠️ On the mixin since 2026-09-10, because the series upload is shown by
           every occasion it makes — so an unstripped phone photo here publishes
           somebody's home GPS on N pages instead of one.
        """
        uploaded = self.cleaned_data.get("image")
        # An unchanged field hands back the stored FieldFile, which has already
        # been through this and must not be re-encoded on every save.
        if not is_new_upload(uploaded):
            return uploaded
        if uploaded.size > settings.EVENT_IMAGE_MAX_UPLOAD_BYTES:
            raise forms.ValidationError(
                f"That image is larger than "
                f"{settings.EVENT_IMAGE_MAX_UPLOAD_BYTES // (1024 * 1024)} MB. "
                f"Most phone photos are well under it.")
        # 🔴 **The check the one above cannot make.** What a decode costs is
        #    the pixel count times a number that depends on how the file was
        #    written, and neither factor is the file size. Measured 2026-09-09:
        #    a 1.48 MB WebP of 8000×6192 needs 762 MB to open and passes the
        #    byte limit with 8.5 MB to spare. Header read only, nothing decoded
        #    — see `core.images.decode_complaint_for`.
        #
        # ⚠️ The complaint names the **format** as well as the size, because
        #    "too many pixels" sends somebody off to resize a file that would
        #    have been fine saved another way. There is also no single pair of
        #    dimensions to name — 8000×6000 and 12000×4000 are both refused and
        #    neither is "too wide" — which is why the sentence is built from
        #    this picture rather than from a constant.
        #
        # ⚠️ `EVENT_IMAGE_MAX_EDGE`, because that is what `normalise_event_image`
        #    is about to draft to and a baseline JPEG's cost is set by it. The
        #    two disagreeing does not raise; it just prices the upload on an
        #    arithmetic the decode will not use.
        from .services import EVENT_IMAGE_MAX_EDGE, normalise_event_image

        complaint = decode_complaint_for(uploaded, EVENT_IMAGE_MAX_EDGE)
        if complaint:
            raise forms.ValidationError(f"That image {complaint}")

        return normalise_event_image(uploaded)

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        administered = ministry_ids_administered_by(user)
        # 🔴 **foundation tier 拿全部**（2026-09-16，D48）。它持不了
        #    `MinistryRole`，所以 `administered` 对它是空集 —— 不分这一支的话，
        #    它打得开发布页、而那一格里**一个 ministry 都没有**：一张永远提交
        #    不了的表单，页面上没有任何东西说得出为什么。
        # ⚠️ `is_active=True`：停办的 ministry 不该再收到新活动。ministry admin
        #    那一档不用这个条件 —— 他的 `MinistryRole` 本来就挂在一个在办的
        #    ministry 上。
        # ⚠️ 这里收窄的是**下拉框**，而它防的是手滑；防越权的是视图里对提交值
        #    再判一次 `can_publish_event()`。两件事，都要有（见类的 docstring）。
        self.fields["ministry"].queryset = (
            Ministry.objects.filter(is_active=True).order_by("name")
            if in_foundation_tier(user)
            else Ministry.objects.filter(id__in=administered))

        # --- 地址那两格（2026-09-16，D50）---------------------------------
        #
        # 🔴 **换在这里，于是三张发布表单一起拿到**（`EventForm`、
        #    `ProgramForm`、`EventSeriesForm`）—— 一处改，三处生效。
        #    各自改一份的话，漏掉的那一张会安安静静地继续收自由文本，
        #    而它存下来的地址在地图上打不开，**不报任何错**。
        #
        # ⚠️ 州是一个**真的** `<select>`（`core.address.state_field()`），
        #    不是 `contact` 那边那段 JS 换出来的下拉 —— 关掉脚本它照样是下拉。
        #    两处为什么不一样，整段写在 `core/address.py` 的模块注释里。
        #
        # ⚠️ **不加 `address_country`。** `Event` 上没有那一列是有意的
        #    （`events/models.py` 那段 🔴 写了理由），而这一批做的正是**美国
        #    地址**的校验 —— 两件事一致，不是矛盾。
        #
        # ⚠️ 两格都 `required=False`，而模型上它们是 `blank=True`：
        #    「地址可以晚一点补」是那一段注释定的，**不许顺手改成必填**。
        self.fields["address_state"] = state_field()
        self.fields["address_postal_code"] = postal_code_field()
        # ⚠️ 另外两格的标签跟着改（2026-09-16 看着截图改的）。模型自动生成的是
        #    「Address street」「Address city」，而旁边两格现在写着「State」
        #    「ZIP code」—— 四格并排，两种命名法。上面那句 `location` 的说明
        #    已经说清了这几格是街道地址，标签里再带一遍 "Address" 是噪音。
        #    🔴 改的是**表单**的标签，不是模型的 `verbose_name`：那两列
        #       `contact.Contact` 上也有，一字不差是有意的（见模型注释），
        #       而这里是这一页怎么说话。
        self.fields["address_street"].label = "Street"
        self.fields["address_city"].label = "City"
        # ⚠️ 那一格是「楼里的哪一间」，不是地址 —— 模型注释里那句话今天只有读
        #    代码的人看得到，而填表的人正对着它。
        self.fields["location"].help_text = (
            "The room or spot inside the building — Chapel, Room 2B, "
            "Back garden. The street address goes in the boxes below.")

        # ⭐ Nothing else is pre-ticked, and that is the expensive decision of
        #    this form. Defaulting to everyone would match today's behaviour and
        #    make the migration free — and its failure mode is publishing a
        #    leaving party to every outside volunteer because somebody did not
        #    change a default. Nothing raises; nobody finds out.
        #
        #    The one exception is their own ministries, and it goes the other
        #    way: it is the **narrowest** useful start, and an internal event is
        #    the commonest reason to be narrowing at all.
        #
        # ⚠️ Only when adding. On an edit the stored audience is the answer, and
        #    re-ticking their own ministry would quietly widen an event somebody
        #    had deliberately narrowed.
        if self.instance.pk is None:
            self.initial.setdefault("visible_to_ministries", list(administered))
        else:
            # ⚠️ The radio is a question about what to **create**, so it has no
            #    answer on an edit page: an event cannot become a repeat rule,
            #    and a rule cannot become one evening. Left in place it would
            #    render a live-looking three-way choice that silently does
            #    nothing — and on `EventForm` it would sit next to `shape`
            #    asking nearly the same question with a different answer.
            self.fields.pop("publish_as", None)

    def clean(self):
        cleaned = super().clean()
        # ⚠️ 首尾空白去掉 —— `"NY "` 和 `"NY"` 在地图查询和将来任何一次去重上
        #    都是两个值，而屏幕上一模一样。整段理由在 `core.address.strip_text`。
        strip_text(cleaned)
        audience = self.clean_audience()
        # ⚠️ Only when the audience itself is usable. An event ticked for nobody
        #    is narrower than every one of its roles, so going on would bury the
        #    one real fault under a list of its own consequences.
        if audience is not None:
            self.refuse_narrowing_below_the_roles(audience)
        return cleaned


class EventForm(PublishFormMixin, forms.ModelForm):
    """P2: publish an event. The ministry dropdown lists only the ones they run.

    ⚠️ The dropdown is there to stop a slip, not to stop an attack — a POST can
       name any id. The view checks can_publish_event() on the submitted value
       as well; two different jobs, both needed.
    """

    class Meta:
        model = Event
        fields = [
            # L5.3. Before the two times deliberately: it changes what they
            # *mean*. On a course they are the two ends of a term, not one
            # sitting, and somebody who fills the dates in first has already
            # answered a different question.
            #
            # ⚠️ Two options here, three in the requirement. The third —
            #    recurring events, a weekly occasion each signed up for
            #    separately — is a generator (L5.4) rather than a value.
            #
            # ⭐ **The third option is offered by `publish_as` below**, not by
            #    this column — because it is not a value this column could
            #    hold. Picking it builds an `EventSeries`, and the view
            #    constructs `EventSeriesForm` from the same POST instead of
            #    this one. Requirement 4's second half, delivered 2026-09-10;
            #    decision 32 for why the radio stays on this one screen.
            # ⚠️ Composed, not retyped: `SHARED_PUBLISH_FIELDS` is the ten
            #    questions both shapes ask, and only the "when" block below is
            #    this form's own. Order is preserved by hand because `shape`
            #    has to come before the two times — see above.
            "name", "ministry", "shape", "people_pick_meetings",
            "start_time", "end_time",
            *(f for f in SHARED_PUBLISH_FIELDS if f not in {"name", "ministry"}),
        ]
        widgets = {
            # Radios, not a dropdown: two options that mean genuinely different
            # things have to be readable side by side, the same reasoning the
            # audience tick-boxes below are written down with. A closed select
            # shows one of them and hides the choice.
            "shape": forms.RadioSelect,
            "start_time": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "end_time": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            # Tick-boxes, not a multi-select list: every option has to be
            # visible at once for the containment between them to be readable.
            "visible_to_ministries": forms.CheckboxSelectMultiple,
            # `accept` is a semantic attribute, not styling — it tells the file
            # picker what to offer, the same exception type="date" gets under
            # phase-c.md's placement rules. It is a convenience and never a
            # check: clean_image() below is the check.
            "image": forms.ClearableFileInput(attrs={"accept": "image/*"}),
        }

    TIME_FIELDS = ("start_time", "end_time")

    #: An event happens between two moments; `people_pick_meetings` rides along
    #: because it only means anything once "a course" is the chosen shape.
    WHEN_FIELDS = ("start_time", "end_time", "people_pick_meetings")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk is None:
            # ⭐ On the publish page `publish_as` **is** this column — the view
            #    writes the chosen answer onto `shape` (see `event_create`).
            #    Asking twice would put two radios on one screen that disagree,
            #    and the lower one would win.
            #
            # ⚠️ Removed rather than hidden. A hidden `shape` would be a second
            #    answer travelling with the POST, and whichever of the two the
            #    view read last would be the one that counted.
            self.fields.pop("shape", None)

        # ⭐ 「人自己挑来哪几讲」只有**一期课**答得上（2026-09-11）。
        #    一场活动只有一个时刻，没有「哪几讲」可挑 —— 那一格摆在那儿是在问
        #    一个不存在的问题，而勾上它对单场活动什么都不做。
        #
        # ⚠️ 判据两种来源，因为这张表单服务两页：发布页上形状还没定，答案在
        #    `publish_as`（换档时 `publish_when` 会重建这张表单，所以它跟着变）；
        #    编辑页上形状已经是列了，答案在 `instance.shape`。
        #
        # ⚠️ 拿掉而不是藏起来。藏起来的话它照样随 POST 提交，于是一个「一场」
        #    活动可以带着一个为真的 `people_pick_meetings` 存进库里 —— 一个
        #    永远不会有人读、但确实在那儿的值。
        if self._shape_now() != Event.Shape.PROGRAM:
            self.fields.pop("people_pick_meetings", None)

    def _shape_now(self):
        """这张表单此刻在编辑哪一种形状。"""
        if self.instance.pk is not None:
            return self.instance.shape
        return (self.data.get("publish_as") if self.is_bound
                else self.initial.get("publish_as"))

    #: ⭐ Decision 21's three-way radio, and **not** a column on either model.
    #:    Two of its answers are `Event.Shape` values; the third builds an
    #:    `EventSeries`, which is a generator rather than a state an event can
    #:    be in (see `Event.Shape`). So it is the page's own control: the view
    #:    reads it to decide which ModelForm to construct, and neither form
    #:    stores it.
    #:
    #: ⚠️ Declared on both forms so its value survives a failed submission —
    #:    a publisher who mistyped a rule must come back to the page with
    #:    "every week" still selected, not silently returned to "one occasion".
    publish_as = forms.ChoiceField(
        required=False, widget=forms.RadioSelect,
        choices=[
            (Event.Shape.SINGLE, Event.Shape.SINGLE.label),
            (Event.Shape.PROGRAM, Event.Shape.PROGRAM.label),
            (PUBLISH_AS_SERIES, "Every week — each one signed up for separately"),
        ],
        label="What kind of event is this",
    )

    def time_changed(self):
        """Did this submission actually move the event?

        Asked here rather than in the view because the answer depends on what
        the widgets above can express, and that is this class's business. The
        view only decides what to do about it (notify everybody who signed up).

        ⚠️ Compared to the minute. datetime-local carries no seconds, so an
           event stored as 09:00:37 comes back from an untouched form as
           09:00:00 — and a plain != would call that a reschedule. Somebody
           correcting a typo in the location would mail every volunteer to say
           the time had changed, which is how people learn to ignore these.

        Reads self.initial, which ModelForm filled from the instance at
        construction; self.instance itself is overwritten during validation.
        """
        def to_minute(value):
            return value.replace(second=0, microsecond=0) if value else value

        return any(
            to_minute(self.initial.get(name)) != to_minute(self.cleaned_data.get(name))
            for name in self.TIME_FIELDS
        )


#: "This form was never asked to filter by the kind of role." (2026-09-08)
#:
#: ⚠️ `None` cannot carry that meaning, and the reason is not style: `None` is a
#:    **legitimate audience**. An account with no Contact is an outsider —
#:    org/audience.py says so in as many words, and every superuser is one — so
#:    a `None` doubling as "not asked" would quietly turn the filter off for
#:    exactly the accounts hardest to notice it on.
NO_AUDIENCE = object()


#: Every name this form's boxes can ever submit — including `nature`, which
#: only exists when an audience was passed.
#:
#: ⚠️ Declared **here**, beside the fields, because there is a second reader:
#:    `events.views.LIST_STATE` carries these onto an event's link and back off
#:    it again. Written out there as well, the two lists drift the first time a
#:    box is added — and this very batch proved it, having had to edit both
#:    files to add `nature`. The failure is silent: the new box simply stops
#:    surviving a click into an event and back.
#:    守卫：events.tests.RoleKindFilterTests.test_the_filter_names_are_declared_once
FILTER_PARAMS = ("q", "ministry", "nature", "kind", "start", "end")


#: 两种形状在用户面前怎么称呼 —— **一份**（2026-09-16）。
#:
#: 🔴 **`views.SIGNUP_KINDS` 从这里拼，不另敲一遍。** My Signups 那一排
#:    （决定 50）和管理列表那一格问的是同一个问题，而两份各写的后果是同一门课
#:    在两页上叫不同的名字 —— 一个每处都渲染正常、只有把两页并排才看得见的错。
#:
#: ⚠️ **「不筛」那一档不在这里**，而这不是遗漏：那一格在两页上是两个不同的东西。
#:    My Signups 上它是一个真的哨兵值（`?kind=all`，三颗按钮里必有一颗按下去）；
#:    这张表单上「没填」就是空串，和 `ministry` / `nature` / `q` 完全一样 ——
#:    于是 `filtered_by`、`_list_state`、Clear 一路都不用为它开特例。
#:
#: ⚠️ 值是 `events` / `programs`，**不是** `Event.Shape` 的 `single` / `program`。
#:    这两个词会进查询串、会被人从链接里读到，而 `single` 对着一门「课」是一个
#:    只有读过模型的人才懂的词。落到 queryset 那一步由 `narrow()` 翻译。
SHAPE_KINDS = (
    ("events", "Events"),
    ("programs", "Programs"),
)


#: 决定「什么时候」的那几格 —— 日期预览只看它们的错误。
#:
#: ⚠️ 一份名单，`EventSeriesForm` 和 `views.series_preview` 共用。分成两份的
#:    后果是静默的：预览那边漏掉一格，那一格填错时页面既不报错也不出日期，
#:    只会一直显示「填好上面几格」——对着一个已经填好的表单说。
WHEN_ANSWERS = (
    "repeat_mode", "repeat_every", "repeat_weekdays", "repeat_ordinals",
    "repeat_weekday", "ends_kind", "ends_after", "ends_on",
    "rule", "starts_on", "start_time", "duration",
)


class DurationBoxes(forms.MultiWidget):
    """「开多久」拆成三格：时 : 分 : 秒（2026-09-11）。

    🔴 **它换掉的那一格有一个不报错的陷阱，而模型里早就写着。**
       `DurationField` 把一个光秃秃的数字读成**秒**，所以有人想写「两小时」
       敲了个 `2`，得到的是五十二个**两秒**的活动，而且一句话都没有
       （`EventSeries.clean()` 里那条 🔴 注释记着这件事，它只挡得住零和负数）。
       三个格子让那个歧义不存在：2 填在「时」那一格里就是两小时。

    ⚠️ 底下仍然是那个 `DurationField`，不是新列、不是新字段类型 ——
       `value_from_datadict()` 把三格拼回 `"H:MM:SS"`，交给它自己去解析。
       所以校验、约束、admin 那一侧一个字都没动。

    ⚠️ 三个原生 `<input type="number">`，没有 JavaScript 照样能填（D24）。
    """

    template_name = "events/widgets/duration_boxes.html"

    def __init__(self, attrs=None):
        # ⚠️ `min="0"`：负的时长由数据库约束挡着，但让浏览器先拦一道，
        #    省掉一次往返。上限只给分和秒 —— 小时没有合理的上限可写。
        common = {"min": "0", "inputmode": "numeric", "class": "duration-box"}
        super().__init__([
            forms.NumberInput(attrs={**common, "aria-label": "Hours"}),
            forms.NumberInput(attrs={**common, "max": "59", "aria-label": "Minutes"}),
            forms.NumberInput(attrs={**common, "max": "59", "aria-label": "Seconds"}),
        ], attrs)

    def use_required_attribute(self, initial):
        """浏览器不要挨个格子要求填。

        🔴 `required` 会被 `MultiWidget` 发到**每一个**子部件上，于是「两小时」
           必须写成 `2` `0` `0` —— 分和秒留空，浏览器就拦下来，而那两格留空
           正是这个控件想让人能做的事。

        ⚠️ 这不是把校验关掉：三格全空时 `value_from_datadict()` 交回空串，
           `DurationField` 照常说「这一格是必填的」。挪掉的只有浏览器那一道。
        """
        return False

    def decompress(self, value):
        """一个 `timedelta`（或者上一趟提交的那个字符串）拆成三格。

        ⚠️ 字符串那一支不是多余的：校验失败重渲那一趟，Django 递进来的是
           `value_from_datadict()` 交出去的东西，也就是 `"2:00:00"`。
           少了它，一次填错会把人已经填对的时长也清空。
        """
        if not value:
            return [None, None, None]
        if isinstance(value, str):
            parts = value.split(":")
            return [*(part or None for part in parts[:3]),
                    *[None] * (3 - len(parts[:3]))]
        total = int(value.total_seconds())
        return [total // 3600, total % 3600 // 60, total % 60]

    def value_from_datadict(self, data, files, name):
        hours, minutes, seconds = super().value_from_datadict(data, files, name)
        if not any(str(part or "").strip() for part in (hours, minutes, seconds)):
            return ""
        try:
            return (f"{int(hours or 0)}:{int(minutes or 0):02d}"
                    f":{int(seconds or 0):02d}")
        except ValueError:
            # ⚠️ 拼不出来就把原样交回去，让 `DurationField` 自己说那句拒绝 ——
            #    在这里另写一句，就是同一个错误有两种说法。
            return ":".join(str(part or "") for part in (hours, minutes, seconds))


class RecurrencePickerMixin(metaclass=DeclarativeFieldsMetaclass):
    """「多久一次」那一块 —— 九个控件写一条 RRULE（2026-09-16 抽出来）。

    🔴 **那个 `metaclass=` 不是装饰，少了它这九格会被整个丢掉。**
       Django 收集表单字段时，只从「自己的类体」和**带 `declared_fields` 的
       基类**里取 —— 一个普通 mixin 两样都不是，于是它身上的
       `forms.ChoiceField(...)` 只是一个普通的类属性，`self.fields` 里一个都没有。
       ⚠️ 这一条是抽取当天实测撞上的：62 条测试同时红在
          `ValueError: 'EventSeriesForm' has no field named 'repeat_weekdays'`。
          **而它之所以吵，纯属运气** —— `clean()` 里恰好有一句
          `add_error("repeat_weekdays", …)`，Django 对着一个不存在的字段名会抛。
          没有那一句的话，这九格会安安静静地不出现在页面上，也不参与校验。
       ⚠️ 旁边的 `PublishFormMixin` **不需要**这个，因为它一个字段都不声明 ——
          它只有方法。差别在这里，不在「谁是 mixin」。

    ⭐ **两张表单共用，而它们存的东西完全不同。** `EventSeriesForm` 把这条规则
       存进 `EventSeries.rule` 一列，往后每年滚着生成场次；`ProgramForm` 用它
       **一次**就扔 —— 一门课把规则展开成一串 `Session` 之后，那个字符串就没有
       第二个读者了（D48b / D49）。共用的是「怎么问」，不是「存不存」。

    ⚠️ **整块是搬过来的，不是重写的**（原本长在 `EventSeriesForm` 上）。写下来
       是因为「抽出去之后悄悄改变了原来那张表单」是最贵的那种重构 ——
       `PublishFormMixin` 自己的 docstring 为同一件事写过一段。
       验收方式是那一次提交里 `events/tests.py` 关于系列的断言一条都没改。

    ⚠️ 用它的表单必须有 `rule` / `starts_on` / `start_time` / `duration` 四格。
       `rule` **故意不在这里声明**：`EventSeriesForm` 的那一格从 `Meta.fields`
       来（带着模型的 `max_length` 和 `help_text`），而 Django 里**声明字段压过
       Meta** —— 在这里声明一个 `rule` 会把它悄悄换掉。
    """

    #: 那一格底下那句话。⚠️ 子类必须给，因为名词不一样（场次／讲）。
    START_TIME_HELP = ""

    # --- the picker (2026-09-11) -------------------------------------------
    #
    # 🔴 **None of these is a column.** They are nine controls over the one
    #    `rule` string: `__init__` takes it apart for them, `clean()` puts it
    #    back together. Storing them would give "what does this rule say" two
    #    answers, and D14's whole point is that it may have one.
    #
    # ⚠️ Every one of them is an ordinary field with an ordinary widget, so the
    #    whole picker works with the scripts off (D24): the wheel is a real
    #    `<select>` and the day strip is seven real tick-boxes. The polish is
    #    added on top by JS and takes nothing away when it is missing.

    # ⚠️ `required=False` 贯穿这几格，而它们**都有安全的默认**（`clean()` 里的
    #    `or WEEKLY` / `or 1` / `or "count"`）。理由不是宽松，是那个「高级」框：
    #    勾了它就是不用选择器，而一个必填的 `ends_kind` 会让一条手写规则
    #    被「This field is required」挡下来 —— 指着一格那个人根本没看的控件。
    #    2026-09-11 走查实测到的。
    repeat_mode = forms.ChoiceField(
        choices=[(WEEKLY, "By week"), (MONTHLY, "By month")], required=False,
        initial=WEEKLY, widget=forms.RadioSelect, label="How often")
    repeat_every = forms.TypedChoiceField(
        choices=[(n, str(n)) for n in range(1, MAX_INTERVAL + 1)], required=False,
        coerce=int, initial=1, label="Every")
    repeat_weekdays = forms.MultipleChoiceField(
        choices=WEEKDAYS, required=False, widget=forms.CheckboxSelectMultiple,
        label="On these days")
    # ⚠️ Several, on purpose: "the first and third Saturday of the month" is a
    #    common way for a charity to run something, and a single-choice
    #    "which one" could not say it (2026-09-11).
    repeat_ordinals = forms.MultipleChoiceField(
        choices=ORDINALS, required=False, widget=forms.CheckboxSelectMultiple,
        label="Which ones")
    repeat_weekday = forms.ChoiceField(
        choices=WEEKDAYS, required=False, initial="SA", label="Day")

    #: ⚠️ 第三档 2026-09-11（L5.9）加的。在那之前「不结束」根本存不下来 ——
    #:    `EventSeries.clean()` 有一条「This rule never stops」的拒绝，
    #:    因为生成会把整条规则一次铺完。现在生成按一年的窗口滚，所以
    #:    「一直到我停掉它」是一个可以表达的、正常的安排。
    ends_kind = forms.ChoiceField(
        choices=[("count", "After a number of occasions"),
                 ("on", "On a date"),
                 ("never", "Never — until I stop it")], required=False,
        initial="count", widget=forms.RadioSelect, label="When it stops")
    ends_after = forms.IntegerField(
        required=False, min_value=1, initial=12, label="occasions")
    ends_on = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}),
        label="until")

    #: The escape hatch. ⚠️ A tick rather than a third mode on the radio above,
    #: because it answers a different question — not "what shape is this
    #: rule" but "ignore the picker, I have written one myself". One boolean,
    #: one winner, no guessing which control the save listened to.
    use_advanced = forms.BooleanField(
        required=False, label="Write the rule myself instead")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # ⚠️ Not required **on the form**, though the column is. The picker is
        #    what normally fills it, and `clean()` below is where it gets
        #    filled — so leaving this required would refuse every ordinary
        #    save with "This field is required" pointing at a box the
        #    publisher was never meant to type in.
        self.fields["rule"].required = False
        # ⭐ The one thing a repeat rule cannot do, said where somebody is
        #    about to run into it (2026-09-11). Picking several days makes it
        #    look as though each of them could be arranged separately, and the
        #    day this bites is the day a volunteer turns up at 19:00 to a
        #    Thursday that started at 10:00.
        #
        # ⚠️ On the form rather than on the model's `help_text`, so it appears
        #    on the two publisher pages without a migration for a sentence.
        #    The admin does not show it, and that is the right audience: this
        #    is advice about the picker, and the admin has no picker.
        # ⚠️ 措辞由子类给（`START_TIME_HELP`）：一条规则排的是「每一场」，
        #    一门课排的是「每一讲」，而这句话是写给正在填那一格的人看的。
        #    ⚠️ 中间那半句（几点开始 + 开多久 + 多个星期几是两条规则）两边一字
        #       不差，所以它在这里只有一份 —— 分成两份的话，哪天改了措辞，
        #       只有其中一页会改。
        self.fields["start_time"].help_text = self.START_TIME_HELP
        # ⚠️ 模型上那句说明写的是「written hours:minutes:seconds — 2:00:00 for
        #    two hours」，而它描述的是一格**已经不存在**的输入框。三个格子之后
        #    那句话不再需要，留着它比没有更糟 —— 页面上找不到它说的那个东西。
        self.fields["duration"].help_text = ""
        # ⚠️ `getattr`，因为 `rule` **只有 `EventSeries` 是一列**（2026-09-16
        #    抽 mixin 时撞上的）。课那一侧它是表单自己的一格，实例上没有它 ——
        #    写死 `self.instance.rule` 会在构造 `ProgramForm` 时当场
        #    `AttributeError`。⚠️ 默认给 `""` 而不是 `None`：下面那句
        #    `if ... or not rule` 对两者都成立，但空串说的是「没有存着的规则」，
        #    而 `None` 读起来像「不知道」。
        self._fill_the_picker_from(
            self.initial.get("rule") or getattr(self.instance, "rule", ""))

    def _fill_the_picker_from(self, rule):
        """Set the picker's controls from an existing rule string.

        ⚠️ Only when this form is **unbound**. On a bound form the controls
           carry what the person just submitted, and overwriting them with the
           stored rule would throw their edit away in front of them.

        ⚠️ A rule the picker cannot draw (`FREQ=DAILY`, `BYMONTHDAY=15`) is not
           an error and is never rewritten: the advanced box is ticked and the
           string is shown exactly as it is. See `recurrence.decompose`.
        """
        if self.is_bound or not rule:
            return
        answers = decompose(rule)
        if answers is None:
            self.initial.setdefault("use_advanced", True)
            return
        self.initial.setdefault("repeat_mode", answers["mode"])
        self.initial.setdefault("repeat_every", answers["every"])
        self.initial.setdefault("repeat_weekdays", answers["weekdays"])
        self.initial.setdefault("repeat_ordinals", answers["ordinals"])
        if answers["weekday"]:
            self.initial.setdefault("repeat_weekday", answers["weekday"])
        if answers["until"]:
            self.initial.setdefault("ends_kind", "on")
            self.initial.setdefault("ends_on", answers["until"])
        elif answers["count"]:
            self.initial.setdefault("ends_kind", "count")
            self.initial.setdefault("ends_after", answers["count"])
        else:
            # 两个都没有 = 不结束。⚠️ 少了这一支，一条自己刚建的无限规则
            #    再打开会显示成「共 None 场」。
            self.initial.setdefault("ends_kind", "never")

    def clean(self):
        """Build `rule` out of the picker, unless they asked to write it.

        ⭐ The composed string goes into `cleaned_data["rule"]` and nowhere
           else. `_post_clean()` copies it onto the instance and
           `EventSeries.clean()` judges it exactly as it judges a hand-typed
           one — so the picker cannot produce a rule the rest of the system
           would not accept, and the sentence a publisher sees is the same
           sentence either way.
        """
        cleaned = super().clean()
        if cleaned.get("use_advanced"):
            # ⚠️ Their string, untouched. The only thing to check is that they
            #    put something there — everything else is `EventSeries.clean()`.
            if not cleaned.get("rule"):
                self.add_error("rule", "Write the rule, or untick the box above.")
            return cleaned

        # 🔴 **Only the picker's own answers decide whether to build the rule.**
        #    The first version asked `if not self.errors`, which reads as
        #    caution and is wrong: `series_preview` runs this form while
        #    somebody is still typing, so `ministry` and `name` are routinely
        #    empty — and an error on either of those silently stopped the rule
        #    from being composed at all. The preview then showed "fill in the
        #    boxes above" to somebody who had just filled them in, with no
        #    complaint to act on. Found in the browser, 2026-09-11.
        picker_is_answered = True

        mode = cleaned.get("repeat_mode") or WEEKLY
        if mode == WEEKLY and not cleaned.get("repeat_weekdays"):
            # ⚠️ Refused rather than defaulted to the first date's weekday.
            #    `FREQ=WEEKLY` with no `BYDAY` is a legal rule meaning exactly
            #    that, so the quiet version would build a real batch out of a
            #    question nobody answered.
            self.add_error("repeat_weekdays", "Pick at least one day.")
            picker_is_answered = False
        if mode == MONTHLY and not cleaned.get("repeat_ordinals"):
            self.add_error("repeat_ordinals",
                           "Pick at least one — first, second, and so on.")
            picker_is_answered = False

        ends = cleaned.get("ends_kind") or "count"
        count = cleaned.get("ends_after") if ends == "count" else None
        until = cleaned.get("ends_on") if ends == "on" else None
        # ⚠️ `"never"` 两个都不给，于是 `compose()` 既不写 COUNT 也不写 UNTIL ——
        #    一条不结束的规则。它不需要校验任何东西，所以这里没有它的分支。
        if ends == "count" and not count:
            self.add_error("ends_after", "Say how many occasions to make.")
            picker_is_answered = False
        if ends == "on" and not until:
            self.add_error("ends_on", "Pick the date it stops on.")
            picker_is_answered = False

        if picker_is_answered:
            cleaned["rule"] = self.rule_to_store(compose(
                mode=mode, every=cleaned.get("repeat_every") or 1,
                weekdays=cleaned.get("repeat_weekdays") or [],
                ordinals=cleaned.get("repeat_ordinals") or [],
                weekday=cleaned.get("repeat_weekday"),
                count=count, until=until))
        return cleaned

    def rule_to_store(self, composed):
        """拼好的规则最终写成什么 —— 默认就是它本身。

        ⭐ 这是一个**钩子**，存在的唯一理由是 `EventSeriesForm` 要覆写它：
           那张表单上 `rule` 是一**列**，而列一变就会撞上
           `_refuse_rewriting_the_rule()`。一门课没有那一列，所以拼出来的
           字符串直接就是答案。
        """
        return composed


class MeetingForm(forms.ModelForm):
    """手工补一讲 —— Meetings 页上那张小表单。D49。

    ⚠️ **不是下面那个 `SessionForm`**，而两张并存是有意的（2026-09-16 撞了一次
       重名才写下来）：那一张是 **admin 的门**，带着 `event` 和 `source` 两格，
       并且在 `save()` 里自己开点名册 —— 因为 `ModelAdmin` 那条路不经过
       `services.add_session()`。这一张是**站点的门**，它把活儿交给
       `add_session()`，于是那两格在这里没有位置：
         · 「哪一门课」由地址决定（`/events/<pk>/meetings/`）——
           一个能指向别的课的下拉，就是一条要在视图里再判一次权限的路，
           而那一处判断迟早和页面那一处走散（同 D27 那条不变量）；
         · 「谁排的」由这条路本身回答：从这一页来的一律是 `MANUAL`。

    ⚠️ 落在学期之外的拒绝**不写在这里**：那是 `Session.clean()` 的规矩
       （「This run ends on …，所以一讲不能超过它」），而 `_post_clean()` 会
       原样把它搬到这张表单上。在这里再写一句，就是同一条规则有两种说法。
    """

    class Meta:
        model = Session
        fields = ["start_time", "end_time"]
        widgets = {
            "start_time": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "end_time": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }


class ProgramForm(RecurrencePickerMixin, EventForm):
    """发布一门课 —— 三档里的第二档，2026-09-16 才真的能排时间（D49）。

    🔴 **它和 `EventSeriesForm` 用同一个选择器，产出的东西却完全不同**，
       而这正是决定 16 那张表的第四格：

         · 一条 series 产出 **N 个互相独立的 `Event`**，每一场各自报名；
         · 一门课是 **一个 `Event` + N 个 `Session`**，报一次管一学期。

       所以这里**不存 `rule`**：规则在发布那一下展开成一串讲次，之后就没有第二
       个读者了（不滚动生成、不续排）。存下来就是「这门课什么时候上」有两个
       答案，而 D14 的整个要点是它只许有一个。

    🔴 **学期的两端不问，从生成出来的第一讲和最后一讲推**（见
       `services.publish_program()`）。问了就是同一件事有两个来源：有人把结束
       日期填到最后一讲之前，而 `Session.clean()` 会拒掉最后那几讲 —— 一个
       「我明明填了 12 次，只排出来 9 次」的页面。

    ⚠️ `start_time` 在这张表单上是一个**时刻**（每讲几点开始），在 `EventForm`
       上是一个**瞬间**。和 `EventSeriesForm` 撞的是同一个名字、同一个理由：
       视图读 `publish_as` 只建其中一张，两张永不同时构造。
    """

    #: ⚠️ 名词跟着页面走：这一页排的是「每一讲」。中间那半句和系列页一字不差，
    #:    而那一半只写在 `RecurrencePickerMixin` 上（见它的注释）。
    START_TIME_HELP = (
        "Every meeting starts at this time, and runs for the same length. "
        "Tuesdays at 19:00 and Thursdays at 10:00 is two courses, not one."
    )

    class Meta(EventForm.Meta):
        #: ⚠️ **减掉那两格**（`start_time` / `end_time`），因为它们是推出来的。
        #:    从 `EventForm.Meta.fields` 里减，不重抄一份 —— 重抄的话，
        #:    以后给发布页加一格，只有两张表单里的一张会长出来。
        fields = [f for f in EventForm.Meta.fields
                  if f not in {"start_time", "end_time"}]
        #: ⚠️ 跟着减掉那两个 widget，否则 Django 会为一个不在 `fields` 里的
        #:    名字准备 `datetime-local`，而这一页的 `start_time` 是时刻。
        widgets = {k: v for k, v in EventForm.Meta.widgets.items()
                   if k not in {"start_time", "end_time"}}

    #: 这一块由发布页自己画（`_publish_when.html` 的第三支）。
    #: ⚠️ 和模板必须逐字对上：多列一个名字那一格**消失**，少列一个它被画
    #:    **两遍**（后一个空输入覆盖前一个）。`NoFieldIsDrawnTwiceTests` 盯着。
    WHEN_FIELDS = (
        "repeat_mode", "repeat_every", "repeat_weekdays",
        "repeat_ordinals", "repeat_weekday",
        "ends_kind", "ends_after", "ends_on",
        "use_advanced", "rule",
        "starts_on", "start_time", "duration",
        "people_pick_meetings",
    )

    #: ⚠️ `rule` 在这里**是表单自己的一格**，不是列。`RecurrencePickerMixin`
    #:    故意不声明它（那会把 `EventSeriesForm` 从 `Meta` 来的那一格悄悄换掉），
    #:    所以两张表单各自提供。
    rule = forms.CharField(
        required=False, max_length=SHORT_TEXT,
        label="Repeat rule",
        help_text="RRULE syntax, e.g. FREQ=WEEKLY;BYDAY=WE;COUNT=12")
    starts_on = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}),
        label="First meeting")
    #: ⚠️ 覆盖 `EventForm` 那一格（它是 `datetime-local` 的瞬间）。这里只问
    #:    钟点 —— 哪一天由 `starts_on` 和规则决定。
    start_time = forms.TimeField(
        widget=forms.TimeInput(attrs={"type": "time"}),
        label="Each meeting starts at")
    #: ⚠️ 走 `DurationBoxes`（时:分:秒三格），连那条「`2` 被 `DurationField`
    #:    读成两秒」的陷阱一起躲掉 —— 理由整段写在那个 widget 上。
    duration = forms.DurationField(
        widget=DurationBoxes, label="Each meeting lasts")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 🔴 **「不结束」那一档在课上不存在，而理由是硬的**：`Event.end_time`
        #    是 `NOT NULL`，而它由**最后一讲**推出来 —— 一条不结束的规则没有
        #    最后一讲。不是「对课没意义」，是存不下来。
        # ⚠️ 改 `choices` 而不是另写一个字段：`_recurrence_picker.html` 里那句
        #    `{% for option in form.ends_kind %}` 因此自动只画两档，模板一个字
        #    不用改，而伪造一个 `ends_kind=never` 会被 `ChoiceField` 当场拒掉。
        self.fields["ends_kind"].choices = [
            (value, label) for value, label in self.fields["ends_kind"].choices
            if value != "never"]

    def clean(self):
        """把规则**当场展开一次**，而这一次就是保存要用的那一份。

        🔴 **不许另算第二遍。** 页面上那份预览、这里的校验、和
           `services.publish_program()` 真正落库的那一串，必须是同一个列表 ——
           各算一遍的结果是「页面说 12 讲，按下去排了 13 讲」，而两边各自都
           渲染正常。所以展开的结果留在 `cleaned_data["moments"]` 里交出去。

        ⚠️ 「写规则我自己来」那一档的校验**就是这次展开**。系列那边靠
           `EventSeries.clean()`（`rule` 在那儿是一列，模型判得了它）；
           课没有那张表，所以这里是它唯一的把关处。
        """
        cleaned = super().clean()
        rule = cleaned.get("rule")
        starts_on = cleaned.get("starts_on")
        start_time = cleaned.get("start_time")
        # ⚠️ 三格缺一就不展开，而**不报第二遍错**：缺的那一格自己已经说了
        #    「这一格是必填的」，在这里再说一句是同一件事的两种说法。
        if not (rule and starts_on and start_time):
            return cleaned
        try:
            moments = occasions(rule, starts_on=starts_on, start_time=start_time,
                                limit=BATCH_CEILING + 1)
        except (ValidationError, ValueError) as complaint:
            # ⚠️ 落在 `rule` 上：那是唯一可能写错的那一格（选择器拼出来的
            #    字符串走不到这里 —— 它拼得出来就一定展得开）。
            self.add_error("rule", f"That rule cannot be read: {complaint}")
            return cleaned
        if not moments:
            self.add_error("rule", "That rule does not fall on any date.")
            return cleaned
        if len(moments) > BATCH_CEILING:
            # ⚠️ 措辞照 `services.generate_occasions()` 那一句，因为它拦的是
            #    同一件事的同一个上限。
            self.add_error("ends_after", (
                f"That is more than {BATCH_CEILING} meetings. Nothing was "
                "scheduled — shorten the course and try again."))
            return cleaned
        cleaned["moments"] = moments
        return cleaned


class EventSeriesForm(RecurrencePickerMixin, PublishFormMixin, forms.ModelForm):
    """Publish a repeat rule — the third of the three shapes. L5.8a.

    ⭐ The same screen as `EventForm` and deliberately so (decision 32): a
       publisher answers one question — "what am I putting on?" — and there are
       three answers. Ten of the eleven things this asks are identical to the
       other two shapes and come from `PublishFormMixin`; only the block below
       differs, because only *when* differs.

    ⚠️ The two forms are never both constructed for one request. The view reads
       `publish_as` and builds one of them from the same POST, so `start_time`
       meaning a `datetime` on one and a `time` on the other cannot collide —
       said out loud because it reads like a trap.

    ⚠️ No `end_time`, and that is the whole shape of the difference: a rule does
       not happen at a moment, it happens repeatedly for a length. `duration` is
       how long each occasion lasts; `Event.end_time` is worked out from it when
       an occasion is generated (in absolute time — see
       `services.generate_occasions`).
    """

    # ⚠️ The same field object as `EventForm`'s, taken rather than retyped:
    #    the three choices and their wording are one list, and a publisher who
    #    submits a bad rule comes back to a page where "every week" is still
    #    the selected option.
    publish_as = EventForm.base_fields["publish_as"]

    #: ⚠️ 逐字保留 2026-09-11 那句 —— 一条规则排的是「每一场」。
    START_TIME_HELP = (
        "Every occasion starts at this time, and runs for the same length. "
        "Tuesdays at 19:00 and Thursdays at 10:00 is two rules, not one."
    )

    #: ⚠️ `start_time` is on both lists and is **not** the same field: a moment
    #:    on `EventForm`, a time of day here. Harmless because the two forms are
    #:    never both built for one request — written down because it reads like
    #:    a trap.
    WHEN_FIELDS = (
        "repeat_mode", "repeat_every", "repeat_weekdays",
        "repeat_ordinals", "repeat_weekday",
        "ends_kind", "ends_after", "ends_on",
        "use_advanced", "rule",
        "starts_on", "start_time", "duration",
    )

    #: ⭐ True, unlike `EventForm`'s. The picker below is a **block** — which
    #:    boxes to ask depends on the mode — so it has to be hand-drawn on the
    #:    rule's own page as well as on the publish page. `EventForm`'s two
    #:    moments are flat fields and stay flat when editing.
    WHEN_BLOCK_ON_EDIT = True


    def rule_to_store(self, composed):
        """The composed rule — or the stored one, when they mean the same thing.

        ⚠️ 2026-09-16 起这是 `RecurrencePickerMixin.rule_to_store()` 的覆写
           （原名 `_rule_for`）。**只有这张表单需要它**，理由就是下面这一段：
           `rule` 在这里是一**列**。

        🔴 **Without this, opening a series and saving anything at all can lock
           the publisher out of it.** `compose()` writes one canonical spelling,
           and several perfectly legal stored rules do not survive the round
           trip:

               FREQ=WEEKLY;INTERVAL=1;BYDAY=TU;COUNT=12  → INTERVAL=1 dropped
               freq=weekly;byday=tu;count=12             → upper-cased
               …;UNTIL=20261231T000000Z                  → converted to local

           All three mean exactly what they meant before. But `rule` is one of
           `GENERATION_FIELDS`, so `_refuse_rewriting_the_rule()` sees it change
           and refuses — and it refuses the **whole save**. Fixing a typo in the
           description would come back as "this rule has already produced
           occasions", pointing at a box the publisher never touched.

        ⚠️ Compared through `decompose()` rather than as strings, because the
           question is "do these two say the same thing", not "are they typed
           the same way". That is also why the `UNTIL=…Z` case is caught: both
           sides decompose to the same date.

        ⚠️ A stored rule the picker cannot read (`None`) falls through to the
           composed one — there is nothing to preserve, and the publisher is
           by definition using the picker rather than that rule.
        """
        stored = self.instance.rule
        if stored and self._mean_the_same(stored, composed):
            return stored
        return composed

    @staticmethod
    def _mean_the_same(stored, composed):
        """两条规则说的是不是同一件事 —— 日子的**顺序**不算数。

        🔴 **顺序这一维是 2026-09-11 代码评审补上的，而漏掉它就等于这层保护
           不存在。** `decompose()` 把 `BYDAY` 按它在字符串里出现的顺序交回一个
           **列表**，而页面上那组勾永远按 Mo…Su 的顺序回传。于是一条存成
           `BYDAY=TH,TU` 的规则，重拼出来是 `BYDAY=TU,TH` —— 两个列表不等，
           这个函数判它「变了」，`_refuse_rewriting_the_rule()` 于是拒掉**整次
           保存**。实测两条都中：`BYDAY=TH,TU` 和 `BYDAY=3SA,1SA`。

           而那正是上面那段 🔴 描述的锁死本身：改个说明，回来告诉你
           「这条规则已经生成过场次了」，指着一个你根本没碰的框。

        ⚠️ 只有 `weekdays` 和 `ordinals` 按集合比。别的字段（间隔、星期几、
           结束方式）顺序没有意义可言，把它们也放宽只会让这个函数少认出
           一种真正的改动。
        """
        first, second = decompose(stored), decompose(composed)
        if first is None or second is None:
            return False
        unordered = {"weekdays", "ordinals"}
        return all(
            set(value) == set(second[key]) if key in unordered
            else value == second[key]
            for key, value in first.items())

    class Meta:
        model = EventSeries
        fields = [
            "name", "ministry",
            # The "when" block. ⚠️ Before the rest for the reason `shape` sits
            # before the two times on the other form: it is the question this
            # shape exists to ask, and somebody who scrolls past it to fill in a
            # location has not yet said what they are publishing.
            "rule", "starts_on", "start_time", "duration",
            *(f for f in SHARED_PUBLISH_FIELDS if f not in {"name", "ministry"}),
        ]
        widgets = {
            # ⚠️ One line, not a textarea. `EventSeries.rule` is a `TextField`
            #    (it is capped at SHORT_TEXT, and the column type is not this
            #    form's business), and a ModelForm turns that into a box eight
            #    rows tall — on a page whose whole point is the four small
            #    questions under it, that box is the largest thing on screen
            #    and it holds a forty-character string that never wraps.
            "rule": forms.TextInput(),
            "starts_on": forms.DateInput(attrs={"type": "date"}),
            "start_time": forms.TimeInput(attrs={"type": "time"}),
            "duration": DurationBoxes,
            # Tick-boxes for the same reason `EventForm` gives: every option has
            # to be visible at once for the containment between them to read.
            "visible_to_ministries": forms.CheckboxSelectMultiple,
            "image": forms.ClearableFileInput(attrs={"accept": "image/*"}),
        }


class EventSeriesAdminForm(AudienceAdminForm):
    """The series' admin form: the audience rules, plus the picture pipeline.

    🔴 **Without `clean_image` a series picture skips the re-encode**, and the
       thing that matters about that re-encode is not the size. `Event.image`'s
       own comment says it: a phone photo carries GPS coordinates, and a
       picture taken at somebody's home would publish where they live to every
       signed-in user. `normalise_event_image()` strips EXIF; the series is the
       one upload this feature added, and it went round the back.

    ⚠️ Its occasions show this file (`Event.poster`), so an unstripped upload
       here is unstripped on every occasion the rule makes — the same
       multiplication the containment rule gets its urgency from.

    ⚠️ It reuses `PublishFormMixin.clean_image` rather than restating it: one
       pipeline, every door. The alternative is places that each nearly strip
       EXIF. ⚠️ Since L5.8a the site has its own `EventSeriesForm`, which gets
       the pipeline by inheriting the mixin; this one is the admin's.
    """

    clean_image = PublishFormMixin.clean_image


class EventPeriodForm(forms.Form):
    """R1: "how many events in this window", plus which ministry ran them.

    Lives here rather than in the view for the reason the grep guard states:
    a view holding date arithmetic gets rewritten along with the templates.
    Every box is optional, and that is what makes one form answer three
    different questions — "what is on next month", "what does the food pantry
    have open at all", and the two together.
    """

    # ⚠️ Matches the name **or** the location, and the second half is the point:
    #    somebody looking for "the one in the kitchen" remembers where it was, not
    #    what it was called. One OR over two indexed-enough columns; the pilot has
    #    tens of events, so there is nothing to optimise yet.
    #
    # ⚠️ Deliberately **not** the ministry's name. There is a ministry dropdown two
    #    boxes along, and a search that also matched it would empty the whole of
    #    Food Pantry onto the page for the word "food" — which reads as the search
    #    having been ignored.
    #
    # ⚠️ The label says what it matches. A placeholder would have said the same
    #    thing and then disappeared the moment somebody started typing.
    q = forms.CharField(
        required=False, max_length=SEARCH, label="Search by name or location",
        # `type="search"` is semantic, not styling — it is what gives a phone the
        # right keyboard and the browser its own clear button. Same exception
        # `type="date"` gets under phase-c.md's placement rules.
        # ⚠️ placeholder 是**语义**属性（同 `type="date"` 那条例外），不是样式：
        #    2026-09-15 起这一格的标签是 `sr-only`（一条式的版式里没有位置放它），
        #    所以框里那句提示是看得见的人唯一读得到的说明。
        #    ⚠️ 标签**没有删**，只是不显示 —— 读屏的人照旧听得到「Search by name
        #       or location」，而那句话比 placeholder 说得更全。
        #
        # 🔴 **那句话里的名词跟着页面走**（2026-09-15 review 抓到）：第一版写死了
        #    「Search events or locations」，于是 `/programs/` 上那个框 ——
        #    **唯一看得见的说明** —— 说的是「events」。正是这个分支另外四个
        #    commit 在拆的那种假名词，从一个新开的门走了回来。
        #    ⚠️ 真正的 placeholder 在 `__init__` 里按 `noun` 填（widget 在这里
        #       是类属性，构造时才知道自己在哪一页上）。
        widget=forms.TextInput(attrs={"type": "search"}),
    )
    start = forms.DateField(
        required=False, label="From",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    end = forms.DateField(
        required=False, label="Until",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    # ⚠️ Every active ministry, not "the ones with events in the current
    #    results". The narrower list reads better right up to the moment
    #    somebody picks a ministry and watches it vanish from the dropdown it
    #    was just chosen from — the options would then depend on the filter
    #    they are part of.
    # 🔴 **多选**（2026-09-15，用户定的）。「食物银行**和**报税援助」是一个真实
    #    的筛选，而单选说不出它 —— 同 `Audience` 那三个勾当初不做成一个三值枚举
    #    的理由（「那一句是一个 enum 说不出来的」）。
    #
    # ⚠️ **只有浏览那两页多选**（`/events/`、`/programs/`），管理列表和报表页
    #    仍然是单选 —— 见 `__init__` 的 `multi_ministry`。
    #
    # ⚠️ `ModelMultipleChoiceField` 交回来的是一个 **queryset**，不是一个对象。
    #    `description()` 和 `narrow()` 各自为此分了支，两处都写了理由。
    ministry = forms.ModelMultipleChoiceField(
        queryset=Ministry.objects.filter(is_active=True).order_by("name"),
        widget=forms.CheckboxSelectMultiple,
        # ⚠️ 多选这一档**没有 `empty_label`** —— `ModelMultipleChoiceField` 不收它
        #    （多选里没有「空选项」这回事，一个都不勾就是没筛）。
        #    那句「这一格管什么」由收起时按钮上的字承担，见 `ministry_label`。
        #    ⚠️ 单选那一档的 `empty_label="Ministry"` 在 `__init__` 里重建时给，
        #       连同它当初那条改口的理由：原来写的是「All ministries」（说的是
        #       「当前没筛」），改成「Ministry」（说的是「这一格管什么」），
        #       而「筛了什么」由底下那行 `Filtered by …` 承担。
        required=False, label="Ministry",
    )

    def __init__(self, *args, ministries=None, audience=NO_AUDIENCE,
                 noun="event", multi_ministry=False, by_shape=False, **kwargs):
        """`ministries` narrows the dropdown to a scope the page already has.

        `by_shape` (2026-09-16) 决定这张表单**有没有**「活动还是课」那一格。
        和 `audience` 同一条写法、同一条理由：不传的页面上根本没有这个字段，
        于是一个伪造的 `?kind=programs` **什么都不筛**，而不是筛掉一半、
        屏幕上却没有任何控件说明为什么。
        ⚠️ 只有管理列表传它。浏览那两页（`/events/` `/programs/`）是**互斥**的
           —— 那里「哪一种」由地址本身回答，再给一格只会问一个已经答过的问题。

        `audience` (2026-09-08) is the person the "kind of role" box is judged
        for. Passing it is what **creates** that box: pages that do not pass it
        have no such field, so a forged `?nature=helping` on the management list
        filters nothing rather than filtering something with no control on
        screen saying so.

        ⚠️ One keyword, not two (a flag plus a contact). Two would have to be
           kept in step by hand, and the way they come apart is silent: the flag
           on and the contact missing means the box is drawn and narrows by the
           wrong person's eligibility.
        ⚠️ `NO_AUDIENCE`, never `None` — see the sentinel's own note.

        ⚠️ Interface only — it is not a permission. The pages that pass it have
           already narrowed their **queryset**, so a forged ministry id in the
           query string filters a list that never contained that ministry's
           events and comes back empty. What this prevents is the other thing:
           a ministry admin offered every ministry in the foundation, picking
           one, and getting an empty list with nothing saying why (2026-08-05).
        """
        super().__init__(*args, **kwargs)
        # 🔴 **搜索框那句提示里的名词跟着页面走**（2026-09-15 review 抓到）。
        #    自 2026-09-15 起这一格的标签是 `sr-only`，所以这句 placeholder 是
        #    **看得见的人唯一读得到的说明** —— 写死「events」的话，`/programs/`
        #    上那个框说的就是错词，而那正是这个分支另外四个 commit 在拆的东西。
        #    ⚠️ 在这里填而不是在字段定义上：widget 是类属性，构造时才知道自己
        #       在哪一页上。
        self.fields["q"].widget.attrs["placeholder"] = (
            f"Search {noun}s or locations")
        if ministries is not None:
            self.fields["ministry"].queryset = ministries
        # 🔴 **单选那一档换回 `ModelChoiceField`**（2026-09-15）。
        #    多选只给浏览那两页；管理列表和报表页仍然是单选，因为 `description()`
        #    —— 那句印在**报表正文**上、被人当口径读的话 —— 说的是「一个
        #    ministry」，而让它说得出 N 个名字是另一件事（用户当时判的「只改浏览
        #    列表」）。
        #
        #    ⚠️ **字段名一个字没改**（仍然是 `ministry`），所以
        #       `test_the_filter_names_are_declared_once` 那条守卫照旧成立 ——
        #       `FILTER_PARAMS` 和 `LIST_STATE` 都不用动。
        if not multi_ministry:
            self.fields["ministry"] = forms.ModelChoiceField(
                queryset=self.fields["ministry"].queryset,
                required=False, label="Ministry", empty_label="Ministry")
        self._multi_ministry = multi_ministry
        self._by_shape = by_shape
        if by_shape:
            # ⚠️ 空选项说的是**这一格管什么**（"Kind"），不是「当前没筛」——
            #    逐字照旁边 `ministry` 的 `empty_label="Ministry"` 和 `nature`
            #    那一格 2026-09-15 的改口。一条式的版式里标签是 `sr-only`，
            #    所以框里那个词是看得见的人唯一读得到的说明。
            #
            # 🔴 **画成原生 `<select>`（走 `_filter_select.html`），不是 Role 那种
            #    弹层。** 判据写在 `_role_filter.html` 顶上：「选项需不需要一句
            #    解释」。Events / Programs 已经是全站主导航上那两格的名字、
            #    在那里也不带解释 —— 再在这里补一句，就是同一对词有两套说法。
            self.fields["kind"] = forms.ChoiceField(
                required=False, label="Kind",
                choices=[("", "Kind"), *SHAPE_KINDS])
        self._audience = audience
        if audience is not NO_AUDIENCE:
            # L1's axis, finally askable (2026-09-08). Until now `nature` was
            # displayed — the Kind column on an event's page — and filterable
            # nowhere, so somebody who only ever comes to *receive* a service
            # had to open events one at a time to find out which ones had a seat
            # for them.
            #
            # ⚠️ The choices are **built from the model**, never typed out a
            #    second time here. ParticipationRole.Nature's own note says a
            #    label carrying its gloss "reads well on the form that asks and
            #    badly in the table cell that reports" — this is the form that
            #    asks, so this is where the gloss belongs.
            #
            # 🔴 `NATURE_INVITATIONS`, **not** `NATURE_EXPLANATIONS`
            #    (2026-09-08). The other dictionary says "they give their time",
            #    and it is right where it is used — a ministry admin opening a
            #    job, talking about the people who will fill it. Here the person
            #    reading the box **is** that person, so it reads "give your
            #    time". Both live side by side in events/models.py; the reason
            #    there are two is written there.
            #
            # ⚠️ 空选项 2026-09-15 改成「Role」，标签从「Role kind」改成「Role」
            #    （跟着新版式）。同 ministry 那一格：说的从「当前没筛」变成
            #    「这一格管什么」，而状态由底下那行 `Filtered by …` 说。
            #
            # ⚠️ **`noun` 在这一格上没有对象了**（而它在别处有，见 `__init__`
            #    里那句 placeholder）。它 2026-09-14 加进来是为了让空选项在课程页上
            #    说「All programs」而不是「All events」—— 走查时抓到的。
            #    现在空选项是「Role」，两页一个词，那件事**不可能再发生**。
            # 🔴 **这一份同时是弹层上画的那几行**（2026-09-15 simplify）。
            #    `nature_options` 和 `nature_label` 现在都从这个字段派生 ——
            #    在此之前它们各从 `Nature.choices` 另拼一遍，于是「看得见的菜单」
            #    和「把关的名单」是两份。改这里就是改屏幕上看得见的那几行，
            #    这两件事从结构上不可能再分家。
            self.fields["nature"] = forms.ChoiceField(
                required=False, label="Role",
                choices=[("", "Any role")] + [
                    # 🔴 **标签只有一个词**（2026-09-15）。解释不再拼进来 ——
                    #    它跟着走 `nature_options` 那条路，画在展开的那一列上。
                    #    ⚠️ 这一版**回到了 `Nature` 那条注释本来的主张**：
                    #       「一个词一档，解释放在旁边而不是塞进标签 —— 带着注解
                    #       的标签在问的那张表单上读得好，在报出来的那一格里读得
                    #       糟」。是这张筛选表单当初把两半又粘回去了。
                    (value, label)
                    for value, label in ParticipationRole.Nature.choices],
            )
        # ⚠️ Ministry first (2026-08-05). Declared after the dates because it was
        #    added later, and declaration order is render order — so the box most
        #    people reach for first was sitting third. Which ministry you are
        #    looking at narrows the list far more than a date range does.
        #
        # ⚠️ `q` first (2026-08-06). Somebody who already knows which event they
        #    want types its name; scanning a dropdown is what you do when you do
        #    not know. The narrower action goes first.
        #
        # ⚠️ `nature` sits next to `ministry` (2026-09-08): both answer "what
        #    kind of thing am I looking at", and the dates answer "when". A name
        #    absent from the list is simply left where it was declared, so this
        #    stays correct on the pages that have no `nature` field at all.
        self.order_fields(["q", "ministry", "kind", "nature", "start", "end"])

    @property
    def by_shape(self):
        """这张表单被要求问「活动还是课」了吗。

        ⚠️ 模板问**这个**，不问 `{% if period.kind %}` —— 逐字同下面那条：
           缺字段时 `Form.__getitem__` 抛的 `KeyError` 会被模板引擎吞掉变成
           空串，于是那是一条失败路径在冒充一个问句。
        """
        return self._by_shape

    @property
    def by_role_kind(self):
        """Was this form asked for the "kind of role" box?

        ⚠️ The template asks **this**, not `{% if period.nature %}`. That
           spelling works only by accident: `Form.__getitem__` raises KeyError
           for a missing field, the template engine swallows it and hands back
           the empty string. It is a failure path standing in for a question,
           and the day the engine stops swallowing it the box appears on two
           pages that must not have it, with nothing behind it.
        """
        return "nature" in self.fields

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start"), cleaned.get("end")
        if start and end and end < start:
            raise forms.ValidationError("The end date cannot be before the start date.")
        return cleaned

    def bounds(self):
        """(start, end) as instants, or None where the box was left empty.

        ⚠️ The end date is turned into midnight at the start of the *next* day,
           because in_period() is half-open [start, end). Passing day_start(end)
           would silently drop everything happening on the last day of the
           window the person asked for — the single most likely wrong answer
           this form could give, and it would look plausible.
        """
        if not self.is_valid():
            return None, None
        start, end = self.cleaned_data.get("start"), self.cleaned_data.get("end")
        return (
            day_start(start) if start else None,
            day_start(end + datetime.timedelta(days=1)) if end else None,
        )

    @property
    def by_many_ministries(self):
        """这一格是不是多选的 —— 模板问这个，决定画哪一份。

        ⚠️ 模板问**这个**，不是 `{% if period.multi_ministry %}` 之类 ——
           同 `by_role_kind` 那一条：一个不存在的属性在模板里会被吞掉变成空串，
           那是一条失败路径冒充一个问题。
        """
        return self._multi_ministry

    @property
    def ministry_options(self):
        """Ministry 那一格展开时的每一行：`(值, 名字, 选没选中)`。

        🔴 **从字段派生，不自己拼**（同 `nature_options`，而那一条的注释写着
           不这么做会怎样）：显示那份和把关那份分家之后，人选了一个菜单里明明
           画着的选项 → 校验判它无效 → 筛选什么都没筛 → 列出**全部** ——
           **全程没有任何报错**。
           守卫：`MinistryFilterTests.test_the_options_come_from_the_field`。

        ⚠️ 选中与否也在这里算：模板里判 `{% if value in period.ministry.value %}`
           要靠字符串和整数的隐式比较，而那件事在 Django 模板里既看不出来也测不了。
        """
        field = self.fields.get("ministry")
        if field is None or not self._multi_ministry:
            return []
        chosen = {str(v) for v in (self.data.getlist("ministry")
                                   if hasattr(self.data, "getlist") else [])}
        return [(value, label, str(value) in chosen)
                for value, label in field.choices]

    @property
    def ministry_label(self):
        """收起时那一格上写的字：`Ministry` / `Food Pantry` / `Food Pantry +1`。

        ⚠️ 用户 2026-09-15 定的三种形态。和 `nature_label` 同一条规矩：选了就
           显示那个词，没选就是字段自己的标签 —— 收起状态下也看得出筛着什么。

        ⚠️ 和 `ministry_options` **同一个来源**（那个 property）：在此之前
           `nature_label` 和 `nature_options` 各推一遍，而改措辞时只改一处的表现是
           「收起时按钮上的词和展开后第一行的词对不上」。
        """
        field = self.fields.get("ministry")
        if field is None:
            return ""
        if not self._multi_ministry:
            chosen = self.cleaned_data.get("ministry") if self.is_valid() else None
            return chosen.name if chosen else field.label
        picked = [label for _value, label, is_on in self.ministry_options if is_on]
        if not picked:
            return field.label
        # ⚠️ 「第一个名字 +N」而不是「2 ministries」（用户定的）：一个数字说不出
        #    是哪几个，而这一条筛选栏上一个多余的年份就能把那一格挤宽 ——
        #    所以只带第一个名字。
        return picked[0] if len(picked) == 1 else f"{picked[0]} +{len(picked) - 1}"

    @property
    def nature_options(self):
        """Role 那一格展开时的每一行：`(值, 一个词, 一句解释)`。

        🔴 **收起时只写那个词，展开时才有解释**（用户 2026-09-15 定的版式）。
           原生 `<select>` 做不到这件事 —— 收起时显示的就是选中那个 `<option>`
           的文字，两处是同一个字符串。所以这一格是自绘的（用户批的破例），
           而这个属性是那一列的数据。

        ⚠️ 解释走 `NATURE_INVITATIONS`（「give your time」/「receive a service」），
           **不是** `NATURE_EXPLANATIONS`。两本字典的区别 2026-09-08 写过：后者是
           第三人称的说明（「他们付出时间」），而这里是在**邀请这个人挑一边**，
           所以用第二人称那一本。

        🔴 **选项从字段派生，不再自己拼**（2026-09-15 simplify）。
           在此之前这里和字段的 `choices` 是**两份各自独立的名单**：一份决定
           屏幕上看得见几行，另一份决定提交上来的值算不算数，连「Any role」
           那四个字都各硬写了一遍。今天两份一致，所以零症状。
           任何一次「让选项跟着人或页面变」的改动（「某类账号不该看到
           Attending」、「课程页不提供 Helping」）都会落在**把关那份**上 ——
           因为那才是校验发生的地方 —— 而显示那份不会跟着变。
           那时的症状特别难查：人选了一个菜单里明明画着的选项 → 校验判它无效
           → `is_valid()` 为假 → 筛选**什么都没筛** → 页面列出**全部**活动，
           而按钮上还写着他选的那个 Role。**全程没有任何报错。**
           守卫：`RoleOptionsComeFromTheFieldTests`。

        ⚠️ 没有 `nature` 字段时交空列表（`audience is NO_AUDIENCE` 的那几页），
           而不是 `KeyError` —— 模板那一格本来就不画。

        ⚠️ `NATURE_INVITATIONS` 用 `.get(value, "")`：字段里多出一个没写解释的值时，
           弹层少一句解释，而不是整页 500。空选项走的也是这条（它本来就
           没有解释）。
        """
        field = self.fields.get("nature")
        if field is None:
            return []
        return [(value, label, NATURE_INVITATIONS.get(value, ""))
                for value, label in field.choices]

    @property
    def nature_label(self):
        """收起时那一格上写的字：选了就是那个词，没选就是字段自己的标签。

        ⚠️ 和 `nature_options` 同一个来源（2026-09-15 simplify）。在此之前这里
           第三次 `dict(ParticipationRole.Nature.choices)` 自己推一遍，而「Role」
           那个词又硬写了两遍 —— 改措辞时只改一处的表现是：收起时按钮上
           的词和展开后第一行的词对不上。
        """
        field = self.fields.get("nature")
        if field is None:
            return ""
        chosen = self.cleaned_data.get("nature") if self.is_valid() else ""
        if not chosen:
            return field.label
        return dict(field.choices).get(chosen, field.label)

    @property
    def filtered_by(self):
        """底下那一行摘要：**筛了哪几类**，没筛则空串（2026-09-15）。

        🔴 和 `description()` 是两句话，不要合并。`description()` 说的是「筛出了
           什么」（"All ministries · 15 Sep 2026 – 01 Oct 2026"），报表正文印的是
           它 —— 一页数字没有一句说明自己涵盖什么，会被人读成「全部」。
           这一句说的是「你此刻筛着哪几类」，配一颗 Clear，短得多。

        ⚠️ 表单无效时交回空串，于是那一行整个不画。**这是有意的**：无效时
           `description()` 会答 "All ministries · all dates"，而那句话正好是一句
           假话盖在错误上（错误另有一行，在表单最底下）。
        """
        if not self.is_valid():
            return ""
        named = []
        if self.cleaned_data.get("q"):
            named.append("search")
        if self.cleaned_data.get("ministry"):
            named.append("ministry")
        if self.cleaned_data.get("nature"):
            named.append("role")
        if self.cleaned_data.get("kind"):
            named.append("kind")
        if self.cleaned_data.get("start") or self.cleaned_data.get("end"):
            named.append("dates")
        # ⚠️ `get_text_list` 而不是 `", ".join` —— 它给的是「a, b and c」，
        #    那是这句话要被读出来的样子。Django 自带，且跟着语言走。
        return get_text_list(named, "and") if named else ""

    @property
    def date_chip(self):
        """那颗胶囊上的字：`Sep 15 – Oct 1` / `From Sep 15` / `Until Oct 1` / `Dates`。

        🔴 **和 `description()` 共用那三个分支，但格式不同** —— 那边给报表正文，
           带年份（`15 Sep 2026 – 01 Oct 2026`）；这里在一条筛选栏上，一个多余的
           年份就会把那一格挤宽。合并会让其中一个变形。

        ⚠️ 单边区间是**一等公民**，不是边角情况（用户 2026-09-15 定的）：
           「从九月一号起，不限截止」是一个真实的筛选，而日历上点两下说不出它 ——
           那两个原生日期框才说得出来。
        """
        start = self.cleaned_data.get("start") if self.is_valid() else None
        end = self.cleaned_data.get("end") if self.is_valid() else None
        if start and end:
            return f"{start:%b %-d} – {end:%b %-d}"
        if start:
            return f"From {start:%b %-d}"
        if end:
            return f"Until {end:%b %-d}"
        return "Dates"

    def description(self):
        """One English line saying what this filter selected.

        Lives here because the form is what knows — the full report page prints
        it under the heading, and a printed report with no statement of what it
        covers is a page of numbers somebody will read as "everything".
        """
        if not self.is_valid():
            return "All ministries · all dates"
        ministry = self.cleaned_data.get("ministry")
        start, end = self.cleaned_data.get("start"), self.cleaned_data.get("end")
        # ⚠️ 单选那一档**一个字没动** —— 这句话印在报表正文上，被人当口径读。
        #    多选那一档今天没有读者（报表页是单选），写在这里是因为
        #    `ministry` 在那一档是 queryset，而 `.name` 会当场 `AttributeError`。
        if not ministry:
            who = "All ministries"
        elif self._multi_ministry:
            who = get_text_list([m.name for m in ministry], "and")
        else:
            who = ministry.name
        if start and end:
            when = f"{start:%d %b %Y} – {end:%d %b %Y}"
        elif start:
            when = f"from {start:%d %b %Y}"
        elif end:
            when = f"until {end:%d %b %Y}"
        else:
            when = "all dates"
        parts = [who, when]
        # ⚠️ The search term has to appear here. This line is printed under the
        #    heading of the full report and on the paper it becomes — and a report
        #    that does not state what it covers gets read as "everything". A
        #    search is the easiest of the three filters to forget you left on.
        search = (self.cleaned_data.get("q") or "").strip()
        if search:
            parts.append(f"matching “{search}”")
        # ⚠️ Written even though the one page that prints this line — the full
        #    report — is not passed an audience today and so has no such box.
        #    It is here for the same reason the search term is: a report that
        #    does not state what it covers gets read as "everything", and the
        #    day somebody hands this form an audience on that page, the line
        #    would go on claiming to cover every event while covering half.
        nature = self.cleaned_data.get("nature")
        if nature:
            parts.append(f"{ParticipationRole.Nature(nature).label.lower()} roles")
        return " · ".join(parts)

    def narrow(self, events):
        """Apply whichever boxes were filled in.

        ⚠️ An invalid form narrows by nothing rather than raising. The page
           still has to render — with the error shown next to the box — and a
           list that 500s because somebody typed a bad date is a worse answer
           than the unfiltered list plus an explanation.
        """
        start, end = self.bounds()
        if start is not None:
            events = events.filter(start_time__gte=start)
        if end is not None:
            events = events.filter(start_time__lt=end)
        ministry = self.cleaned_data.get("ministry") if self.is_valid() else None
        if ministry:
            # ⚠️ 多选那一档交回来的是 queryset，单选是一个对象 —— 两种都在这里
            #    落地，而**不是**在调用方分支：`narrow()` 是全项目唯一落筛选的
            #    地方，分支挪出去就变成两份。
            # ⚠️ `ministry__in` 对一个 FK **不会**造成 join 重复行（不同于受众那个
            #    M2M —— `for_audience()` 专门用 `Exists` 躲的正是那件事）。
            events = events.filter(
                ministry__in=ministry if self._multi_ministry else [ministry])
        kind = self.cleaned_data.get("kind") if self.is_valid() else ""
        if kind:
            # 🔴 **用 `EventQuerySet` 上现成的两个谓词，不在这里重写判据。**
            #    `views._of_shape()`（决定 45，那两张互斥的浏览页）调的是同一对
            #    方法，而它自己的注释写着「用的是 `EventQuerySet` 上那两个谓词
            #    （models.py），不是在这里重写判据」。两个调用方，一份判据 ——
            #    各写一遍 `filter(shape=…)` 的表现是同一门课在浏览页上是课、
            #    在管理列表上不是。
            #
            # ⚠️ 这里**不**收 `_of_shape()` 当函数用：那个函数是二选一，没有
            #    「两种都要」这一档，而这一格的默认就是那一档。把第三档加进它
            #    会让那两页多出一个它们不许有的状态。
            events = (events.programs() if kind == "programs"
                      else events.single_occasions())
        search = (self.cleaned_data.get("q") or "").strip() if self.is_valid() else ""
        if search:
            # ⚠️ `.strip()` above, and it matters more than it looks: a trailing
            #    space from a phone's autocorrect would make `icontains` match
            #    nothing at all, and the page would come back empty with the box
            #    apparently holding a perfectly good word.
            # 🔴 **街道和城市也在搜索范围里**（2026-09-15 加地址时补的）。
            #    这一格的标签写着「Search by name or location」，而在加地址之前
            #    `location` 就是全部的「哪里」。加完之后不补这两列的话，页面上那句
            #    话就成了一句**每个字都对的假话**：有人输入街道名，一条都搜不到，
            #    而标签明写着能按 location 搜。
            #    ⚠️ **不搜** state / postal_code：没有人输入「CA」或一串邮编来找
            #       活动，而每一列都是一次 `icontains`。
            events = events.filter(
                Q(name__icontains=search)
                | Q(location__icontains=search)
                | Q(address_street__icontains=search)
                | Q(address_city__icontains=search))
        nature = self.cleaned_data.get("nature") if self.is_valid() else ""
        if nature:
            # 🔴 **A subquery, never a join.** Written
            #    `filter(roles__role__nature=nature)`, an event that opened two
            #    helping roles comes back **twice** — paging and every count
            #    under it corrupted, and on the page it reads only as "why is
            #    this event listed twice". org/audience.py has already paid for
            #    this exact lesson once, on `visible_to_ministries__in`.
            #
            # 🔴 **`for_audience()`, not every role on the event.** An event's
            #    page already narrows its role table to the roles open to the
            #    person reading it (views.py's `to_join`). Filtering on the full
            #    table would list events under "Attending" whose attending role
            #    that person cannot see — filtered in by something the page then
            #    refuses to show. Two answers from one set of data.
            #
            # ⚠️ `self._audience` can only be a real audience here: `nature` is
            #    empty unless the field exists, and the field exists only when
            #    an audience was passed.
            events = events.filter(pk__in=(
                EventRole.objects.for_audience(self._audience)
                .filter(role__nature=nature).values("event_id")))
        return events


class RoleFormBase(EventAudienceFormMixin, forms.ModelForm):
    """Open one job and say how many people it wants — on an event or on a rule.

    ⭐ One body, two doors. `EventRoleForm` opens a job on an occasion;
       `EventSeriesRoleForm` opens one on a recipe, which then appears on every
       occasion the recipe makes. The questions are identical — which job, how
       many, who may sign up, any notes, and the escape hatch for a job nobody
       has entered before — so the answers are written once.

    ⚠️ The only thing that differs is which row the new one hangs on, and
       **the model already declares that**: `AUDIENCE_PARENT` is `"event"` on
       one and `"series"` on the other. This is the third place that attribute
       is read (after `refuse_bad_audience()` and `submitted_event()`), so it
       is a mechanism this codebase already has rather than one invented here.

    Open one job on an event and say how many people it wants.

    2026-08-04: it can also **add to the vocabulary**. A ministry admin is not
    staff, so the admin site is shut to them, and a job nobody had entered
    before used to be a dead end — the dropdown was the whole world.

    ⚠️ The cost is real and is accepted rather than hidden: ParticipationRole is
       the grouping dimension for R5 and R7, so two rows meaning one job split
       one column of every report in two. Nothing raises; both halves look
       right. The duplicate check below is what keeps that rare, and it only
       catches exact-after-normalising matches — see
       services.matching_participation_role().
    """

    new_role_name = forms.CharField(
        required=False, max_length=100, label="…or add a new kind of role",
        help_text="Only if none of the above fits. Everyone will see it from then on.",
    )
    # L1. Required only when a new role is being added — clean() enforces that,
    # because "required together with another field" is not something a field
    # can say about itself.
    #
    # ⚠️ No default, and no empty option that quietly means helping. Filed
    #    wrongly, an ESL seat would ask its students to record hours and would
    #    count them among the people who helped — neither of which raises
    #    anything. Making it a choice somebody has to make is the whole point.
    # ⚠️ The wording is built from the model's labels plus the gloss beside them
    #    in events/models.py, never typed out here — the same rule the served_as
    #    field two hundred lines up follows, and for the same reason: a copy in
    #    a form file is how the two come to say slightly different things.
    new_role_nature = forms.ChoiceField(
        required=False,
        choices=[
            ("", "---------"),
            *((value, f"{label} — {NATURE_EXPLANATIONS[value]}")
              for value, label in ParticipationRole.Nature.choices),
        ],
        label="…and what people in it are doing",
        help_text="Everybody at an event is a participant — this says which kind.",
    )

    class Meta:
        model = EventRole
        # ⚠️ `stop_at_needed_count` 排在数字后面，因为它讲的是那个数字
        #    （2026-08-19）。它默认勾上 —— 理由写在模型上，那是唯一的一份。
        fields = [
            "role", "needed_count", "stop_at_needed_count",
            # L2. Same three columns as the event, answering "who may sign up"
            # rather than "who may see it".
            *Audience.AUDIENCE_FIELDS,
            "notes",
        ]
        widgets = {"visible_to_ministries": forms.CheckboxSelectMultiple}

    def __init__(self, *args, parent, **kwargs):
        super().__init__(*args, **kwargs)
        # ⚠️ Set by the name the model declares, not by a literal. An event's
        #    roles hang on `event` and a recipe's on `series`; writing either
        #    one here would make this base serve exactly one of its two
        #    subclasses, which is the shape `submitted_event()` had to be
        #    corrected out of on 2026-09-10.
        setattr(self.instance, self._meta.model.AUDIENCE_PARENT, parent)
        self.fields["role"].queryset = ParticipationRole.objects.filter(is_active=True)
        # ⚠️ The definition goes on **this** field too, not only on the "kind"
        #    picker beside it (2026-09-08). That one is on the path for somebody
        #    inventing a new job; whoever picks an existing row from this list
        #    never saw it — and the two catch-all rows sit next to each other
        #    here reading "General participant (attending)" and
        #    "(helping)", where "attending" in ordinary English means "coming
        #    along". Choosing wrong is silent in every direction: no hours
        #    recorded, out of the staffing denominator, into "people served".
        #
        # ⚠️ Composed from NATURE_EXPLANATIONS rather than retyped, so the two
        #    halves of each term cannot drift.
        self.fields["role"].help_text = "; ".join(
            f"{label.lower()} — {gloss}"
            for label, gloss in (
                (ParticipationRole.Nature(value).label, gloss)
                for value, gloss in NATURE_EXPLANATIONS.items()
            )
        ).capitalize() + "."

        # ⭐ A new role starts as wide as the event it belongs to — "if you can
        #    see it you can sign up for it" is requirement 6's ordinary case,
        #    and it is the only default that cannot violate the invariant below.
        #
        #    Starting narrow would make requirement 8 (one publish recruiting
        #    inside and outside at once) mean widening every role by hand, and
        #    forgetting shows up as "outside volunteers can see the event and
        #    sign up for nothing in it" — which raises nothing.
        #
        # ⚠️ Only when adding. On an edit the stored answer is the answer;
        #    re-inheriting would silently widen a role somebody had narrowed.
        if self.instance.pk is None:
            inherited = Audience.Spec.of(parent)
            self.initial.setdefault("visible_to_outsiders", inherited.outsiders)
            self.initial.setdefault("visible_to_all_staff", inherited.all_staff)
            self.initial.setdefault(
                "visible_to_ministries", sorted(inherited.ministries))
        # Not required on its own any more: one of `role` and `new_role_name`
        # has to be filled, and clean() is where "one of" can be said.
        self.fields["role"].required = False
        # ⚠️ Directly under the dropdown it is the alternative to. Declared
        #    fields render after Meta.fields by default, which put "…or add a
        #    new kind of role" three boxes below the one it replaces — far
        #    enough down that it reads as a fourth thing to fill in rather than
        #    as the other half of a choice.
        # ⚠️ `new_role_nature` goes directly after the name it belongs to. It is
        #    declared after it too, but declaration order is not render order —
        #    that is what this call exists for, and the comment above says why.
        self.order_fields([
            "role", "new_role_name", "new_role_nature",
            "needed_count", "stop_at_needed_count",
            *Audience.AUDIENCE_FIELDS,
            "notes",
        ])

    def _get_validation_exclusions(self):
        """Keep `event` in play, so "that role is already open" is checked here.

        🔴 Same trap as ProfileForm's names and EmergencyContactForm's
           duplicate, found in the same audit (2026-08-19).
           `eventrole_unique_per_event` spans (event, role); the parent is set
           on the instance above rather than rendered; and Django skips any
           constraint mentioning a field it excluded from validation. So
           opening the same role twice on one event validated cleanly and
           raised IntegrityError at the INSERT — a 500 on the Edit & Roles
           page, reachable by ordinary use of the dropdown right above it.

        ⚠️ Note what this does **not** do: the duplicate-*vocabulary* check in
           clean() below stays exactly where it is. That one is about two rows
           in ParticipationRole meaning one job, which no constraint expresses
           (it is a normalised-name comparison). This is about one row twice.
        """
        exclude = super()._get_validation_exclusions()
        # ⚠️ By the declared name, so this works for both subclasses: the
        #    uniqueness constraint spans (event, role) on one table and
        #    (series, role) on the other.
        exclude.discard(self._meta.model.AUDIENCE_PARENT)
        return exclude

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get("role")
        new_name = (cleaned.get("new_role_name") or "").strip()

        if role and new_name:
            raise forms.ValidationError(
                "Pick a role from the list or add a new one — not both.")
        if not role and not new_name:
            raise forms.ValidationError("Pick a role, or add a new one.")

        # L2×L3. ⚠️ Runs before the vocabulary checks below rather than after,
        #    so that a submission with two faults reports the audience one too
        #    instead of hiding it behind a name clash.
        # ⚠️ `audience`, not `role` — that name is taken twelve lines up by the
        #    ParticipationRole this form selected, and rebinding it here handed
        #    an Audience.Spec to anything below that went looking for the role.
        #    Nothing does today; the duplicate-vocabulary branch directly below
        #    is where the next reader would reach for it (2026-08-28).
        audience = self.clean_audience()
        if audience is not None:
            self.refuse_wider_than_its_event(audience)

        if new_name:
            from .services import matching_participation_role

            if not cleaned.get("new_role_nature"):
                # ⚠️ Asked, never defaulted. See the field above: the failure of
                #    a default is an ESL seat that asks its students for hours,
                #    and nothing about that shows up until the report is wrong.
                self.add_error("new_role_nature", forms.ValidationError(
                    "Say what people in this new role are doing."))

            existing = matching_participation_role(new_name)
            if existing is not None:
                # ⚠️ Named in the message. "That already exists" leaves the
                #    person hunting a dropdown of thirty entries for something
                #    they may have spelled differently; the name they need to
                #    look for is the whole content of this error.
                #
                # ⚠️ And its kind is named too, because the duplicate check
                #    ignores `nature` (see services.matching_participation_role).
                #    Somebody adding an attending "ESL seat" while a helping one
                #    exists is refused, and without this half the message sends
                #    them to a dropdown entry that is not the thing they wanted.
                self.add_error("new_role_name", forms.ValidationError(
                    f"There is already a role called “{existing.name}” "
                    f"({existing.get_nature_display()}). Pick it from the list "
                    f"above instead of adding a second one."))
        return cleaned

    def save(self, commit=True):
        """Create the vocabulary entry first, then the row that points at it."""
        new_name = (self.cleaned_data.get("new_role_name") or "").strip()
        if new_name:
            from .services import create_participation_role

            self.instance.role = create_participation_role(
                new_name, nature=self.cleaned_data["new_role_nature"])
        return super().save(commit=commit)



class EventRoleForm(RoleFormBase):
    """One job on one occasion. The door every event page has had since B10."""

    class Meta(RoleFormBase.Meta):
        model = EventRole


class EventSeriesRoleForm(RoleFormBase):
    """One job on a repeat rule, which every occasion it makes will then open.

    ⚠️ Identical to its sibling except for the table, which is the point —
       a publisher answering "who do I need on the door" should not meet two
       different forms depending on which of the three shapes they picked.

    ⚠️ Its audience starts as wide as the **series**, by the same decision 15
       the event side follows: if you can see the thing, you can sign up for a
       job in it, until somebody narrows one on purpose.
    """

    class Meta(RoleFormBase.Meta):
        model = EventSeriesRole

class HoursForm(forms.Form):
    """Entering hours by hand, for somebody added from a paper sign-in sheet."""

    hours = forms.DecimalField(max_digits=6, decimal_places=2, min_value=0, label="Hours")


class NotifyForm(forms.Form):
    """P6: the message that goes out, and why.

    The body is editable and is stored as written — a snapshot. Editing the
    event afterwards must not rewrite what this notice said.
    """

    reason = forms.ChoiceField(label="Reason")
    # Same constant as EventNotification.message, from core/limits.py. A plain
    # Form gets nothing from the model, so this is the only thing standing
    # between a pasted document and the column it is written to.
    message = forms.CharField(
        widget=forms.Textarea, max_length=LONG_TEXT, label="Message")

    def __init__(self, *args, **kwargs):
        # Imported here rather than at module level to keep the import graph of
        # this file to models + org, matching the other forms above.
        from .models import EventNotification

        super().__init__(*args, **kwargs)
        self.fields["reason"].choices = EventNotification.Reason.choices


# P5's GrantForm used to live here and now lives in org/forms.py. Its subject is
# a ministry, and org/views.py was importing it back across the one dependency
# INSTALLED_APPS spells out (events -> org). See org/forms.py.


class EventStatusForm(forms.ModelForm):
    """Just the status, for the inline dropdown on the manage list.

    A ModelForm rather than a bare ChoiceField so the valid values come from
    Event.Status itself; a hand-written choice list here would be a second copy
    that stops matching the day a status is added.
    """

    class Meta:
        model = Event
        fields = ["status"]


class SessionForm(forms.ModelForm):
    """One meeting on a course — the admin's door onto `Session`.

    ⚠️ It exists so that adding a meeting through the admin reaches the same
       service `add_session()` gives the programmatic path. Decision 18's mechanism has two ends
       — rows appearing when somebody signs up, and rows appearing when a
       meeting is added after they did — and the second end has to hold on every
       door or the symptom is a register that is simply empty on the day, with
       nothing raising.

    ⚠️ Why a form and not `ModelAdmin.save_model()`: that hook is one of the
       four `AdminHasNoLogicGuardTests` refuses, and rightly — admin.py renders,
       it does not decide. Pointing `SessionAdmin.form` at a form that delegates
       to `events.services` is the arrangement `EventAdmin` and `EventRoleAdmin`
       already use for their audience rules, and `EventRoleForm.save()` above
       is the precedent for a form's save() doing more than one thing.

    ⚠️ Not a signal, either. There is not one anywhere in this codebase, and the
       reason to keep it that way is the reason signals are tempting here: the
       work would happen with nothing at the call site saying so.

    🔴 **The admin never passes `commit=True`**, which is why the work below is
       arranged the way it is rather than sitting under an `if commit:`.
       `ModelAdmin.save_form()` is hard-coded to `form.save(commit=False)`; the
       instance is saved by `save_model()` afterwards, and `save_related()` then
       calls `save_m2m()`. So a register top-up written the obvious way runs on
       every path **except the only one that exists today**, and its symptom is
       precisely what this class was added to prevent — week thirteen scheduled,
       and an empty register on the night. Caught by review, not by the tests;
       `test_a_meeting_added_in_the_admin_opens_the_register` now holds it.

    ⚠️ `EventRoleForm.save()` above sidesteps this by doing its extra work
       *before* `super().save()`, unconditionally. That is not available here:
       the register cannot be opened until the meeting has a primary key.
    """

    class Meta:
        model = Session
        fields = ["event", "start_time", "end_time", "source"]

    def _open_registers(self):
        """Put everybody already signed up onto this meeting's register."""
        from .services import open_registers_for

        open_registers_for(self.instance.event, sessions=[self.instance])

    def save(self, commit=True):
        session = super().save(commit=commit)
        if commit:
            self._open_registers()
            return session
        # Deferred to `save_m2m()`, the one hook the admin is guaranteed to call
        # after it has saved the instance itself. Wrapping rather than replacing:
        # whatever Django put there still has to run.
        saving_related = self.save_m2m

        def save_m2m():
            saving_related()
            self._open_registers()

        self.save_m2m = save_m2m
        return session


class EventGrantForm(forms.Form):
    """把这一场活动交给某个人管。D47。

    A plain Form，不是 ModelForm：`granted_by` 从 session 来、`event` 从地址栏来，
    两个都不许是表单上的格子 —— 一个能填的格子就是一个能撒谎的格子
    （同 `org.forms.GrantForm`）。

    🔴 **只列有登录账号的人**（用户 2026-09-15 定的）。授权一个登不进来的人，
       那一行是死的 —— 而页面上没有任何东西会说它是死的。
       ⚠️ 账号就是邮箱（D12/D30），所以「没有邮箱的人」天然不在这张名单里。
    """

    contact = forms.ModelChoiceField(queryset=None, label="Who")
    start_date = forms.DateField(
        required=False, label="Starting on",
        widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["contact"].queryset = Contact.objects.filter(
            is_active=True,
            contact_type=Contact.ContactType.INDIVIDUAL,
            # ⚠️ `user` 是 `accounts.User.contact` 的反向名（OneToOne），
            #    所以这一句读作「有账号的人」。
            user__isnull=False,
        ).order_by("legal_last_name", "legal_first_name")
