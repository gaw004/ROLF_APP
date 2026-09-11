"""Delete the pictures of events that are over.

⚠️ A management command, not a shell script — the opposite of the backup
   decision in phase-c.md, and for the reason that rule actually gives. Backups
   must run when the application cannot start, so they may depend on nothing but
   `pg_dump` and a URL. This one has to ask the ORM which events have finished
   and then clear a column; there is no version of it that does not need Django.

Runs daily on the platform's scheduler (C3.5). Daily means a picture can outlive
its event by up to a day, which is the price of not having a second piece of
infrastructure to fire something at an exact moment.
"""

from django.core.management.base import BaseCommand

from events.services import (events_with_images_to_purge,
                             purge_event_image, purge_series_image,
                             series_with_images_to_purge)


class Command(BaseCommand):
    help = "Delete images attached to events that have already finished."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="List what would be deleted and delete nothing.",
        )

    def handle(self, *args, **options):
        # ⚠️ Listed before deleting. The queryset is defined by "end_time has
        #    passed", which stays true as rows are cleared — but iterating a
        #    queryset while saving the rows it selects is a habit worth not
        #    having, and the count below has to be honest.
        # ⚠️ Two passes, because there are two owners of a picture and their
        #    "is it over" questions are different rows: an event's own picture
        #    goes when that event is over, and a series' picture goes when its
        #    **last** occasion is over. One pass over events alone would leave
        #    every series picture behind for ever.
        self.purge_series(**options)

        events = list(events_with_images_to_purge())
        if not events:
            self.stdout.write("No finished events are still holding an image.")
            return

        for event in events:
            if options["dry_run"]:
                self.stdout.write(f"would delete: {event.image.name} ({event.name})")
                continue
            name = event.image.name
            purge_event_image(event)
            self.stdout.write(f"deleted: {name} ({event.name})")

        verb = "would be deleted" if options["dry_run"] else "deleted"
        self.stdout.write(self.style.SUCCESS(f"{len(events)} image(s) {verb}."))

    def purge_series(self, **options):
        """The other owner: a repeat rule's picture, shown by all its occasions."""
        series = list(series_with_images_to_purge())
        if not series:
            # ⚠️ Quiet at verbosity 0, because this runs on a daily cron and a
            #    line every night saying nothing happened is how a log stops
            #    being read. The events pass below has the same bug and it is
            #    left alone here — one fix per thing.
            if options.get("verbosity", 1):
                self.stdout.write("No finished series are still holding an image.")
            return
        for one in series:
            if options["dry_run"]:
                self.stdout.write(f"would delete: {one.image.name} ({one.name})")
            else:
                purge_series_image(one)
        if not options["dry_run"] and options.get("verbosity", 1):
            self.stdout.write(self.style.SUCCESS(
                f"Deleted {len(series)} series picture(s)."))
