"""Forms for the self-service and ministry-admin pages.

Permanent assets: plain django.forms, no admin import (there is a guard), and
every one of them takes its context as an explicit keyword argument rather than
reaching into a request. Phase C's views construct the same classes unchanged.
"""

import datetime

from django import forms
from django.conf import settings

from contact.models import EmergencyContact, RelationshipType
from core.timeutils import day_start
from org.models import Ministry
from org.permissions import ministry_ids_administered_by

from .models import Event, EventRole, Participation, ParticipationRole


class SignUpForm(forms.Form):
    """Pick a role, and — for a minor — record the guardian's consent.

    The consent half is shown only when it applies, but it is never the form
    that decides whether consent was required: services.sign_up() judges that,
    because the same rule has to hold for an admin registering somebody from a
    paper list. The form only decides what to draw.
    """

    event_role = forms.ModelChoiceField(queryset=EventRole.objects.none(), label="Role")

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
        required=False, label="They are the volunteer's…",
    )
    # ⚠️ At least one of these two. Consent carrying only a *name* satisfies the
    #    paperwork and leaves P6 with no address to send anything to, so the
    #    signup would go in already guaranteed to be unreachable.
    consent_email = forms.EmailField(required=False, label="Guardian's email")
    consent_phone = forms.CharField(required=False, label="Guardian's phone")
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
            event.roles.select_related("role").order_by("role__name")
        )
        # Asked through services, so the form and the two service-layer gates
        # cannot answer it differently — an event that waives the rule must
        # waive it on the page too, or the boxes are drawn and then ignored.
        from .services import consent_required_for

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


class EventForm(forms.ModelForm):
    """P2: publish an event. The ministry dropdown lists only the ones they run.

    ⚠️ The dropdown is there to stop a slip, not to stop an attack — a POST can
       name any id. The view checks can_publish_event() on the submitted value
       as well; two different jobs, both needed.
    """

    class Meta:
        model = Event
        fields = [
            "name", "event_type", "ministry", "start_time", "end_time",
            "location", "status", "requires_guardian_consent", "description",
            "image",
        ]
        widgets = {
            "start_time": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "end_time": forms.DateTimeInput(attrs={"type": "datetime-local"}),
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
        self.fields["ministry"].queryset = Ministry.objects.filter(
            id__in=ministry_ids_administered_by(user)
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


class EventPeriodForm(forms.Form):
    """R1: "how many events in this window", plus which ministry ran them.

    Lives here rather than in the view for the reason the grep guard states:
    a view holding date arithmetic gets rewritten along with the templates.
    Every box is optional, and that is what makes one form answer three
    different questions — "what is on next month", "what does the food pantry
    have open at all", and the two together.
    """

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
        self.order_fields(["ministry", "start", "end"])

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
        return f"{who} · {when}"

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

    class Meta:
        model = EventRole
        fields = ["role", "needed_count", "notes"]

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
        self.order_fields(["role", "new_role_name", "needed_count", "notes"])

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

            existing = matching_participation_role(new_name)
            if existing is not None:
                # ⚠️ Named in the message. "That already exists" leaves the
                #    person hunting a dropdown of thirty entries for something
                #    they may have spelled differently; the name they need to
                #    look for is the whole content of this error.
                self.add_error("new_role_name", forms.ValidationError(
                    f"There is already a role called “{existing.name}”. "
                    f"Pick it from the list above instead of adding a second one."))
        return cleaned

    def save(self, commit=True):
        """Create the vocabulary entry first, then the row that points at it."""
        new_name = (self.cleaned_data.get("new_role_name") or "").strip()
        if new_name:
            from .services import create_participation_role

            self.instance.role = create_participation_role(new_name)
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
    message = forms.CharField(widget=forms.Textarea, label="Message")

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
