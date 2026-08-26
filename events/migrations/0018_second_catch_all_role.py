"""A catch-all row for each half of the axis, and both names say which.

L1 split participation roles into helping and attending (0016). The catch-all
row — where a signup lands when nobody picked a specific job — was left as the
single helping one it had always been, so a beneficiary who came for no
particular named service had nowhere correct to go.

Two changes, and the rename is not cosmetic:

    general            "General participant"  →  "General participant (helping)"
    general-attending  (new)                     "General participant (attending)"

Left as a bare "General participant" beside "General participant (attending)",
the first row's half is invisible — readable only by knowing that no bracket
means helping, which is a rule nobody is ever told. Both named, the pair reads
itself. See 06-roadmap.md L1.6 and D27's "what is missing and what is not
counted must not look the same".

⚠️ `code="general"` is untouched and must stay that way — ImmutableCodeMixin,
   read by name in models.py, and matched on by 0003's get_or_create. The two
   codes are therefore asymmetrical; matching them is not available.

⚠️ Only the exact seeded text is renamed, the same rule 0015 applied to itself:
   a foundation that has already renamed this row in the admin meant to.

⚠️ The new row is created with its nature spelled out. Relying on the column's
   default would make the *attending* catch-all a helping one, and nothing
   anywhere would report it.
"""

from django.db import migrations

HELPING_CODE = "general"
ATTENDING_CODE = "general-attending"

OLD_HELPING_NAME = "General participant"
NEW_HELPING_NAME = "General participant (helping)"
ATTENDING_NAME = "General participant (attending)"


def add_the_other_half(apps, schema_editor):
    ParticipationRole = apps.get_model("events", "ParticipationRole")
    ParticipationRole.objects.filter(
        code=HELPING_CODE, name=OLD_HELPING_NAME,
    ).update(name=NEW_HELPING_NAME)
    ParticipationRole.objects.get_or_create(
        code=ATTENDING_CODE,
        defaults={"name": ATTENDING_NAME, "nature": "attending"},
    )


def back(apps, schema_editor):
    """⚠️ Lossy, and says so: the attending catch-all is deleted.

    Any signup that landed on it would block the delete (PROTECT from
    EventRole.role), which is the right outcome — reversing past the point
    where somebody used the row is not something this can do quietly.
    """
    ParticipationRole = apps.get_model("events", "ParticipationRole")
    ParticipationRole.objects.filter(
        code=HELPING_CODE, name=NEW_HELPING_NAME,
    ).update(name=OLD_HELPING_NAME)
    ParticipationRole.objects.filter(code=ATTENDING_CODE).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("events", "0017_served_as_not_applicable"),
    ]

    operations = [
        migrations.RunPython(add_the_other_half, back),
    ]
