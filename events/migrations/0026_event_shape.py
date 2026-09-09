"""L5.3: what kind of event this is, and whether people choose their meetings.

Pure AddField, no backfill — the default says "one occasion", which is what
every event in this database was before L5.1 gave meetings somewhere to live.
The one kind of row that default gets wrong is fixed by 0027, which explains
why it cannot wait.
"""


from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0025_participation_withdrew'),
    ]

    operations = [
        migrations.AddField(
            model_name='event',
            name='people_pick_meetings',
            field=models.BooleanField(default=False, help_text='Leave unticked and signing up covers every meeting. Tick it for a group somebody joins for a few weeks of a term.', verbose_name='People choose which meetings they attend'),
        ),
        migrations.AddField(
            model_name='event',
            name='shape',
            field=models.CharField(choices=[('single', 'One occasion'), ('program', 'A course or program — sign up once')], default='single', help_text="A course runs over weeks and is signed up to once — its start and end are the term's two ends, not one sitting.", max_length=20, verbose_name='What kind of event is this'),
        ),
        migrations.AddField(
            model_name='historicalevent',
            name='people_pick_meetings',
            field=models.BooleanField(default=False, help_text='Leave unticked and signing up covers every meeting. Tick it for a group somebody joins for a few weeks of a term.', verbose_name='People choose which meetings they attend'),
        ),
        migrations.AddField(
            model_name='historicalevent',
            name='shape',
            field=models.CharField(choices=[('single', 'One occasion'), ('program', 'A course or program — sign up once')], default='single', help_text="A course runs over weeks and is signed up to once — its start and end are the term's two ends, not one sitting.", max_length=20, verbose_name='What kind of event is this'),
        ),
    ]
