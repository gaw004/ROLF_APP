import logging
from collections import namedtuple
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.validators import FileExtensionValidator
from django.db import IntegrityError, models, transaction

from core.limits import LONG_TEXT
from core.storages import public_storage

#: What `HomePage.hero` answers with: the file to fill the screen, and which of
#: the two kinds it is.
#:
#: ⚠️ A **namedtuple**, so `hero.kind` reads as itself in a template while
#:    `hero[1]` still works for anything that already unpacked it. The template
#:    is the whole reason it exists: `{% if page.hero.1 == "video" %}` is a line
#:    nobody can check by reading, and this is the one rule the front page and
#:    the rest of the site have to keep agreeing about.
Hero = namedtuple("Hero", "file kind")

logger = logging.getLogger(__name__)


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

    #: Every field that puts an object in the public bucket.
    #:
    #: ⚠️ A tuple rather than a mention per field in `save()`, because everything
    #:    that knows about the bucket has to agree on it: the replacement below,
    #:    and `core.services.orphaned_home_media`, which decides what is *not*
    #:    pointed at. Those two disagreeing means the sweep deletes a live
    #:    picture — the one failure mode here that a user would actually see.
    #:
    #: ⚠️ It held three more until 2026-09-09, the rungs of the picture's
    #:    `srcset` ladder. Their files are still in the bucket and nothing
    #:    points at them any more, which is precisely what
    #:    `orphaned_home_media()` exists to report — see
    #:    `purge_orphaned_home_media`, and revisions.md 六十一 for why the
    #:    ladder went.
    MEDIA_FIELDS = ("hero_image", "hero_video")

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

        ⚠️ One property, two callers — the front page and the shared dark
           backdrop, which show **the same photograph** and both crop it with
           `object-position`. Formatting it at each call site is how those two
           end up disagreeing, and the symptom would be a picture framed one way
           on the front page and another way behind the rest of the site, with
           both looking deliberate. `_hero_backdrop.html` carries a comment
           about exactly that kind of divergence; it was written after it
           happened once.
        """
        return f"{self.hero_focus_x}% {self.hero_focus_y}%"

    def _refuse_an_unaffordable_hero(self):
        """Raise if opening this picture would cost more than the instance has.

        🔴 **The floor under the shell, and it has to be here rather than in
           `refresh_palette`.** `HomePageForm` refuses an oversized upload, but
           the form is one of two ways in — `page.hero_image = ...; page.save()`
           from a shell or a management command is the other, and it does not
           pass through any form at all. Until 2026-09-09 the thing standing in
           that path was the pixel check inside `core.renditions.render_ladder`;
           removing the ladder removed it, so this replaces it rather than
           dropping it. **Taking a defence away means first looking at what it
           was defending.**

        ⚠️ **Not inside `refresh_palette`, which swallows every exception** —
           deliberately, because a ramp is decoration and must never stop
           somebody saving the page. That is exactly the wrong shape for this
           one: the process would be dead before the `except` ran. A complaint
           about memory has to be raised by something whose job is to refuse.

        ⚠️ Priced against `PALETTE_SAMPLE_EDGE`, because since the ladder went
           the palette is the only thing that ever decodes this picture. The
           same number is in `core.admin.HomePageForm`; the two disagreeing
           does not raise, it just prices an upload on arithmetic the decode
           will not use.

        ⚠️ Only called when the picture changed. Re-reading the stored file on
           every save of the verse would be a download per keystroke-worth of
           edit, and whatever is already in the bucket was priced on its way in.
        """
        from django.core.exceptions import ValidationError

        from .images import decode_complaint_for
        from .palette import PALETTE_SAMPLE_EDGE

        if not self.hero_image:
            return
        complaint = decode_complaint_for(self.hero_image, PALETTE_SAMPLE_EDGE)
        if complaint:
            raise ValidationError(f"That picture {complaint}")

    def strip_hero_metadata(self):
        """Take the camera's metadata off the picture. Pixels are untouched.

        ⚠️ **Returns nothing, and it used to return the bytes** (2026-09-09).
           Handing them back existed for one caller — the srcset ladder was cut
           from them rather than fetching the file out of R2 a second time — and
           that caller is gone. A return value nobody reads is a claim that
           somebody might, and the next person to add a caller would inherit a
           whole photograph's worth of bytes without asking for them.

        🔴 **This bucket is public and unsigned**, so everything the camera
           wrote travels with the photograph — including where it was taken.
           Measured on 2026-09-01: the hero kept all 4 GPS tags while Memories,
           which is *private*, kept none. The asymmetry was backwards.

        ⚠️ Lossless, and orientation survives — the whole working is over
           `core.images.without_metadata`. Nothing here re-encodes, so "do not
           compress unless it is free" is kept to the letter.

        ⚠️ **Rewrites the field only when the bytes actually change.** A
           photograph with no metadata, or one that is not a JPEG, must not be
           handed a new uuid filename for nothing: that is a new URL, and a new
           URL is every cache in the world missing.

        ⚠️ `save=False`. The caller is `save()` itself, one line above the write
           that stores this name.
        """
        from .images import without_metadata

        if not self.hero_image:
            return
        try:
            self.hero_image.open("rb")
            original = self.hero_image.read()
        except Exception:
            # ⚠️ Logged and carried, like the palette. An object store that will
            #    not hand the file back is not a reason to refuse the save — the
            #    picture is already stored, and the alternative is a 500 on the
            #    page somebody just used successfully.
            logger.exception("Could not read the hero picture to strip metadata")
            return
        stripped = without_metadata(original)
        if stripped is original or stripped == original:
            return
        name = Path(self.hero_image.name).name
        self.hero_image.save(name, ContentFile(stripped), save=False)

    def __str__(self):
        return "Home page"

    def save(self, *args, **kwargs):
        """Store the row, re-deriving whatever the picture changed."""
        # ⚠️ Forced, not merely defaulted. Without this a second row can be
        #    created through the shell or a fixture, and then load() below picks
        #    one of them by chance.
        self.pk = self.SINGLETON_PK
        # ⚠️ Read **before** the write, obviously — but also *outside* the
        #    on_commit callback below, which runs when the old row is already
        #    gone from the database and could no longer be asked.
        stored = self._stored_media()
        # ⚠️ **Only when the picture actually changed.** Stripping the metadata
        #    rewrites the file under a new uuid, so doing it on every save would
        #    mean fixing a typo in the verse silently invalidates every browser
        #    and CDN copy of the background — and leaves a file behind each
        #    time. Nothing would raise; the bill arrives as bandwidth.
        #
        # ⚠️ Compared against **the stored name**, not against Django's
        #    `FieldFile._committed`. That flag was the first attempt and it is
        #    wrong in a way that is silent: `FieldFile.save()` sets it True
        #    *before* it calls `instance.save()`, so on the upload path — the
        #    only path that matters — the branch never ran. Everything else
        #    stayed green.
        changed = self.hero_image.name != stored.get("hero_image")
        # 🔴 **These three run in this order and each position was paid for.**
        #      · the floor first, because it is the only one of the three that
        #        has not decoded anything yet — pricing a picture *after*
        #        opening it is the mistake the whole gate exists to avoid;
        #      · the palette second, while `self.hero_image` is still the file
        #        that was uploaded. `strip_hero_metadata` rebinds the field to a
        #        fresh `FieldFile` with no open handle, so sampling afterwards
        #        is a round trip to R2 for the whole photograph. Measured, and
        #        it is why the strip used to hand its bytes back;
        #      · the strip last. It is lossless, so the ramp is the same either
        #        way — the order is about the download, not about the colour.
        if changed:
            self._refuse_an_unaffordable_hero()
        self.refresh_palette()
        if changed:
            self.strip_hero_metadata()
        superseded = self._superseded_media(stored)
        super().save(*args, **kwargs)
        if superseded:
            from .services import discard_media

            # ⚠️ **After the commit, not here.** Deleting inline means a
            #    transaction that then rolls back leaves a row pointing at a
            #    file that no longer exists — the front page renders a broken
            #    image and nothing anywhere raises. on_commit runs immediately
            #    when there is no transaction open, so the plain case is
            #    unaffected.
            transaction.on_commit(lambda: discard_media(superseded))

    def _stored_media(self):
        """`{field: name}` as the database currently has it. `{}` before insert.

        ⚠️ One read, taken **before** anything in `save()` touches a file, and
           handed to both callers. They ask it different questions — "did the
           picture change?" and "what is no longer pointed at?" — but
           `strip_hero_metadata` rebinds `hero_image` to a new name, so a second
           read taken after it would be comparing that name against itself and
           finding nothing superseded. The old file would stay in the bucket for
           good, and nothing would raise.
        """
        if not self.pk:
            return {}
        stored = (
            type(self)._base_manager.filter(pk=self.pk)
            .values(*self.MEDIA_FIELDS).first()
        )
        return stored or {}

    def _superseded_media(self, stored):
        """`[(storage, name)]` — the objects this save stops pointing at.

        ⚠️ **The whole reason this exists**: replacing the front page's picture
           used to leave the old one in the bucket for good. Django writes the
           new upload under a new key and simply forgets the old one, so every
           change of picture added a file that nothing referred to, that no
           page could reach, and that nobody would ever think to look for.
           These are also the largest files the project stores — the front page
           is the one upload that is **not** re-encoded — so it is the most
           expensive place in the system to leak a file.

        ⚠️ Compared against **the database**, not against `self`. An instance
           whose field was reassigned in memory has already lost the old name;
           the stored row is the only thing that still knows it.

        ⚠️ Clearing a field counts, and that is deliberate (asked and answered
           2026-08-14). "Remove the picture" means the picture is gone — a file
           left behind in a **public** bucket is still on a URL that works,
           which is the opposite of what was asked for.

        ⚠️ Per field, so switching from a picture to a video does not take the
           picture with it. `hero` prefers the video when both are set, and
           somebody setting one has not said anything about the other.
        """
        return [
            (self._meta.get_field(field).storage, stored[field])
            for field in self.MEDIA_FIELDS
            if stored.get(field) and stored[field] != getattr(self, field).name
        ]

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
        """`Hero(file, kind)` for whatever should fill the screen, or None.

        ⚠️ Video wins when both are set. The alternative — showing both, or
           picking by upload date — gives a page whose appearance depends on
           something nobody can see from the form.

        ⚠️ **The only place that rule is written**, as of 2026-08-13. Until then
           `home.html` decided it a second time with a `{% if page.hero_video %}`
           of its own and nothing used this property but the tests — so the rule
           had two implementations, one of them dead, and a green suite proved
           nothing about the page anybody actually loads. The front page reads
           this now.

        ⚠️ It answers for the **front page only**. Every other page shows the
           image and never the video, and that is deliberate rather than an
           oversight — see `core.context_processors.site_appearance`, which
           reaches for `hero_image` directly for exactly that reason.
        """
        if self.hero_video:
            return Hero(self.hero_video, "video")
        if self.hero_image:
            return Hero(self.hero_image, "image")
        return None

    @classmethod
    def for_request(cls, request):
        """`current()`, fetched at most once per request.

        ⚠️ The front page asked for this row **twice** on every hit until
           2026-08-13: once in `core.views.home` for the verse and the picture,
           and once in the `site_appearance` context processor, which runs on
           every page in the site including this one. Two identical SELECTs on
           the busiest public URL there is, and neither call site could see the
           other — which is what makes it worth a method rather than a note.

        ⚠️ Cached on the **request**, not on the class or the module. A
           process-wide cache would serve the old picture to everybody until the
           worker restarted, and the symptom would be "I saved it and nothing
           happened, so I saved it again".
        """
        page = getattr(request, "_homepage", None)
        if page is None:
            page = cls.current()
            request._homepage = page
        return page
