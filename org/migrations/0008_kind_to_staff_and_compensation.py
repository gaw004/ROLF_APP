"""Data half of the axis split: rewrite the old three values into two axes (D32).

    employee   →  kind=staff, compensation=paid
    volunteer  →  kind=staff, compensation=unpaid
    board      →  kind=board, compensation=unpaid

The fact each row was carrying is preserved exactly; what changes is the
vocabulary it is written in. "This was a paid employee post" survives as
(staff, paid), and "this was an unpaid post" survives as (staff, unpaid) — the
two of them are only indistinguishable *backwards*, which is what makes the
reverse below lossy.

⚠️ Both tables, position and historicalposition, through one function and one
   mapping. The shadow table is not cosmetic here: 0007 filled its new column
   with the field default, so a row from last March would otherwise read
   "employee" + "unpaid" — a combination that cannot have been true, sitting in
   the one table this project keeps for answering "what did it say back then".

⚠️ The rows in the shadow table today are all pre-pilot test data and will be
   cleared before go-live (confirmed 2026-08-19), so this migration's risk
   against real history is nil. The function is written properly anyway: after
   go-live there is no second chance at it.
"""

from django.db import migrations

#: old kind → (new kind, compensation). Imported by the tests, which assert it
#: is still exactly these three rows — the guard is on the table, not the data.
FORWARD = {
    "employee": ("staff", "paid"),
    "volunteer": ("staff", "unpaid"),
    "board": ("board", "unpaid"),
}

#: (kind, compensation) → old kind. board is handled separately: it maps back
#: on `kind` alone and simply drops whatever compensation said.
BACKWARD = {
    ("staff", "paid"): "employee",
    # ⚠️ Conservative, and it is the reverse's sharper edge — see the docstring
    #    on backwards() below.
    ("staff", "stipend"): "employee",
    ("staff", "unpaid"): "volunteer",
}

TABLES = ("Position", "HistoricalPosition")


def forwards(apps, schema_editor):
    for label in TABLES:
        model = apps.get_model("org", label)
        for old, (kind, compensation) in FORWARD.items():
            # queryset.update(), not a loop of save(): three statements for the
            # whole table, and it does not depend on knowing that the fake model
            # a migration gets carries none of simple-history's signals. That
            # happens to be true; not having to know it is better.
            model.objects.filter(kind=old).update(kind=kind, compensation=compensation)


def backwards(apps, schema_editor):
    """Lossy, in two ways, and the second one is worse than the first.

    1. staff + unpaid all map back to "volunteer". Whether that post was
       originally an employee post or a volunteer one is information the
       forward direction *added*, and it cannot be recovered.

    2. `stipend` did not exist in the old vocabulary at all. It maps back to
       "employee" — the conservative reading, matching the one this project
       already takes on reports (D32 section 2). So a stipend post that goes
       back and then forward again comes out as `paid`, and neither pass
       raises.

    Which is to say: this direction is a way to stop the bleeding, not a door
    to walk back and forth through.
    """
    for label in TABLES:
        model = apps.get_model("org", label)
        # board needs no statement at all: the value is unchanged and the
        # compensation column is about to be dropped by 0007's reverse.
        for (kind, compensation), old in BACKWARD.items():
            model.objects.filter(kind=kind, compensation=compensation).update(kind=old)


class Migration(migrations.Migration):

    dependencies = [
        ("org", "0007_position_compensation"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
