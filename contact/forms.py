import json

from django import forms
from django.core.exceptions import ValidationError
from localflavor.us.us_states import STATE_CHOICES

from .models import Contact


def us_state_choices_json():
    """The US state list the state picker reads, as JSON.

    One function because two forms hand it to the same script: this app's admin
    form and accounts.ProfileForm. Written out in either of them would be two
    copies of a list that has to agree — and the copy nobody is looking at is
    the one that goes stale when localflavor adds a territory.
    """
    return json.dumps([[code, str(name)] for code, name in STATE_CHOICES])


class ContactAdminForm(forms.ModelForm):
    """Form for Contact — currently shown by the admin, not written for it.

    The name-vs-contact-type rule lives in database constraints so it holds on
    every save path; this form feeds the admin JS its state list, and applies the
    graded duplicate check (B4.3b).

    ⚠️ force_save is a virtual field (never stored) and the duplicate check is a
       call into the model. Both are django.forms, not admin — Phase C's create
       page imports this class as it stands.
    """

    force_save = forms.BooleanField(
        required=False, label="Not a duplicate — save anyway")

    class Meta:
        model = Contact
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # address_state is a plain text field so non-US regions can be typed in.
        # address_state_toggle.js builds the US-state dropdown from this list.
        self.fields["address_state"].widget.attrs["data-us-states"] = us_state_choices_json()
        # ⚠️ The checkbox appears or hides here, decided from the submitted data
        #    — NOT by editing self.fields[...].widget inside clean() and then
        #    raising. On a second submission carrying some other validation
        #    error, that ordering flips the checkbox back to hidden and the user
        #    is left believing they never ticked it.
        if not self._duplicate_hit_in_data():
            self.fields["force_save"].widget = forms.HiddenInput()

    def _submitted(self, name):
        return (self.data or {}).get(self.add_prefix(name), "")

    def _cleaned_phone(self):
        """The submitted phone in E.164, or "" — needed before clean() runs."""
        raw = self._submitted("phone")
        if not raw:
            return ""
        try:
            return self.fields["phone"].clean(raw)
        except ValidationError:
            return ""

    def _duplicate_hit_in_data(self):
        if not self.data:
            return False
        return Contact.find_exact_duplicates(
            last_name=self._submitted("legal_last_name"),
            first_name=self._submitted("legal_first_name"),
            phone=self._cleaned_phone(),
            exclude_pk=self.instance.pk,
        ).exists()

    def clean(self):
        cleaned = super().clean()
        # Graded, and the grading is the point (goal.md「Contact 重名的处理」):
        #   same name only      -> warning after the fact, never blocks
        #   same name AND phone -> blocked here, until force_save is ticked
        # The judgement itself belongs to the model; this only reacts to it.
        duplicates = Contact.find_exact_duplicates(
            last_name=cleaned.get("legal_last_name"),
            first_name=cleaned.get("legal_first_name"),
            phone=cleaned.get("phone"),
            exclude_pk=self.instance.pk,
        )
        if duplicates.exists() and not cleaned.get("force_save"):
            # Blocking on save rather than warning afterwards: a warning arrives
            # once the duplicate row is already in the table, and somebody then
            # has to go back and delete it.
            names = "、".join(str(contact) for contact in duplicates[:3])
            raise ValidationError({
                "force_save": (
                    f"A record with the same name and number already exists: {names}. "
                    "If this really is a different person, tick this box and save again."
                ),
            })
        return cleaned
