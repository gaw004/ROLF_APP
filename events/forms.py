"""Forms for the self-service and ministry-admin pages.

Permanent assets: plain django.forms, no admin import (there is a guard), and
every one of them takes its context as an explicit keyword argument rather than
reaching into a request. Phase C's views construct the same classes unchanged.
"""

from django import forms

from contact.models import RelationshipType
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

    event_role = forms.ModelChoiceField(queryset=EventRole.objects.none(), label="工种")

    consent_given_by = forms.CharField(max_length=200, required=False, label="家长/监护人姓名")
    consent_relationship = forms.ModelChoiceField(
        queryset=RelationshipType.objects.filter(usable_as_emergency_contact=True),
        required=False, label="关系",
        help_text="读作「同意人 是 报名人 的 ___」。",
    )
    consent_method = forms.ChoiceField(
        choices=[("", "---------"), *Participation.ConsentMethod.choices],
        required=False, label="同意方式",
    )
    # ⚠️ At least one of these two. Consent carrying only a *name* satisfies the
    #    paperwork and leaves P6 with no address to send anything to, so the
    #    signup would go in already guaranteed to be unreachable.
    consent_email = forms.EmailField(required=False, label="家长邮箱")
    consent_phone = forms.CharField(required=False, label="家长电话")

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
        # is_minor is three-state; unknown takes the cautious branch.
        self.needs_consent = contact.is_minor in (True, None)
        if not self.needs_consent:
            for name in self.CONSENT_FIELDS:
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
            "location", "status", "description",
        ]
        widgets = {
            "start_time": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "end_time": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["ministry"].queryset = Ministry.objects.filter(
            id__in=ministry_ids_administered_by(user)
        )


class EventRoleForm(forms.ModelForm):
    """Open one job on an event and say how many people it wants."""

    class Meta:
        model = EventRole
        fields = ["role", "needed_count", "notes"]

    def __init__(self, *args, event, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.event = event
        self.fields["role"].queryset = ParticipationRole.objects.filter(is_active=True)


class HoursForm(forms.Form):
    """Entering hours by hand, for somebody added from a paper sign-in sheet."""

    hours = forms.DecimalField(max_digits=6, decimal_places=2, min_value=0, label="工时")


class NotifyForm(forms.Form):
    """P6: the message that goes out, and why.

    The body is editable and is stored as written — a snapshot. Editing the
    event afterwards must not rewrite what this notice said.
    """

    reason = forms.ChoiceField(label="原因")
    message = forms.CharField(widget=forms.Textarea, label="正文")

    def __init__(self, *args, **kwargs):
        # Imported here rather than at module level to keep the import graph of
        # this file to models + org, matching the other forms above.
        from .models import EventNotification

        super().__init__(*args, **kwargs)
        self.fields["reason"].choices = EventNotification.Reason.choices


class GrantForm(forms.Form):
    """P5: appoint somebody as a ministry's admin.

    A plain Form: granted_by comes from the session, not from the page, and a
    field somebody could type into would be a field somebody could lie in.
    """

    contact = forms.ModelChoiceField(queryset=None, label="谁")
    start_date = forms.DateField(
        required=False, label="生效日", widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, **kwargs):
        from contact.models import Contact

        super().__init__(*args, **kwargs)
        self.fields["contact"].queryset = Contact.objects.filter(
            is_active=True, contact_type=Contact.ContactType.INDIVIDUAL)
