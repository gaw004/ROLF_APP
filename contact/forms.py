import json

from django import forms
from localflavor.us.us_states import STATE_CHOICES

from .models import Contact


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
