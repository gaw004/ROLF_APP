"""L1: is somebody in this role giving time, or receiving a service?

Schema only — there is deliberately no backfill step, and that is worth a line
because the same round's 0014 is nothing but a backfill. The difference is what
the default asserts: `served_as` had to be left blank on historical rows
because nobody had made that claim about them, whereas every ParticipationRole
in the database today is genuinely a helping one. `default="helping"` therefore
restates a fact rather than inventing one, and no row's meaning changes here.

⚠️ Verify that sentence rather than trusting it: open the roles list in the
   admin after migrating and read the rows. If a foundation has already entered
   something like "ESL seat", it wants flipping by hand — which is allowed
   right up until somebody signs up through it (ParticipationRole.clean()).

See participants.md L1 and 06-roadmap.md L1.1.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0015_general_role_is_a_participant'),
    ]

    operations = [
        migrations.AddField(
            model_name='participationrole',
            name='nature',
            field=models.CharField(choices=[('helping', 'Helping'), ('attending', 'Attending')], default='helping', help_text='Everybody at an event is a participant — this says which kind. Lifting, interpreting and the welcome desk are helping; an ESL seat or a food parcel is attending.', max_length=20, verbose_name='What somebody in this role is doing'),
        ),
    ]
