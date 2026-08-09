"""Memories: the photographs on the wall behind the feather.

One model. Everything about *which* photos appear where on a given day is
computed in services.py and stored nowhere — see the note on the daily draw
there for why that is not laziness.
"""

from django.conf import settings
from django.db import models

from core.models import TimeStampedModel
from core.storages import memories_storage
from org.models import Ministry

#: Both derivatives go here. One prefix, because they live in a bucket of their
#: own already (settings.STORAGES["memories"]) and a second level of foldering
#: would only make the object keys longer.
PHOTO_DIR = "gallery"


class GalleryPhotoQuerySet(models.QuerySet):
    def on_the_wall(self):
        """The photos eligible for the wall, newest first.

        ⚠️ `.only()` is deliberate and is about **R2, not the database**. The
           wall renders 60 photos, and every one of them needs its pixel size to
           be laid out. `photo.thumb.width` would answer that by *opening the
           file* — which on object storage is a network round trip, per photo,
           per page load. The width and height are columns for exactly that
           reason (see `thumb_width` below), and this makes sure nothing
           accidentally reaches for the file instead.
        """
        return self.select_related("ministry").order_by("-created_at", "-id")


class GalleryPhoto(TimeStampedModel):
    """One photograph on the Memories wall.

    ⚠️ **Two derivatives are stored, not one.** `thumb` is what the two moving
       rows show and `image` is what the lightbox shows. Serving the large one
       in the rows would mean ~60 full-size images on a phone; serving only the
       small one would mean the lightbox — the entire point of clicking — shows
       a soft 700px picture blown up to fill the window.

    ⚠️ **The original upload is not kept.** What arrives is re-encoded into
       these two and then dropped, on the same reasoning as event images: an
       uploaded phone photo carries GPS coordinates, and a picture taken at a
       volunteer's home would otherwise publish where they live. The trade is
       stated rather than hidden — 1600px is the largest anyone will ever see
       this photo again, so the foundation should keep its own originals.

    ⚠️ These files are the only ones in the system that **no backup can bring
       back**. The pg_dump covers columns, and these are objects in a bucket, so
       the bucket's own versioning is the whole of the safety net. That is why
       Memories has a bucket to itself rather than sharing the event-images one,
       which must have versioning **off** — the note over STORAGES in
       config/settings/base.py has the full reasoning.
    """

    #: Null means foundation-wide. A ministry admin's uploads always carry
    #: theirs; only the foundation tier can leave it empty.
    #:
    #: ⚠️ PROTECT rather than CASCADE, matching Event.ministry. Ministries are
    #:    retired with is_active=False and never deleted (there is deliberately
    #:    no delete_ministry permission), so a cascade here could only ever fire
    #:    by accident — and it would take the photographs with it.
    ministry = models.ForeignKey(
        Ministry, on_delete=models.PROTECT, null=True, blank=True,
        related_name="gallery_photos",
        help_text="Leave empty for a foundation-wide photo.",
    )

    #: Shown in the lightbox. Longest edge GALLERY_IMAGE_MAX_EDGE.
    image = models.ImageField(upload_to=PHOTO_DIR, storage=memories_storage)

    #: Shown in the two moving rows. Longest edge GALLERY_THUMB_MAX_EDGE.
    thumb = models.ImageField(
        upload_to=PHOTO_DIR, storage=memories_storage,
        width_field="thumb_width", height_field="thumb_height",
    )

    # ⚠️ Columns, not `thumb.width` / `thumb.height`. Django fills these on save
    #    because of the width_field/height_field above, and reading them costs
    #    nothing; reading the properties opens the file, which is a request to
    #    R2. Sixty of those on every page load is the difference between a page
    #    that renders and one that times out. They are also what lets the
    #    template reserve each photo's box before the image arrives, so the row
    #    does not reflow as photos load.
    thumb_width = models.PositiveIntegerField(editable=False)
    thumb_height = models.PositiveIntegerField(editable=False)

    #: SHA-256 of the stored large image's bytes. What "the same photo" means.
    #:
    #: ⚠️ Taken over the **re-encoded** file, not the upload. That is what makes
    #:    it useful: the same photograph sent twice as JPEG and as PNG, or
    #:    straight off the phone and again after a round trip through a chat
    #:    app, arrives as different bytes but comes out of
    #:    normalise_gallery_image identical — same pixels, same resize, same
    #:    WebP settings, EXIF stripped either way.
    #:
    #: ⚠️ Unique **across the whole wall**, not per ministry. The wall is one
    #:    surface; the same picture twice on it is the thing being prevented,
    #:    and which ministry uploaded it does not change that.
    #:
    #: ⚠️ It catches byte-identical pictures and nothing more. A re-crop, a
    #:    re-save at a different quality, or a screenshot of the same photo are
    #:    all different files and all get through. Said plainly because
    #:    "duplicate detection" sounds like it means more than this does.
    image_digest = models.CharField(max_length=64, unique=True, editable=False)

    #: The year shown under the photo in the lightbox. Taken from the upload's
    #: EXIF when it has any, otherwise the year it was uploaded.
    #:
    #: ⚠️ Read out **before** the EXIF is stripped, in services.normalise_
    #:    gallery_image. Order matters and there is no second chance: once the
    #:    metadata is gone the only date left is "today", which for a photo
    #:    somebody is uploading from a shoebox is simply wrong.
    taken_year = models.PositiveSmallIntegerField()

    #: Who put it there. Kept for the audit question ("who published this?"),
    #: never shown to visitors.
    #:
    #: ⚠️ SET_NULL: a photo must outlive the account of whoever uploaded it.
    #:    CASCADE here would mean deleting a departing staff member's login
    #:    silently deletes the foundation's photographs.
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="gallery_photos",
    )

    objects = models.Manager.from_queryset(GalleryPhotoQuerySet)()

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "gallery photo"
        verbose_name_plural = "gallery photos"
        indexes = [
            # The wall draws from the newest N; the manage page lists one
            # ministry's own.
            models.Index(fields=["-created_at"]),
            models.Index(fields=["ministry", "-created_at"]),
        ]

    def __str__(self):
        where = self.ministry.name if self.ministry_id else "Foundation"
        return f"{where} · {self.taken_year}"

    @property
    def aspect(self):
        """width / height of the thumbnail. > 1 is landscape, < 1 is portrait.

        Guarded against a zero height rather than trusting the column: a
        division by zero here would take down the whole wall over one bad row.
        """
        return (self.thumb_width / self.thumb_height) if self.thumb_height else 1.0

    @property
    def caption(self):
        """The one line of small type under the photo in the lightbox.

        Assembled here rather than in the template so that the same sentence
        cannot drift between the page and the tests that check it.
        """
        where = self.ministry.name if self.ministry_id else "River of Life Foundation"
        return f"{where} · {self.taken_year}"
