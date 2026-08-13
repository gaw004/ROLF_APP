"""Memories: the strip, the uploads, and the feather that leads to them.

⚠️ Every class that stores a file overrides MEDIA_ROOT into a temp directory
   **and** points all three storage aliases at the local filesystem. The second
   half matters as much as the first: production has these on Cloudflare R2, and
   a test that reached it would need credentials, would be slow, and would leave
   objects in a real bucket. base.py already keeps the aliases local — the
   override here says so out loud, so that a future change to the defaults
   cannot quietly send the test suite onto the network.
"""

import datetime
import io
import itertools
import tempfile

from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image as PILImage

from accounts.services import register_account
from core.models import HomePage
from core.timeutils import local_today
from events.tests import PageTestCase
from gallery.forms import MAX_PHOTOS_PER_UPLOAD
from gallery.models import GalleryPhoto
from gallery.services import (
    MIN_STRIP_SPAN,
    STRIP_COUNT,
    STRIP_SIZE,
    digest_of,
    normalise_gallery_image,
    repeats_for,
    strips_for,
)
from org.models import Ministry, MinistryRole
from org.permissions import (
    can_delete_gallery_photo,
    can_upload_gallery_photo,
    foundation_admin_group,
)

LOCAL_STORAGE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "memories": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "public": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


#: Bumped for every photo built by `a_photo`, so that two of them are never the
#: same picture.
#:
#: ⚠️ This exists because duplicates are now refused. Without it, a test that
#:    makes twelve photos makes **one** photo twelve times: the digests match,
#:    eleven are skipped, and the test fails somewhere far away with a message
#:    about row counts. Every photo differing by a pixel keeps the fixtures
#:    saying what they look like they say.
_serial = itertools.count()


def a_photo(size=(1200, 800), fmt="JPEG", exif=None, colour=(90, 120, 160),
            unique=True):
    """One real image file, as an upload. Same shape as the events helper.

    `unique=False` when a test wants the *same* photo twice — which is what the
    duplicate rules are about, so it has to be expressible.
    """
    if unique:
        # ⚠️ The **whole fill** is shifted, not a pixel or a corner patch. Both
        #    of those were tried and neither survives: the image is JPEG-encoded
        #    and then resized with LANCZOS on the way in, and a small mark in a
        #    flat field is smoothed straight out — so the digests matched anyway
        #    and the fixtures silently collapsed to one photo. Colour is the
        #    only difference that survives every step, and no assertion here
        #    looks at it.
        n = next(_serial)
        colour = (20 + (90 + n * 7) % 200,
                  20 + (120 + n * 13) % 200,
                  20 + (160 + n * 29) % 200)
    image = PILImage.new("RGB", size, colour)
    buffer = io.BytesIO()
    if exif is not None:
        image.save(buffer, fmt, exif=exif)
    else:
        image.save(buffer, fmt)
    return SimpleUploadedFile(
        f"photo{next(_serial)}.{fmt.lower()}", buffer.getvalue(),
        content_type=f"image/{fmt.lower()}")


def store(ministry=None, size=(700, 500), taken_year=2026, **extra):
    """One saved GalleryPhoto, files and digest included.

    The direct route used by the layout tests, which are about `strips_for` and
    have no interest in the upload page.
    """
    image, thumb, year = normalise_gallery_image(a_photo(size=size))
    return GalleryPhoto.objects.create(
        ministry=ministry, image=image, thumb=thumb,
        image_digest=digest_of(image), taken_year=extra.pop("year", taken_year),
        **extra)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(), STORAGES=LOCAL_STORAGE)
class GalleryImageProcessingTests(TestCase):
    """What is stored, and what is deliberately thrown away.

    The four rules are the event-image ones — upright first, EXIF then dropped,
    resized down only, Pillow opening the file *is* the validation — plus the
    two things that are specific here: two derivatives, and the year read off
    the metadata on the way past.
    """

    def test_two_derivatives_come_out_at_their_own_sizes(self):
        image, thumb, _ = normalise_gallery_image(a_photo(size=(4000, 3000)))
        with PILImage.open(image) as large, PILImage.open(thumb) as small:
            self.assertEqual(max(large.size), 1600)
            self.assertEqual(max(small.size), 700)
            self.assertEqual(large.format, "WEBP")
            self.assertEqual(small.format, "WEBP")

    def test_the_large_one_is_not_made_from_the_small_one(self):
        """⚠️ The bug this catches is silent and total.

        `Image.thumbnail()` resizes **in place**, and since 2026-08-13 the
        pipeline relies on that rather than defending against it: the large
        derivative is made first and the thumbnail is taken from the result.
        Swap the two calls and every "large" image comes out at 700px — a valid
        file, the right format, simply the wrong picture. Nothing raises; the
        lightbox is just soft forever.
        """
        image, thumb, _ = normalise_gallery_image(a_photo(size=(3000, 2000)))
        with PILImage.open(image) as large, PILImage.open(thumb) as small:
            self.assertGreater(max(large.size), max(small.size))

    def test_the_full_size_picture_is_never_held_twice(self):
        """⚠️ 2026-08-13, after the front page's version of this restarted
        production and 502'd everybody looking at the site.

        A 6000×4000 photograph is 72 MB once decoded, and this used to make
        **four** of them: one from exif_transpose, one from a convert() into
        the mode it was already in, and one per derivative. Measured at 238 MB
        of peak memory for a single upload, against a 512 MB instance already
        holding two workers. Now it makes one, and the peak is 134 MB.

        ⚠️ The assertion is on **buffers at the original size**, not on memory:
           a memory number would drift with Pillow and the machine, while this
           is the actual rule. One is Pillow's own — `exif_transpose` copies
           when there is no orientation tag to act on — and it stays until
           there is a reason to fight it.

        ⚠️ Nothing else notices a regression here. Put the `convert()` back and
           every photograph is byte-for-byte identical, every other test stays
           green, and the only difference is a number that shows up as a
           restart on a busy afternoon.
        """
        from unittest import mock

        size = (3000, 2000)
        seen = []

        def spy_on(name):
            real = getattr(PILImage.Image, name)

            def spy(self, *args, **kwargs):
                seen.append((name, self.size))
                return real(self, *args, **kwargs)
            return mock.patch.object(PILImage.Image, name, spy)

        with spy_on("copy"), spy_on("convert"):
            normalise_gallery_image(a_photo(size=size))

        at_full_size = [name for name, seen_size in seen if seen_size == size]
        self.assertLessEqual(
            len(at_full_size), 1,
            f"the original was duplicated {len(at_full_size)} times before "
            f"being resized ({at_full_size}); one is exif_transpose's and the "
            f"rest are 72 MB each on a phone photograph")

    def test_a_small_photo_is_not_blown_up(self):
        image, thumb, _ = normalise_gallery_image(a_photo(size=(300, 200)))
        with PILImage.open(image) as large, PILImage.open(thumb) as small:
            self.assertEqual(large.size, (300, 200))
            self.assertEqual(small.size, (300, 200))

    def test_a_sideways_phone_photo_comes_out_upright(self):
        exif = PILImage.Exif()
        exif[274] = 6  # Orientation: rotate 90°
        image, thumb, _ = normalise_gallery_image(a_photo(size=(400, 200), exif=exif))
        with PILImage.open(image) as large, PILImage.open(thumb) as small:
            self.assertEqual(large.size, (200, 400))
            self.assertEqual(small.size, (200, 400))

    def test_no_exif_survives_into_either_derivative(self):
        """⚠️ Privacy, not file size — and it has to hold for **both** files.

        GPS coordinates live in this block. Checking only the large one would
        leave the thumbnail publishing a volunteer's home address, and the
        thumbnail is the one that appears sixty times on a page.
        """
        exif = PILImage.Exif()
        exif[271] = "TestPhone"
        exif[272] = "Model X"
        image, thumb, _ = normalise_gallery_image(a_photo(exif=exif))
        for name, stored in (("image", image), ("thumb", thumb)):
            with self.subTest(derivative=name), PILImage.open(stored) as check:
                self.assertFalse(dict(check.getexif()))

    def test_the_year_is_read_off_the_exif_before_it_is_stripped(self):
        """⚠️ Order of operations, and there is no second chance.

        The read has to happen before the re-encode, because the re-encode is
        what destroys the metadata. Get it the wrong way round and every photo
        out of somebody's shoebox is labelled with this year.
        """
        exif = PILImage.Exif()
        exif[36867] = "2011:06:04 14:02:11"  # DateTimeOriginal
        _, _, year = normalise_gallery_image(a_photo(exif=exif))
        self.assertEqual(year, 2011)

    def test_a_photo_with_no_exif_falls_back_to_this_year(self):
        _, _, year = normalise_gallery_image(a_photo())
        self.assertEqual(year, local_today().year)

    def test_a_camera_with_a_dead_clock_does_not_get_believed(self):
        """A flat battery reports 1970, and "1970" under a photo of a
        children's camp is worse than no year at all — so it falls back rather
        than passing it through. EXIF itself dates from the mid-90s, which is
        where the floor comes from."""
        exif = PILImage.Exif()
        exif[36867] = "1970:01:01 00:00:00"
        _, _, year = normalise_gallery_image(a_photo(exif=exif))
        self.assertEqual(year, local_today().year)

    def test_a_clock_set_to_the_future_does_not_get_believed_either(self):
        # The other end of the same check. A year that has not happened yet is
        # not a year worth printing under a photograph.
        exif = PILImage.Exif()
        exif[36867] = "2099:03:02 08:00:00"
        _, _, year = normalise_gallery_image(a_photo(exif=exif))
        self.assertEqual(year, local_today().year)

    def test_a_malformed_exif_timestamp_costs_a_year_not_the_upload(self):
        exif = PILImage.Exif()
        exif[36867] = "not a date at all"
        _, _, year = normalise_gallery_image(a_photo(exif=exif))
        self.assertEqual(year, local_today().year)

    def test_an_svg_is_refused(self):
        """⚠️ SVG can carry script, and it would be served from our own origin.

        Opening it with Pillow *is* the check — nothing extra keeps it out.
        """
        from django.core.exceptions import ValidationError

        svg = SimpleUploadedFile(
            "logo.svg", b'<svg xmlns="http://www.w3.org/2000/svg"></svg>',
            content_type="image/svg+xml")
        with self.assertRaises(ValidationError):
            normalise_gallery_image(svg)


class GalleryPermissionTests(TestCase):
    """Who may put a photo up, and who may take one down. D20's two tiers.

    ⚠️ These ask org.permissions directly rather than through the pages. The
       pages are tested separately; this class is about the rule itself, which
       is the thing that must not drift.
    """

    def setUp(self):
        self.pantry = Ministry.objects.create(code="food_pantry", name="Food Pantry")
        self.tax = Ministry.objects.create(code="tax_help", name="Tax Help")

        self.zhang = self.account("zhang", "张")
        MinistryRole.objects.create(contact=self.zhang.contact, ministry=self.pantry)
        self.lisi = self.account("lisi", "李")

        self.director = self.account("director", "王")
        self.director.groups.add(foundation_admin_group())

    def account(self, handle, last_name):
        return register_account(
            password="a-good-long-password", legal_last_name=last_name,
            email=f"{handle}@example.com", birth_date=datetime.date(1980, 1, 1))

    def photo(self, ministry):
        """A row, not a file — these tests never touch storage."""
        return GalleryPhoto(ministry=ministry, taken_year=2026,
                            thumb_width=700, thumb_height=500)

    def test_a_ministry_admin_may_upload_for_their_own_ministry(self):
        self.assertTrue(can_upload_gallery_photo(self.zhang, self.pantry))

    def test_a_ministry_admin_may_not_upload_for_another_ministry(self):
        self.assertFalse(can_upload_gallery_photo(self.zhang, self.tax))

    def test_a_ministry_admin_may_not_publish_a_foundation_wide_photo(self):
        """⚠️ D20's test applied literally: "a photo that speaks for the whole
        foundation" contains no "of some ministry", so it is the global tier's.
        A ministry admin publishes their ministry's memories, not the
        foundation's."""
        self.assertFalse(can_upload_gallery_photo(self.zhang, None))

    def test_the_foundation_tier_may_publish_a_foundation_wide_photo(self):
        self.assertTrue(can_upload_gallery_photo(self.director, None))

    def test_the_foundation_tier_may_upload_for_any_ministry(self):
        self.assertTrue(can_upload_gallery_photo(self.director, self.tax))

    def test_a_plain_volunteer_may_upload_nothing(self):
        self.assertFalse(can_upload_gallery_photo(self.lisi, self.pantry))
        self.assertFalse(can_upload_gallery_photo(self.lisi, None))

    def test_a_ministry_admin_may_delete_their_own_ministrys_photo(self):
        self.assertTrue(can_delete_gallery_photo(self.zhang, self.photo(self.pantry)))

    def test_a_ministry_admin_may_not_delete_another_ministrys_photo(self):
        self.assertFalse(can_delete_gallery_photo(self.zhang, self.photo(self.tax)))

    def test_a_ministry_admin_may_not_delete_a_foundation_wide_photo(self):
        self.assertFalse(can_delete_gallery_photo(self.zhang, self.photo(None)))

    def test_the_foundation_tier_may_delete_anybodys_photo(self):
        """⚠️ Deliberately wider than uploading. Somebody has to be able to take
        down a photo of a child whose family has asked for it to go, at an hour
        when that ministry's admin is not reachable."""
        for ministry in (self.pantry, self.tax, None):
            with self.subTest(ministry=ministry):
                self.assertTrue(
                    can_delete_gallery_photo(self.director, self.photo(ministry)))

    def test_a_superuser_gets_no_exemption(self):
        """The module docstring's standing rule, checked here too: a superuser
        holds no ministry scope by design, and the admin is their route."""
        from django.contrib.auth import get_user_model

        root = get_user_model().objects.create_superuser(
            email="root@example.com", password="a-good-long-password")
        self.assertFalse(can_upload_gallery_photo(root, self.pantry))
        self.assertFalse(can_delete_gallery_photo(root, self.photo(self.pantry)))


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(), STORAGES=LOCAL_STORAGE)
class StripDrawTests(TestCase):
    """The daily draw: which photos are up, how they are cut into strips, and
    how big each one is drawn.

    ⚠️ Much smaller than it was. Until 2026-08-07 this class also covered a
       measured height spread and a three-way shape mix with a crop ceiling.
       Those tests were **deleted along with the code** — a test for a rule
       nothing implements is worse than no test, because it passes.
    """

    def setUp(self):
        self.ministry = Ministry.objects.create(code="youth", name="Youth")

    def make_photos(self, count, size=(700, 500)):
        return [store(self.ministry, size) for _ in range(count)]

    def flat(self, day=None):
        """Every photo on the page, in the order the lightbox walks them —
        top strip first, left to right within each."""
        return [item for strip in strips_for(day) for item in strip]

    def test_an_empty_wall_is_no_strips_rather_than_empty_ones(self):
        """⚠️ An empty list, not a list of empty strips. An empty strip is a
        sized, drifting band with nothing in it; the page has a sentence to say
        instead, and it needs to know to say it."""
        self.assertEqual(strips_for(), [])

    def test_the_page_is_capped_even_when_there_are_more_photos(self):
        """⚠️ A cap on the page, not on what the foundation keeps. Without it
        the page keeps working right up until the day it does not."""
        self.make_photos(STRIP_SIZE + 8)
        self.assertEqual(len(self.flat()), STRIP_SIZE)

    def test_a_photo_appears_only_once(self):
        """⚠️ Across the whole page, not within one strip. The draw produces
        one list and then cuts it into pieces, so this is what says the pieces
        do not overlap — the same photograph on two strips reads as "wait, have
        I seen that already"."""
        self.make_photos(20)
        drawn = [item.photo.pk for item in self.flat()]
        self.assertEqual(len(drawn), len(set(drawn)))

    def test_the_wall_holds_still_within_one_day(self):
        """⚠️ Re-drawing per request means the page rearranges itself under
        somebody who reloads, or who comes back from the lightbox — which reads
        as a bug, not as variety."""
        self.make_photos(20)
        day = datetime.date(2026, 8, 6)
        self.assertEqual([i.photo.pk for i in self.flat(day)],
                         [i.photo.pk for i in self.flat(day)])

    def test_a_different_day_draws_a_different_wall(self):
        """The other half of the same rule — without this the "daily" in daily
        draw is a lie and an older photo never gets another turn."""
        self.make_photos(STRIP_SIZE + 30)
        self.assertNotEqual(
            [i.photo.pk for i in self.flat(datetime.date(2026, 8, 6))],
            [i.photo.pk for i in self.flat(datetime.date(2026, 8, 7))])

    def test_the_sequence_is_numbered_without_gaps_or_repeats(self):
        """The lightbox walks this, and it walks the **whole page** — so the
        numbering runs across the strips rather than restarting on each. A gap
        is a blank photo; a repeat is two arrow presses that land in the same
        place; a per-strip restart is an arrow key that jumps between bands."""
        self.make_photos(15)
        self.assertEqual([i.index for i in self.flat()],
                         list(range(len(self.flat()))))

    def test_the_photos_are_split_across_three_strips(self):
        """2026-08-08: 「同样的原理，再来两条照片带子」."""
        self.make_photos(20)
        self.assertEqual(len(strips_for()), STRIP_COUNT)

    def test_the_last_strip_is_never_the_only_long_one(self):
        """⚠️ The ceiling divide. 20 photos over three strips is 7/7/6, not
        6/6/8 — the naive split leaves the remainder on the last band, which is
        the one nearest the bottom of the screen and the most obviously wrong
        length."""
        self.make_photos(20)
        lengths = [len(strip) for strip in strips_for()]
        self.assertEqual(sorted(lengths, reverse=True), [7, 7, 6])
        self.assertLessEqual(max(lengths) - min(lengths), 1)

    def test_a_handful_of_photos_makes_fewer_strips_rather_than_empty_ones(self):
        """⚠️ An empty band is a stripe of blank page that looks like something
        failed to load — and "only a couple of photos" is the state a
        foundation is in on its first day, so it is the case least able to
        afford looking broken."""
        for count in (1, 2, 3):
            with self.subTest(photos=count):
                GalleryPhoto.objects.all().delete()
                self.make_photos(count)
                strips = strips_for()
                self.assertEqual(len(strips), count)
                for strip in strips:
                    self.assertTrue(strip)

    def test_every_photo_is_drawn_in_its_own_shape(self):
        """No crop, ever. The whole re-composition pass — a shape mix, target
        ratios, a maximum acceptable loss — was deleted rather than switched
        off, so this asserts the property that replaced it: what is drawn is
        the shape of what was photographed."""
        for size in [(1600, 900), (900, 1600), (600, 600), (1200, 900)]:
            store(self.ministry, size)
        for item in self.flat():
            with self.subTest(photo=item.photo.pk):
                self.assertAlmostEqual(item.aspect, item.photo.aspect, places=6)

    def test_no_photo_is_ever_drawn_larger_than_it_is_stored(self):
        """⚠️ **This is the blur report, turned into a test** (2026-08-08):
        「有些照片为了做成等高的照片都糊了」.

        The equal-height version forced every photo to the band, which means
        *enlarging* anything stored smaller than it — a 400px scan drawn at
        340 CSS px on a 2x display is being asked for 680 real pixels and has
        400. Every photo is now drawn at `band × relative_height`, and this
        asserts the one property that makes softness impossible: that factor is
        never above 1.
        """
        for size in [(1600, 900), (900, 1600), (600, 600), (240, 180), (80, 400)]:
            store(self.ministry, size)
        for item in self.flat():
            with self.subTest(photo=item.photo.pk):
                self.assertGreater(item.relative_height, 0)
                self.assertLessEqual(item.relative_height, 1.0)

    def test_one_uniform_scale_is_applied_to_every_photo(self):
        """⚠️ The other half: sizes differ **because the photographs differ**,
        not because anything was chosen per photo.

        Drawn height is `stored height ÷ GALLERY_THUMB_MAX_EDGE`, so the ratio
        between any two photos' drawn heights equals the ratio between their
        stored heights — one scale factor over the whole page, exactly like the
        `SCALE 0.10` on the first reference design.
        """
        tall = store(self.ministry, (900, 1600))     # thumb 394 x 700
        wide = store(self.ministry, (1600, 900))     # thumb 700 x 394
        drawn = {item.photo.pk: item.relative_height for item in self.flat()}
        self.assertAlmostEqual(
            drawn[tall.pk] / drawn[wide.pk],
            tall.thumb_height / wide.thumb_height,
            places=6)

    def test_a_photo_smaller_than_the_band_comes_out_smaller_on_the_page(self):
        """The variety now comes from the photographs themselves: a small scan
        is drawn small rather than being blown up to match its neighbours."""
        big = store(self.ministry, (1600, 900))
        small = store(self.ministry, (240, 135))
        drawn = {item.photo.pk: item.relative_height for item in self.flat()}
        self.assertLess(drawn[small.pk], drawn[big.pk])

    def test_the_width_a_photo_takes_up_follows_its_drawn_height(self):
        """⚠️ `width_ratio` is **not** the same thing as `aspect`, and it was
        while every photo filled the band. The marquee's duration is the sum of
        these, so conflating the two would make the strip drift at the wrong
        speed by however much the photographs differ in height."""
        for size in [(1600, 900), (900, 1600), (240, 135)]:
            store(self.ministry, size)
        for item in self.flat():
            with self.subTest(photo=item.photo.pk):
                self.assertAlmostEqual(
                    item.width_ratio, item.relative_height * item.aspect,
                    places=9)

    def test_a_short_strip_is_repeated_enough_to_fill_the_window(self):
        """⚠️ The small-collection case, which is the state the foundation
        starts in — and therefore the moment this page must not look broken.

        The track loops by shifting itself left by one copy of its contents.
        With a few photos that copy is narrower than the window, so a blank
        stretch would walk across the page on the day somebody uploads their
        first few photographs.
        """
        self.make_photos(4)
        for strip in strips_for():
            span = sum(item.width_ratio for item in strip)
            with self.subTest(span=span):
                self.assertGreaterEqual(span * repeats_for(span), MIN_STRIP_SPAN)

    def test_a_strip_is_not_repeated_more_than_it_needs_to_be(self):
        """The other end: copies past the seam are wasted markup — a full wall
        is sixty extra <img> tags per unnecessary copy.

        The property is "one fewer copy would break something", where something
        is either the two-copy floor (which *is* the seam) or MIN_STRIP_SPAN.
        Asserted that way rather than as a fixed number, because the count
        follows from the strip's width and so moves whenever the photos do.
        """
        for count in (4, 40):
            with self.subTest(photos=count):
                GalleryPhoto.objects.all().delete()
                self.make_photos(count)
                for strip in strips_for():
                    span = sum(item.width_ratio for item in strip)
                    copies = repeats_for(span)
                    self.assertGreaterEqual(copies, 2)
                    self.assertTrue(
                        copies == 2 or (copies - 1) * span < MIN_STRIP_SPAN,
                        f"{copies} copies of {span:.1f} units is one too many")

    def test_the_seam_never_needs_fewer_than_two_copies(self):
        """⚠️ Two is the floor because the second copy **is** the seam. With
        one, the strip would jump back to its start in a single frame at the
        end of every loop."""
        self.assertEqual(repeats_for(0), 2)
        self.assertEqual(repeats_for(MIN_STRIP_SPAN * 4), 2)

    def test_the_stored_files_are_never_touched_by_the_draw(self):
        """The draw is arithmetic over columns; it opens no file and writes
        nothing. Cropping on the way in would be irreversible, and these files
        are in no pg_dump."""
        photos = self.make_photos(12)
        before = [(p.thumb_width, p.thumb_height) for p in photos]
        strips_for()
        after = [(p.thumb_width, p.thumb_height)
                 for p in GalleryPhoto.objects.order_by("pk")]
        self.assertEqual(before, after)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(), STORAGES=LOCAL_STORAGE)
class WallPageTests(PageTestCase):
    """The page itself: who gets in, and what the markup has to carry."""

    def setUp(self):
        super().setUp()
        self.director = self.account("director", "王",
                                     birth_date=datetime.date(1970, 1, 1))
        self.director.groups.add(foundation_admin_group())

    def add_photo(self, ministry=None, size=(700, 500)):
        return store(ministry, size, taken_year=2019)

    def test_an_anonymous_visitor_is_sent_to_log_in(self):
        """⚠️ Half the gate. The other half is that the files are in a private
        bucket behind signed URLs — without that this decorator would be
        protecting the page while every photo on it stayed a permanent public
        link."""
        response = self.client.get(reverse("gallery:wall"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("accounts:login"), response.url)

    def test_a_plain_volunteer_may_look_at_the_wall(self):
        self.login(self.lisi)
        self.assertEqual(self.client.get(reverse("gallery:wall")).status_code, 200)

    def test_the_title_is_on_the_page(self):
        self.login(self.lisi)
        self.assertContains(self.client.get(reverse("gallery:wall")), "MEMORIES")

    def test_an_empty_wall_says_so_rather_than_looking_broken(self):
        self.login(self.lisi)
        self.assertContains(self.client.get(reverse("gallery:wall")), "Nothing here yet")

    def test_the_strip_carries_its_own_span_so_the_speed_stays_constant(self):
        """⚠️ Without this the animation needs a hardcoded duration, and a
        fuller strip would drift faster — the more photos the foundation adds,
        the more hurried the page gets."""
        for _ in range(6):
            self.add_photo(self.pantry)
        self.login(self.lisi)
        page = self.client.get(reverse("gallery:wall")).content.decode()
        self.assertIn("--wall-duration", page)
        self.assertIn("var(--wall-speed)", page)

    def test_a_short_strip_is_repeated_enough_to_fill_the_window(self):
        """The small-collection case, reached through the page rather than
        through the service — this is what actually lands in the markup."""
        for _ in range(4):
            self.add_photo(self.pantry)
        self.login(self.lisi)
        for strip in self.client.get(reverse("gallery:wall")).context["strips"]:
            with self.subTest(strip=strip["position"]):
                self.assertGreaterEqual(
                    strip["span"] * len(strip["repeats"]), MIN_STRIP_SPAN)

    def test_the_page_carries_three_strips_once_there_are_photos_for_them(self):
        """2026-08-08: 「同样的原理，再来两条照片带子」."""
        for _ in range(12):
            self.add_photo(self.pantry)
        self.login(self.lisi)
        response = self.client.get(reverse("gallery:wall"))
        self.assertEqual(len(response.context["strips"]), STRIP_COUNT)
        self.assertEqual(
            response.context["strips"][0]["photos"][0].index, 0)

    def test_a_handful_of_photos_makes_fewer_strips_rather_than_empty_ones(self):
        """⚠️ An empty strip is a blank horizontal band that reads as something
        having failed to load — and "only a couple of photos" is the state a
        foundation is in on its first day, so it is the case least able to
        afford looking broken."""
        for _ in range(2):
            self.add_photo(self.pantry)
        self.login(self.lisi)
        strips = self.client.get(reverse("gallery:wall")).context["strips"]
        self.assertTrue(strips)
        for strip in strips:
            with self.subTest(strip=strip["position"]):
                self.assertTrue(strip["photos"])

    def test_the_strips_start_at_different_points_in_their_loop(self):
        """⚠️ All three drift left at the same speed, so without a stagger every
        one of them opens with a photo flush to the left edge and the three read
        as a single block sliding. The stylesheet turns `--strip-index` into a
        **negative** animation-delay, which starts an animation part-way
        through rather than delaying it."""
        for _ in range(12):
            self.add_photo(self.pantry)
        self.login(self.lisi)
        page = self.client.get(reverse("gallery:wall")).content.decode()
        for position in range(STRIP_COUNT):
            with self.subTest(strip=position):
                self.assertIn(f"--strip-index: {position}", page)

        from pathlib import Path
        css = Path("assets/app.css").read_text()
        block = css[css.index("  .wall-track {\n    width: max-content;"):]
        self.assertIn("animation-delay: calc(", block[:600])
        self.assertIn("-0.37", block[:600])

    def test_the_number_of_copies_is_published_to_the_stylesheet(self):
        """⚠️ The keyframes shift by `-100% / var(--wall-sets)`. Hardcoding
        `-50%` there instead would skip half the strip's contents every loop
        whenever the count is not two — and only on the small walls, which is
        where nobody is looking."""
        self.add_photo(self.pantry)
        self.login(self.lisi)
        page = self.client.get(reverse("gallery:wall")).content.decode()
        self.assertIn("--wall-sets:", page)

    def test_the_keyframes_never_hardcode_the_shift(self):
        from pathlib import Path

        css = Path("assets/app.css").read_text()
        start = css.index("@keyframes wall-drift-left")
        block = css[start:start + 400]
        self.assertIn("var(--wall-sets", block)
        self.assertNotIn("-50%", block)

    def test_every_photo_carries_its_aspect_and_nothing_else(self):
        """⚠️ `--h` is gone (2026-08-07). Every photo is drawn at the strip's
        full height, so the only thing the template needs is how wide that
        makes it. A leftover `--h` would be a variable nothing reads, which is
        the kind of thing that gets "restored" later by somebody who assumes it
        used to work."""
        self.add_photo(self.pantry)
        self.login(self.lisi)
        page = self.client.get(reverse("gallery:wall")).content.decode()
        self.assertIn("--ar:", page)
        self.assertNotIn("--h:", page)

    def test_the_lightbox_sequence_is_a_json_block_and_holds_the_large_images(self):
        """⚠️ The rows carry thumbnails; the full-size links appear only here.
        Reading the sequence off the DOM instead would pick up the seam clones
        and make every arrow press need two."""
        photo = self.add_photo(self.pantry)
        self.login(self.lisi)
        page = self.client.get(reverse("gallery:wall")).content.decode()
        self.assertIn('id="wall-sequence"', page)
        self.assertIn('type="application/json"', page)
        self.assertIn(photo.image.name.rsplit("/", 1)[-1], page)

    def test_the_sequence_holds_one_entry_per_photo_not_one_per_tile(self):
        for _ in range(5):
            self.add_photo(self.pantry)
        self.login(self.lisi)
        response = self.client.get(reverse("gallery:wall"))
        self.assertEqual(len(response.context["sequence"]), 5)

    def test_the_seam_clone_is_hidden_from_assistive_technology(self):
        """⚠️ The clone exists to hide the loop's seam. Left in the
        accessibility tree it means every photo announced twice, and a keyboard
        user walking one row twice."""
        self.add_photo(self.pantry)
        self.login(self.lisi)
        page = self.client.get(reverse("gallery:wall")).content.decode()
        self.assertIn("wall-set--clone", page)
        self.assertIn('tabindex="-1"', page)

    def test_no_photo_on_the_strip_carries_a_visible_label(self):
        """2026-08-08: 「每个图不要带标签了」.

        ⚠️ Asserted on the **element**, not on the text. The words are still on
           the page twice over — once in the lightbox's JSON block and once in
           an sr-only span — so `assertNotContains(response, "Food Pantry")`
           would fail while the page was perfectly correct, and, worse, the
           reverse check would have gone on passing after the label was
           removed. What changed is that nothing is drawn, and that is what
           this looks at.
        """
        self.add_photo(self.pantry)
        self.login(self.lisi)
        page = self.client.get(reverse("gallery:wall")).content.decode()
        self.assertNotIn("wall-caption", page)

    def test_each_photo_still_has_a_name_for_screen_readers(self):
        """⚠️ The label was the button's accessible name. Taking it out without
        putting an sr-only one back leaves sixty buttons that a screen reader
        announces as "button, button, button" with nothing to tell them apart —
        the image is `alt=""`, so there is no other source."""
        self.add_photo(self.pantry)
        self.login(self.lisi)
        page = self.client.get(reverse("gallery:wall")).content.decode()
        self.assertIn('<span class="sr-only">Food Pantry · 2019</span>', page)

    def test_the_lightbox_still_names_the_ministry_and_the_year(self):
        """The one place the line survives: under the enlarged photo. That is a
        different thing from a label on every tile, and it was asked for
        separately (2026-08-06)."""
        self.add_photo(None)
        self.login(self.lisi)
        response = self.client.get(reverse("gallery:wall"))
        self.assertEqual([entry["caption"] for entry in response.context["sequence"]],
                         ["River of Life Foundation · 2019"])

    def test_hovering_slows_the_strip_without_stopping_it(self):
        """Two separate mechanisms, and mixing them up is the bug this guards:

          · **stopping** is the lightbox, and it uses `animation-play-state`;
          · **slowing** is hover, and it uses the Web Animations API's
            playbackRate.

        ⚠️ Slowing must not be done by changing `animation-duration`. The
           browser recomputes "how far through am I" against the new duration,
           so the strip jumps a long way the instant the mouse arrives.
        """
        from pathlib import Path

        source = Path("assets/js/app.js").read_text()
        self.assertIn("updatePlaybackRate", source)
        body = source[source.index("linger(strip, on) {"):]
        self.assertIn("animation-play-state", Path("assets/app.css").read_text())
        self.assertIn("updatePlaybackRate", body[:body.index("\n  },")])

    def test_the_hover_slowdown_stays_visibly_in_motion(self):
        """⚠️ **Both ends of this range are real feedback**, which is why it is
        a range and not a single number.

        2026-08-07 asked for 「慢很多很多倍」and got 0.05. 2026-08-08 came back
        with 「慢到我以为带子都停了」and asked for 2.5x. So:

          · too fast and hovering does nothing worth doing;
          · **too slow and it becomes indistinguishable from the lightbox's
            stop**, which is a different state that has to stay readable —
            0.05 had quietly stolen it.

        The floor is the half of this nobody thinks to write down.
        """
        from pathlib import Path

        source = Path("assets/js/app.js").read_text()
        rate = float(source.split("lingerRate:")[1].split(",")[0])
        self.assertLess(rate, 1.0, "hovering should slow the strip")
        self.assertGreaterEqual(
            rate, 0.2, "this slow reads as stopped — see the 2026-08-08 note")

    def test_the_slowdown_factor_lives_in_exactly_one_place(self):
        """⚠️ There was a `--wall-linger` in the stylesheet that nothing read.
        A second home for the same number is how the two end up disagreeing,
        and the dead one is the one people edit."""
        from pathlib import Path

        self.assertNotIn("--wall-linger", Path("assets/app.css").read_text())

    def test_the_strip_and_not_each_photo_carries_the_hover_handler(self):
        """⚠️ Per-photo handlers fire leave-then-enter every time the pointer
        crosses a divider, so the speed stutters. Shrinking is per-photo (CSS
        `:hover`); slowing is for the whole strip."""
        self.add_photo(self.pantry)
        self.login(self.lisi)
        page = self.client.get(reverse("gallery:wall")).content.decode()
        strip = page[page.index('class="wall-strip"'):]
        self.assertIn("linger($el, true)", strip[:400])
        self.assertNotIn("linger(", strip[strip.index("wall-photo"):])

    def test_hovering_one_strip_does_not_slow_the_others(self):
        """⚠️ The bug this rules out is subtle and was one edit away.

        Alpine keeps only the **last** element registered under a given
        `x-ref` name, so three strips all writing `x-ref="track"` would leave
        `$refs.track` pointing at the third one — and hovering the first strip
        would slow the third. The symptom is "the slowdown works sometimes",
        which is the worst kind to chase. `linger()` therefore takes the
        hovered element and finds its own track inside it.
        """
        from pathlib import Path

        source = Path("assets/js/app.js").read_text()
        self.assertIn("linger(strip, on)", source)
        # ⚠️ Sliced to the function **body**, not searched over the whole file.
        #    The comment above `linger` names `$refs.track` while explaining why
        #    it is not used — a whole-file `assertNotIn` fails on the
        #    explanation, which is the sort of test that teaches people to
        #    delete their comments.
        body = source[source.index("linger(strip, on) {"):]
        body = body[:body.index("\n  },")]
        self.assertIn('strip?.querySelector(".wall-track")', body)
        self.assertNotIn("$refs", body)

    def test_the_photos_are_rounded_and_lifted_off_the_page(self):
        """2026-08-08: 「每张照片都带一点圆角，来点悬浮感」.

        ⚠️ The radius goes on the `<img>`, not on the `.wall-photo` box. The box
           is the full band height and the photo is centred inside it, so
           rounding the box would draw its curve above and below the picture
           where there is nothing to round.

        ⚠️ Dark mode needs its own shadow. A black shadow on a black page is
           nothing at all, so there it becomes a faint white hairline — same
           job, which is lifting the photo off the background.
        """
        from pathlib import Path

        css = Path("assets/app.css").read_text()
        block = css[css.index("  .wall-photo img {"):]
        self.assertIn("border-radius:", block[:900])
        self.assertIn("box-shadow:", block[:900])
        self.assertIn(".dark .wall-photo img {", css)

    def test_the_dark_photos_are_not_given_a_wide_black_glow(self):
        """⚠️ **Reported by the user, not measured by me**: 「gallery 的照片有
        奇怪的阴影」.

        The first version kept a white hairline **and** a
        `0 12px 28px rgb(0 0 0 / 0.55)` drop shadow — which is exactly what the
        comment above it had already argued against. On a dark background a
        black shadow does not read as lift; it smears a band round each photo
        that is dirtier than the page behind it.

        Lift on a dark background comes from the object's own edge being
        *brighter*, not from what is behind it being darker — behind it is
        already dark, so there is no information left to add there. Only a very
        tight contact shadow survives, and it has to stay inside the corner
        radius or the smear comes back.
        """
        import re
        from pathlib import Path

        css = Path("assets/app.css").read_text()
        start = css.index("  .dark .wall-photo img {")
        block = css[start:css.index("}", start)]
        blurs = [int(b) for b in re.findall(r"\b(\d+)px\b", block)]
        self.assertTrue(blurs)
        self.assertLessEqual(
            max(blurs), 8,
            "a wide black shadow on a dark page is a smear, not a lift")
        # And the hairline is still doing the actual work.
        self.assertIn("0 0 0 1px rgb(255 255 255", block)

    def test_three_strips_fit_the_page_with_room_for_the_title(self):
        """⚠️ `--strip-h` is not only a height — it is the denominator of the
        uniform scale every photo is drawn at, so it cannot be set by "what
        looks right" alone. Three strips have to leave room for the title in
        the corner.

        ⚠️ The gaps are no longer counted in dvh here, because they are no
           longer measured in dvh: they are the same 14px as the gap between
           two photos (2026-08-08). Three strips is 78dvh, and 28px of gap is
           under 4dvh on any screen this page is usable on.
        """
        from pathlib import Path
        import re

        css = Path("assets/app.css").read_text()
        strip_h = float(re.search(r"--strip-h: ([\d.]+)dvh;", css).group(1))
        gap_px = float(re.search(r"--photo-gap: ([\d.]+)px;", css).group(1))
        # The gaps, in dvh, on the shortest screen worth supporting (600px).
        gap_dvh = (STRIP_COUNT - 1) * gap_px / 600 * 100
        self.assertLess(
            STRIP_COUNT * strip_h + gap_dvh, 90,
            "three strips do not leave room for anything else")

    def test_the_title_sits_in_the_corner_and_is_no_longer_a_headline(self):
        """2026-08-08: 「title 变小些，换到页面右下角的位置」.

        ⚠️ It is still **first in the DOM**. The visual position is the corner;
           the reading order is the top, because letting a screen-reader user
           hear sixty photographs before hearing what the page is called has it
           backwards. Absolute positioning is what lets those two differ.
        """
        from pathlib import Path

        self.add_photo(self.pantry)
        self.login(self.lisi)
        page = self.client.get(reverse("gallery:wall")).content.decode()
        self.assertLess(page.index("wall-title"), page.index("wall-strips"))

        css = Path("assets/app.css").read_text()
        head = css[css.index("  .wall-head {"):]
        self.assertIn("position: absolute;", head[:300])
        self.assertIn("right: var(--wall-pad);", head[:300])
        self.assertIn("bottom:", head[:300])

    def test_the_two_gaps_are_one_value(self):
        """2026-08-08: 「每个带子图片高度占满的时候，中间的距离和同一带子两张
        照片的距离一样」.

        ⚠️ Asserted as "the same custom property", not as "two numbers that
           match". Two literals would satisfy the request today and drift the
           first time somebody adjusts one of them — and nothing would say so,
           because both values would still be perfectly reasonable.
        """
        from pathlib import Path

        css = Path("assets/app.css").read_text()
        self.assertIn("--photo-gap:", css)
        self.assertNotIn("--strip-gap", css)
        # Both axes read the same property.
        photos = css[css.index("  .wall-track,\n  .wall-set {"):]
        self.assertIn("gap: var(--photo-gap);", photos[:1400])
        strips = css[css.index("  .wall-strips {"):]
        self.assertIn("gap: var(--photo-gap);", strips[:400])

    def test_the_dark_background_matches_both_forms_of_the_sticky_bar(self):
        """2026-08-08: 「gallery 深色背景换成黑色模式的顶吸 bar 的颜色和透明度」.

        ⚠️ The bar has **two** dark forms and which one shows depends on whether
           a hero image has been uploaded — opaque `ink-900` without one,
           `rgb(0 0 0 / 0.62)` over the photo with one. Copying only one of
           them would be right on some deployments and wrong on others, and
           neither would raise.
        """
        from pathlib import Path

        css = Path("assets/app.css").read_text()
        bar = css[css.index("  .dark .home-bar.is-solid {"):]
        self.assertIn("background-color: var(--color-ink-900);", bar[:200])
        page = css[css.index("  .dark .wall-page {"):]
        self.assertIn("background-color: var(--color-ink-900);", page[:200])

        glass_bar = css[css.index("  .dark.has-hero .home-bar.is-solid {"):]
        glass_page = css[css.index("  .dark.has-hero .wall {"):]
        for rule in ("background-color: rgb(0 0 0 / 0.62);", "backdrop-filter: blur(14px);"):
            with self.subTest(rule=rule):
                self.assertIn(rule, glass_bar[:250])
                self.assertIn(rule, glass_page[:250])

    def test_the_page_carries_the_hero_photo_the_glass_sits_on(self):
        """⚠️ Without this layer the same 62% black composites over a white
        canvas and comes out mid-grey — the numbers copied correctly and the
        result nothing like the bar. The translucency only means something if
        there is something behind it."""
        self.add_photo(self.pantry)
        page_obj = HomePage.current()
        page_obj.hero_image.save("hero.webp", ContentFile(
            a_photo(size=(400, 300)).read()), save=True)
        self.login(self.lisi)
        page = self.client.get(reverse("gallery:wall")).content.decode()
        self.assertIn("background-image: url(", page)
        # Dark only — light mode is unchanged by any of this.
        self.assertIn("hidden bg-cover bg-center dark:block", page)
        # ⚠️ **And the class the glass is selected by.** This was missed on the
        #    first pass: the photo layer rendered, `has-hero` did not, so every
        #    `.dark.has-hero` rule silently failed to match and the photo came
        #    through at nearly full strength behind the strips. Nothing errors,
        #    and it does not read as a missing class — it reads as "the dark
        #    mode looks wrong".
        self.assertIn('class="h-full has-hero"', page)

    def test_without_a_hero_image_neither_the_layer_nor_the_class_appears(self):
        """The other half. With no hero uploaded the dark page falls back to a
        solid `ink-900` — which is exactly what the sticky bar does in the same
        situation, so the two still match."""
        self.add_photo(self.pantry)
        self.login(self.lisi)
        page = self.client.get(reverse("gallery:wall")).content.decode()
        self.assertNotIn("has-hero", page)
        self.assertIn('class="h-full"', page)

    def test_the_glass_is_not_on_the_body(self):
        """⚠️ The hero layer is a **child** of `<body>`, so it paints above the
        body's own background. A `backdrop-filter` on the body cannot sample
        its own descendant — the glass has to sit on `.wall`, which is above
        it."""
        from pathlib import Path

        css = Path("assets/app.css").read_text()
        page = css[css.index("  .dark .wall-page {"):]
        self.assertNotIn("backdrop-filter", page[:200])

    def test_the_strip_animation_never_animates_opacity(self):
        """⚠️ **A guard for a trap that is documented as having cost three
        debugging sessions.** An ancestor animating `opacity` kills a
        descendant's `backdrop-filter` permanently and silently — not just
        while the animation runs. `.wall-track` is inside the glass, animates
        forever, and would be the obvious place to add a fade-in."""
        from pathlib import Path

        css = Path("assets/app.css").read_text()
        frames = css[css.index("  @keyframes wall-drift-left {"):]
        self.assertNotIn("opacity", frames[:300])

    def test_the_page_follows_dark_mode(self):
        """⚠️ Unlike the front page, which deliberately does not. The wall's
        background is the sticky bar's colour in each mode, so it needs the
        same first-frame script every other page has — and that script is a
        shared partial precisely so the two cannot drift."""
        self.add_photo(self.pantry)
        self.login(self.lisi)
        page = self.client.get(reverse("gallery:wall")).content.decode()
        self.assertIn("wall-page", page)
        self.assertIn('localStorage.getItem("theme")', page)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(), STORAGES=LOCAL_STORAGE)
class ManagePageTests(PageTestCase):
    """Putting photos up and taking them down, through the pages."""

    def setUp(self):
        super().setUp()
        self.director = self.account("director", "王",
                                     birth_date=datetime.date(1970, 1, 1))
        self.director.groups.add(foundation_admin_group())

    def post_photos(self, ministry=None, count=1, files=None, follow=False):
        data = {} if ministry is None else {"ministry": ministry.pk}
        uploads = files if files is not None else [a_photo() for _ in range(count)]
        return self.client.post(
            reverse("gallery:manage"), {**data, "images": uploads}, follow=follow)

    def test_a_plain_volunteer_cannot_open_the_manage_page(self):
        self.login(self.lisi)
        self.assertEqual(self.client.get(reverse("gallery:manage")).status_code, 403)

    def test_a_ministry_admin_can_add_a_photo_for_their_ministry(self):
        self.login(self.zhang)
        self.assertEqual(self.post_photos(self.pantry).status_code, 302)
        photo = GalleryPhoto.objects.get()
        self.assertEqual(photo.ministry, self.pantry)
        self.assertEqual(photo.uploaded_by, self.zhang)

    def test_both_derivatives_and_the_size_columns_are_filled_in_on_save(self):
        """⚠️ The columns are not an optimisation. Reading `thumb.width` opens
        the file, which on R2 is a network round trip — sixty of those per page
        load is the difference between a page that renders and one that does
        not."""
        self.login(self.zhang)
        self.post_photos(self.pantry)
        photo = GalleryPhoto.objects.get()
        self.assertTrue(photo.image.name)
        self.assertTrue(photo.thumb.name)
        # 1200x800 down to a 700px longest edge.
        self.assertEqual((photo.thumb_width, photo.thumb_height), (700, 467))

    def test_a_ministry_admin_posting_another_ministrys_id_is_refused(self):
        """⚠️ The dropdown stops a slip; this stops a POST. Two jobs, and the
        form only does the first one."""
        self.login(self.zhang)
        self.assertEqual(self.post_photos(self.tax).status_code, 200)
        self.assertFalse(GalleryPhoto.objects.exists())

    def test_a_ministry_admin_posting_no_ministry_at_all_is_refused(self):
        """The foundation-wide case reached by doing nothing, which is exactly
        why the blank option is taken out of their dropdown as well."""
        self.login(self.zhang)
        self.post_photos(None)
        self.assertFalse(GalleryPhoto.objects.exists())

    def test_the_foundation_tier_can_publish_a_foundation_wide_photo(self):
        self.login(self.director)
        self.assertEqual(self.post_photos(None).status_code, 302)
        self.assertIsNone(GalleryPhoto.objects.get().ministry)

    def test_a_ministry_admin_sees_only_their_own_ministrys_photos(self):
        """⚠️ Narrowed in the queryset, not hidden in the template — the second
        kind still sends the other ministry's rows to the browser."""
        self.login(self.director)
        self.post_photos(self.tax)
        self.login(self.zhang)
        self.post_photos(self.pantry)
        response = self.client.get(reverse("gallery:manage"))
        self.assertEqual([p.ministry for p in response.context["photos"]],
                         [self.pantry])

    def test_the_foundation_tier_sees_every_ministrys_photos(self):
        self.login(self.zhang)
        self.post_photos(self.pantry)
        self.login(self.director)
        self.post_photos(self.tax)
        response = self.client.get(reverse("gallery:manage"))
        self.assertEqual(len(response.context["photos"]), 2)

    def test_deleting_takes_the_files_with_the_row(self):
        """⚠️ Deleting only the row leaves two objects in the bucket that
        nothing points at — invisible, and holding a photograph somebody asked
        to have taken down."""
        self.login(self.zhang)
        self.post_photos(self.pantry)
        photo = GalleryPhoto.objects.get()
        storage, names = photo.image.storage, (photo.image.name, photo.thumb.name)
        self.assertTrue(all(storage.exists(name) for name in names))

        self.client.post(reverse("gallery:delete", args=[photo.pk]))
        self.assertFalse(GalleryPhoto.objects.exists())
        for name in names:
            with self.subTest(file=name):
                self.assertFalse(storage.exists(name))

    def test_a_get_does_not_delete_anything(self):
        """⚠️ A link that deletes on GET is followed by every prefetcher and
        crawler that sees the page, and these files are the one thing here that
        no backup brings back."""
        self.login(self.zhang)
        self.post_photos(self.pantry)
        photo = GalleryPhoto.objects.get()
        self.client.get(reverse("gallery:delete", args=[photo.pk]))
        self.assertTrue(GalleryPhoto.objects.filter(pk=photo.pk).exists())

    def test_a_ministry_admin_cannot_delete_another_ministrys_photo(self):
        self.login(self.director)
        self.post_photos(self.tax)
        photo = GalleryPhoto.objects.get()
        self.login(self.zhang)
        self.assertEqual(
            self.client.post(reverse("gallery:delete", args=[photo.pk])).status_code,
            403)
        self.assertTrue(GalleryPhoto.objects.filter(pk=photo.pk).exists())

    def test_ten_photos_go_up_in_one_submission(self):
        self.login(self.zhang)
        self.assertEqual(
            self.post_photos(self.pantry, count=MAX_PHOTOS_PER_UPLOAD).status_code,
            302)
        self.assertEqual(GalleryPhoto.objects.count(), MAX_PHOTOS_PER_UPLOAD)

    def test_more_than_ten_is_refused_and_none_of_them_land(self):
        """⚠️ All or nothing on the count. Taking the first ten and dropping the
        rest silently is worse than refusing: the admin has no way to tell which
        eleven they picked became which ten."""
        self.login(self.zhang)
        response = self.post_photos(self.pantry, count=MAX_PHOTOS_PER_UPLOAD + 1)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(GalleryPhoto.objects.exists())
        self.assertContains(response, "at a time is the limit")

    def test_the_same_photo_twice_in_one_batch_is_added_once(self):
        """The picker lets somebody ctrl-click the same file twice, and they
        will."""
        self.login(self.zhang)
        twin = a_photo(unique=False)
        self.post_photos(self.pantry, files=[twin, a_photo(unique=False)])
        self.assertEqual(GalleryPhoto.objects.count(), 1)

    def test_a_photo_already_on_the_wall_is_skipped_not_duplicated(self):
        self.login(self.zhang)
        self.post_photos(self.pantry, files=[a_photo(unique=False)])
        self.post_photos(self.pantry, files=[a_photo(unique=False)])
        self.assertEqual(GalleryPhoto.objects.count(), 1)

    def test_the_skipped_ones_are_named_rather_than_vanishing(self):
        """⚠️ A batch of three that quietly becomes two is the shape of problem
        where somebody re-uploads the missing one, watches nothing happen, and
        concludes the page is broken — when it did exactly what it was told."""
        self.login(self.zhang)
        self.post_photos(self.pantry, files=[a_photo(unique=False)])
        response = self.post_photos(
            self.pantry, files=[a_photo(unique=False), a_photo()], follow=True)
        self.assertContains(response, "Skipped 1 already on the wall")
        self.assertEqual(GalleryPhoto.objects.count(), 2)

    def test_a_batch_of_nothing_but_repeats_is_refused_outright(self):
        self.login(self.zhang)
        self.post_photos(self.pantry, files=[a_photo(unique=False)])
        response = self.post_photos(self.pantry, files=[a_photo(unique=False)])
        self.assertContains(response, "Nothing to add")

    def test_the_same_photo_as_a_png_is_still_the_same_photo(self):
        """⚠️ Why the digest is taken over the **re-encoded** file rather than
        the upload. The same picture sent as JPEG and as PNG is different bytes
        going in and identical bytes coming out — different formats are exactly
        how a duplicate arrives in practice (a phone, then the same photo out of
        a chat app)."""
        self.login(self.zhang)
        self.post_photos(self.pantry, files=[
            a_photo(size=(400, 400), colour=(10, 20, 30), unique=False, fmt="PNG")])
        self.post_photos(self.pantry, files=[
            a_photo(size=(400, 400), colour=(10, 20, 30), unique=False, fmt="PNG")])
        self.assertEqual(GalleryPhoto.objects.count(), 1)

    def test_one_bad_file_in_a_batch_stores_none_of_them(self):
        """⚠️ The re-encode happens in clean, before anything is written. The
        other order means a batch of ten whose ninth is a PDF has already
        stored eight by the time it finds out."""
        self.login(self.zhang)
        pdf = SimpleUploadedFile("notes.pdf", b"%PDF-1.4 not really",
                                 content_type="application/pdf")
        response = self.post_photos(
            self.pantry, files=[a_photo(), a_photo(), pdf])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(GalleryPhoto.objects.exists())

    def test_the_digest_is_stored_so_the_column_can_enforce_it(self):
        self.login(self.zhang)
        self.post_photos(self.pantry)
        photo = GalleryPhoto.objects.get()
        self.assertEqual(len(photo.image_digest), 64)

    def test_the_manage_page_is_reachable_from_the_menu(self):
        """⚠️ Every management page in this project was once reachable only by
        typing its URL. The wall's entrance is deliberately hidden; the upload
        page's must not be."""
        self.login(self.zhang)
        page = self.client.get(reverse("events:event_list")).content.decode()
        self.assertIn(reverse("gallery:manage"), page)

    def test_a_plain_volunteer_is_offered_no_entrance_to_it(self):
        self.login(self.lisi)
        page = self.client.get(reverse("events:event_list")).content.decode()
        self.assertNotIn(reverse("gallery:manage"), page)


class FeatherEntranceTests(PageTestCase):
    """The two ways in, and the reason there have to be two.

    ⚠️ This class exists because the accessible route is a **single element on
       one shared component**, and removing it would break nothing visible:
       the drifting feathers still work for everybody who can see them, and the
       people who cannot are exactly the people who will not file the bug.
    """

    def test_the_drifting_feathers_lead_to_the_wall(self):
        self.login(self.lisi)
        page = self.client.get(reverse("events:event_list")).content.decode()
        self.assertIn(f'data-memories-url="{reverse("gallery:wall")}"', page)

    def test_the_url_comes_from_the_template_not_from_the_bundle(self):
        """⚠️ Same trap as the feather image paths, one layer along: a URL
        hardcoded in app.js works in development and keeps working until
        somebody moves the route, at which point nothing errors and the
        feathers simply stop leading anywhere."""
        from pathlib import Path

        source = Path("assets/js/app.js").read_text()
        self.assertIn("dataset.memoriesUrl", source)
        self.assertNotIn("/memories/", source)

    def test_the_drifting_feather_is_the_only_way_in(self):
        """⚠️ **The cost of that, written down so it is never mistaken for a
        bug.** 2026-08-07: the still feather that used to sit in the top bar
        was taken out, because the wall's entrance should be found rather than
        listed. What follows from that:

          · the drifting layer is `aria-hidden`, so screen readers never
            mention it;
          · it is not reachable by keyboard;
          · it is `display: none` under prefers-reduced-motion.

        So keyboard users, screen-reader users, and anybody who has asked for
        reduced motion cannot reach this page and will not learn it exists.
        That is the decision, not an oversight — but it is the kind of decision
        that gets quietly reversed by somebody "fixing the missing link", so it
        is asserted here in the direction it was decided.
        """
        self.login(self.lisi)
        for name in ["events:event_list", "accounts:profile"]:
            with self.subTest(page=name):
                page = self.client.get(reverse(name)).content.decode()
                self.assertNotIn(f'href="{reverse("gallery:wall")}"', page)

    def test_the_front_page_carries_the_drifting_layer_and_no_link(self):
        page = self.client.get(reverse("home")).content.decode()
        self.assertIn("feather-sky", page)
        self.assertNotIn(f'href="{reverse("gallery:wall")}"', page)

    def test_the_drifting_layer_still_lets_clicks_through_to_the_page(self):
        """⚠️ The bug this protects against is documented in app.css and cost a
        real debugging session: with `pointer-events` on the full-screen
        container, the sign-up button is dead for the two seconds a feather is
        passing over it — and nobody can reproduce that. Only the feather image
        itself may take clicks.
        """
        from pathlib import Path

        css = Path("assets/app.css").read_text()
        sky = css.index(".feather-sky {")
        self.assertIn("pointer-events: none", css[sky:sky + 200])
        feather = css.index("\n  .feather {")
        self.assertIn("pointer-events: auto", css[feather:feather + 200])


class StorageSplitTests(TestCase):
    """The four buckets, and the two policies that cannot share one.

    ⚠️ None of this can be checked against a real bucket from a test, so what
       is asserted is the *configuration* — that the aliases exist, that the
       models name the right one, and that no migration has frozen a backend.
       The bucket-level settings (private for all but `public`, and no automatic
       deletion anywhere near memories) are a deployment checklist item, written
       into 03-roadmap.md's C3.5.

    ⚠️ **2026-08-12:** these docstrings used to explain the split as "versioning
       on for memories, off for event images". **R2 has no object versioning**,
       so that was never true of the store this runs on. Every assertion below
       is unchanged and still worth making — what changed is the reason.
    """

    def test_every_alias_the_models_ask_for_exists(self):
        from django.conf import settings

        for alias in ("default", "memories", "public", "staticfiles"):
            with self.subTest(alias=alias):
                self.assertIn(alias, settings.STORAGES)

    def test_gallery_photos_are_not_in_the_event_image_bucket(self):
        """⚠️ The whole reason for a third bucket. purge_event_images sweeps the
        event-images bucket daily and an object lifecycle rule would be a
        reasonable thing to add to it; gallery photos are the only files here
        that no backup brings back, so no automatic deletion may ever be able to
        reach them. One bucket cannot hold both policies.

        ⚠️ Asserted on the **callable**, not on `field.storage`. Django resolves
           a callable once, when the model class is loaded, and keeps the
           resulting instance — so `field.storage` is a frozen object from
           import time and comparing it to a freshly-resolved one compares two
           different instances of the same class and fails for no real reason.
           The callable is the thing this test is actually about anyway: it is
           what names the bucket, and what the migration records.
        """
        from core.storages import memories_storage

        for name in ("image", "thumb"):
            with self.subTest(field=name):
                field = GalleryPhoto._meta.get_field(name)
                self.assertIs(field._storage_callable, memories_storage)

    def test_gallery_photos_do_not_silently_fall_back_to_the_default_bucket(self):
        """The failure this rules out: dropping the `storage=` argument. The
        photos would go to the event-images bucket — the one that gets swept —
        and that would be visible nowhere until the day a photograph vanished
        because an event it had nothing to do with had finished."""
        from django.core.files.storage import default_storage

        field = GalleryPhoto._meta.get_field("image")
        self.assertIsNot(field.storage, default_storage)

    def test_the_front_pages_media_is_in_the_public_bucket(self):
        """⚠️ Public on purpose: the front page needs no login, so signing its
        URLs would protect nothing while making the largest file the site
        serves uncacheable."""
        from core.models import HomePage
        from core.storages import public_storage

        for name in ("hero_image", "hero_video"):
            with self.subTest(field=name):
                field = HomePage._meta.get_field(name)
                self.assertIs(field._storage_callable, public_storage)

    def test_no_migration_hardcodes_a_storage_backend(self):
        """⚠️ Passing a Storage *instance* to `storage=` writes that instance's
        configuration — bucket name, endpoint, and in the worst case the access
        key — into a migration file that is committed and replayed everywhere.
        A callable is serialised as a dotted path instead, which is what lets
        development stay on local disk while production is on R2 **without the
        migrations differing**.
        """
        from pathlib import Path

        for path in Path(".").glob("*/migrations/*.py"):
            text = path.read_text()
            with self.subTest(migration=str(path)):
                self.assertNotIn("S3Storage", text)
                self.assertNotIn("bucket_name", text)

    def test_no_bucket_name_or_key_is_committed(self):
        """The credentials come from the environment. .env.example documents
        the names and holds no values — same rule SECRET_KEY has followed since
        the beginning."""
        from pathlib import Path

        example = Path(".env.example").read_text()
        for line in example.splitlines():
            if line.startswith("R2_"):
                with self.subTest(line=line):
                    self.assertTrue(line.endswith("="), line)
