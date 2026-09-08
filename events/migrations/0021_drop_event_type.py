"""Drop `EventType` and `Event.event_type` — a mandatory field nobody read.

Every publisher had to pick a type to save an event (`event_type` was a
non-null FK, and it sat on EventForm), while no front-end template ever
displayed one: the sole reader was the admin changelist. L2.6 was going to
give it a reader; the foundation had never asked for event categorisation
(participants.md 第六节, "R1–R8 / P1–P6 里一条都没提到活动分类"), so the
table goes instead. See 06-roadmap.md L2.6 and revisions.md.

⚠️ **This one really deletes data**, unlike 0020. Two columns go: the values
   on every existing event and the dictionary rows themselves. What makes that
   acceptable is that nothing could read them — and the nightly pg_dump
   (C3.6, scripts/backup/backup.sh) still holds them if a later question
   needs answering.

⚠️ `HistoricalEvent.event_type` goes in the same migration and must: the
   shadow table carries its own copy of the column, and leaving it behind
   would keep the FK's data alive in the one table nobody thinks to look at.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0020_audience_reverse_name'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='event',
            name='event_type',
        ),
        migrations.RemoveField(
            model_name='historicalevent',
            name='event_type',
        ),
        migrations.DeleteModel(
            name='EventType',
        ),
    ]
