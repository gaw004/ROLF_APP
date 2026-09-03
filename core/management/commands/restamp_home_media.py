"""Put the public bucket's Cache-Control onto the objects that predate it.

⚠️ **A one-off, run once after the deploy that added the header** — and then
   again only if the header's value changes. New uploads carry it on their own
   (`object_parameters` in config/settings/prod.py), so this is not a job for a
   scheduler; a scheduler here would rewrite the same two objects for ever.

⚠️ Unlike its neighbour purge_orphaned_home_media, this one **acts by default
   and needs no second word.** The asymmetry is the blast radius: that command
   deletes the front page's photograph from the one bucket the pg_dump does not
   cover, this one rewrites a response header on a file it copies onto itself.
   Being wrong here costs a month of caching on a picture; being wrong there
   costs the picture.

Why it exists at all is in `core.services.restamp_home_media`, and the symptom
that led to it is in the 🔴 note over the public branch of `_r2()`: with no
Cache-Control, Chrome invents a freshness window of 10% of the file's age, and
the dark-mode backdrop — a `position: fixed` layer — has nothing to paint while
it revalidates. The page goes black mid-browse and comes back on reload.
"""

from django.core.management.base import BaseCommand

from core.services import restamp_home_media


class Command(BaseCommand):
    help = ("Rewrite Cache-Control and Content-Type on every live object the "
            "front page points at, in place, without changing their URLs.")

    def handle(self, *args, **options):
        stamped = restamp_home_media()
        if not stamped:
            # ⚠️ Not an error and not a success. An empty front page is a
            #    legitimate state (the backdrop falls back to plain dark), and
            #    so is a storage that refused — which is why the failure path
            #    logs rather than raises. The log is where the difference is.
            self.stdout.write(self.style.WARNING(
                "Nothing was restamped. Either the front page has no picture "
                "or no video set, or the copies failed — check the log."))
            return

        for name in stamped:
            self.stdout.write(f"restamped: {name}")
        self.stdout.write(self.style.SUCCESS(
            f"{len(stamped)} object(s) restamped. Their URLs did not change, "
            f"so a browser holding an old copy keeps it until its own "
            f"heuristic window closes — check with curl -I, not by reloading."))
