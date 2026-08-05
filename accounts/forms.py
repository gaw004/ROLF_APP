"""The volunteer-facing account forms. Plain django.forms, no admin (D18)."""

from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError
from phonenumber_field.formfields import PhoneNumberField

from contact.models import Contact, EmergencyContact, RelationshipType


class RegistrationForm(forms.Form):
    """Account details plus the few Contact fields the rest of the phase needs.

    A plain Form rather than a ModelForm: it writes two tables, and
    register_account() is what knows how. The form's job is to check the input.

    ⚠️ ContactForm's same-name-same-number hard block is deliberately NOT
       applied here. That block exists for a member of staff typing somebody in
       ("did you already enter this person?"); on a self-service signup it would
       tell a genuine new volunteer that they already exist while giving them no
       way in. Register them, and let merge_contacts() sort duplicates out
       afterwards — it walks Contact._meta.related_objects, so signups and
       grants are covered automatically.
    """

    username = forms.CharField(max_length=150, label="Username")
    email = forms.EmailField(label="Email")
    password = forms.CharField(widget=forms.PasswordInput, label="Password")
    legal_last_name = forms.CharField(max_length=100, label="Last name")
    legal_first_name = forms.CharField(max_length=100, required=False, label="First name")
    phone = PhoneNumberField(region="US", required=False, label="Phone")
    # Collected, and not optional-by-accident: P3's minor check reads it, and
    # is_minor treats a missing date as "unknown", which takes the cautious
    # branch (consent required). Left blank on purpose is fine; the person then
    # walks the consent flow.
    birth_date = forms.DateField(
        required=False, label="Date of birth",
        help_text="Leave this blank and we will treat you as under 18, which "
                  "means signups ask for a guardian's consent.",
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def clean_username(self):
        username = self.cleaned_data["username"]
        if get_user_model().objects.filter(username__iexact=username).exists():
            raise ValidationError("That username is already taken.")
        return username

    def clean_password(self):
        # Django's configured validators, so the rules are the ones in settings
        # rather than a second opinion invented here.
        password = self.cleaned_data["password"]
        password_validation.validate_password(password)
        return password


class ProfileForm(forms.ModelForm):
    """The volunteer's own details, editable by the volunteer.

    Without this page every one of these fields was a dead end. A birth date
    left blank at registration makes Contact.is_minor return None, and the
    cautious branch means that person is asked for guardian consent at every
    single signup, forever, with no way to correct it themselves. A mistyped
    email or phone puts them on P6's "unreachable" list, which only a staff
    account could then fix.

    ⚠️ birth_date is freely editable, decided 2026-07-31. The cost is real and
       accepted, not overlooked: a minor can raise their own birth date and
       walk past the consent gate in services.sign_up(). The mitigation is that
       Contact carries simple-history, so the change is on the record. See
       phase-c.md's known-gaps table.
    """

    class Meta:
        model = Contact
        fields = [
            "legal_first_name", "legal_last_name", "email", "phone",
            "birth_date", "preferred_communication_method", "preferred_language",
            "address_street", "address_city", "address_state",
            "address_postal_code", "address_country",
        ]
        widgets = {"birth_date": forms.DateInput(attrs={"type": "date"})}
        labels = {
            "legal_first_name": "First name",
            "legal_last_name": "Last name",
            "email": "Email",
            "phone": "Phone",
            "birth_date": "Date of birth",
            "preferred_communication_method": "Preferred contact method",
            # The field has been on Contact since the data-core phase, narrowed
            # to living languages. It was simply never offered to the person it
            # describes — only a staff account could set it.
            "preferred_language": "Preferred language",
            "address_street": "Street",
            "address_city": "City",
            "address_state": "State or province",
            "address_postal_code": "Postal code",
            "address_country": "Country",
        }
        help_texts = {
            "birth_date": (
                "Leave this blank and signups will ask for a guardian's consent, "
                "because we cannot tell whether you are under 18."
            ),
            "email": "Used for password resets and for notices about your events.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # ⚠️ Required here, and deliberately not by a database constraint.
        #    Contact.email stays blank=True at the model level because the same
        #    table holds people a ministry admin wrote down on the day and
        #    organisations, neither of which logs in or has an inbox. What must
        #    hold is narrower: an account that can log in can be got back into
        #    (password reset) and can be told its signup went through. That is a
        #    property of accounts, so it is enforced where accounts are made —
        #    RegistrationForm — and here, where their owner edits them.
        self.fields["email"].required = True
        # The address is optional in full. A postcode without a street is not
        # wrong, it is partial, and refusing it would only teach people to type
        # something false into the other boxes.
        for name in ["address_street", "address_city", "address_state",
                     "address_postal_code", "address_country"]:
            self.fields[name].required = False

    # contact_type is deliberately absent: this form edits a person, and
    # offering "organisation" here would break the name constraints in a way
    # the volunteer cannot understand or undo.


class EmergencyContactForm(forms.ModelForm):
    """Somebody to call. All four fields are required, by the model's design.

    Reachable by the volunteer because P6's guardian fallback reads this table
    when a minor has no consent address, and the attendance page shows the
    number on the day — both of which were unfillable from outside the admin.
    """

    class Meta:
        model = EmergencyContact
        fields = ["name", "phone", "email", "relationship_type"]
        labels = {
            "name": "Their name",
            "phone": "Their phone",
            "email": "Their email",
            "relationship_type": "They are your…",
        }
        help_texts = {
            "email": (
                "Required. If you are under 18 this is where we tell them the "
                "event has changed, and email reaches them for free where a "
                "text message does not."
            ),
            "relationship_type": (
                "Read it as a sentence: “Wang Xiuying is your parent.” "
                "Pick what this person is to you, not the other way round."
            ),
        }

    def __init__(self, *args, person, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.person = person
        # The same narrowing the model declares, so the page cannot offer a
        # type the FK would refuse (limit_choices_to is not applied to a plain
        # ModelChoiceField built from the field's full queryset).
        self.fields["relationship_type"].queryset = (
            RelationshipType.objects.filter(usable_as_emergency_contact=True)
        )
