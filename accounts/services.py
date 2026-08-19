"""Creating a login. Permanent asset (D18).

P1 in one sentence: every new account also creates a Contact. That rule lives
here, in a function, and not as null=False on User.contact — see below.
"""

import secrets

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string

from contact.models import Contact
from core.timeutils import local_now

from .models import EmailVerification


@transaction.atomic
def register_account(
    *, email, password, legal_first_name="", legal_last_name="",
    phone="", birth_date=None, **contact_fields,
):
    """Create a login and the Contact behind it, together. Returns the User.

    ⚠️ `email` is the login name (2026-08-06) and therefore required, where it
       used to be an optional extra beside `username`. The Contact gets the same
       address: they are one value, and the day they are two is the day somebody
       changes their address in "My profile" and their password reset keeps going
       to the old one. See accounts/models.py for why the *column* is not
       duplicated either.

    ⚠️ One transaction, and it matters. Half a registration — a User with no
       Contact — is worse than none: the person can log in, every page that
       reads request.user.contact fails for them, and nothing in the data says
       why. Either both rows exist or neither does.

    ⚠️ Do not "tidy" this into User.contact being non-nullable. A superuser is a
       technical account matching no real person (D12), and createsuperuser has
       no Contact to offer. P1 is a rule about *this* flow, not about the
       column, and D9's own test says so: a rule with a legitimate exception
       does not belong in a constraint, because a constraint admits no
       exceptions. D21's third requirement spells this out.

    The account is deliberately plain: is_staff=False, is_superuser=False, no
    groups. Volunteers do not belong in the admin, and /admin/ has to actually
    refuse them rather than merely not link there.
    """
    contact = Contact.objects.create(
        contact_type=Contact.ContactType.INDIVIDUAL,
        legal_first_name=legal_first_name,
        legal_last_name=legal_last_name,
        email=email,
        phone=phone,
        birth_date=birth_date,
        **contact_fields,
    )
    user = get_user_model().objects.create_user(
        email=email,
        password=password,
        first_name=legal_first_name,
        last_name=legal_last_name,
        contact=contact,
    )
    return user


def set_login_email(user, email):
    """Point an account at a new address — login name and contact record together.

    The one function that knows this is two rows, so that no caller has to
    remember it is. `User.email` is what the person types at the login box and
    what a password reset is sent to; `Contact.email` is what P6 notifies and
    what the signup confirmation goes to. They are the same address, and the
    failure mode of letting them drift is invisible: the profile page shows the
    new address, the reset email goes to the old one, and nothing anywhere
    reports a difference.

    ⚠️ Normalised through the manager, so the stored value matches what
       `get_by_natural_key()` will look for.
    """
    # ⚠️ `get_user_model()` and not `type(user)`: a view hands this
    #    `request.user`, which is a SimpleLazyObject, and `type()` of that is the
    #    wrapper rather than the model. It has no manager, so the normalisation
    #    step turned into an AttributeError on the ordinary save path.
    user.email = get_user_model().objects.normalize_email(email)
    user.save(update_fields=["email"])
    contact = user.contact
    if contact is not None and contact.email != user.email:
        contact.email = user.email
        contact.save(update_fields=["email"])
    return user


# --- Email verification (2026-08-19) -----------------------------------------
#
# Registration used to create the account and log the person straight in, so
# the address on it was whatever had been typed into the box. Nothing checked,
# and nothing raised: a typo produces somebody who cannot be reached — no reset
# link, no signup confirmation, no "the event has moved" — and a stranger's
# address produces an account that makes this application send that stranger
# our mail.
#
# The flow is deliberately the strict one (decided 2026-08-19): the account is
# created, but nobody is logged in until a code has been typed back.
#
# ⚠️ The code is a **six-digit number in the mail**, not a link. A link would be
#    less code (Django's own token machinery does resets) but it also has to
#    survive being opened in a different browser than the one the form is in —
#    which is exactly what happens on a phone, and would mean somebody
#    "verified" in a session that is not the one they were registering in.


class VerificationError(Exception):
    """Refusing a code, with a sentence for the person who typed it.

    One exception with a message, rather than four subclasses: every caller does
    the same thing with it (put it under the box), and the *reason* differs only
    in the words. See `check_verification_code` for what each one has to say and
    why it is allowed to say it.
    """


def _new_code():
    """Six digits, from the cryptographic generator, zero-padded.

    ⚠️ `secrets`, not `random`. `random` is a Mersenne Twister seeded from the
       clock — a few observed codes are enough to predict the next one, and this
       code is the only thing standing between a stranger and an account made
       with somebody else's address.

    ⚠️ Zero-padded and kept as a string all the way through. As an integer,
       "004821" comes back as 4821, and the person typing what the mail said is
       told they are wrong.
    """
    upper = 10 ** EmailVerification.CODE_LENGTH
    return f"{secrets.randbelow(upper):0{EmailVerification.CODE_LENGTH}d}"


def issue_verification_code(user):
    """Make a code for `user`'s current address and store it hashed.

    Returns the code in the clear — it has exactly one caller, the one that puts
    it in the mail. Nothing else may hold on to it.

    ⚠️ Any earlier live code is spent as a side effect. Two live codes means the
       older mail still works, so a person who asked for a resend because they
       suspected somebody was reading the first one gains nothing.
    """
    now = local_now()
    user.email_verifications.filter(consumed_at__isnull=True).update(consumed_at=now)
    code = _new_code()
    EmailVerification.objects.create(
        user=user,
        email=user.email,
        code_hash=make_password(code),
        expires_at=now + EmailVerification.VALID_FOR,
    )
    return code


def send_verification_code(user):
    """Issue a code and mail it. Returns the EmailVerification row.

    ⚠️ Plain `send_mail`, not core/notifications. The same call the password
       reset makes, and for the same reason: this is account plumbing, not a
       volunteering notification, and it must work on a deployment where the
       notification provider is not configured yet. See config/settings/dev.py.
    """
    code = issue_verification_code(user)
    context = {"code": code, "email": user.email,
               "minutes": int(EmailVerification.VALID_FOR.total_seconds() // 60)}
    send_mail(
        subject=render_to_string(
            "accounts/verification_subject.txt", context).strip(),
        message=render_to_string("accounts/verification_email.txt", context),
        from_email=None,          # DEFAULT_FROM_EMAIL
        recipient_list=[user.email],
    )
    return user.email_verifications.first()


def live_verification(user):
    """This account's newest code if it can still be typed, else None."""
    latest = user.email_verifications.first()
    if latest is None or latest.is_spent:
        return None
    return latest


def seconds_until_resend(user):
    """How long before another code may be asked for. 0 when it may be now.

    ⚠️ Counted from when the last code was **created**, not from when the
       person last pressed anything: the thing being rationed is outbound mail.
    """
    latest = user.email_verifications.first()
    if latest is None:
        return 0
    ready_at = latest.created_at + EmailVerification.RESEND_AFTER
    return max(0, int((ready_at - local_now()).total_seconds()))


def check_verification_code(user, code):
    """Check `code` against this account's newest one. Marks it verified, or raises.

    ⚠️ Every refusal here says **which** thing went wrong — expired, too many
       tries, or simply wrong. That does not leak anything: whoever is typing
       already has to be holding this session, and telling somebody "that code
       has expired" instead of "wrong code" is the difference between pressing
       resend and typing the same six digits again, slower.

    🔴 **The refusal is raised outside the transaction, and that is the whole
       shape of this function.** A wrong guess has to cost an attempt, and an
       attempt is a write — so raising from inside `atomic()` rolls the
       increment back with it. Every wrong guess would then be free, and
       MAX_ATTEMPTS would never be reached: five wrong guesses, `attempts`
       still 0, and the only thing standing between a stranger and a
       million-wide space would be the fifteen minutes.

       Found by the test that asserts `attempts == 1`, not by reading it — the
       first draft of this function was decorated `@transaction.atomic` and had
       a comment claiming exactly the behaviour it did not have.

    ⚠️ The address is compared against the one recorded on the row. A code
       proves the address it was sent to; if the account's address changed in
       between, that code proves nothing about the new one.
    """
    with transaction.atomic():
        attempt = (user.email_verifications
                   .select_for_update()
                   .filter(consumed_at__isnull=True)
                   .first())
        # These two refuse without writing anything, so rolling back is not a
        # thing that can happen to them.
        if attempt is None or attempt.is_spent:
            raise VerificationError(
                "That code has expired or has already been used. "
                "Ask for a new one below.")
        if attempt.email != user.email:
            raise VerificationError(
                "Your email address changed after that code was sent, so it no "
                "longer applies. Ask for a new one below.")

        attempt.attempts += 1
        attempt.save(update_fields=["attempts", "updated_at"])
        correct = check_password(code, attempt.code_hash)
        if correct:
            attempt.consumed_at = local_now()
            attempt.save(update_fields=["consumed_at", "updated_at"])
            mark_email_verified(user)
            return attempt
        left = EmailVerification.MAX_ATTEMPTS - attempt.attempts

    # Committed: the guess is counted whatever the caller does with this.
    if left <= 0:
        raise VerificationError(
            "That code is wrong, and it has now been tried too many times. "
            "Ask for a new one below.")
    raise VerificationError(
        f"That code is wrong. {left} more "
        f"{'try' if left == 1 else 'tries'} before it stops working.")


def mark_email_verified(user):
    """The one place the flag is set, so every path that sets it is findable.

    Called from `check_verification_code` and from the Google branch of
    registration, which is the only other way an address gets proved.
    """
    if not user.email_verified:
        user.email_verified = True
        user.save(update_fields=["email_verified"])
    return user
