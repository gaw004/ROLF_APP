"""Backfill served_as on the rows that predate it — only where it is provable.

🔴 The convenient version of this migration is `default="volunteer"`, and it is
   forbidden (D38 section 9). Every historical row would come out claiming
   somebody served on their own time, and nobody ever said that. It is the same
   objection Participation.checked_in_method already carries in the model: a
   default back-dates a claim onto rows nobody checked.

So the split is between what the data proves and what it does not:

    no `kind=staff` tenure covering the event's day  →  "volunteer"
    a `kind=staff` tenure covering the event's day   →  left empty

The first is not an assumption. Somebody with no post at the foundation that
day had no working capacity to be doing this in — there is no other thing it
could have been. The second is genuine ignorance and stays that way; those rows
show up on reports in their own cell, "identity not recorded", and are never
folded into either side.

⚠️ `kind=staff`, not "any tenure at all", and that half-sentence was added on
   2026-08-19 to settle a disagreement inside D38 itself: section 9 said "any
   active Assignment", section 5 said a board member must never be asked this
   question at all. Under section 9's literal wording a trustee's old rows went
   into "not recorded"; under this one they come out as `volunteer`, matching
   what a new row for the same trustee would get. One predicate for both, so
   they cannot drift.

⚠️ Nothing is written to served_as_declared_by, not one row. These rows carry
   no statement by anybody: writing "admin" would assert that some
   administrator judged the case, and "self" would be worse.
"""

from django.db import migrations

from core.querysets import in_effect_on
from core.timeutils import local_date_of

VOLUNTEER = "volunteer"
STAFF = "staff"


def backfill(apps, schema_editor):
    Assignment = apps.get_model("org", "Assignment")
    Participation = apps.get_model("events", "Participation")

    # Grouped by the day the event ran, because that is what the question is
    # asked against — and because it turns "one query per signup" into "one
    # query per event date", which on this table is a couple of dozen.
    #
    # ⚠️ local_date_of(), never start_time.date(). The stored instant comes
    #    back in UTC, so an event that ran at 6pm Pacific reports the next day
    #    and every tenure comparison is then off by one. R8 shipped with
    #    exactly that bug once (D16).
    by_day = {}
    for pk, contact_id, start_time in Participation.objects.values_list(
        "pk", "contact_id", "event_role__event__start_time",
    ):
        by_day.setdefault(local_date_of(start_time), []).append((pk, contact_id))

    provable = []
    for day, rows in by_day.items():
        # ⚠️ in_effect_on() rather than a date comparison written out here.
        #    It is a plain Q over field names, so it applies just as well to
        #    the model a migration is handed, and it is the one place in this
        #    project that knows a null start date means "always has been" —
        #    which is not a detail worth rediscovering in a migration.
        on_the_books = set(
            Assignment.objects.filter(
                in_effect_on(day), position__kind=STAFF, position__is_active=True,
            ).values_list("contact_id", flat=True)
        )
        provable += [pk for pk, contact_id in rows if contact_id not in on_the_books]

    Participation.objects.filter(pk__in=provable).update(served_as=VOLUNTEER)


def unbackfill(apps, schema_editor):
    """Clear what this migration wrote — and only what it could have written.

    ⚠️ Rows with a declared_by are somebody's statement, made through the
       application after this migration ran. Wiping those would destroy real
       evidence to undo a backfill, so they are left exactly where they are.
    """
    Participation = apps.get_model("events", "Participation")
    Participation.objects.filter(
        served_as=VOLUNTEER, served_as_declared_by="",
    ).update(served_as="")


class Migration(migrations.Migration):

    dependencies = [
        ("events", "0013_participation_served_as"),
        # Named explicitly because this reads org.Assignment: without it the
        # migration graph is free to run this before that table has the shape
        # this query expects.
        ("org", "0008_kind_to_staff_and_compensation"),
    ]

    operations = [
        migrations.RunPython(backfill, unbackfill),
    ]
