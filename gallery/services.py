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
import time
import uuid
from dataclasses import dataclass
from random import Random

from django.core.cache import cache
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image as PILImage
from PIL import ImageOps as PILImageOps

from core.images import draft_to, over_pixel_budget, stored_size, upright_size
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


def _resized_to(image, target):
    """`image` at exactly `target`. `None`, or already there, leaves it alone.

    ⚠️ Not `thumbnail()`, which works the target out for itself from the size
       of whatever it is handed. That is the difference this whole arrangement
       exists for — see `core.images.stored_size`.
    """
    if target is None or image.size == target:
        return image
    return image.resize(target, PILImage.LANCZOS)


def _resized(image, max_edge):
    """`image` with its longest edge at `max_edge`. Never enlarged.

    Returns `image` itself when it is already small enough, so a small scan is
    stored as it arrived.

    ⚠️ Callers must still go **large first, then small**: the thumbnail is
       taken from the large derivative, so it resamples something that is
       already 1600px instead of decoding the photograph a second time.
       Backwards, the "large" image comes out at the thumbnail's size — a valid
       file, the right format, simply the wrong picture, and nothing raises.
       `test_the_large_one_is_not_made_from_the_small_one` is what holds it.
    """
    return _resized_to(image, stored_size(image.size, max_edge))


def _encode(image):
    """The stored WebP for one derivative."""
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


#: Read in pieces rather than whole: an upload may be 10 MB, and the point of
#: this whole round of work is to stop handling one photograph from costing
#: more memory than it needs to.
_DIGEST_CHUNK = 64 * 1024


def source_digest_of(uploaded):
    """SHA-256 of the file **as it was uploaded**, before anything touches it.

    ⚠️ The companion to `digest_of`, and they answer different questions. Both
       are needed and neither replaces the other:

         · `digest_of` hashes the **stored** derivative, so it recognises the
           same photograph sent once as a JPEG and again as a PNG. The price is
           that it necessarily moves whenever this pipeline moves, because the
           pipeline is what produces the bytes it hashes.
         · this one hashes the **upload**, so it recognises "that exact file
           again" — the common case, a folder picked twice — and **nothing
           about it moves when the pipeline is rewritten**.

    ⚠️ It exists because of what 2026-08-13 cost. Adding `draft()` changed
       every stored byte, and with only `digest_of` that meant every photograph
       already on the wall silently stopped being recognised. It was affordable
       exactly once, while the wall held nothing but test uploads. This column
       is what stops the next such change costing the same thing: after it, the
       most common kind of duplicate is still caught across any pipeline
       change at all.

    ⚠️ Rewinds afterwards. `normalise_gallery_image` reads the same file next,
       and an upload left at EOF opens as a truncated image — which surfaces as
       "that file could not be read as an image" against a perfectly good
       photograph.
    """
    uploaded.seek(0)
    digest = hashlib.sha256()
    for chunk in iter(lambda: uploaded.read(_DIGEST_CHUNK), b""):
        digest.update(chunk)
    uploaded.seek(0)
    return digest.hexdigest()


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
            # ⚠️ Read **before** `draft_to` rewrites `source.size`. Everything
            #    stored is sized from this, never from the decoded size — see
            #    `core.images.stored_size`.
            native = upright_size(source)
            # 🔴 **The floor.** `GalleryPhotoForm.clean_images` checks this
            #    before calling — and it has to, because it skips one photo of
            #    ten and keeps the batch going, which is a decision only the
            #    form can make. But the form is not the only way in: a
            #    management command, a bulk import or a shell session reaches
            #    this function directly, and `draft_to` below is a no-op for
            #    PNG and WebP, so nothing else would stand between an enormous
            #    upload and a full decode. One comparison, on numbers already
            #    read from the header.
            if over_pixel_budget(native):
                raise ValidationError(
                    f"That photo is {native[0]} × {native[1]} pixels, past the "
                    f"{settings.IMAGE_MAX_PIXELS // 1_000_000} megapixel limit.")
            # The memory fix. Full note over `core.images.draft_to`; the short
            # version is that a 49 MP photograph is 146 MB of pixels thrown away
            # in the next breath, and this asks libjpeg not to produce them.
            draft_to(source, stored_size(native, GALLERY_IMAGE_MAX_EDGE))
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
            #
            # ⚠️ 2026-08-13, second round: with `_draft` above, "fully decoded"
            #    now means **at the drafted scale**, which is the intended
            #    scale. What this line still rules out is a *second*,
            #    unasked-for reduction on top of it.
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
            #    The thumbnail is resampled from the large derivative, so it
            #    costs a 1600px resize rather than a second decode. Swap these
            #    and the lightbox picture comes out at 700px — valid file,
            #    right format, wrong picture, no error.
            #
            # ⚠️ The large one is sized from `upright_size` — the photograph's
            #    own dimensions — and **not** from `picture.size`, which is
            #    whatever scale libjpeg decoded at. The thumbnail is then sized
            #    from the large, which is already decoder-independent.
            large = _resized_to(
                picture, stored_size(native, GALLERY_IMAGE_MAX_EDGE))
            image = _encode(large)
            thumb = _encode(_resized(large, GALLERY_THUMB_MAX_EDGE))
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
    #: The thumbnail's signed URL, or "" when nobody has filled it in.
    #:
    #: ⚠️ **Carried here rather than reached through `photo.thumb.url` in the
    #:    template**, and it is not tidiness: signing happens on every `.url`
    #:    call, so markup that asks the FieldFile would re-sign on every render
    #:    and put back the cache miss `services.wall_urls` exists to remove.
    #:    Holding the string is what makes that impossible to undo by accident.
    #:
    #: ⚠️ Defaulted, so `strips_for()` — which knows nothing about signing —
    #:    still constructs these, and so the layout tests that build them
    #:    directly do not each have to invent a URL.
    thumb_url: str = ""

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


# --- Taking photos back off the wall ----------------------------------------

#: How many photos one submission may take down.
#:
#: ⚠️ A cap on the **request**, the same reasoning as MAX_PHOTOS_PER_UPLOAD and
#:    a different number for a different cost. Each removal is two objects out
#:    of R2 — the large one and the thumbnail — so this is 120 round trips done
#:    one after another inside one request. Past a few hundred, the request
#:    times out **halfway**, and because each row is committed as it goes, what
#:    is left behind is "some of them, and you cannot see which".
#:
#: ⚠️ 60 because it is the number already on the page: STRIP_SIZE is what a
#:    day's wall holds, so "clear what is up today" is one submission.
MAX_PHOTOS_PER_REMOVAL = STRIP_SIZE


@dataclass(frozen=True)
class Removal:
    """What a batch of submitted ids turned out to mean.

    ⚠️ Four fields rather than a list of photos, because "what will not happen"
       is half of what the confirmation page has to say. A page that shows 8
       thumbnails after you ticked 10 boxes, and does not account for the other
       two, is a page that looks like it lost them.
    """

    #: The photos that will actually go, in the order they were submitted —
    #: which is the order they sit in on the page, because a browser sends its
    #: inputs in document order and the confirmation page keeps that order.
    photos: list
    #: How many submitted ids are no longer rows. Somebody else got there first.
    missing: int
    #: How many are real photos this person may not take down.
    forbidden: int
    #: True when the batch is over the cap and **nothing** will be done.
    #:
    #: ⚠️ Separate from `photos` being empty, because the two mean opposite
    #:    things to say out loud: "you picked too many" is about the request,
    #:    "none of those were yours" is about the photos. One flag covering both
    #:    would produce a message that is wrong in one of the two cases.
    over_cap: bool = False


def resolve_removal(user, ids):
    """Work out which of `ids` this person is actually taking down.

    ⚠️ Called **twice** per removal — once to build the confirmation page and
       again when it comes back — and it has to be, because that is what makes
       the confirmation page carry no authority. The hidden ids that come back
       are re-judged from scratch, so a page left open while somebody's role was
       revoked cannot delete anything on the strength of having been rendered
       earlier.

    ⚠️ Unknown and forbidden ids are **counted, never named** (decided
       2026-08-14). Skipping them rather than refusing the batch is what was
       asked for; naming them would answer "does photo 412 exist, and whose is
       it?" for anybody willing to type numbers into a form — which the wall
       does not otherwise tell you.

    ⚠️ Over the cap refuses the **whole** batch instead of taking the first 60.
       The upload path does the opposite, and the asymmetry is the point:
       an upload that silently keeps ten of twelve can be finished by sending
       the other two, while a removal that silently takes 60 of 200 has already
       done something no one can undo, to a set nobody can name afterwards.

    ⚠️ **That branch is a backstop, not the experience** (2026-08-14). Being
       told "too many, start again" after picking two hundred boxes is a bad
       way to learn the limit, so the page now stops the 61st box being ticked
       at all — the remaining boxes go grey and say why. This still has to be
       here because that is Alpine's doing, and the no-JS path submits whatever
       it likes; but with a browser running, nobody reaches it.
    """
    from org.permissions import can_delete_gallery_photo

    wanted = []
    for value in ids:
        try:
            number = int(value)
        except (TypeError, ValueError):
            # ⚠️ Not an error. A non-numeric id can only come from a hand-made
            #    POST, and this is the same shrug the missing ones get — there
            #    is no photograph behind it either way.
            continue
        if number not in wanted:
            wanted.append(number)

    if len(wanted) > MAX_PHOTOS_PER_REMOVAL:
        return Removal(photos=[], missing=0, forbidden=0, over_cap=True)

    found = GalleryPhoto.objects.on_the_wall().in_bulk(wanted)
    photos, forbidden = [], 0
    for number in wanted:
        photo = found.get(number)
        if photo is None:
            continue
        if not can_delete_gallery_photo(user, photo):
            forbidden += 1
            continue
        photos.append(photo)
    return Removal(
        photos=photos,
        missing=len(wanted) - len(found),
        forbidden=forbidden,
    )


def remove_photo(photo):
    """Delete one photo: both files first, then the row. Returns its caption.

    ⚠️ **The files go before the row does, and both of them go.** Deleting only
       the row leaves two objects in the bucket that nothing points at any more
       — invisible, unbilled to anyone's attention, and still holding a
       photograph somebody asked to have taken down. The same mistake the front
       page made until 2026-08-14, with a worse subject.

    ⚠️ One function, two callers — the single Remove button and the batch. This
       is a four-line body that it would be entirely natural to write out at
       each call site, and then the batch path would be the one that forgets the
       thumbnail: nothing would break, no test would go red, and the leak would
       only ever show up as a bucket bill.

    ⚠️ The caption is read out **before** anything is deleted, because it is
       assembled from the row and the row is about to stop existing.
    """
    caption = photo.caption
    photo.image.delete(save=False)
    photo.thumb.delete(save=False)
    photo.delete()
    return caption


#: How long one batch of signed URLs is handed out for.
#:
#: 🔴 **This is what lets a browser cache work at all**, and it has to stay
#:    strictly under `querystring_expire` on the memories bucket
#:    (config/settings/prod.py, four hours). Somebody served a URL in the last
#:    second of a window still has the remainder of the signature's life to
#:    fetch it with — an hour, at these two numbers.
#:
#: ⚠️ Three hours rather than "as long as possible": the gap between the two is
#:    the whole safety margin, and a window equal to the expiry would hand out
#:    URLs that die on arrival.
WALL_URL_WINDOW = 3 * 3600


def wall_urls(photos):
    """`{photo_id: {"thumb": url, "image": url}}` for one wall, signed once.

    🔴 **A presigned URL is different every time it is generated**, and that is
       the whole problem. django-storages signs on every `url()` call and
       botocore stamps the current moment into the signature, so the same
       photograph arrives under a new URL on every render — a new cache key,
       every time. Measured 2026-09-01: the wall re-fetched all sixty
       thumbnails on every visit, a browser-cache hit rate of exactly zero.
       Upstream says as much in the docstring of its own `S3StaticStorage`.

    ⚠️ **One cache entry for the whole page, never one per photograph.**
       `CACHES` is the database backend, so a per-photo cache would be 120
       SELECTs on a page that today runs a handful — more expensive than the
       signing it replaces, and this project has query-count tests for exactly
       that class of regression.

    ⚠️ 120 and not 60: the strips carry thumbnails, the lightbox sequence
       carries the full-size images, and both are signed.

    ⚠️ Keyed by the **photo ids**, not by the day. The draw is seeded by the
       date so the page holds still, but a photograph taken down disappears
       from it — so two renders minutes apart can legitimately differ, and a
       date-only key would go on serving a URL for a picture that has gone.

    ⚠️ Returns plain strings. Handing the template a `FieldFile` would let a
       `.url` slip back in and re-sign, quietly restoring the bug — which is why
       the two templates are given these instead.
    """
    if not photos:
        return {}

    now = int(time.time())
    ids = sorted(photo.pk for photo in photos)
    digest = hashlib.sha256(repr(ids).encode()).hexdigest()[:16]
    key = f"gallery:wall-urls:{now // WALL_URL_WINDOW}:{digest}"

    urls = cache.get(key)
    if urls is None:
        urls = {photo.pk: {"thumb": photo.thumb.url, "image": photo.image.url}
                for photo in photos}
        # ⚠️ Expires **with** the window, not a full window from now. The key
        #    already changes at the boundary, so a longer life would only leave
        #    an unreachable row behind in the cache table for every window that
        #    passes — the database backend has no eviction of its own.
        cache.set(key, urls, WALL_URL_WINDOW - (now % WALL_URL_WINDOW))
    return urls
