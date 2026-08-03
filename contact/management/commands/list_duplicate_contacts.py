"""List contacts that look like duplicates. Reporting only — never merges."""

from django.core.management.base import BaseCommand

from contact.models import Contact


class Command(BaseCommand):
    help = "List contacts sharing a normalised name and a phone number."

    def handle(self, *args, **options):
        # Same judgement as the admin filter and the form hint: one QuerySet
        # method, called from three places.
        duplicates = Contact.objects.possible_duplicates().order_by(
            "legal_last_name", "legal_first_name", "phone", "pk")
        if not duplicates:
            self.stdout.write("No records share a name and a number.")
            return
        for contact in duplicates:
            self.stdout.write(f"#{contact.pk}\t{contact}")
        self.stdout.write(f"\n{len(duplicates)} in total. Merge them at /contacts/merge/.")
