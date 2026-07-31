import json

from django import forms
from django.core.exceptions import ValidationError
from localflavor.us.us_states import STATE_CHOICES

from .models import Contact


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
        required=False, label="确认不是重复人员，强制保存")

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
                    f"已有同名同号的记录：{names}。"
                    "确认这是另一个人的话，勾选本框再保存。"
                ),
            })
        return cleaned
