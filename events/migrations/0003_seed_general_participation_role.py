"""Create the catch-all participation role, code="general".

Participation.event_role is not nullable, so "signed up, no particular job" has
to have somewhere to land — which makes this row an invariant of the schema
rather than demo data. It was only ever created by seed_demo, and seed_demo
refuses to run with DEBUG off: a production database would have come up without
it, and the first person to be signed up without a specific job would have had
nowhere to go. See phase-b.md's model table and 02-roadmap.md B6.

get_or_create rather than create: the row already exists on any machine that has
run seed_demo, and this migration has to be a no-op there.
"""

from django.db import migrations

GENERAL_CODE = "general"
GENERAL_NAME = "General volunteer"


def add_general_role(apps, schema_editor):
    ParticipationRole = apps.get_model("events", "ParticipationRole")
    ParticipationRole.objects.get_or_create(
        code=GENERAL_CODE, defaults={"name": GENERAL_NAME})


def remove_general_role(apps, schema_editor):
    # Only if nothing points at it. A role with signups behind it is somebody's
    # hours history, and PROTECT on EventRole.role would refuse anyway — better
    # to say so than to raise an IntegrityError on the way down. Queried from
    # the other side because EventRole.role has related_name="+", so there is no
    # reverse accessor to ask.
    ParticipationRole = apps.get_model("events", "ParticipationRole")
    EventRole = apps.get_model("events", "EventRole")
    role = ParticipationRole.objects.filter(code=GENERAL_CODE).first()
    if role and not EventRole.objects.filter(role=role).exists():
        role.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("events", "0002_eventnotification"),
    ]

    operations = [
        migrations.RunPython(add_general_role, remove_general_role),
    ]
