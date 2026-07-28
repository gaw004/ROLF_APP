from django.core.management import call_command
from django.test import TestCase


class NoMissingMigrationsTests(TestCase):
    """Guards the whole project, which is why it lives in core rather than in one app."""

    def test_no_model_changes_are_missing_a_migration(self):
        # makemigrations --check exits non-zero when a model change has no
        # migration yet, which surfaces here as SystemExit. Without this test a
        # forgotten makemigrations only shows up at deploy time.
        call_command("makemigrations", "--check", "--dry-run", verbosity=0)
