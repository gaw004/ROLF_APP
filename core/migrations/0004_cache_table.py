"""The table Django's database cache lives in (2026-08-06).

Created here rather than by a `createcachetable` line in a deploy script,
because of what forgetting it looks like: `django-ratelimit` counts in the cache,
so a missing table means the **registration page raises** — and it raises on the
one page whose visitors have no account yet and no way to report it. A migration
cannot be forgotten; a runbook step can.

⚠️ It reads `settings.CACHES` for the table name, so it creates whatever the
   settings ask for **at the time it runs**. Renaming the cache table later needs
   a new migration; this one will not notice.

⚠️ `createcachetable` is already idempotent — it checks for the table first — so
   re-running or applying this to a database that had the table created by hand
   is safe. The backwards direction drops it, because a cache is by definition
   reconstructible: nothing in it is data.
"""

from django.core.management import call_command
from django.db import migrations


def create_cache_table(apps, schema_editor):
    call_command(
        "createcachetable", database=schema_editor.connection.alias, verbosity=0)


def drop_cache_table(apps, schema_editor):
    from django.conf import settings

    for cache in settings.CACHES.values():
        if cache["BACKEND"].endswith("db.DatabaseCache"):
            schema_editor.execute(
                f'DROP TABLE IF EXISTS "{cache["LOCATION"]}"')


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_text_length_limits"),
    ]

    operations = [
        migrations.RunPython(create_cache_table, drop_cache_table),
    ]
