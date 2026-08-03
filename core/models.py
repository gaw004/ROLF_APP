from django.db import models


class TimeStampedModel(models.Model):
    """Abstract base: adds created/updated timestamps to any model that inherits it."""
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="time created")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="time updated")

    class Meta:
        abstract = True


class ImmutableCodeMixin:
    """`code` is tidied on save, and refuses to change once it has been stored.

    Four dictionary-ish tables want exactly this (RelationshipType, Ministry,
    EmploymentType, Position) and all four want it for one reason: `code` is
    what the rest of the codebase matches on, so renaming it breaks lookups
    *silently* — filter(code="food_pantry") simply stops returning rows, and
    nothing raises. See goal.md D5.

    Carries no fields on purpose. An abstract model holding `code` would tie
    four tables' migrations to one class, and their columns differ anyway
    (max_length, help_text). This mixin carries behaviour only.

    ⚠️ Uniqueness is NOT here. It belongs to UniqueConstraint(Lower("code")) on
       each model: bulk_create never calls save(), so lowercasing here
       guarantees nothing at all about what is in the table. See goal.md D9.

    ⚠️ code_change_error() returns a message rather than raising, because most
       of these models have a second rule to report as well and Django's
       clean() has to hand back all of its errors in one ValidationError —
       raising from a shared super().clean() would drop whatever the subclass
       had already found.
    """

    def save(self, *args, **kwargs):
        # Cosmetic, like RelationshipType.save() — see the warning above.
        self.code = (self.code or "").strip().lower()
        super().save(*args, **kwargs)

    def code_change_error(self):
        """The complaint about a changed code, or None if it is untouched.

        editable=False would stop ModelForms but also the add form, so
        immutability is split in two: the admin makes the field read-only on
        the change page, and this compares against the value in the database
        so a script or a shell session hits the same wall.
        """
        if not self.pk:
            return None
        stored = (
            type(self)._base_manager.filter(pk=self.pk)
            .values_list("code", flat=True).first()
        )
        if stored is None or stored == (self.code or "").strip().lower():
            return None
        return (
            f'Code cannot be changed once created (it is "{stored}"). '
            "Code is what the rest of the system matches on."
        )
