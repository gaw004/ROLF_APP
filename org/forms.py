"""Forms for the org pages. Permanent asset: plain django.forms, no admin (D18).

Here rather than in events/forms.py, where this started. The subject of P5 is a
ministry, so by D17 it belongs to this app — and the import direction settles
it: events depends on org (Event -> Ministry), so org reaching back into events
inverts the one dependency INSTALLED_APPS spells out. Same lesson as the
cross-app admin assembly in B5: the arrow points one way, and a form is not an
exception to it.
"""

from django import forms

from contact.models import Contact


class GrantForm(forms.Form):
    """P5: appoint somebody as a ministry's admin.

    A plain Form: granted_by comes from the session, not from the page, and a
    field somebody could type into would be a field somebody could lie in.
    """

    contact = forms.ModelChoiceField(queryset=None, label="谁")
    start_date = forms.DateField(
        required=False, label="生效日", widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["contact"].queryset = Contact.objects.filter(
            is_active=True, contact_type=Contact.ContactType.INDIVIDUAL)
