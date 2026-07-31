"""Creating a login. Permanent asset (D18).

P1 in one sentence: every new account also creates a Contact. That rule lives
here, in a function, and not as null=False on User.contact — see below.
"""

from django.contrib.auth import get_user_model
from django.db import transaction

from contact.models import Contact


@transaction.atomic
def register_account(
    *, username, password, email="", legal_first_name="", legal_last_name="",
    phone="", birth_date=None, **contact_fields,
):
    """Create a login and the Contact behind it, together. Returns the User.

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
        username=username,
        email=email,
        password=password,
        first_name=legal_first_name,
        last_name=legal_last_name,
        contact=contact,
    )
    return user
