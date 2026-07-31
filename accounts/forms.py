"""The registration form. Permanent asset: plain django.forms, no admin (D18)."""

from django import forms
from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError
from phonenumber_field.formfields import PhoneNumberField


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

    username = forms.CharField(max_length=150, label="用户名")
    email = forms.EmailField(label="邮箱")
    password = forms.CharField(widget=forms.PasswordInput, label="密码")
    legal_last_name = forms.CharField(max_length=100, label="姓")
    legal_first_name = forms.CharField(max_length=100, required=False, label="名")
    phone = PhoneNumberField(region="US", required=False, label="电话")
    # Collected, and not optional-by-accident: P3's minor check reads it, and
    # is_minor treats a missing date as "unknown", which takes the cautious
    # branch (consent required). Left blank on purpose is fine; the person then
    # walks the consent flow.
    birth_date = forms.DateField(
        required=False, label="生日",
        help_text="不填的话，报名时会按未成年人处理（需要家长同意）。",
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def clean_username(self):
        username = self.cleaned_data["username"]
        if get_user_model().objects.filter(username__iexact=username).exists():
            raise ValidationError("这个用户名已经有人用了。")
        return username

    def clean_password(self):
        # Django's configured validators, so the rules are the ones in settings
        # rather than a second opinion invented here.
        password = self.cleaned_data["password"]
        password_validation.validate_password(password)
        return password
