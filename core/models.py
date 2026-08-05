from django.core.validators import FileExtensionValidator
from django.db import IntegrityError, models


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


class HomePage(models.Model):
    """The public front page: one picture (or one video) and one verse.

    A **singleton** — `HomePage.load()` is the only way anything reads it, and it
    always answers, creating the row on first use. There is no list of home
    pages to choose between, and a model that can hold two rows eventually holds
    two rows, after which "which one is live" becomes a question somebody has to
    answer at 11pm.

    ⚠️ Who may edit it is `foundation_admin`, and that is enforced by Django's
       `change_homepage` permission granted to that group in
       org/permissions.py — not by anything here. A ministry admin publishes
       events; the face of the foundation is a foundation-wide decision, which
       is D20's test for which tier a thing belongs to.

    ⚠️ The media is **not in any backup**, same as event images and for the same
       reason: `pg_dump` covers columns, and these are files. One picture and one
       verse is a minute of work to restore by hand, so the trade is accepted
       rather than solved. The verse itself *is* a column and *is* backed up.
    """

    #: Nothing anywhere refers to a second row, and this is what keeps that true.
    SINGLETON_PK = 1

    MEDIA_DIR = "home"

    hero_image = models.ImageField(
        upload_to=MEDIA_DIR, blank=True,
        help_text="Full-screen background. Landscape, at least 2000px wide.",
    )
    # ⚠️ FileField rather than a video-specific field: Django has no VideoField,
    #    and nothing here transcodes. What arrives is what is served, so the size
    #    of the file somebody uploads is the size every visitor downloads.
    hero_video = models.FileField(
        upload_to=MEDIA_DIR, blank=True,
        validators=[FileExtensionValidator(["mp4", "webm"])],
        help_text="Optional, and used instead of the image when set. It plays "
                  "muted and on a loop — browsers refuse to autoplay sound, and "
                  "a page that made noise on arrival would be worse anyway. "
                  "Keep it under 10 MB: every visitor downloads all of it.",
    )
    verse_text = models.TextField(
        blank=True,
        help_text="The passage itself. Shown first, in large type.",
    )
    verse_reference = models.CharField(
        max_length=120, blank=True,
        help_text="Where it is from, shown underneath — e.g. Colossians 3:23–24.",
    )

    class Meta:
        verbose_name = "home page"
        verbose_name_plural = "home page"

    def __str__(self):
        return "Home page"

    def save(self, *args, **kwargs):
        # ⚠️ Forced, not merely defaulted. Without this a second row can be
        #    created through the shell or a fixture, and then load() below picks
        #    one of them by chance.
        self.pk = self.SINGLETON_PK
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Refused. The public front page cannot be "not there"."""
        raise IntegrityError(
            "The home page is a singleton and cannot be deleted. Clear its "
            "fields instead — the page then falls back to the built-in logo."
        )

    @classmethod
    def load(cls):
        """The one row, created on first use. Always answers."""
        instance, _ = cls.objects.get_or_create(pk=cls.SINGLETON_PK)
        return instance

    @property
    def hero(self):
        """(file, kind) for whatever should fill the screen, or None.

        ⚠️ Video wins when both are set. The alternative — showing both, or
           picking by upload date — gives a page whose appearance depends on
           something nobody can see from the form.
        """
        if self.hero_video:
            return self.hero_video, "video"
        if self.hero_image:
            return self.hero_image, "image"
        return None
