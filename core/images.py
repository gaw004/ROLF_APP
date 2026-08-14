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

#: EXIF tag 274, Orientation. Values 5–8 are the quarter turns — for those the
#: picture's width and height swap once it has been turned upright.
_EXIF_ORIENTATION = 274
_ORIENTATIONS_THAT_TURN = frozenset({5, 6, 7, 8})


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
    try:
        orientation = source.getexif().get(_EXIF_ORIENTATION)
    except Exception:
        orientation = None
    if orientation in _ORIENTATIONS_THAT_TURN:
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
    if target is not None:
        source.draft("RGB", target)
