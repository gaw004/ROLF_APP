"""Rename the catch-all role to English.

events/0003 seeded it as 通用志愿者. The interface is English (goal.md D23), and
this row is one of the few pieces of *data* the interface always shows: it is
the role every signup lands on when nobody picked a specific job, so it appears
on the signup dropdown, the registrations page and the report of every event
that uses it.

A separate migration rather than an edit to 0003, for the reason 0003 itself
gives: it has already been applied, and get_or_create matches on code, so
amending it would leave databases spelled differently depending on when they
were first migrated.

Only the exact seeded text is touched — a foundation that has already renamed
it in the admin meant to (D5: display names are theirs, only `code` is fixed).
"""

from django.db import migrations

OLD_NAME = "通用志愿者"
NEW_NAME = "General volunteer"


def to_english(apps, schema_editor):
    ParticipationRole = apps.get_model("events", "ParticipationRole")
    ParticipationRole.objects.filter(code="general", name=OLD_NAME).update(name=NEW_NAME)


def back(apps, schema_editor):
    ParticipationRole = apps.get_model("events", "ParticipationRole")
    ParticipationRole.objects.filter(code="general", name=NEW_NAME).update(name=OLD_NAME)


class Migration(migrations.Migration):

    dependencies = [
        ("events", "0004_event_requires_guardian_consent_and_more"),
    ]

    operations = [
        migrations.RunPython(to_english, back),
    ]
