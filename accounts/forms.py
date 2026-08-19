"""The volunteer-facing account forms. Plain django.forms, no admin (D18)."""

from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm
from django.core.exceptions import ValidationError
from django.core.validators import MaxLengthValidator
from phonenumber_field.formfields import PhoneNumberField

from contact.forms import us_state_choices_json
from contact.models import Contact, EmergencyContact, RelationshipType
from core.limits import EMAIL, PASSWORD


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

    # ⚠️ There is no username box (2026-08-06). The address *is* the login name,
    #    so asking for a second handle was asking somebody to invent and remember
    #    a thing that identified nothing.
    # ⚠️ forms.EmailField has **no default max_length**, and User.email is
    #    varchar(254). Without this a 400-character address passed validation
    #    and blew up at the INSERT — a 500 on the most ordinary path in the
    #    project, and one that looked like a database problem rather than an
    #    unvalidated form.
    email = forms.EmailField(max_length=EMAIL, label="Email")
    # ⚠️ Capped for a different reason than the rest: nothing long is *stored*
    #    (a hash is 78 bytes whatever went in), but hashing is deliberately
    #    slow, so the submitted length is CPU we spend before we store
    #    anything. See core/limits.py.
    password = forms.CharField(
        widget=forms.PasswordInput, max_length=PASSWORD, label="Password")
    # ⚠️ Both required (2026-08-19). The first name used to be optional here
    #    while the last name was not, which is the shape of a rule nobody
    #    decided: `Contact` has carried a database constraint on the last name
    #    since D9 and none on the first, so the form simply inherited that
    #    asymmetry. Both columns now have one, and this page is where a
    #    volunteer meets it — the red `*` next to each box comes from
    #    `required`, via core/components/field.html.
    legal_last_name = forms.CharField(max_length=100, label="Last name")
    legal_first_name = forms.CharField(max_length=100, label="First name")
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

    def clean_email(self):
        """One account per address, checked case-insensitively.

        ⚠️ `iexact`, matching the `user_email_taken` constraint and
           `UserManager.get_by_natural_key()`. An exact check here would let
           "Mei@x.com" through when "mei@x.com" exists — the constraint would
           then refuse the INSERT, and the person would get a 500 instead of a
           sentence under the box.

        ⚠️ This does tell an anonymous visitor that a given address has an
           account here. Accepted: the alternative is a registration form that
           swallows the address and reports success, leaving somebody waiting for
           an account that was never made.
        """
        email = self.cleaned_data["email"].strip().lower()
        if get_user_model().objects.filter(email__iexact=email).exists():
            raise ValidationError(
                "An account with that email address already exists. "
                "Log in instead, or use a different address.")
        return email

    def clean_password(self):
        # Django's configured validators, so the rules are the ones in settings
        # rather than a second opinion invented here.
        password = self.cleaned_data["password"]
        password_validation.validate_password(password)
        return password


class VolunteerPasswordChangeForm(PasswordChangeForm):
    """Django's own change form, relabelled and capped.

    Subclassed rather than used directly for two reasons, neither cosmetic:

    ⚠️ Django's labels are "Old password", "New password" and "New password
       confirmation". The third one is the problem — it reads as a fourth thing
       to think about rather than as "type it again", which is the single most
       common place a change-password form goes wrong.

    ⚠️ `SetPasswordForm`'s new-password fields carry **no max_length**, so
       without this a submitted megabyte would be hashed. Same reasoning as
       RegistrationForm, same constant. The old-password field is capped too:
       it is hashed as well, to compare against the stored hash.

    The validators, the "your two entries differ" check and the old-password
    check are all Django's. None of that is re-implemented here.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        labels = {
            "old_password": "Current password",
            "new_password1": "New password",
            "new_password2": "Confirm new password",
        }
        for name, label in labels.items():
            self.fields[name].label = label
            self.fields[name].max_length = PASSWORD
            self.fields[name].validators.append(MaxLengthValidator(PASSWORD))


class VolunteerSetPasswordForm(SetPasswordForm):
    """The form behind a reset link. Same two caps as the change form.

    ⚠️ The cap is the whole reason this class exists, and it is the same hole
       VolunteerPasswordChangeForm was written to close: `SetPasswordForm`'s two
       fields carry **no max_length**, so a submitted megabyte is hashed. That
       one is reachable while logged in; this one is reachable by anybody
       holding a link, which is the wider door of the two.

    ⚠️ Subclassed rather than reusing VolunteerPasswordChangeForm: that one also
       asks for the current password, and somebody arriving here is precisely
       the person who does not have it.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        labels = {
            "new_password1": "New password",
            "new_password2": "Confirm new password",
        }
        for name, label in labels.items():
            self.fields[name].label = label
            self.fields[name].max_length = PASSWORD
            self.fields[name].validators.append(MaxLengthValidator(PASSWORD))


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
            # 🔴 **Country first** (2026-08-19), matching the Contact admin's
            #    fieldset. It is the field the next one depends on: pick United
            #    States and "State or province" becomes a dropdown of the 50
            #    states. Asked last, somebody types their state into a free-text
            #    box and then watches it turn into a dropdown underneath them —
            #    or, worse, fills the whole address in and never learns the
            #    dropdown existed.
            "address_country",
            "address_street", "address_city", "address_state",
            "address_postal_code",
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
            "email": (
                "This is also the address you log in with. Changing it here "
                "changes your login."
            ),
        }

    def __init__(self, *args, user, **kwargs):
        # `user` is explicit rather than reached for through `instance.user`,
        # matching every other form in this project — and because that reverse
        # accessor raises when a Contact has no login, which is the normal state
        # for somebody a ministry admin wrote down on the day.
        self.user = user
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
        # 🔴 Both names required, and this line is load-bearing in a way that is
        #    easy to miss (2026-08-19).
        #
        #    The columns are `blank=True` — a ministry admin writing somebody
        #    down on the day may only have half a name, and an organisation has
        #    neither — so ModelForm builds both boxes optional. Meanwhile the
        #    *database* refuses an individual without them. Clearing the last
        #    name here therefore validated cleanly and blew up at the INSERT:
        #    an IntegrityError 500 on My profile, from a form that reported no
        #    error at all.
        #
        #    ⚠️ And Django's own constraint validation does not catch it. A
        #       ModelForm checks constraints in `_post_clean`, but it skips any
        #       constraint that mentions a field the form does not render — and
        #       both of these mention `contact_type`, which this form
        #       deliberately does not offer (see the closing note). So the one
        #       thing standing between a volunteer and that 500 is this line.
        #       The admin never hit it only because ContactAdminForm is
        #       `fields = "__all__"`, so `contact_type` is on it.
        #
        #    Guard: accounts.tests.ProfileNameTests.
        self.fields["legal_first_name"].required = True
        self.fields["legal_last_name"].required = True
        # The address is optional in full. A postcode without a street is not
        # wrong, it is partial, and refusing it would only teach people to type
        # something false into the other boxes.
        for name in ["address_street", "address_city", "address_state",
                     "address_postal_code", "address_country"]:
            self.fields[name].required = False
        # The US state picker, same as the Contact admin's (2026-08-19).
        #
        # ⚠️ The column stays free text and this stays optional — a dropdown of
        #    US states cannot say "Ontario", and somebody with a foreign address
        #    must not be pushed into picking a wrong answer or leaving it blank.
        #    The dropdown is drawn by contact/static/contact/address_state_toggle.js
        #    only while the country is US, and it writes into this box rather
        #    than submitting anything of its own.
        #
        # ⚠️ `data-us-states` is data handed to a script, not styling — which is
        #    what the styling-lives-in-CSS rule for widget attrs is about. Same
        #    attribute, same source (contact.forms.us_state_choices_json), so
        #    the two pages cannot come to different lists of states.
        self.fields["address_state"].widget.attrs["data-us-states"] = us_state_choices_json()

    def clean_email(self):
        """The address is the login name, so it has to stay unique across accounts.

        ⚠️ Two Contacts may share an address and always could — a family with one
           inbox, an organisation and its coordinator — so this is checked
           against **User**, not Contact. Excluding this person's own account is
           the whole trick: without the exclude, saving the page without touching
           the email box would report that the address is taken, by themselves.
        """
        email = self.cleaned_data["email"].strip().lower()
        clash = get_user_model().objects.filter(email__iexact=email)
        if self.user is not None:
            clash = clash.exclude(pk=self.user.pk)
        if clash.exists():
            raise ValidationError(
                "Another account already uses that email address. "
                "Since it is also your login name, two accounts cannot share it.")
        return email

    def save(self, commit=True):
        """Save the person, then point their login at the same address.

        Two rows, one value — see services.set_login_email() for why this is not
        left to the caller. The same shape as EventRoleForm.save(), which creates
        the vocabulary entry before the row that points at it.
        """
        contact = super().save(commit=commit)
        if commit and self.user is not None:
            from .services import set_login_email

            set_login_email(self.user, contact.email)
        return contact

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

    def _get_validation_exclusions(self):
        """Keep `person` in play, so the "already recorded" constraint is checked.

        🔴 Found on 2026-08-19 while auditing for the trap that took My profile
           down, and it is the *same* trap: `emergencycontact_unique_per_person`
           spans (person, name, phone), `person` is set on the instance rather
           than rendered, and Django drops any constraint that mentions a field
           it has excluded from validation. So adding the same person twice —
           tapping Add twice, or writing Mum down again a year later —
           validated cleanly and raised IntegrityError at the INSERT: a 500 on
           My profile, from a form reporting no error.

        ⚠️ The rule itself is not restated here (D14): the one statement of it
           is the constraint, and putting `person` back is what lets Django ask
           the constraint. The sentence the volunteer reads is the constraint's
           own `violation_error_message`, landed on the name box by
           `CONSTRAINT_FIELD["emergency_contact_duplicate"]`.
        """
        exclude = super()._get_validation_exclusions()
        exclude.discard("person")
        return exclude
