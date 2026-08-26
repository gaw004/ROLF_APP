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
from core.limits import LONG_TEXT, PHONE, SEARCH
from core.timeutils import day_start
from org.models import Ministry
from org.permissions import ministry_ids_administered_by

from .models import (
    NATURE_EXPLANATIONS,
    SERVED_AS_EXPLANATIONS,
    Audience,
    Event,
    EventRole,
    Participation,
    ParticipationRole,
    askable_served_as,
    refuse_empty_audience,
    refuse_redundant_audience,
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
            event.roles.with_signup_counts()
            .select_related("role").order_by("role__name")
        )
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


class AudienceFormMixin:
    """The two rules an audience obeys on its own, for any form that edits one.

    🔴 This is where they live, and not in `Model.clean()`, because a
       ManyToMany is written after save() while full_clean() runs before it: on
       a new object the field cannot be read at all, and on an existing one it
       reads the row already in the database rather than what is being
       submitted. A form is the first layer that holds the new value. The full
       working is in events/models.py, above refuse_empty_audience().

    ⚠️ So the admin needs `form = ` pointing at a subclass of this, or the admin
       has no check whatsoever. Not drawing a control keeps nobody out; this
       does.
    """

    def audience(self):
        """(outsiders, all_staff, ministries) as submitted."""
        return (
            self.cleaned_data.get("visible_to_outsiders"),
            self.cleaned_data.get("visible_to_all_staff"),
            self.cleaned_data.get("visible_to_ministries"),
        )

    def clean_audience(self):
        """Run both rules, putting each message where the reader can act on it.

        ⚠️ Errors land on a **field**, never on the form as a whole. "Say who
           this is for" at the top of a long publish form leaves somebody
           hunting for which box it means.
        """
        outsiders, all_staff, ministries = self.audience()
        try:
            refuse_empty_audience(
                outsiders=outsiders, all_staff=all_staff, ministries=ministries)
        except ValidationError as error:
            self.add_error("visible_to_outsiders", error)
        try:
            refuse_redundant_audience(all_staff=all_staff, ministries=ministries)
        except ValidationError as error:
            self.add_error("visible_to_ministries", error)


class AudienceAdminForm(AudienceFormMixin, forms.ModelForm):
    """The admin's form for anything carrying an audience.

    🔴 Without it the admin has **no audience check at all** — the two rules
       cannot live in Model.clean() (a ManyToMany is written after save() while
       full_clean() runs before it; the working is in events/models.py above
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
        self.clean_audience()
        return cleaned


class EventForm(AudienceFormMixin, forms.ModelForm):
    """P2: publish an event. The ministry dropdown lists only the ones they run.

    ⚠️ The dropdown is there to stop a slip, not to stop an attack — a POST can
       name any id. The view checks can_publish_event() on the submitted value
       as well; two different jobs, both needed.
    """

    class Meta:
        model = Event
        fields = [
            "name", "event_type", "ministry", "start_time", "end_time",
            "location", "status", "requires_guardian_consent",
            # L3. Right after the lifecycle fields and before the prose, because
            # "who is this for" is a publishing decision rather than a detail.
            *Audience.AUDIENCE_FIELDS,
            "description", "image",
        ]
        widgets = {
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
        if not uploaded or not hasattr(uploaded, "content_type"):
            return uploaded
        if uploaded.size > settings.EVENT_IMAGE_MAX_UPLOAD_BYTES:
            raise forms.ValidationError(
                f"That image is larger than "
                f"{settings.EVENT_IMAGE_MAX_UPLOAD_BYTES // (1024 * 1024)} MB. "
                f"Most phone photos are well under it.")
        from .services import normalise_event_image

        return normalise_event_image(uploaded)

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        administered = ministry_ids_administered_by(user)
        self.fields["ministry"].queryset = Ministry.objects.filter(id__in=administered)
        # ⚠️ Same set the dropdown above is built from, not a second lookup.
        #    Two answers to "which ministries are theirs" would drift apart on
        #    exactly the account where it matters.
        self.fields["visible_to_ministries"].queryset = Ministry.objects.filter(
            is_active=True).order_by("name")

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
        self.clean_audience()
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

    def __init__(self, *args, ministries=None, **kwargs):
        """`ministries` narrows the dropdown to a scope the page already has.

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
        # ⚠️ Ministry first (2026-08-05). Declared after the dates because it was
        #    added later, and declaration order is render order — so the box most
        #    people reach for first was sitting third. Which ministry you are
        #    looking at narrows the list far more than a date range does.
        #
        # ⚠️ `q` first (2026-08-06). Somebody who already knows which event they
        #    want types its name; scanning a dropdown is what you do when you do
        #    not know. The narrower action goes first.
        self.order_fields(["q", "ministry", "start", "end"])

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
        return events


class EventRoleForm(forms.ModelForm):
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
        fields = ["role", "needed_count", "stop_at_needed_count", "notes"]

    def __init__(self, *args, event, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.event = event
        self.fields["role"].queryset = ParticipationRole.objects.filter(is_active=True)
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
            "needed_count", "stop_at_needed_count", "notes",
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
