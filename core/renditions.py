"""The front page's photograph at the widths a screen actually has.

The hero was the one upload in this project that was stored and served exactly
as it arrived — Memories goes through `gallery.services.normalise_gallery_image`
(1600px WebP) and event pictures through `events.services.normalise_event_image`,
and neither of those paths ever reached this field. What that cost was measured
on 2026-08-31, on the live file: 5312×2819 and 3.65 MB, which is **59.9 MB of
decoded bitmap**, painted as a `position: fixed` layer on every page in dark
mode. A 1512×982 screen at 2x can show 5.9 MP; the browser was being handed 15
and throwing away nine before anything reached the glass.

⚠️ **This is not compression, and the distinction is the whole brief.** The
   original is kept, untouched, and stays the top rung of the front page's
   `srcset` — a 5K display still downloads and draws every pixel that was
   uploaded. What changes is that a phone stops downloading a picture for a
   screen it does not have. On any given display the pixels that reach it are
   the same pixels; there are simply no longer six times too many of them in
   flight.

⚠️ **The two consumers pick a rung by different mechanisms, and that is not an
   inconsistency.** The front page's picture is always visible, so it is an
   `<img srcset sizes="100vw">` and the browser chooses. The shared dark
   backdrop is `display: none` in light mode, where an `<img>` is fetched and a
   `background-image` is not — so it stays a background and `app.css` chooses
   with a media query. Measured, not assumed; the whole finding is in
   `core/templates/core/components/_hero_backdrop.html`.

⚠️ Its shape is deliberately `core.palette`'s: derived from `hero_image`,
   refreshed from `HomePage.save()`, and importing PIL inside the function so
   that the model layer does not drag it in.

⚠️ **Policy lives here, arithmetic lives in `core.images`.** That module's
   docstring draws the line and this file stays on its side of it: how many
   rungs, how wide, in what format and at what quality are decisions about the
   front page, and `gallery.services` is deliberately not shared with — the two
   pipelines agree today and have no reason to stay agreed.
"""

import io
import uuid
from collections import namedtuple

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile

from core.images import draft_to, over_pixel_budget, upright_size, width_size

#: What `render_ladder` answers with: the rungs, and the upright size of the
#: picture they were cut from.
#:
#: ⚠️ The size is returned rather than left for the caller to ask the file for.
#:    `ImageField.width` **opens the file** unless a `width_field` is filled, so
#:    reading it to build a `srcset` would put an R2 round trip on the busiest
#:    public URL in the site, once per render. This function has the picture
#:    open already and both numbers are free here.
#:
#: ⚠️ **Both** numbers, not just the width. The width is the `w` descriptor; the
#:    height is needed for the aspect ratio, and the aspect ratio is what makes
#:    `sizes` correct for a picture drawn with `object-fit: cover` — see
#:    `HomePage.hero_sizes`.
Ladder = namedtuple("Ladder", "size files")

#: The rungs, in ascending width. `srcset` picks by "viewport width × DPR", so
#: these are the numbers a browser compares against.
#:
#: ⚠️ **Capped at 2560 for a server-side reason, not a visual one.** The
#:    largest rung is what `draft_to` is asked for, and it is what decides
#:    whether libjpeg can halve the decode: against the live 5312px photograph
#:    2560 buys a 1/2 decode (~11 MB peak), while 3840 buys nothing at all —
#:    5312 // 3840 == 1 — and a 24 MP upload would then decode whole, in the
#:    150 MB range, against a 512 MB instance already holding two workers.
#:    This repository has been taken down that way twice; see the measurements
#:    over `core.images.draft_to` and `gallery.services.normalise_gallery_image`.
#:
#: ⚠️ Nothing below 1280. The smallest real target is a 390pt phone at 3x, which
#:    asks for 1170; a rung under that would only ever serve 1x handsets.
#:
#: ⚠️ Adding a rung is a migration, because each one is a field on HomePage —
#:    see the note over `HomePage.MEDIA_FIELDS` for why they cannot be a JSON
#:    blob instead.
HERO_RENDITION_WIDTHS = (1280, 1920, 2560)

#: Matches `gallery.services.GALLERY_IMAGE_QUALITY`. The same number by
#: coincidence rather than by sharing: it is the quality of *this* picture, and
#: the day one of the two moves the other has no reason to follow.
HERO_RENDITION_QUALITY = 82


def rendition_field(width):
    """The field on HomePage that holds the rung `width` pixels wide.

    ⚠️ One function, so that the naming rule is written once. The model
       declares the fields, `MEDIA_FIELDS` sweeps them and the srcset
       properties read them — three places that have to agree on a string, and
       the way they stop agreeing is somebody typing it a fourth time.
    """
    return f"hero_image_{width}"


def render_ladder(image_file):
    """`Ladder(width, {width: ContentFile})` for `image_file`.

    `files` holds a rung for every configured width narrower than the picture:
    empty when the picture is already narrower than the smallest rung, and
    never containing a rung wider than the original — a ladder that upscales is
    bytes without detail, and it would also lie in the `w` descriptor about
    what the browser is getting.

    `width` is the picture's own width **once turned upright**, which is what
    the original's `w` descriptor on the front page has to say.

    ⚠️ **The original is never one of the returned files and is never
       rewritten.** The caller stores these alongside it. That is the constraint
       this whole feature was built under.

    The order below is `gallery.services.normalise_gallery_image`'s, and every
    line of it was paid for there:

      · `upright_size` **before** `draft_to`, because the latter rewrites
        `source.size` and every target has to come from the size the photograph
        actually arrived at (see `core.images.stored_size`);
      · `draft_to` at the widest rung — the memory fix, and the reason the
        widest rung is 2560;
      · `exif_transpose(in_place=True)`, which avoids a second full-size copy
        living under `source` for the rest of the call;
      · `load()`, so that a later resize cannot quietly ask the decoder for a
        *second* reduction on top of the drafted one;
      · `convert` only when the mode is not already the one wanted, because
        converting into the mode you are in still returns a full-size copy.

    ⚠️ **Every rung below the widest is resampled from the widest, not from the
       decode.** Same rule as the Memories thumbnail, same reason: the decoded
       picture's size depends on which scale libjpeg picked for this particular
       file, and only the widest rung is sized from `upright_size`. Taking the
       small ones off it makes them decoder-independent too — and costs one
       resize each instead of a second decode.
    """
    from PIL import Image as PILImage
    from PIL import ImageOps as PILImageOps

    with PILImage.open(image_file) as source:
        native = upright_size(source)
        # 🔴 **The floor, and it belongs here rather than only in the form.**
        #    `HomePageForm` refuses an oversized upload, but the form is one of
        #    three ways into this function — `rebuild_hero_renditions` and a
        #    plain `page.hero_image = ...; page.save()` from a shell both arrive
        #    without passing it. Measured on 2026-09-02: a 50 MP PNG of 0.16 MB
        #    reached this line through `save()` and decoded to a 366 MB process,
        #    on the 512 MB instance the limit exists to protect.
        #
        # ⚠️ Costs nothing: `upright_size` has already read the header, and this
        #    is one comparison against numbers that are in hand.
        #
        # ⚠️ Note the asymmetry it removes — metadata stripping was put in
        #    `HomePage.save()` precisely so the shell and the command are
        #    covered too. The pixel check for the same field stopped at the
        #    admin screen until this line.
        if over_pixel_budget(native):
            raise ValidationError(
                f"That picture is {native[0]} × {native[1]} pixels, past the "
                f"{settings.IMAGE_MAX_PIXELS // 1_000_000} megapixel limit that "
                f"keeps a decode inside this instance's memory.")
        wanted = {}
        for width in HERO_RENDITION_WIDTHS:
            target = width_size(native, width)
            if target is not None:
                wanted[width] = target
        if not wanted:
            # ⚠️ Still reports the width. A picture too narrow for any rung has
            #    no ladder and is served as itself — but the caller stores the
            #    number either way, so that nothing later has to open the file
            #    to find it out.
            return Ladder(native, {})

        widest = max(wanted)
        draft_to(source, wanted[widest])
        PILImageOps.exif_transpose(source, in_place=True)
        source.load()

        picture = source
        mode = "RGBA" if "A" in picture.getbands() else "RGB"
        if picture.mode != mode:
            picture = picture.convert(mode)

        largest = picture.resize(wanted[widest], PILImage.LANCZOS)
        rungs = {widest: largest}
        for width in sorted(wanted, reverse=True)[1:]:
            rungs[width] = largest.resize(wanted[width], PILImage.LANCZOS)

        return Ladder(native,
                      {width: _encode(image) for width, image in rungs.items()})


def _encode(image):
    """One rung as a stored WebP.

    ⚠️ No `exif=`, so none is written. Stated rather than assumed — "Pillow does
       not copy it by default" is the kind of default that changes, and these
       files are public: the original's GPS tags have no business on a URL that
       needs no login.
    """
    buffer = io.BytesIO()
    image.save(buffer, "WEBP", quality=HERO_RENDITION_QUALITY, method=6)
    return ContentFile(buffer.getvalue(), name=f"{uuid.uuid4().hex}.webp")
