"""Any event that already has meetings on it is a course — 0026 defaulted it wrong.

`shape` defaults to `single`, which is the truth for every event the foundation
has ever published: before L5.1 an event *was* one occasion, and there was no
table in which meetings could exist. But L5.1 and L5.2 landed on 5 and 8
September and the development databases have been growing runs ever since, so
"every existing row is a one-off" stopped being true three days before this
column arrived.

🔴 Leaving those rows at `single` is not merely untidy, and this is the reason
   the migration exists rather than a note in the roadmap. From L5.3 on:

     · `Session.clean()` refuses a meeting on a one-off occasion — so those runs
       could never gain another meeting, and the error would name a rule the row
       appears to be breaking already;
     · `services.open_register()` keys on the shape, so nobody signing up would
       be put on those registers. The meetings would still be drawn on the
       schedule and listed on the detail page (both key on *having* meetings,
       which is the right test for what to draw) — and the register underneath
       them would stay permanently empty, with nothing raising anywhere.

   That is precisely the silent failure L5.3 is being added to close, so it must
   not be shipped in the same commit.

⚠️ The test is `sessions.exists()`, and it is the one place in this codebase
   where that question is allowed to answer "what shape is this". Everywhere
   else it would be wrong — a course with no dates on it yet is still a course
   (see `Event.Shape`). Here there is no other evidence to read: these rows
   predate the column, so what they *have* is all that is left to go on, and a
   run with meetings is unambiguous in that direction.

⚠️ Reversible on purpose, and the reverse is a no-op rather than a mirror.
   Putting every course back to `single` on the way down would rewrite rows this
   migration never touched, and the column is dropped by 0026's reverse anyway.
"""

from django.db import migrations


def courses_are_the_ones_with_meetings(apps, schema_editor):
    Event = apps.get_model("events", "Event")
    Event.objects.filter(sessions__isnull=False).distinct().update(
        shape="program")


class Migration(migrations.Migration):

    dependencies = [
        ("events", "0026_event_shape"),
    ]

    operations = [
        migrations.RunPython(
            courses_are_the_ones_with_meetings,
            migrations.RunPython.noop,
        ),
    ]
