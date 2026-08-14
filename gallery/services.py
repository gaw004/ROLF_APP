"""Everything the Memories strip computes: the uploads, and the daily draw.

Two halves that share nothing but this file:

  · normalise_gallery_image — bytes. What is stored, and what is thrown away.
  · strip_for               — which photos are up today, and in what order.

⚠️ The second half used to do a great deal more (2026-08-06 → 2026-08-07): two
   rows drifting in opposite directions, a random height per photo tuned to a
   measured spread, and a re-composition pass that cropped some photos into
   other shapes so a row would not look monotone. All of it is gone. Deleted
   rather than left switched off: the tests and the constants were the only
   things keeping those rules honest, and a rule nothing exercises is worse
   than no rule.

⚠️ **Nothing here scales a photograph up, and nothing crops one** (2026-08-08).
   Every photo on the strip is drawn at one shared fraction of its own stored
   size, so what differs between them is what differed between the
   photographs. The equal-height version that came before this had to enlarge
   anything smaller than the band, and that is what
   「有些照片为了做成等高的照片都糊了」 was. See `WallPhoto.relative_height`.
"""

import hashlib
import io
import math
import uuid
from dataclasses import dataclass
from random import Random

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image as PILImage
from PIL import ImageOps as PILImageOps

from core.timeutils import local_today

from .models import GalleryPhoto

# --- Storing an upload ------------------------------------------------------

#: Longest edge of the picture the lightbox shows. Sized from the display: a
#: lightbox fills most of a laptop window, so anything smaller is visibly soft
#: at the one moment somebody has chosen to look closely.
GALLERY_IMAGE_MAX_EDGE = 1600

#: Longest edge of the picture the strip shows.
#:
#: ⚠️ **This constant is also the layout's denominator**, not just a storage
#:    size. A photo is drawn at `band × (stored height ÷ this)` — see
#:    `WallPhoto.relative_height` — so the tallest possible thumbnail exactly
#:    fills the band and everything else comes out proportionally smaller.
#:    Changing it changes how big every photo on the page looks.
#:
#: ⚠️ 700 against a band of ~340 CSS px is a little over 2x, which is what keeps
#:    the strip sharp on a retina screen. Dropping it to save bytes would make
#:    every photo on the page soft, and the softness would arrive at 2x displays
#:    only — i.e. not on whichever machine the change was made on.
GALLERY_THUMB_MAX_EDGE = 700

GALLERY_IMAGE_QUALITY = 82

#: EXIF tag 36867, DateTimeOriginal — when the shutter actually fired. 306
#: (DateTime) is the fallback and means "when the file was last written", which
#: a scan or an edit will have overwritten.
_EXIF_DATETIME_ORIGINAL = 36867
_EXIF_DATETIME = 306

#: Anything earlier than this is not believed. EXIF itself dates from the
#: mid-90s, so a "1970" or "1980" in this field is a camera that came up with a
#: flat battery and no clock — and "1970" printed under a photograph of a
#: children's camp is worse than no year at all.
#:
#: ⚠️ The honest limitation, stated rather than hidden: a **scan** of an old
#:    photograph carries the scanner's date, not the day it was taken, and
#:    nothing here can tell the difference. A 1975 picture scanned last year is
#:    captioned with last year. Reading it off the metadata was chosen over
#:    asking the uploader to type it (2026-08-06), and this is what that costs.
EXIF_EARLIEST_PLAUSIBLE_YEAR = 1995


def _exif_year(source):
    """The year the photo was taken, or None. Never raises.

    ⚠️ Called **before** exif_transpose and before the re-encode, because both
       of those are the point at which the metadata stops existing. There is no
       second chance: after the WebP is written, the only date left is today's,
       and for a photo somebody is uploading out of a shoebox that is simply a
       wrong answer rather than a missing one.

    ⚠️ Wrapped in a bare except on purpose. EXIF is written by thousands of
       devices and a fair number of them write nonsense; a malformed timestamp
       must cost the caller a year, not an upload.
    """
    try:
        exif = source.getexif()
        for tag in (_EXIF_DATETIME_ORIGINAL, _EXIF_DATETIME):
            raw = exif.get(tag)
            if not raw:
                continue
            # "2019:07:14 11:32:01" — the colons are what EXIF specifies.
            year = int(str(raw)[:4])
            # ⚠️ Both ends matter. A camera with a flat battery reports 1970 and
            #    one with a wrongly-set clock can report 2099 — both parse
            #    perfectly and both would be printed under the photograph.
            if EXIF_EARLIEST_PLAUSIBLE_YEAR <= year <= local_today().year:
                return year
    except Exception:
        pass
    return None


def _shrink_and_encode(image, max_edge):
    """Resize `image` **in place** (never up) and write it out as WebP.

    ⚠️ **In place, and the caller has to know that.** This used to take a
       `.copy()` of its own, and that copy was the problem: at full size a
       6000×4000 photograph is 72 MB, so one per derivative cost **238 MB of
       peak memory for a single upload** on a 512 MB instance (measured
       2026-08-13, after the front page's version of the same mistake restarted
       production and 502'd everybody). Making the large derivative first means
       the second call resizes something that is already 1600px, and its copy
       is cheap: 238 MB → 134 MB, with the stored bytes of the large derivative
       **unchanged** — which is what keeps `image_digest` meaning today what it
       meant yesterday.

    ⚠️ So callers must go **large first, then small**. Backwards, the "large"
       image comes out at the thumbnail's size — a valid file, the right
       format, simply the wrong picture, and nothing raises. That is what the
       old `.copy()` defended against; it is now defended by
       `test_the_large_one_is_not_made_from_the_small_one` instead.
    """
    image.thumbnail((max_edge, max_edge), PILImage.LANCZOS)
    buffer = io.BytesIO()
    # No exif= argument, so none is written. Stated rather than assumed, because
    # "Pillow does not copy it by default" is the kind of default that changes.
    image.save(buffer, "WEBP", quality=GALLERY_IMAGE_QUALITY, method=6)
    return ContentFile(buffer.getvalue(), name=f"{uuid.uuid4().hex}.webp")


def digest_of(image_file):
    """SHA-256 of a stored image's bytes. What "the same photo" means here.

    ⚠️ Taken over the re-encoded file rather than the upload, which is the whole
       point: the same photograph sent as a JPEG and again as a PNG is different
       bytes going in and identical bytes coming out, because the re-encode
       fixes the orientation, the size, the colour mode and the WebP settings
       and drops the metadata.
    """
    image_file.seek(0)
    return hashlib.sha256(image_file.read()).hexdigest()


def normalise_gallery_image(uploaded):
    """Re-encode an upload into (image, thumb, taken_year).

    The same four rules as events.services.normalise_event_image — EXIF upright
    first, EXIF then dropped, resized down only, and Pillow opening the file
    *is* the validation that keeps SVG out — with two differences:

      · two derivatives come out instead of one, because the strip and the
        lightbox want very different sizes (see the constants above);
      · the year is read off the metadata on the way past, so that nobody has
        to type it in. That read has to happen here and nowhere else, because
        here is the last moment the metadata exists.

    ⚠️ Deliberately **not** shared with the event-image function. They agree
       today and have no reason to stay agreed: event images are purged after
       the event and sized for a 140px card, these are kept for good and sized
       for a lightbox. Folding them together would mean the next change to one
       silently reaches the other.

    Raises ValidationError for anything that is not a usable image, so the form
    can put the complaint next to the field.
    """
    try:
        with PILImage.open(uploaded) as source:
            taken_year = _exif_year(source)
            # ⚠️ **`in_place=True`** (2026-08-13, after ten photographs killed
            #    the instance). Without it `exif_transpose` hands back a
            #    *second* full-size picture — `image.copy()` when there is no
            #    orientation tag to act on — while the first stays alive under
            #    `source` for the rest of this function. Two decoded copies of
            #    a phone photograph where one will do.
            #
            #    Peak RSS for one upload, measured on Linux (what the
            #    deployment runs; macOS reports a different shape and was
            #    misleading):
            #        12 MP    139 MB -> 92 MB
            #        24 MP    240 MB -> 148 MB
            #        49 MP    470 MB -> 284 MB
            #    The last row is the one that mattered: against a 512 MB
            #    instance already holding two workers, that upload had nowhere
            #    to fit, and the symptom was a restart rather than an error.
            #
            # ⚠️ Permitted **only because the stored bytes do not change** —
            #    checked over the foundation's own photographs and over every
            #    orientation tag, SHA-256 equal on both derivatives in all of
            #    them. That is the whole licence for this line: `image_digest`
            #    is a hash of these bytes, so a pipeline that shifts them by one
            #    pixel value stops recognising photographs that are already on
            #    the wall, and stops silently.
            PILImageOps.exif_transpose(source, in_place=True)
            # ⚠️ **The decode is forced here deliberately** — this line is the
            #    other half of "the stored bytes do not change".
            #
            #    `thumbnail()` below asks the JPEG decoder for a reduced-scale
            #    decode of its own (its `reducing_gap`), and that request only
            #    has an effect while the file is still unread. Today it never
            #    fires, because `exif_transpose` reads the file first — but that
            #    is *its* implementation detail, not a promise. The day it stops
            #    doing so, a 5120px photograph would quietly be decoded at
            #    2560px: still a valid 1600px WebP, nothing raised, and every
            #    digest different from every digest stored before that day.
            #
            #    One cheap line instead of a dependency on somebody else's
            #    internals. `test_the_picture_is_fully_decoded_before_resizing`
            #    is what holds it.
            source.load()
            # RGBA is kept where it exists — WebP carries alpha, and flattening
            # a scan with transparent corners onto white would put a white box
            # on a dark page.
            #
            # ⚠️ Converted only when the mode is not already the one wanted.
            #    `convert()` into the mode an image is already in still returns
            #    a **full-size copy** — 72 MB for a phone photograph, spent to
            #    produce pixels identical to the ones it was handed.
            #
            # ⚠️ A second name rather than rebinding `source`: the `with`
            #    keeps its own reference to whatever `open()` returned, so
            #    rebinding would not release the original — it would only read
            #    as though it had.
            picture = source
            wanted = "RGBA" if "A" in picture.getbands() else "RGB"
            if picture.mode != wanted:
                picture = picture.convert(wanted)
            # ⚠️ **Large first, then small, and the order is load-bearing.**
            #    _shrink_and_encode resizes in place, so the second call works
            #    on an image that is already 1600px and its copy is cheap.
            #    Swap these two lines and the lightbox picture comes out at
            #    700px — valid file, right format, wrong picture, no error.
            image = _shrink_and_encode(picture, GALLERY_IMAGE_MAX_EDGE)
            thumb = _shrink_and_encode(picture.copy(), GALLERY_THUMB_MAX_EDGE)
    except ValidationError:
        raise
    except Exception as error:
        raise ValidationError(
            "That file could not be read as an image. JPEG, PNG and WebP work; "
            "SVG and PDF do not."
        ) from error

    return image, thumb, taken_year or local_today().year


# --- The daily draw ---------------------------------------------------------

#: How many photos are on the page at once, across all the strips together.
#:
#: ⚠️ A cap on what the *page* carries, not on what the foundation may keep.
#:    Older photos are never deleted; they are simply not up today, and the
#:    draw below gives them another turn tomorrow. Without a cap the page keeps
#:    working right up until the day it does not, and that day arrives quietly.
STRIP_SIZE = 60

#: How many strips are stacked on the page (2026-08-08: 「再来两条照片带子」).
#:
#: ⚠️ The 60 above is split between them rather than multiplied by them. Three
#:    strips of sixty would be 180 photographs and 360 `<img>` tags once the
#:    seam copies are counted — the page would take a visible moment to settle
#:    on a laptop and would be unusable on a phone.
STRIP_COUNT = 3

#: How wide one strip has to be, in strip-height units, before the marquee can
#: loop without a hole in it.
#:
#: ⚠️ **This is the small-collection case, and it is the state the foundation
#:    starts in.** The track loops by shifting itself left by exactly one copy
#:    of its contents, which only looks seamless if one copy is at least as wide
#:    as the window. Six photos are about 8 units ≈ 2700px, so on a wide screen
#:    the strip would run out and a blank stretch would walk across the page —
#:    the worst thing this page could do on the day somebody uploads their first
#:    few photographs. `repeats_for()` works out how many copies to make.
#:
#: ⚠️ Measured in **strip heights**, and the strip got shorter when it became
#:    three of them — so the same 14 units is now fewer pixels, and a short
#:    strip needs proportionally more copies. That falls out of the arithmetic
#:    rather than needing a second constant, which is why the unit is what it is.
MIN_STRIP_SPAN = 14


def repeats_for(span):
    """How many copies of the strip the track needs. Never fewer than two.

    Two is the floor because the second copy *is* the seam — with one, the
    strip would jump back to its start in a single frame at the end of every
    loop.
    """
    if span <= 0:
        return 2
    return max(2, math.ceil(MIN_STRIP_SPAN / span))


@dataclass(frozen=True)
class WallPhoto:
    """One photo as the template needs it. Not a model — nothing here is stored."""

    photo: GalleryPhoto
    #: Index in the sequence the lightbox arrows walk.
    index: int
    #: Width / height as drawn, which **is** the photograph's own ratio.
    #:
    #: ⚠️ Nothing is ever cropped or re-shaped. The "crop some photos into
    #:    another shape so the row looks varied" machinery — a shape mix, target
    #:    ratios, a maximum acceptable loss — was deleted on 2026-08-07 and this
    #:    is now always `photo.aspect`.
    aspect: float

    @property
    def relative_height(self):
        """Drawn height as a share of the band, from the photo's stored pixels.

        ⚠️ **This is the whole of "no scaling" (2026-08-08).** Every photo is
           drawn at `band × (its own stored height ÷ GALLERY_THUMB_MAX_EDGE)`,
           and since no stored thumbnail is taller than that constant, the
           factor is the same for every photo on the page — one uniform scale,
           exactly like the `SCALE 0.10` on the first reference design. Sizes
           then differ because the *photographs* differ, which is what was
           asked for: 「按每张图原生的大小就好」.

        ⚠️ The bug this replaces: forcing every photo to the band height means
           **upscaling** anything whose stored height is below it — a 400px
           scan blown up to 340 CSS px on a 2x display is being asked for 680
           real pixels and has 400. That is the blur in
           「有些照片为了做成等高的照片都糊了」. Here the factor is always
           ≤ 1, so a photo is only ever drawn smaller than it is stored, never
           larger, and nothing can go soft.

        ⚠️ Guarded against a zero height rather than trusting the column: a
           division by zero here would take down the whole page over one bad
           row.
        """
        if not GALLERY_THUMB_MAX_EDGE:
            return 1.0
        return min(1.0, (self.photo.thumb_height or 0) / GALLERY_THUMB_MAX_EDGE)

    @property
    def width_ratio(self):
        """Width as a share of the band height, which is what the span sums.

        Height is `band × relative_height`, so width is that times the aspect
        ratio. ⚠️ Not the same as `aspect` any more — it was while every photo
        filled the band, and the marquee's duration is computed from the sum of
        these, so conflating them would make the strip drift at the wrong speed
        by however much the photos differ.
        """
        return self.relative_height * self.aspect


def _seeded(*parts):
    """A Random seeded by a string.

    ⚠️ **Never `hash()`.** Python salts string hashing per process, so a strip
       seeded that way would differ between the web workers serving the same
       request-to-request — two people, or the same person twice, would see
       different photos on the same day, and it would be reproducible nowhere.
    """
    return Random("|".join(str(p) for p in parts))


def strips_for(day=None):
    """The photos on each strip for one day. A list of lists, top strip first.

    ⚠️ **Seeded by the date, so the page holds still.** Re-drawing per request
       would mean it rearranges itself under somebody who reloads, or who comes
       back from the lightbox — and a page that changes when you are not
       looking at it reads as a bug, not as variety. It changes at local
       midnight, which is what gives an older photo another turn.

    ⚠️ A photo is on **one** strip or another, never two at once: the draw
       produces one list and then cuts it into pieces. (Each strip separately
       repeats its own contents to hide the loop's seam — that is a copy inside
       one strip, and is not the same thing.)

    ⚠️ Strips that would be empty are **not returned**. Three photos make one
       strip of three, not one strip of three and two empty bands — and an
       empty band is a stripe of blank page that looks like something failed to
       load. This is the state a foundation is in on its first day, so it is
       the case least able to afford looking broken.

    ⚠️ `index` runs across the whole page, top strip first and left to right
       within each. That is the order the lightbox arrows walk, so every photo
       on the page is reachable from any other.
    """
    day = day or local_today()
    ids = list(GalleryPhoto.objects.values_list("id", flat=True))
    if not ids:
        return []

    rng = _seeded("strip", day.isoformat())
    drawn = rng.sample(ids, min(STRIP_SIZE, len(ids)))

    by_id = GalleryPhoto.objects.on_the_wall().in_bulk(drawn)
    photos = [by_id[i] for i in drawn if i in by_id]

    # ⚠️ Cut into as many pieces as there are photos to fill, never more. The
    #    ceiling divide is what keeps the last strip from being the only long
    #    one: 20 photos over 3 strips is 7/7/6, not 6/6/8.
    wanted = min(STRIP_COUNT, len(photos))
    per_strip = math.ceil(len(photos) / wanted)

    strips, index = [], 0
    for start in range(0, len(photos), per_strip):
        strip = []
        for photo in photos[start:start + per_strip]:
            strip.append(WallPhoto(photo=photo, index=index, aspect=photo.aspect))
            index += 1
        strips.append(strip)
    return strips
