from django.contrib.auth.models import AbstractUser
from django.db import models


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
    """

    contact = models.OneToOneField(
        "contact.Contact",
        on_delete=models.SET_NULL,     # deleting a contact shouldn't delete their account
        null=True, blank=True,
        related_name="user",
    )
