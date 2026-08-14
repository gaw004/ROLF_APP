"""Clear out the front page's old pictures that were left behind.

⚠️ **This is a one-off, not a scheduled job**, and that is the difference
   between it and purge_event_images. That one has a *rule* — a picture goes
   when its event is over — so it can be trusted to a scheduler. This one has
   no rule: it deletes files because nothing in the database mentions them,
   which is also what a file looks like halfway through being uploaded, or
   during a deploy where one worker is on the new row and another is still on
   the old. Run by a person, who knows that neither of those is happening.

   Since 2026-08-14 `HomePage.save()` deletes the picture it replaces, so the
   list this prints stops growing. What it prints today is what accumulated
   before that.

⚠️ **Prints by default and deletes only when told twice** — the opposite
   default to purge_event_images, deliberately. Being wrong there costs a
   picture that was going to be deleted anyway; being wrong here costs the
   front page's photograph, from the one bucket the pg_dump does not cover.
"""

from django.core.management.base import BaseCommand

from core.services import discard_media, orphaned_home_media
from core.storages import public_storage


class Command(BaseCommand):
    help = ("List objects under the front page's prefix that no row points at. "
            "Pass --delete to actually remove them.")

    def add_arguments(self, parser):
        parser.add_argument(
            "--delete", action="store_true",
            help="Delete them. Without this the command only prints the list.",
        )

    def handle(self, *args, **options):
        # ⚠️ Asked once and kept. Listing again after the deletes would answer a
        #    different question, and the count below has to be about what was
        #    actually looked at.
        orphans = orphaned_home_media()
        if not orphans:
            self.stdout.write("Nothing orphaned in the front page's bucket.")
            return

        for name in orphans:
            self.stdout.write(f"orphaned: {name}")

        if not options["delete"]:
            self.stdout.write(self.style.WARNING(
                f"{len(orphans)} orphaned object(s). Nothing was deleted — "
                f"re-run with --delete once you have read the list."))
            return

        storage = public_storage()
        gone = discard_media((storage, name) for name in orphans)
        # ⚠️ `gone`, not `orphans`. discard_media swallows a storage error on
        #    purpose, so the two numbers can differ — and reporting the number
        #    asked for as the number deleted is how a bucket quietly stays full
        #    while the command says it emptied it.
        self.stdout.write(self.style.SUCCESS(f"{len(gone)} object(s) deleted."))
        if len(gone) != len(orphans):
            self.stdout.write(self.style.ERROR(
                f"{len(orphans) - len(gone)} could not be deleted — see the log."))
