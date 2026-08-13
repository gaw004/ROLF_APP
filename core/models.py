from django.core.validators import FileExtensionValidator
from django.db import IntegrityError, models

from core.limits import LONG_TEXT
from core.storages import public_storage


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

    # ⚠️ The **public** bucket, named explicitly, unlike every other upload in
    #    the project. This page is the one thing here that needs no login, so
    #    these two files are public by definition — and a signed URL for public
    #    content buys nothing while costing the CDN cache on the biggest file
    #    the site serves. Decided 2026-08-06 with the rest of the R2 split.
    hero_image = models.ImageField(
        upload_to=MEDIA_DIR, blank=True, storage=public_storage,
        help_text="Full-screen background. Landscape, at least 2000px wide.",
    )
    # ⚠️ FileField rather than a video-specific field: Django has no VideoField,
    #    and nothing here transcodes. What arrives is what is served, so the size
    #    of the file somebody uploads is the size every visitor downloads.
    hero_video = models.FileField(
        upload_to=MEDIA_DIR, blank=True, storage=public_storage,
        validators=[FileExtensionValidator(["mp4", "webm"])],
        help_text="Optional, and used instead of the image when set. It plays "
                  "muted and on a loop — browsers refuse to autoplay sound, and "
                  "a page that made noise on arrival would be worse anyway. "
                  "Keep it under 10 MB: every visitor downloads all of it.",
    )
    verse_text = models.TextField(
        blank=True, max_length=LONG_TEXT,
        help_text="The passage itself. Shown first, in large type.",
    )
    verse_reference = models.CharField(
        max_length=120, blank=True,
        help_text="Where it is from, shown underneath — e.g. Colossians 3:23–24.",
    )
    #: Ten hex strings keyed by step, derived from hero_image. Empty = use the
    #: built-in teal.
    #:
    #: ⚠️ Stored rather than computed per request. Quantising a photograph is
    #:    tens of milliseconds, and this would otherwise run on **every page
    #:    view of the whole site** — the colours are in the shared shell.
    #:
    #: ⚠️ Not editable by hand. It is derived, and a derived value somebody can
    #:    also type is two answers to one question. Recomputed in save().
    brand_palette = models.JSONField(default=dict, blank=True, editable=False)

    #: Which part of the picture has to survive being cropped, as a percentage
    #: from the left and from the top. 50/50 — the middle — is what a browser
    #: does on its own, so the default changes nothing.
    #:
    #: ⚠️ **A point, not a crop rectangle, and that is the whole design**
    #:    (2026-08-13). The picture is shown full-bleed, so the shape of the
    #:    hole it has to fill is different on every device: a phone held
    #:    upright keeps a tall slice, a laptop keeps a wide one. No single
    #:    stored crop can be right for both — it would simply be cropped again.
    #:    Naming the point that must stay visible is the one instruction that
    #:    survives every screen.
    #:
    #: ⚠️ The file is never touched. Cropping happens in the browser, so
    #:    changing this is free and reversible, and the original is still there
    #:    the day somebody wants a different framing.
    hero_focus_x = models.PositiveSmallIntegerField(
        default=50,
        verbose_name="focus, across",
        help_text="0 is the left edge, 100 the right. 50 keeps the middle — "
                  "raise it to save something on the right of the picture.",
    )
    hero_focus_y = models.PositiveSmallIntegerField(
        default=50,
        verbose_name="focus, down",
        help_text="0 is the top edge, 100 the bottom. 50 keeps the middle — "
                  "lower it (towards 0) to stop faces being cut off the top.",
    )

    class Meta:
        verbose_name = "home page"
        verbose_name_plural = "home page"
        constraints = [
            # ⚠️ In the database, not only in the form (D9 / D14). The field
            #    type already refuses negatives; this is the other end. A 300
            #    here is not a validation nicety — object-position accepts it,
            #    so the picture would simply slide off its own frame and the
            #    page would come out mostly blank, with nothing raised.
            models.CheckConstraint(
                condition=models.Q(hero_focus_x__lte=100)
                & models.Q(hero_focus_y__lte=100),
                name="homepage_hero_focus_within_the_picture",
                violation_error_message="The focus has to be between 0 and 100 "
                                        "— it is a position inside the picture.",
                violation_error_code="homepage_focus_out_of_range",
            ),
        ]

    @property
    def hero_focus(self):
        """The focal point as CSS writes it: "50% 50%".

        ⚠️ One property, two callers — the front page's `object-position` and
           every other page's `background-position`, which show **the same
           photograph**. Formatting it at each call site is how those two end
           up disagreeing, and the symptom would be a picture framed one way on
           the front page and another way behind the rest of the site, with
           both looking deliberate. `_hero_backdrop.html` carries a comment
           about exactly that kind of divergence; it was written after it
           happened once.
        """
        return f"{self.hero_focus_x}% {self.hero_focus_y}%"

    def __str__(self):
        return "Home page"

    def save(self, *args, **kwargs):
        # ⚠️ Forced, not merely defaulted. Without this a second row can be
        #    created through the shell or a fixture, and then load() below picks
        #    one of them by chance.
        self.pk = self.SINGLETON_PK
        self.refresh_palette()
        super().save(*args, **kwargs)

    def refresh_palette(self):
        """Re-derive the brand ramp from the current picture. Never raises.

        ⚠️ A failure here must not stop somebody saving the page. The palette is
           decoration; the verse and the picture are the content. So anything
           that goes wrong — an unreadable file, a photograph with no usable
           colour in it — clears the ramp and lets the built-in teal stand.
        """
        from .palette import dominant_colour, ramp_from

        if not self.hero_image:
            self.brand_palette = {}
            return
        try:
            colour = dominant_colour(self.hero_image)
            self.brand_palette = {str(k): v for k, v in (ramp_from(colour) or {}).items()}
        except Exception:
            self.brand_palette = {}

    def delete(self, *args, **kwargs):
        """Refused. The public front page cannot be "not there"."""
        raise IntegrityError(
            "The home page is a singleton and cannot be deleted. Clear its "
            "fields instead — the page then falls back to the built-in logo."
        )

    @classmethod
    def load(cls):
        """The one row, created if it is not there. Always answers.

        ⚠️ Writes. Use `current()` on read paths — see the warning there.
        """
        instance, _ = cls.objects.get_or_create(pk=cls.SINGLETON_PK)
        return instance

    @classmethod
    def current(cls):
        """The one row if it exists, otherwise an unsaved blank one. Never writes.

        ⚠️ The read-path version, and the distinction is not pedantry: the
           shared shell asks for this on **every page view of the whole site**,
           and `load()` would put a `get_or_create` there — a write on the read
           path, and one extra query the first time it runs. Two query-count
           tests caught exactly that.
        """
        return cls.objects.filter(pk=cls.SINGLETON_PK).first() or cls()

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
