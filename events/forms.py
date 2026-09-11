"""Forms for the self-service and ministry-admin pages.

Permanent assets: plain django.forms, no admin import (there is a guard), and
every one of them takes its context as an explicit keyword argument rather than
reaching into a request. Phase C's views construct the same classes unchanged.
"""

import datetime

from django import forms
from django.core.exceptions import ValidationError
from django.conf import settings
from django.db.models import Q

from contact.models import EmergencyContact, RelationshipType
from core.images import decode_complaint_for, is_new_upload
from core.limits import LONG_TEXT, PHONE, SEARCH
from core.timeutils import day_start
from django.utils.timezone import localtime
from org.audience import Audience
from org.forms import AudienceFormMixin
from org.models import Ministry
from org.permissions import ministry_ids_administered_by

from . import schedule
from .models import (
    NARROWING_MESSAGE,
    PARENT_NOUN,
    NATURE_EXPLANATIONS,
    NATURE_INVITATIONS,
    SERVED_AS_EXPLANATIONS,
    Event,
    EventRole,
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


class EventForm(EventAudienceFormMixin, forms.ModelForm):
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
            #    separately — is a generator (L5.4) rather than a value, and it
            #    joins this field when the generator exists. Offering it now
            #    would be a radio button that saves nothing.
            "name", "ministry", "shape", "people_pick_meetings",
            "start_time", "end_time",
            "location", "status", "requires_guardian_consent",
            # L3. Right after the lifecycle fields and before the prose, because
            # "who is this for" is a publishing decision rather than a detail.
            *Audience.AUDIENCE_FIELDS,
            "description", "image",
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
        self.fields["ministry"].queryset = Ministry.objects.filter(id__in=administered)

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

    def clean(self):
        cleaned = super().clean()
        audience = self.clean_audience()
        # ⚠️ Only when the audience itself is usable. An event ticked for nobody
        #    is narrower than every one of its roles, so going on would bury the
        #    one real fault under a list of its own consequences.
        if audience is not None:
            self.refuse_narrowing_below_the_roles(audience)
        return cleaned

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
FILTER_PARAMS = ("q", "ministry", "nature", "start", "end")


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
    ministry = forms.ModelChoiceField(
        queryset=Ministry.objects.filter(is_active=True).order_by("name"),
        required=False, label="Ministry", empty_label="All ministries",
    )

    def __init__(self, *args, ministries=None, audience=NO_AUDIENCE, **kwargs):
        """`ministries` narrows the dropdown to a scope the page already has.

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
        if ministries is not None:
            self.fields["ministry"].queryset = ministries
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
            # ⚠️ The empty option says "All events", not "Any kind": the box
            #    filters *events*, and an event is not a kind of anything.
            self.fields["nature"] = forms.ChoiceField(
                required=False, label="Role kind",
                choices=[("", "All events")] + [
                    (value, f"{label} ({NATURE_INVITATIONS[value]})")
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
        self.order_fields(["q", "ministry", "nature", "start", "end"])

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
        who = ministry.name if ministry else "All ministries"
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
        if ministry is not None:
            events = events.filter(ministry=ministry)
        search = (self.cleaned_data.get("q") or "").strip() if self.is_valid() else ""
        if search:
            # ⚠️ `.strip()` above, and it matters more than it looks: a trailing
            #    space from a phone's autocorrect would make `icontains` match
            #    nothing at all, and the page would come back empty with the box
            #    apparently holding a perfectly good word.
            events = events.filter(
                Q(name__icontains=search) | Q(location__icontains=search))
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


class EventRoleForm(EventAudienceFormMixin, forms.ModelForm):
    """Open one job on an event and say how many people it wants.

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

    def __init__(self, *args, event, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.event = event
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
            inherited = Audience.Spec.of(event)
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
           `eventrole_unique_per_event` spans (event, role); `event` is set on
           the instance above rather than rendered; and Django skips any
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
        exclude.discard("event")
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
