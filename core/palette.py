"""Build the brand colour ramp from whatever picture is on the front page.

⚠️ The whole design of this module is one idea: **take the hue and the
   saturation from the photograph, and pin the relative luminance of every step
   to the value the hand-tuned teal already had.**

   Contrast is a function of relative luminance and nothing else. Pinning it
   means every ratio measured for the original palette — link text 7.17:1,
   white on the primary button 5.21:1, the dark-mode link 10.10:1 — holds for
   **any** photograph, exactly, not approximately. A pale photo cannot produce
   unreadable link text, because the link step is not allowed to be pale.

   The obvious alternative — take the colour and scale its lightness — fails
   silently: a yellow-ish hue at the same HSL lightness is far brighter than a
   blue one, so the ramp drifts light and some text stops being readable
   without anything raising.

⚠️ Semantic colours (success / warning / danger / info) are deliberately **not**
   derived from anything. Red has to stay red. A green photograph must not make
   "danger" green.
"""

import colorsys

#: Relative luminance of each step of the original hand-tuned palette, measured
#: from the hex values in assets/app.css. These are the numbers the contrast
#: table in design-system.md was built on, so reproducing them reproduces every
#: ratio in it.
TARGET_LUMINANCE = {
    50: 0.93067, 100: 0.83421, 200: 0.69195, 300: 0.52799, 400: 0.35849,
    500: 0.23779, 600: 0.15152, 700: 0.09653, 800: 0.06638, 900: 0.04666,
}

#: Saturation of each step of the original palette. Used as the ceiling rather
#: than as the value: the photo decides, but it cannot exceed what the tuned
#: ramp used, or the darker steps turn into neon.
REFERENCE_SATURATION = {
    50: 0.600, 100: 0.633, 200: 0.629, 300: 0.607, 400: 0.563,
    500: 0.653, 600: 0.654, 700: 0.589, 800: 0.508, 900: 0.464,
}

#: ⚠️ A photograph of fog has no meaningful hue, and a saturation near zero
#:    gives a grey "brand" colour that reads as broken rather than as tasteful.
#:    Below this the extracted colour is rejected and the built-in teal stands.
MIN_USABLE_SATURATION = 0.12


def _linear(channel):
    channel /= 255
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb):
    """WCAG relative luminance of an (r, g, b) triple in 0–255."""
    r, g, b = (_linear(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _rgb(hue, saturation, lightness):
    r, g, b = colorsys.hls_to_rgb(hue, lightness, saturation)
    return round(r * 255), round(g * 255), round(b * 255)


def _lightness_for(hue, saturation, target):
    """The HSL lightness that lands this hue on `target` relative luminance.

    Binary search rather than algebra: luminance is monotone in lightness for a
    fixed hue and saturation, which is all a bisection needs, and the closed
    form for sRGB's piecewise transfer function is not worth writing down.
    """
    low, high = 0.0, 1.0
    for _ in range(40):
        middle = (low + high) / 2
        if relative_luminance(_rgb(hue, saturation, middle)) < target:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def ramp_from(rgb):
    """Ten hex strings keyed by step, or None if this colour is unusable.

    None means "keep the built-in palette" — see MIN_USABLE_SATURATION.
    """
    r, g, b = (c / 255 for c in rgb)
    hue, _lightness, saturation = colorsys.rgb_to_hls(r, g, b)
    if saturation < MIN_USABLE_SATURATION:
        return None

    ramp = {}
    for step, target in TARGET_LUMINANCE.items():
        # The photo's saturation, capped by what the tuned ramp used at this
        # step. Capping rather than replacing keeps a muted photo muted.
        step_saturation = min(saturation, REFERENCE_SATURATION[step])
        lightness = _lightness_for(hue, step_saturation, target)
        ramp[step] = "#%02x%02x%02x" % _rgb(hue, step_saturation, lightness)
    return ramp


def dominant_colour(image_file):
    """The most significant colour in a picture, as (r, g, b).

    ⚠️ Quantised rather than averaged. The mean of a photograph is always a
       muddy brown-grey — average a sunset and you get mud — because opposing
       hues cancel. Quantising asks "which colours actually occupy this image"
       and then takes the largest group.

    ⚠️ Near-white, near-black and near-grey groups are skipped. Sky and shadow
       dominate most photographs by area while saying nothing about their
       colour; a picture of a red barn under a big sky is a red picture.
    """
    from PIL import Image

    with Image.open(image_file) as source:
        image = source.convert("RGB")
        image.thumbnail((160, 160))
        quantised = image.quantize(colors=16, method=Image.MEDIANCUT)
        palette = quantised.getpalette()
        counts = sorted(quantised.getcolors(), reverse=True)

    best = None
    for count, index in counts:
        rgb = tuple(palette[index * 3:index * 3 + 3])
        _hue, lightness, saturation = colorsys.rgb_to_hls(*(c / 255 for c in rgb))
        if saturation < MIN_USABLE_SATURATION or lightness < 0.12 or lightness > 0.92:
            continue
        best = rgb
        break
    return best
