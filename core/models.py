import io
import logging
from collections import namedtuple
from pathlib import Path

from django.core.files.base import ContentFile
from django.core.validators import FileExtensionValidator
from django.db import IntegrityError, models, transaction

from core.limits import LONG_TEXT
from core.renditions import HERO_RENDITION_WIDTHS, rendition_field
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

    #: Every field that puts an object in the public bucket — the two uploads
    #: and the derived rungs of the picture's `srcset` ladder.
    #:
    #: ⚠️ A tuple rather than a mention per field in `save()`, because everything
    #:    that knows about the bucket has to agree on it: the replacement below,
    #:    and `core.services.orphaned_home_media`, which decides what is *not*
    #:    pointed at. Those two disagreeing means the sweep deletes a live
    #:    picture — the one failure mode here that a user would actually see.
    #:
    #: ⚠️ **The renditions belong in here and it is not a formality.** They are
    #:    files in the same bucket under the same prefix, so a rung left out is
    #:    a rung `orphaned_home_media()` reports as rubbish and offers to
    #:    delete — while a page is serving it. Left out of the other half, every
    #:    change of picture leaks three more files that nothing points at.
    MEDIA_FIELDS = ("hero_image", "hero_video",
                    *(rendition_field(width) for width in HERO_RENDITION_WIDTHS))

    # ⚠️ The **public** bucket, named explicitly, unlike every other upload in
    #    the project. This page is the one thing here that needs no login, so
    #    these two files are public by definition — and a signed URL for public
    #    content buys nothing while costing the CDN cache on the biggest file
    #    the site serves. Decided 2026-08-06 with the rest of the R2 split.
    hero_image = models.ImageField(
        upload_to=MEDIA_DIR, blank=True, storage=public_storage,
        help_text="Full-screen background. Landscape, at least 2000px wide.",
    )
    # --- The srcset ladder, derived from hero_image (2026-08-31) -------------
    #
    # ⚠️ **Three declared fields rather than one JSON column of widths**, and
    #    the reason is the two sweeps: `_superseded_media()` and
    #    `core.services.orphaned_home_media()` both walk `MEDIA_FIELDS` and ask
    #    each one for `.name`. A JSON blob of paths would be invisible to both,
    #    so replacing the picture would leak its rungs and the orphan sweep
    #    would offer to delete the live ones. Adding a rung costs a migration;
    #    that is the price of the files being swept like every other file here.
    #
    # ⚠️ `editable=False`, so they stay out of the admin form. They are derived,
    #    and a derived value somebody can also set by hand is two answers to one
    #    question — the same rule `brand_palette` is under. `core/admin.py`
    #    lists its fields explicitly, so nothing there needs to change.
    #
    # ⚠️ Empty is a supported state, not a broken one: the srcset properties
    #    below return "" and the templates fall back to `src` alone, which is
    #    exactly how the page behaved before this existed. That is what makes
    #    the deploy safe in the window before `rebuild_hero_renditions` runs.
    hero_image_1280 = models.ImageField(
        upload_to=MEDIA_DIR, blank=True, editable=False, storage=public_storage)
    hero_image_1920 = models.ImageField(
        upload_to=MEDIA_DIR, blank=True, editable=False, storage=public_storage)
    hero_image_2560 = models.ImageField(
        upload_to=MEDIA_DIR, blank=True, editable=False, storage=public_storage)
    #: The uploaded picture's own width, for the `w` it is offered under.
    #:
    #: ⚠️ **Stored, because reading it costs a download.** `ImageField.width`
    #:    opens the file whenever there is no `width_field` to answer from, so a
    #:    `srcset` built by asking the field would put an R2 round trip on the
    #:    front page once per render. Filled by `refresh_renditions`, which has
    #:    the picture open anyway.
    #:
    #: ⚠️ Not Django's `width_field=`, deliberately. That fills itself from a
    #:    `post_init` signal, which reads the file on **every instance load**
    #:    for as long as the column is empty — so the rows that predate this
    #:    field would pay exactly the download it exists to prevent, on every
    #:    page view, until somebody happened to save them.
    hero_image_width = models.PositiveIntegerField(default=0, editable=False)
    #: Its height, for the aspect ratio `hero_sizes` is built on. Same reasons
    #: as the width above: stored because reading it costs a download, and not
    #: Django's `height_field=` because that one reads on every instance load
    #: until the column is filled.
    hero_image_height = models.PositiveIntegerField(default=0, editable=False)
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

    @property
    def hero_rungs(self):
        """`[(width, url)]` for every rendition that exists, narrowest first.

        Read by `hero_srcset`, which turns them into the front page's candidate
        list, and by `rebuild_hero_renditions` to report what it cut.

        🔴 **It briefly had a third reader and no longer does.** For two days
           the shared dark backdrop chose between these with CSS breakpoints,
           capped at 2560, so that a laptop would not re-download the whole
           photograph on every navigation. That re-download was the missing
           `Cache-Control` on the public bucket, fixed in the same batch — and
           with the reason gone, the cap only made the same photograph visibly
           softer on inner pages than on the front page. Reported by the person
           looking at it, then measured: 1.15x upscale on a 1470x750 screen, on
           top of 0.127 bytes/px against the original's 0.244.

           The backdrop now takes the original, and the rungs it used to be
           handed were deleted rather than left as a `var()` fallback: the
           original is written inside the same `{% if %}` as the element, so
           nothing past it could ever be reached. Three URLs of dead data in
           the `style` attribute of every inner page, and a chain a reader had
           to trace before finding out it was inert.
        """
        rungs = []
        for width in HERO_RENDITION_WIDTHS:
            rendition = getattr(self, rendition_field(width))
            if rendition:
                rungs.append((width, rendition.url))
        return rungs

    @property
    def hero_srcset(self):
        """The candidates for the front page, **including the original**.

        ⚠️ This is the half of the feature that keeps the promise: nothing is
           compressed away. The front page is where the photograph is the point,
           so the file that was uploaded stays on the ladder as its widest rung
           and a display big enough to use it still gets every pixel.

        ⚠️ Sized by `width`, never by the longest edge — `w` is what the browser
           compares against "viewport width × DPR". See `core.images.width_size`
           for why the two differ on a portrait upload.
        """
        rungs = self.hero_rungs
        if not rungs or not self.hero_image:
            return ""
        candidates = [f"{url} {width}w" for width, url in rungs]
        # ⚠️ The width has to be known and has to be wider than the top rung.
        #    Zero is what the column holds for a row that predates this feature,
        #    and offering the original at `0w` would tell the browser it is the
        #    narrowest candidate there is — every screen would then choose it,
        #    which is the download this whole feature removes, arriving through
        #    the field meant to prevent it. Leaving it off is the safe half of
        #    that trade; after `rebuild_hero_renditions` it never happens.
        if self.hero_image_width > rungs[-1][0]:
            candidates.append(f"{self.hero_image.url} {self.hero_image_width}w")
        return ", ".join(candidates)

    @property
    def hero_sizes(self):
        """What to put in `sizes=` for a picture drawn with `object-fit: cover`.

        🔴 **`100vw` is wrong here, and it is wrong in the direction that makes
           the front page blurry on a phone.** `sizes` states the width of the
           `<img>` *box*, and the browser picks a rung from that — but this box
           is the whole viewport and the picture is drawn with `cover`, so the
           width actually painted is

               max(viewport width, viewport height × the picture's aspect)

           A 390×844 phone showing a 1.884-aspect photograph paints it 1590 CSS
           px wide — 4770 device pixels at 3x — while `100vw` claims 390 and
           gets a 1280 rung stretched nearly four times. Before the ladder
           existed the same phone was handed the full-size original and looked
           right, so shipping `100vw` would have been a regression dressed as an
           optimisation, on the one page where the photograph *is* the content.

        ⚠️ Falls back to `100vw` when the dimensions are unknown (a row that
           predates these columns). That is the pre-ladder behaviour and it is
           only ever reached alongside an empty `srcset`, which ignores `sizes`
           entirely.

        ⚠️ The ratio is rounded to three places rather than printed in full:
           `sizes` is re-evaluated on every resize, and the extra digits buy
           nothing at any real viewport height.
        """
        if not self.hero_image_width or not self.hero_image_height:
            return "100vw"
        aspect = round(self.hero_image_width / self.hero_image_height, 3)
        return f"max(100vw, calc(100vh * {aspect}))"

    def strip_hero_metadata(self):
        """Take the camera's metadata off the picture. Pixels are untouched.

        Returns the bytes the picture ends up as — stripped, or the original
        where there was nothing to strip — so the caller can build the ladder
        from them.

        ⚠️ **Returning them is not a convenience.** `FieldFile.save()` below
           rebinds `self.hero_image` to a fresh `FieldFile` with no open handle,
           so the very next read of it is a round trip to R2 for bytes that are
           still in this function's local variable. Measured: one avoidable GET
           of the whole photograph on every change of the front page picture.

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
            return None
        try:
            self.hero_image.open("rb")
            original = self.hero_image.read()
        except Exception:
            # ⚠️ Logged and carried, like the ladder below. An object store that
            #    will not hand the file back is not a reason to refuse the save
            #    — the picture is already stored, and the alternative is a 500
            #    on the page somebody just used successfully.
            logger.exception("Could not read the hero picture to strip metadata")
            return None
        stripped = without_metadata(original)
        if stripped is original or stripped == original:
            return original
        name = Path(self.hero_image.name).name
        self.hero_image.save(name, ContentFile(stripped), save=False)
        return stripped

    def refresh_renditions(self, data=None):
        """Re-derive the srcset ladder from the current picture. Never raises.

        ⚠️ Failure clears the rungs rather than stopping the save, exactly like
           `refresh_palette` above and for the same reason: the picture is the
           content, the ladder is an optimisation. With the rungs empty both
           templates fall back to `src` alone — the page is heavier than it
           should be and completely correct, which is the right way round.

        ⚠️ **The uploads are inside the guard too, not only the encoding.** They
           are three network writes to R2, and a timeout on the second one used
           to propagate out of `save()` — so a transient object-store hiccup
           turned "change the front page picture" into a 500, with the picture
           itself unsaved because `super().save()` had not run yet. That is the
           precise opposite of the paragraph above. Anything that fails now
           leaves the ladder empty and the page correct.

        ⚠️ **The old rungs are not deleted here.** They are in `MEDIA_FIELDS`,
           so `_superseded_media()` reads them off the stored row and
           `discard_media` removes them after the commit — the same path the
           picture itself takes. Deleting them here would run before the write
           and orphan them if it rolled back.

        ⚠️ `data` is the picture's bytes when the caller already has them —
           `strip_hero_metadata` has just read and rewritten the file, and
           reaching for `self.hero_image` again would fetch from R2 what is
           sitting in memory a stack frame away. Omitted, the file is read.
        """
        from .renditions import render_ladder

        if not self.hero_image:
            self._clear_renditions()
            return
        try:
            source = io.BytesIO(data) if data is not None else self.hero_image
            size, ladder = render_ladder(source)
            for width in HERO_RENDITION_WIDTHS:
                content = ladder.get(width)
                if content is None:
                    # ⚠️ Cleared, not left alone. A rung kept from the previous
                    #    photograph would be served under this one's `srcset` —
                    #    the wrong picture, at one screen width only, with
                    #    nothing raised. This is also how a narrower upload
                    #    drops the rungs it does not fill.
                    setattr(self, rendition_field(width), "")
                    continue
                getattr(self, rendition_field(width)).save(
                    content.name, content, save=False)
        except Exception:
            # ⚠️ Logged rather than swallowed in silence. A front page that
            #    quietly stops being responsive looks exactly like one that
            #    never was, and the only symptom is a bandwidth bill.
            logger.exception("Could not build the hero ladder")
            self._clear_renditions()
            return
        self.hero_image_width, self.hero_image_height = size

    def _clear_renditions(self):
        """Forget every derived rung. The page falls back to `src` alone."""
        for width in HERO_RENDITION_WIDTHS:
            setattr(self, rendition_field(width), "")
        self.hero_image_width = 0
        self.hero_image_height = 0

    def __str__(self):
        return "Home page"

    def save(self, *args, rebuild_hero=False, **kwargs):
        """Store the row, re-deriving whatever the picture changed.

        ⚠️ `rebuild_hero` forces the derived work on a picture whose **name has
           not changed** — the backfill case, where the file has been in the
           bucket since before there was a ladder to cut or metadata to strip.
           `rebuild_hero_renditions` is the only caller.

           It is a flag rather than the command doing the work itself and then
           saving, and that was measured rather than guessed: stripping renames
           the file, so a command that stripped, cut the ladder and *then*
           called `save()` had its rename detected here and did the whole thing
           a second time — building three more rungs and orphaning the first
           three, which no sweep could find because the row had never pointed at
           them. One path in, once.
        """
        # ⚠️ Forced, not merely defaulted. Without this a second row can be
        #    created through the shell or a fixture, and then load() below picks
        #    one of them by chance.
        self.pk = self.SINGLETON_PK
        self.refresh_palette()
        # ⚠️ Read **before** the write, obviously — but also *outside* the
        #    on_commit callback below, which runs when the old row is already
        #    gone from the database and could no longer be asked.
        #
        # ⚠️ Read **once**, and before `refresh_renditions` below: that call
        #    reassigns the rung fields, so a second read afterwards would
        #    compare the new names against themselves and find nothing
        #    superseded — the old rungs would stay in the bucket for good.
        stored = self._stored_media()
        # ⚠️ **Only when the picture actually changed**, unlike the palette
        #    above — and the asymmetry is deliberate. Re-deriving a ramp is tens
        #    of milliseconds against the same bytes; re-deriving the ladder
        #    decodes the photograph, re-encodes three WebPs, and stores them
        #    under three *new* uuid names. Doing that on every save would mean
        #    fixing a typo in the verse silently invalidates every browser and
        #    CDN copy of the background — and leaves three files behind each
        #    time. Nothing would raise; the bill arrives as bandwidth.
        #
        # ⚠️ Compared against **the stored name**, not against Django's
        #    `FieldFile._committed`. That flag was the first attempt and it is
        #    wrong in a way that is silent: `FieldFile.save()` sets it True
        #    *before* it calls `instance.save()`, so on the upload path — the
        #    only path that matters — the branch never ran and no ladder was
        #    ever cut. Everything else stayed green.
        if rebuild_hero or self.hero_image.name != stored.get("hero_image"):
            # ⚠️ **Stripping before the ladder, not after.** The rungs are cut
            #    from whatever it leaves behind, and its bytes are handed
            #    straight over — see `strip_hero_metadata` for why fetching
            #    them again would be a needless round trip.
            self.refresh_renditions(self.strip_hero_metadata())
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

        ⚠️ One read, handed to both callers in `save()`. They ask it different
           questions — "did the picture change?" and "what is no longer pointed
           at?" — but a second read taken *after* `refresh_renditions()` would
           be answering about names this save has already replaced.
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
