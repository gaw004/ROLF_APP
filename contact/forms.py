import json

from django import forms
from django.core.exceptions import ValidationError
from localflavor.us.us_states import STATE_CHOICES

from .models import Contact, Relationship
from .services import direction_choices, orient, parse_direction_choice


class ContactAdminForm(forms.ModelForm):
    """Admin form for Contact.

    The name-vs-contact-type rules live on the model (Contact.clean / Contact.save)
    so they hold for every save path; this form only feeds the admin JS the data it
    needs to swap the state widget between a US dropdown and a free-text box.
    """

    class Meta:
        model = Contact
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # address_state is a plain text field so non-US regions can be typed in.
        # address_state_toggle.js builds the US-state dropdown from this list.
        self.fields["address_state"].widget.attrs["data-us-states"] = json.dumps(
            [[code, str(name)] for code, name in STATE_CHOICES]
        )


class RelationshipForm(forms.ModelForm):
    """Record a relationship from either side, without exposing A and B.

    The type dropdown lists both readings of every type, phrased around the
    contact whose page you came from; save() routes the two contacts into
    contact_a / contact_b accordingly. Nobody entering data has to know that the
    row has a direction, and both sides of a relationship can be entered from
    either person's page.

    ⚠️ This is django.forms, not admin — Django's compatibility promise covers
       it, and Phase C's HTMX view imports this exact class. The guard test in
       core/tests.py makes sure it stays that way.
    """

    direction_choice = forms.ChoiceField(label="关系")
    other = forms.ModelChoiceField(
        queryset=Contact.objects.filter(is_active=True), label="对方")

    # contact_a / contact_b are deliberately not on this form, so a constraint
    # error naming one of them has nowhere to land. Both mean the same thing to
    # the person filling it in: the other party they picked.
    FIELD_ALIASES = {"contact_a": "other", "contact_b": "other"}

    class Meta:
        model = Relationship
        fields = ["start_date", "end_date"]

    def __init__(self, *args, subject, **kwargs):
        # subject is an explicit keyword argument, not something fished out of
        # request or a parent object. Phase C's view calls
        # RelationshipForm(subject=contact) and nothing else changes.
        super().__init__(*args, **kwargs)
        self.subject = subject
        self.fields["direction_choice"].choices = direction_choices(subject)
        self.fields["other"].queryset = Contact.objects.filter(
            is_active=True).exclude(pk=subject.pk)

    def clean(self):
        cleaned = super().clean()
        value = cleaned.get("direction_choice")
        other = cleaned.get("other")
        if value and other:
            relationship_type, subject_is_a = parse_direction_choice(value)
            self.instance.relationship_type = relationship_type
            self.instance.contact_a, self.instance.contact_b = orient(
                subject=self.subject, other=other, subject_is_a=subject_is_a,
            )
            self._check_constraints()
        return cleaned

    def _check_constraints(self):
        """Validate the constraints ModelForm would otherwise skip.

        _post_clean excludes every field that is not on the form, and
        Model.validate_constraints() then skips any constraint mentioning an
        excluded field. contact_a and contact_b are not on this form, so the
        self-reference and duplicate-pair rules would never run here — the first
        sign of trouble would be an IntegrityError 500 on save. This is exactly
        the D14 pitfall, reached from the other end.
        """
        try:
            self.instance.validate_constraints()
        except ValidationError as error:
            for field, messages in error.message_dict.items():
                target = self.FIELD_ALIASES.get(field, field)
                self.add_error(target if target in self.fields else None, messages)
