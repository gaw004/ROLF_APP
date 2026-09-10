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
       that needs no login — while Memories, which is *private*, kept none.

       ⚠️ It matters **more** since 2026-09-09, not less. The front page's
          srcset ladder was removed that day, so this original is the only file
          the page serves: every screen downloads exactly these bytes, rather
          than most of them getting a rung cut from it.

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
       draws it on its side — a perfectly valid file, nothing raised anywhere,
       the front page simply sideways. Verified before this shipped.

       ⚠️ Until 2026-09-09 the symptom was stranger and easier to misread: the
          derived rungs had already been turned by `exif_transpose`, so the
          same photograph came out sideways on a large screen and upright on a
          small one. With the ladder gone there is one file and one answer.

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


#: What one pixel costs to decode, in bytes, split into the part `draft_to`
#: can take away and the part it cannot.
#:
#: 🔴 **A pixel count is not a cost, and treating it as one is what let a
#:    1.48 MB file take the instance down.** Measured 2026-09-09 on one
#:    5000×4000 picture saved four ways, decoded at four different draft
#:    targets — the same twenty megapixels cost between 2.9 MB and 310 MB
#:    depending only on how the file was written:
#:
#:        存法              edge=4000  1600   900   320
#:        JPEG baseline       78 MB   21 MB  6.5MB 2.9MB   ← draft works
#:        JPEG progressive   135 MB   78 MB   64MB  60MB   ← floors out
#:        PNG                 78 MB   77 MB    —    77 MB  ← draft is a no-op
#:        WebP               310 MB  308 MB    —   308 MB  ← ditto, and 4x
#:
#: ⚠️ **Only baseline JPEG gets cheaper when it is drafted.** libjpeg decodes a
#:    progressive file by first building the coefficient array for the *whole*
#:    frame, and `scale_denom` does not shrink that — which is why the second
#:    row stops falling. PNG and WebP cannot be drafted at all (`draft_to` says
#:    so, and `core.palette.dominant_colour` has said so since 2026-08-12).
#:    So three of the four rows are priced on the size the picture **arrived**
#:    at, and only the first is priced on the size it decodes to.
#:
#: ⚠️ Every figure is rounded up from the measurements by **about a quarter,
#:    and by the same proportion in each row** — 4.1 becomes 5, 16.2 becomes 20.
#:    Rounding them by different amounts was the first attempt and it is subtly
#:    wrong: taking 4.1 to 6 while taking 16.2 to 17 flattens the ratio between
#:    the rows from 4x to under 3x, and the ratio is the whole finding. A margin
#:    is for the platform difference (these were fitted on macOS and the
#:    deployment runs Linux — `gallery.services.normalise_gallery_image` carries
#:    the same warning), not something to spend where it happens to feel safe.
#:
#: ⚠️ So what the tests assert is the **inequalities between the rows**, never a
#:    byte count: WebP several times a JPEG, progressive dearer than baseline,
#:    and only baseline JPEG getting cheaper when it is drafted. Re-measure and
#:    the numbers move; the shape is what must not.
#:
#: ⚠️ WebP being 20 rather than 3 is measured, not explained; it is linear in
#:    the pixel count across three sizes (4.4 MP 15.5 B/px, 20 MP 16.2,
#:    49.5 MP 15.4), so whatever libwebp holds scales with the frame. Not
#:    knowing why does not make it less true.
#:
#: ⚠️ **The one place the model runs under a real decode is a heavily drafted
#:    baseline JPEG**: at a 1/8 decode it charges 1.6 MB where 2.9 was measured,
#:    because the fixed part of libjpeg's working set does not shrink with the
#:    scale. Stated rather than papered over — it cannot change a decision,
#:    since both figures are three orders of magnitude inside the budget, and
#:    giving that row a fixed cost would over-charge every large photograph on
#:    the path the budget actually protects.
DECODE_BYTES_PER_PIXEL = {
    # (format, progressive): (per pixel of the original, per pixel decoded)
    ("JPEG", False): (0, 5),
    ("JPEG", True): (4, 5),
    ("PNG", False): (5, 0),
    ("WEBP", False): (20, 0),
}

#: Anything unrecognised is priced as the worst row above. A format nobody has
#: measured is not a cheap format, and this gate is the last thing between an
#: upload and a decode.
UNKNOWN_BYTES_PER_PIXEL = (20, 0)

#: Formats Pillow names separately but decodes through the JPEG reader.
#:
#: 🔴 **MPO is what an ordinary phone photograph is**, and leaving it out
#:    refused them (found in review, 2026-09-09, before this shipped). A JPEG
#:    carrying an MPF segment — a second frame for depth or a wide-angle pair,
#:    which Samsung, Sony, Fujifilm and much of Android write by default — is
#:    reported by Pillow as `MPO` rather than `JPEG`. `MpoImageFile` subclasses
#:    `JpegImageFile` and `draft_to` reduces it exactly the same way, so the
#:    cost is a JPEG's; but keyed on the raw format string it fell through to
#:    `UNKNOWN_BYTES_PER_PIXEL` and a 4000×3000 photograph was priced at
#:    229 MB and refused on all three upload paths. The old megapixel gate let
#:    it through, so this was a regression on real photographs from real
#:    phones — and one whose only symptom is somebody being told their ordinary
#:    picture is too big.
#:
#: ⚠️ An **alias table rather than `isinstance(source, JpegImageFile)`**, so
#:    that `decode_cost` stays a function of four plain values and can be
#:    tested without opening a file. That is what the module docstring means by
#:    arithmetic: it must be checkable without Pillow in the room.
FORMAT_ALIASES = {"MPO": "JPEG"}


def bytes_per_pixel(fmt, progressive):
    """`(per native pixel, per decoded pixel)` for a picture written this way.

    ⚠️ **The single lookup**, because there are three callers and the aliasing
       below has to apply to all of them. Written out three times is how a
       phone photograph comes to be refused on one path and accepted on
       another — the copies would each look right.
    """
    key = (FORMAT_ALIASES.get(fmt, fmt), bool(progressive))
    return DECODE_BYTES_PER_PIXEL.get(key, UNKNOWN_BYTES_PER_PIXEL)


def decode_cost(fmt, progressive, native, drafted):
    """Roughly how many bytes decoding this picture will take.

    Pure arithmetic over four numbers, so it can be tested without a file:
    `native` and `drafted` are pixel counts, the first as the picture was
    written and the second as the decoder has been asked to produce it. They
    are equal for everything except a baseline JPEG that `draft_to` reduced.

    ⚠️ An estimate, and it says so by being generous — about a quarter over
       every row it was fitted to. The one exception, and why it cannot change
       an answer, is in the last note over `DECODE_BYTES_PER_PIXEL`.
    """
    per_native, per_drafted = bytes_per_pixel(fmt, progressive)
    return native * per_native + drafted * per_drafted


def is_progressive(source):
    """Does this JPEG hold its picture progressively?

    ⚠️ Two spellings, and Pillow sets **both together** — `JpegImagePlugin.SOF`
       writes them as a pair for SOF2/6/10/14, and an Adobe APP14 segment sets
       neither (it sets `info["adobe"]`). So either key alone would do today;
       both are read because the pair is what upstream documents, and getting
       this wrong prices a progressive photograph as a baseline one — the
       direction that costs an instance rather than a picture.

       ⚠️ Stated this way after a review found the earlier note claiming the
          two spellings came from different markers. They do not, and a reason
          that is wrong is worse than none: it invites somebody to "simplify"
          on a premise that was never true.
    """
    return bool(source.info.get("progressive") or source.info.get("progression"))


def over_decode_budget(source, native):
    """Would decoding `source` cost more than this instance can spare?

    ⚠️ **`source` must already have been through `draft_to`**, because its
       `size` is what the decoder has been told to produce and that is half the
       arithmetic. `draft()` sets decoder parameters and decodes nothing, so
       asking after it is free — and asking *before* it would price every
       baseline JPEG at full size and refuse ordinary phone photographs.

    ⚠️ `native` is the **size the picture arrived at** — a `(width, height)`
       like everything else in this module takes — passed rather than read back
       off `source` because `draft()` has already rewritten `source.size` by
       the time this runs. The pipelines all have it in hand from `upright_size`
       a line or two earlier.

    ⚠️ The file-level twin is `decode_complaint_for` below. One threshold,
       two ways in — the shape `over_pixel_budget` / `too_many_pixels` had,
       kept for the same reason: the forms want to complain next to a field,
       the pipelines want a floor under the shell and the management commands.
    """
    from django.conf import settings

    drafted_width, drafted_height = source.size
    cost = decode_cost(source.format, is_progressive(source),
                       native[0] * native[1], drafted_width * drafted_height)
    return cost > settings.IMAGE_DECODE_BUDGET_BYTES


def _rewound(upload):
    """Put the read position back. Says whether it worked; never raises.

    ⚠️ The caller hands the same object to the pipeline next and Django's
       uploaded files are read from wherever they were left, so the rewind is
       not optional. But it is also the one line that can fail on a file that
       is not there — a `FieldFile` opens its object lazily, so `seek` is where
       a missing key or an object-store timeout surfaces. Reporting that rather
       than raising is what lets the caller's own answer stand.
    """
    try:
        upload.seek(0)
        return True
    except Exception:
        return False


def decode_complaint_for(upload, max_edge):
    """The same question asked of a file nobody has opened yet, and the
    sentence to show if the answer is yes. `""` when the picture fits.

    ⚠️ It answers with the complaint rather than with a boolean **so that the
       picture is opened once**. The three forms that call it want two
       different things — two of them show the sentence beside the field, and
       `GalleryPhotoForm` only wants to know which of ten files to skip — and a
       predicate plus a separate message-builder would mean two header reads
       and two chances for them to disagree about the answer.

    🔴 **The check a byte limit cannot make, and neither can a pixel limit.**
       What a decode costs is the pixel count *times a number that depends on
       how the file was written*, and those two facts have almost nothing to do
       with the file's size. Measured 2026-09-09: a 1.48 MB WebP of 8000×6192
       sailed through the 50-megapixel gate that stood here before and needs
       **762 MB** to open, on a 512 MB instance. The gate was not too loose; it
       was measuring the wrong thing.

    ⚠️ **`Image.open()` parses the header and stops**, and `draft()` only sets
       decoder parameters — so this whole function reads a few hundred bytes
       and decodes not one pixel, which is exactly what makes it safe to ask of
       a file precisely because it might be enormous. Decoding to find out
       whether decoding was affordable is the mistake this exists to avoid.

    ⚠️ `max_edge` is the caller's, because the answer genuinely differs: the
       front page samples at `core.palette.PALETTE_SAMPLE_EDGE`, Memories
       stores at 1600 and event pictures at 900, and a baseline JPEG drafted to
       one of those costs a quarter of what it costs drafted to another. A
       shared default here would be a policy decision smuggled into an
       arithmetic module — see this module's docstring.

    ⚠️ The file position is put back, because the caller hands the same object
       to the pipeline next and Django's uploaded files are read from wherever
       they were left.

    ⚠️ Unreadable is **not** "too expensive". Whatever cannot be opened is
       somebody else's complaint to make, with a message about the format
       rather than about the size — `normalise_*` raises it a moment later.

    🔴 **A file the storage will not hand over is not a reason to refuse a
       save**, and getting that wrong is how a transient object-store hiccup
       turns "change the front page picture" into a 500 — the exact failure
       `HomePage.refresh_renditions` was once fixed for. Which is why the
       rewind below cannot be a bare `finally: upload.seek(0)`: on a `FieldFile`
       whose object is missing, the seek itself raises, and it raises *from the
       finally*, replacing the `""` this function had already decided to
       return. `too_many_pixels` carried that same shape from 2026-09-01 and
       never fired, because it was only ever handed a real upload from a form;
       calling this from `save()` is what walked into it.
    """
    from PIL import Image

    if not _rewound(upload):
        return ""
    try:
        with Image.open(upload) as source:
            native = upright_size(source)
            draft_to(source, stored_size(native, max_edge))
            if not over_decode_budget(source, native):
                return ""
            return decode_complaint(source, native)
    except Exception:
        return ""
    finally:
        _rewound(upload)


def affordable_width(source, native):
    """How wide this picture could have been and still fit the budget.

    The other half of the complaint: "it is too big" tells somebody they have a
    problem, and this is what tells them when they have finished fixing it.
    Answers in pixels of width, keeping the picture's aspect ratio, and assumes
    the same format — which is why the sentence built below offers changing the
    format as the *other* way out.

    ⚠️ Deliberately approximate and rounded to something a person would type.
       The budget it is derived from is itself an estimate; "2713 px" would
       claim a precision that is not there.
    """
    from django.conf import settings

    width, height = native
    per_native, per_drafted = bytes_per_pixel(source.format,
                                              is_progressive(source))
    # 🔴 **Priced as though `draft_to` will not help at all**, which is what
    #    makes the answer safe to act on. Its scale weakens as a picture
    #    shrinks — libjpeg's 1/8 becomes 1/4, then 1/2, then nothing — so a
    #    smaller picture is *not* proportionally cheaper, and extrapolating
    #    from this picture's own draft scale advises a width that is still
    #    refused. That was the first version: an 8000×6000 progressive JPEG was
    #    told "about 6200 px wide", and 6200 px came back "about 5600 px". A
    #    number whose whole job is to say "you have finished fixing it" must
    #    never need saying twice.
    #
    # ⚠️ The cost is that a baseline JPEG is advised a smaller size than it
    #    strictly needs. That is the right way to be wrong here, and it is the
    #    same trade `affordable_megapixels` states for the help text.
    per_pixel = per_native + per_drafted
    if per_pixel <= 0:                           # pragma: no cover - defensive
        return width
    pixels = settings.IMAGE_DECODE_BUDGET_BYTES / per_pixel
    ratio = width / max(1, height)
    return max(200, int((pixels * ratio) ** 0.5 // 100 * 100))


def affordable_megapixels(fmt):
    """How large a picture of `fmt` may be when `draft_to` cannot help it.

    For the help text beside a file picker, so somebody can check before they
    upload rather than after. Answers in megapixels, rounded down.

    ⚠️ **The undrafted case deliberately.** It is the honest number for PNG and
       WebP — neither can be drafted at all — and for JPEG it is a floor rather
       than the limit, because the pipelines all draft one down before decoding
       it. A help text that quoted the JPEG figure would send people away with
       photographs the site would have taken.
    """
    from django.conf import settings

    per_native, per_drafted = bytes_per_pixel(fmt, progressive=False)
    # ⚠️ Never below 1. Rounding down is right — it is a limit somebody is
    #    held to — but "over about 0 megapixels" is not a sentence, and a help
    #    text that says it teaches people the numbers on this page are noise.
    return max(1, int(settings.IMAGE_DECODE_BUDGET_BYTES
                      / max(1, per_native + per_drafted) / 1_000_000))


def decode_complaint(source, native):
    """Why this picture was refused, and what would make it fit.

    Returns the half of the sentence that is arithmetic. Every caller puts its
    own noun in front of it — "That photo", "That picture", "That image" — so
    the person reads about the thing they were actually uploading.

    ⚠️ **Here rather than copied into five call sites**, and it is not a
       violation of this module's "no policy" rule: three numbers have to be
       worked out (what it costs, what the budget is, how small it would have
       to be) and none of them is a decision. What *is* policy — which noun,
       whether one bad file stops a batch — stays with the caller. The rule
       this module's docstring actually states is that the arithmetic must not
       be written twice, because it was, and the copies drifted inside a day.

    🔴 It names the **format**, not just the size, and that is the whole point
       of the sentence. "Too many pixels" sends somebody off to resize a file
       that would have been fine saved another way: at 20 megapixels the same
       photograph is 78 MB as a JPEG and 310 MB as a WebP.
    """
    from django.conf import settings

    drafted_width, drafted_height = source.size
    cost = decode_cost(source.format, is_progressive(source),
                       native[0] * native[1], drafted_width * drafted_height)
    megabytes = cost / (1024 * 1024)
    budget = settings.IMAGE_DECODE_BUDGET_BYTES // (1024 * 1024)
    fmt = source.format or "an unrecognised format"
    smaller = f"scaling it to about {affordable_width(source, native)} px wide"
    # ⚠️ "Save it as JPEG" is only advice worth giving to somebody who has not
    #    already done so — and telling a person to convert a JPEG to a JPEG is
    #    how a helpful message teaches people to stop reading them.
    #
    # ⚠️ The comma belongs to the two-clause form only. Kept in both, the JPEG
    #    branch reads "Scaling it to about 2900 px wide, will fix it." — which
    #    is the kind of small wrongness that makes a careful message look
    #    machine-made.
    if FORMAT_ALIASES.get(fmt, fmt) == "JPEG":
        remedy = f"{smaller[0].upper()}{smaller[1:]}"
    else:
        remedy = f"Saving it as JPEG, or {smaller},"
    return (f"is {native[0]} × {native[1]} and saved as {fmt}, which needs "
            f"about {megabytes:.0f} MB of memory to open — this server can "
            f"spare {budget} MB. {remedy} will fix it.")


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
