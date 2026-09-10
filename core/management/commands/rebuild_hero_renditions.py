"""Cut the srcset ladder for a picture that was uploaded before it existed.

⚠️ **A one-off, run once after the deploy that added the ladder** — and again
   only if `HERO_RENDITION_WIDTHS` changes. Every upload after that deploy
   builds its own rungs in `HomePage.save()`, so there is nothing here for a
   scheduler to do but re-encode the same photograph for ever, changing three
   uuid filenames and invalidating every cached copy each time it runs.

⚠️ **Run this before `restamp_home_media`, not after.** The rungs are written by
   `FieldFile.save()`, so they carry the public bucket's `Cache-Control` from
   the moment they are uploaded; the original is the only object left needing
   the header rewritten. In the other order the restamp runs against files that
   do not exist yet and the rungs — the ones every browser will actually be
   downloading — are the objects left without a cache policy.

Safe to run twice: it rebuilds from the same original and replaces the rungs,
and `HomePage.save()` hands the superseded ones to `discard_media` on commit,
exactly as replacing the picture does. The cost of a needless run is three new
filenames, which is three cold caches — not three orphaned files.
"""

from django.core.management.base import BaseCommand

from core.models import HomePage


class Command(BaseCommand):
    help = ("Re-derive the front page picture's responsive sizes, and take the "
            "camera's metadata off the original. Needed once for a picture "
            "uploaded before either existed.")

    def handle(self, *args, **options):
        page = HomePage.load()
        if not page.hero_image:
            # ⚠️ Not an error. A front page with no picture is a supported
            #    state — the backdrop falls back to plain dark — and reporting
            #    it as a failure would send somebody looking for a fault.
            self.stdout.write(self.style.WARNING(
                "The front page has no picture, so there is nothing to size."))
            return

        # ⚠️ **Metadata first, and it is why this command still has a job on a
        #    picture whose rungs are already cut.** `HomePage.save()` strips on
        #    the way in, so every upload after that change is clean — but the
        #    photograph already sitting in the public bucket predates it and
        #    still carries whatever the camera wrote, GPS included. Stripping
        #    gives the original a new name, which is exactly why it has to
        #    happen before the rungs are cut rather than after.
        before = page.hero_image.name
        # ⚠️ **One call, not "do the work then save".** `save()` owns stripping
        #    and cutting the ladder; this flag only tells it to do so for a
        #    picture whose name has not changed. Doing it here first and saving
        #    afterwards was measured building the ladder twice and orphaning
        #    three rungs — the rename made `save()` think a new picture had
        #    arrived. The note on `HomePage.save` has the working.
        page.save(rebuild_hero=True)
        stripped = HomePage.load().hero_image.name != before

        if stripped:
            self.stdout.write(self.style.SUCCESS(
                "Camera metadata removed from the original — the pixels are "
                "unchanged, the picture is on a new URL."))

        page = HomePage.load()
        rungs = page.hero_rungs
        if not rungs:
            self.stdout.write(self.style.WARNING(
                f"No sizes were cut. The picture is {page.hero_image_width}px "
                f"wide, which is narrower than the smallest one worth storing "
                f"— it will be served as itself."))
            return

        for width, url in rungs:
            self.stdout.write(f"{width}w  {url}")
        self.stdout.write(self.style.SUCCESS(
            f"{len(rungs)} size(s) stored for a {page.hero_image_width}px "
            f"picture. The original is untouched and is still what a display "
            f"big enough for it receives."))
