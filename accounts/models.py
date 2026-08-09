from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower

from core.limits import EMAIL


class UserManager(BaseUserManager):
    """Accounts are made by email address. Replaces Django's username manager.

    Django's own UserManager takes `username` as its first positional argument
    and nothing here has a username, so it had to be replaced rather than
    subclassed at the edges: `createsuperuser` calls `create_superuser()` with
    the USERNAME_FIELD, and a manager whose signature says `username` would
    make that command fail with a TypeError from inside Django.

    ⚠️ Addresses are lowercased **in full**, and that is not what
       `BaseUserManager.normalize_email()` does — it lowercases the domain only,
       because the local part is technically case-sensitive. Technically true
       and wrong here: this column is the login name, and "Mei@x.com" typed at
       the login box has to find the account created as "mei@x.com". Storing
       exactly what was typed means the person who capitalised their name on
       Tuesday cannot log in on Wednesday, and nothing about the message they
       get would tell them why.

       The database backs this up rather than trusting it: see the
       `user_email_taken` constraint below, which is what a shell script or a
       bulk path meets.
    """

    use_in_migrations = True

    @classmethod
    def normalize_email(cls, email):
        return super().normalize_email(email).strip().lower()

    def _create_user(self, email, password, **extra_fields):
        # Refused here rather than left to the unique constraint: an account
        # with no address cannot be reset, cannot be told its signup went
        # through, and — now that the address *is* the login — cannot log in
        # either. There is no useful "some of it" state to allow.
        if not email:
            raise ValueError("An account needs an email address; it is the login name.")
        user = self.model(email=self.normalize_email(email), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email=None, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email=None, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("A superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("A superuser must have is_superuser=True.")
        return self._create_user(email, password, **extra_fields)

    def get_by_natural_key(self, username):
        """Look an account up by address, ignoring case.

        ⚠️ This is the method `ModelBackend.authenticate()` and the password
           reset both go through, and Django's version does an exact match. With
           an exact match, a row written in mixed case by any path that skipped
           the manager — a shell session, a fixture, a migration — becomes an
           account nobody can log into, and the message says "wrong password".

           Safe against MultipleObjectsReturned precisely because of the
           `user_email_taken` constraint: the database will not hold two rows
           that differ only in case.
        """
        return self.get(**{f"{self.model.USERNAME_FIELD}__iexact": username})


class User(AbstractUser):
    """A login account. Optionally one-to-one with a Contact (see goal.md D12).

    Three concepts that are deliberately kept separate: Contact is who someone
    is, Assignment (Phase B) is what post they hold, and User is whether they
    can log in and what they can see.

    `contact` is nullable on purpose: a superuser is a purely technical account
    corresponding to no real person, and not every employee needs a login. It
    hangs off Contact rather than Assignment because a person may hold several
    posts but should still have only one account.

    Permissions go through Django Groups. Do not add an `is_employee` style
    field and branch on it — employment and access are different questions.

    ⚠️ **The login name is the email address** (2026-08-06). `username` is gone
       from the table, not merely unused. The alternative on the table was to
       keep the column and copy the address into it, which is cheaper by a long
       way — no manager, no admin rewrite, no migration — and it stores the same
       value in two columns that are then free to disagree. The whole reason
       this project has `MinistryRole` in one module and `timeutils` in another
       is that the copy which drifts is always the one nobody remembers is
       there. A second copy of somebody's login name is not the place to make
       an exception.

       What it costs, said plainly: `createsuperuser` now asks for an email,
       every `User.objects.create_user(username=…)` call had to change, and
       accounts made before this (all of them demo data) needed an address
       invented for them — see migration 0002.
    """

    # ⚠️ Removing the field, not blanking it. `username = None` is Django's
    #    documented way to drop a field an abstract parent declared; leaving it
    #    in place "just in case" would leave a unique NOT NULL column that every
    #    insert has to invent a value for.
    username = None

    # blank=False and unique, unlike AbstractUser's. Both matter: the address is
    # how the account is identified, and phase-c.md's rule is that an account
    # which can log in can be got back into.
    email = models.EmailField(
        "email address", max_length=EMAIL, unique=True,
        error_messages={"unique": "An account with that email address already exists."},
    )

    USERNAME_FIELD = "email"
    # ⚠️ Must not contain USERNAME_FIELD — Django's auth.E002 check fails
    #    otherwise, and AbstractUser's value is ["email"], which is now exactly
    #    that. Empty means `createsuperuser` asks for the address and a
    #    password and nothing else.
    REQUIRED_FIELDS = []

    objects = UserManager()

    contact = models.OneToOneField(
        "contact.Contact",
        on_delete=models.SET_NULL,     # deleting a contact shouldn't delete their account
        null=True, blank=True,
        related_name="user",
    )

    class Meta(AbstractUser.Meta):
        constraints = [
            # Case-insensitive uniqueness, in the database, on top of the
            # field's own unique=True.
            #
            # ⚠️ Both are needed and neither is redundant. `unique=True` is what
            #    Django's auth.E003 check requires of a USERNAME_FIELD, and it
            #    is exact — "Mei@x.com" and "mei@x.com" are two rows to it, and
            #    two accounts for one inbox is one person who cannot tell which
            #    of them they are logging into. This constraint is the rule
            #    itself, in the only place that admits no exceptions (D9): the
            #    manager lowercases, but `save()` does not call `clean()` and a
            #    bulk path calls neither.
            models.UniqueConstraint(
                Lower("email"),
                name="user_email_taken",
                violation_error_code="user_email_taken",
                violation_error_message=(
                    "An account with that email address already exists."),
            ),
        ]

    def display_name(self):
        """What to call this person in the interface. Never their login name.

        ⚠️ The shared top bar used to print `get_username()`, which was a short
           handle like "lisi" and is now a whole email address — 30-odd
           characters of somebody's private address across the header of every
           page, including whatever is on the screen behind them at a
           volunteering event. A name is what belongs there.

        Falls through rather than picking one source: the Contact is the record
        of who somebody is, the User's own name fields are what registration
        filled in, and the address is the last resort because a technical
        account (a superuser, D12) has nothing else.
        """
        contact = self.contact
        if contact is not None:
            return contact.plain_name
        return self.get_full_name() or self.email

    def clean(self):
        """Normalise the address the same way the manager does.

        `clean()` is what every ModelForm calls — ours and the admin's — so an
        address typed in the admin lands lowercased like one that came through
        registration. It is not a guarantee (`save()` does not call this, D9's
        standing caveat); the `user_email_taken` constraint is the guarantee.
        """
        super().clean()
        self.email = self.__class__.objects.normalize_email(self.email)
