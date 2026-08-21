"""Rename the catch-all role: "General volunteer" → "General participant".

The row every signup lands on when nobody picked a specific job, so it shows up
on the signup dropdown, the registrations page and every event's report. Which
makes it one of the few pieces of *data* that has to agree with the interface's
vocabulary — and the interface stopped calling everyone a volunteer on
2026-08-20 (participants.md section 5): a person collecting a food parcel or
sitting in an ESL class is landing on this same row.

⚠️ `code` is untouched and must stay that way. It is an ImmutableCodeMixin
   column, `ParticipationRole.GENERAL_CODE` is read by name in code, and
   0003_seed_general_participation_role's get_or_create matches on it — the
   display name is the only half that was ever ours to change (D5: display
   names belong to the foundation, only `code` is fixed).

A separate migration rather than an edit to 0005, for the reason 0003 gives
about itself: it has already been applied, so amending it would leave databases
spelled differently depending on when they were first migrated.

Only the exact seeded text is touched — a foundation that has already renamed
this row in the admin meant to.
"""

from django.db import migrations

OLD_NAME = "General volunteer"
NEW_NAME = "General participant"


def to_participant(apps, schema_editor):
    ParticipationRole = apps.get_model("events", "ParticipationRole")
    ParticipationRole.objects.filter(code="general", name=OLD_NAME).update(name=NEW_NAME)


def back(apps, schema_editor):
    ParticipationRole = apps.get_model("events", "ParticipationRole")
    ParticipationRole.objects.filter(code="general", name=NEW_NAME).update(name=OLD_NAME)


class Migration(migrations.Migration):

    dependencies = [
        ("events", "0014_backfill_served_as"),
    ]

    operations = [
        migrations.RunPython(to_participant, back),
    ]
