"""Creating a login. Permanent asset (D18).

P1 in one sentence: every new account also creates a Contact. That rule lives
here, in a function, and not as null=False on User.contact — see below.
"""

from django.contrib.auth import get_user_model
from django.db import transaction

from contact.models import Contact


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
