"""Image geometry: the arithmetic both upload paths need, and nothing else.

⚠️ **No policy lives here.** How large a picture is stored, at what quality,
   how many derivatives come out of one upload and how long they survive are
   decisions belonging to whichever app is doing the storing —
   `events.services.normalise_event_image` and
   `gallery.services.normalise_gallery_image` are deliberately *not* shared for
   exactly that reason, and their docstrings say so. What is shared here is the
   part that is not a decision at all: how to work out an aspect-preserving
   target size, and how to ask a JPEG for a cheaper decode.

⚠️ It is a module rather than a copy in each app **because the copies drifted
   inside a day**. The reduced-scale decode was first written as one copy per
   app, held together by a behaviour check in each app's tests — and the two
   still produced pictures a pixel apart, because only one of them had been
   given the size arithmetic below. The behaviour checks were both green: they
   asked "was this decoded smaller?", which was true on both sides. Arithmetic
   is not policy, and a wrong aspect ratio is wrong in both apps.
"""

import io

#: EXIF tag 274, Orientation. Values 5–8 are the quarter turns — for those the
#: picture's width and height swap once it has been turned upright.
_EXIF_ORIENTATION = 274
_ORIENTATIONS_THAT_TURN = frozenset({5, 6, 7, 8})


#: JPEG markers this module has to know about.
#:
#: ⚠️ `APP0`–`APP15` is where every metadata block lives — Exif is APP1, XMP is
#:    another APP1, ICC is APP2, and camera makers put their own in the rest.
#:    `SOS` is where the compressed picture itself begins, and nothing past it
#:    is ever touched.
_JPEG_SOI = b"\xff\xd8"
_JPEG_APP_FIRST, _JPEG_APP_LAST = 0xE0, 0xEF
_JPEG_SOS = 0xDA


def without_metadata(data):
    """The same JPEG with its metadata gone, **pixel for pixel identical**.

    🔴 **Why this exists: `HomePage.hero_image` was the one upload in the
       project stored exactly as it arrived, and it is the one in a public,
       unsigned bucket.** Measured on 2026-09-01, straight through the real
       upload path: a phone photograph kept all 4 of its GPS tags, on a URL
       that needs no login — while Memories (private bucket) and the hero's own
       responsive rungs both came out with none. The front page now offers that
       original as the top rung of its `srcset`, so it is not merely sitting in
       a bucket, it is being handed to every large display.

    ⚠️ **Lossless, and that is the whole reason it is written this way.** A JPEG
       is a chain of segments; the compressed picture lives after `SOS` and the
       metadata lives in the `APPn` segments before it. Dropping those and
       copying the rest verbatim leaves every pixel bit-identical, which is what
       lets this satisfy "do not compress unless it costs nothing" exactly —
       nothing is re-encoded, nothing is resampled. Re-saving through Pillow
       would have been three lines and would have re-compressed the photograph.

    🔴 **Orientation is put back, and leaving it out is a real bug rather than a
       nicety.** A phone shooting in portrait writes a *landscape* raster plus
       `Orientation=6`, and the browser turns it. Strip that and the browser
       draws it on its side — while the rungs, whose pixels were already turned
       by `exif_transpose`, come out upright. The visible result is the same
       photograph sideways on a large screen and correct on a small one, with
       nothing raised anywhere. Verified before this shipped.

    ⚠️ **JPEG only; anything else is returned untouched.** PNG and WebP keep
       metadata in a different structure and would each need their own surgery.
       Left as a stated gap rather than papered over, on the grounds that GPS
       comes from cameras and cameras write JPEG — but it *is* a gap: a PNG
       exported from software that embedded location would still carry it.

    ⚠️ Returns the input unchanged when there is nothing to remove, so callers
       can compare and skip a pointless re-upload.
    """
    from PIL import Image

    if not data.startswith(_JPEG_SOI):
        return data
    try:
        with Image.open(io.BytesIO(data)) as source:
            orientation = source.getexif().get(_EXIF_ORIENTATION)
    except Exception:
        # Unreadable metadata is not a reason to refuse the upload — the same
        # judgement as `turns_upright`. Hand it back as it came.
        return data

    out = bytearray(_JPEG_SOI)
    if orientation and orientation != 1:
        keep = Image.Exif()
        keep[_EXIF_ORIENTATION] = orientation
        payload = keep.tobytes()
        out += b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload

    index = 2
    while index < len(data):
        if data[index] != 0xFF:
            # Not a marker where one is required. Rather than guess at a
            # malformed file, give back exactly what arrived.
            return data
        marker = data[index + 1]
        if marker == _JPEG_SOS:
            # ⚠️ Everything from here on is copied verbatim, and that includes
            #    the scan data and the trailing EOI. This is the line that makes
            #    the function lossless.
            out += data[index:]
            break
        length = int.from_bytes(data[index + 2:index + 4], "big")
        if not _JPEG_APP_FIRST <= marker <= _JPEG_APP_LAST:
            out += data[index:index + 2 + length]
        index += 2 + length

    stripped = bytes(out)
    return stripped if stripped != data else data


def a_flat_png(size):
    """A PNG of one colour at `size`, as bytes. For tests, and only for tests.

    ⚠️ Here rather than in a test module because **two of them want it** —
       `core.tests` asks whether the pixel gate refuses one, `gallery.tests`
       asks whether a batch upload skips one and names it. It was written twice,
       identically, on the day the gate was added.

    ⚠️ Flat colour is the whole point: it is what makes a picture enormous in
       pixels and negligible on the wire, which is the shape that slipped
       between the byte limit and Pillow's bomb guard. A photograph does not
       compress anything like this.
    """
    from PIL import Image

    image = Image.new("RGB", size, (120, 60, 30))
    buffer = io.BytesIO()
    image.save(buffer, "PNG", compress_level=9)
    image.close()
    return buffer.getvalue()


def is_new_upload(value):
    """Is this a file somebody just chose, rather than the one already stored?

    ⚠️ A `clean_<field>` for a file field is handed the **stored `FieldFile`**
       when nobody touched the input, and re-validating or re-encoding that is
       both wasted work and, on a private bucket, a download. `content_type`
       is the attribute only an upload carries.

    ⚠️ Named rather than left as `hasattr(value, "content_type")` at each call
       site: that expression says how the question is answered, not what is
       being asked, and it is now asked in three forms across two apps.
    """
    return bool(value) and hasattr(value, "content_type")


def over_pixel_budget(size):
    """Is a picture this size more than the instance can afford to decode?

    ⚠️ Takes a size rather than a file, so that the pipelines — which have the
       photograph open and its dimensions already in hand — can ask without a
       second read. `too_many_pixels` below is the same question asked of a
       file that has not been opened yet. One threshold, two ways in.
    """
    from django.conf import settings

    width, height = size
    return width * height > settings.IMAGE_MAX_PIXELS


def too_many_pixels(upload):
    """Is this upload past `IMAGE_MAX_PIXELS`? Reads the header, never decodes.

    🔴 **The check the byte limit cannot make.** A decode costs width × height ×
       channels and has almost nothing to do with the file size: a 9000×9000 PNG
       of flat colour is 0.25 MB on the wire and 243 MB in memory, which is how
       it passed both the 10 MB gate and Pillow's own bomb limit at once.
       Measured 2026-09-01. The full accounting is over `IMAGE_MAX_PIXELS`.

    ⚠️ **`Image.open()` parses the header and stops** — `size` is available
       without a single pixel being decoded, which is what makes it safe to ask
       this question of a file precisely because it might be enormous. Decoding
       first to find out whether decoding was affordable is the mistake this
       exists to avoid.

    ⚠️ The file position is put back, because the caller is going to hand the
       same object to the pipeline next and Django's uploaded files are read
       from wherever they were left.

    ⚠️ Unreadable is **not** "too many pixels". Whatever cannot be opened is
       somebody else's complaint to make, with a message about the format
       rather than about the size — `normalise_*` raises it a moment later.
    """
    from PIL import Image

    try:
        upload.seek(0)
        with Image.open(upload) as source:
            size = source.size
    except Exception:
        return False
    finally:
        upload.seek(0)
    return over_pixel_budget(size)


def turns_upright(source):
    """Does turning this picture upright swap its width and height?

    ⚠️ One reader for the tag, because **two callers have to agree about it**:
       `upright_size` reports the size after the turn, and `draft_to` has to
       ask the decoder for a size before it. They disagreed until 2026-08-31,
       and the cost is in the note over `draft_to`.
    """
    try:
        exif = source.getexif()
        orientation = exif.get(_EXIF_ORIENTATION) if exif else None
    except Exception:
        # Bare except on purpose — see `upright_size`. EXIF is written by
        # thousands of devices and some of them write nonsense.
        return False
    return orientation in _ORIENTATIONS_THAT_TURN


def upright_size(source):
    """The picture's size **after** it is turned upright.

    ⚠️ Must be read before `draft_to` runs, because that rewrites
       `source.size`. Everything downstream is sized from this rather than from
       the decoded size, which is the whole point — see `stored_size`.

    ⚠️ Bare except on purpose, matching `gallery.services._exif_year`: EXIF is
       written by thousands of devices and some write nonsense. An unreadable
       orientation costs a photograph nothing here — this is only ever the
       difference between (w, h) and (h, w), and `ImageOps.exif_transpose` is
       what actually turns the pixels.
    """
    width, height = source.size
    if turns_upright(source):
        return height, width
    return width, height


def stored_size(size, max_edge):
    """What a picture that arrived at `size` is stored at. None = leave it be.

    ⚠️ **Worked out from the size the photograph arrived at, and then handed to
       the resize** — never recomputed from the size it decoded to. With a
       reduced-scale decode in front, the decoded picture's aspect ratio is a
       hair off the original: a 1/2 or 1/4 decode ceilings both edges, and
       ceiling two numbers does not preserve a ratio. `Image.thumbnail()` works
       its own target out from whatever it is handed, so it rounds the short
       edge off that shifted ratio.

       Measured, on one of the foundation's own photographs (6000×3429): the
       event card came out 900×515 where the arithmetic says 900×514, and the
       Memories derivative 1600×914 or 1600×915 depending on the decode. One
       pixel, and the wrong one — but the reason to close it is not the pixel.
       It is that the stored dimensions would otherwise depend on **which scale
       libjpeg picked for that particular photograph**, and `thumb_width` /
       `thumb_height` are columns the Memories layout divides by. Nobody
       chasing a layout would ever think to look there.

    ⚠️ The longest edge is assigned rather than computed, so floating point
       cannot land it on 1599.

    ⚠️ Never enlarges. A 400px scan blown up to 1600px is bytes and no detail,
       and `gallery.services.WallPhoto.relative_height` is built on nothing
       being taller than the constant.
    """
    width, height = size
    if max(width, height) <= max_edge:
        return None
    if width >= height:
        return max_edge, max(1, round(height * max_edge / width))
    return max(1, round(width * max_edge / height)), max_edge


def width_size(size, max_width):
    """The same, keyed on **width** rather than on the longest edge.

    ⚠️ Not a convenience wrapper, and the difference is only invisible on
       landscape pictures. `srcset` describes each candidate with a `w` — the
       number the browser compares against "viewport width × device pixel
       ratio" — so a ladder built with `stored_size` would label a portrait
       photograph by its **height** and hand phones the wrong rung. The front
       page asks for landscape and the focus picker assumes it, but the field
       accepts whatever is uploaded: there is a 5120×5120 in the development
       bucket right now.

    ⚠️ Here rather than in `core.renditions` for the reason this module's own
       docstring gives — aspect-preserving arithmetic is not policy, and the
       copies drift inside a day. How many rungs there are and how wide they go
       is the caller's decision and stays there.

    ⚠️ `None` means "already narrow enough, leave it be", exactly as above, and
       the ladder relies on it: a rung wider than the picture is not generated
       at all rather than upscaled into bytes without detail.
    """
    width, height = size
    if width <= max_width:
        return None
    return max_width, max(1, round(height * max_width / width))


def draft_to(source, target):
    """Ask a JPEG to decode at reduced scale. A no-op when `target` is None.

    This is the memory fix. A photograph's file is compressed; working on it
    means decoding it, and what that costs is the **pixel count, not the file
    size** — 49 MP is 146 MB of pixels out of a 6 MB file. The decode is then
    thrown away immediately, because nothing here stores a picture larger than
    a low four-figure number of pixels on its longest edge.

    JPEG holds its picture coarse-detail-first, so libjpeg can be stopped at
    1/2, 1/4 or 1/8 scale and still produce the **whole** frame, only less
    finely — and the fine detail was about to be resampled away. Measured on
    Linux, one 49 MP upload through the Memories pipeline: 278 MB of peak RSS
    without this, 47 MB with it, against a 512 MB instance already holding two
    workers.

    ⚠️ `target` must be the aspect-preserving size from `stored_size`, which is
       why this takes a size rather than a maximum edge. Pillow picks its scale
       from `min(width // target_width, height // target_height)`, so a square
       target lets the **short** edge pin the scale at 1 and the call does
       nothing whatever — for a 4032×3024 photograph, which is what most phones
       produce. Measured: a square target left the 12 MP case at 165 MB; the
       aspect-correct one takes it to 70 MB. A wrong target here looks exactly
       like a right one, and nothing raises.

    ⚠️ It cannot decode *below* what is asked for. Pillow's scale is a floor
       division by the target, so the decoded picture is at least the target on
       both edges and the resize afterwards always has something to resample
       down from.

    ⚠️ Two limits, stated rather than hidden — the same two as over
       `core.palette.dominant_colour`:
         · **no-op for PNG and WebP.** Only JPEG stores its picture in layers,
           so a very large PNG is still decoded whole. The honest answer to
           that one is a limit on dimensions, not a trick.
         · libjpeg offers 1/2, 1/4 and 1/8 and nothing finer, so **1/8 is the
           floor** whatever is asked for. A genuinely enormous photograph
           (20000px wide) still decodes at 2500px.
    """
    if target is None:
        return
    # 🔴 **The target has to be expressed in the raster's own axes, and until
    #    2026-08-31 it was not** — which made this whole function a silent
    #    no-op for exactly the uploads it was written for.
    #
    #    Every caller computes its target from `upright_size`, i.e. from the
    #    picture *after* the EXIF turn. The decoder has not turned anything yet.
    #    So for a photograph shot in portrait on a phone — a 4000×3000 raster
    #    with orientation 6, which is what "portrait" means in a JPEG — the
    #    target arrives as 2560×3413 against a 4000×3000 raster, and Pillow
    #    computes `scale = min(4000 // 2560, 3000 // 3413) = min(1, 0) = 0`.
    #    Its `for s in [8, 4, 2, 1]` loop then finds nothing `<= 0`, falls
    #    through, and leaves the scale at 1: the full 12 MP is decoded, on the
    #    512 MB instance this function exists to protect. Nothing raises, the
    #    output is byte-identical, and the only trace is the memory.
    if turns_upright(source):
        target = (target[1], target[0])
    source.draft("RGB", target)
