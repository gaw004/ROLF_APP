"""A third served_as value, and the constraint it makes possible.

`not_applicable` is not a third identity — it is "this question does not arise
on this row", which is what a signup for a role people *attend* looks like.
Blank could not carry it: blank already means "predates D38, and the backfill
could not prove anything" (0014), and one absence standing for two different
facts is how a column stops being evidence.

The constraint is the payoff. "A place somebody attends records no hours" used
to read across two tables — hours here, the nature of the role on
ParticipationRole — so no CheckConstraint could see it. With the value on the
row, the test is local, and every write path obeys it (D9).

⚠️ AddConstraint on a populated table, so the obvious question is whether it
   can fail: it cannot. Nothing writes `not_applicable` until L1.3, so no
   existing row can violate it — this migration adds the guarantee, and the
   writer arrives in the next step.

See participants.md L4, D38 sections 5 and 9, and 06-roadmap.md L1.2.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contact', '0009_contact_contact_individual_has_a_first_name'),
        ('events', '0016_participationrole_nature'),
    ]

    operations = [
        migrations.AlterField(
            model_name='historicalparticipation',
            name='served_as',
            field=models.CharField(blank=True, choices=[('volunteer', 'Volunteering'), ('work', 'Scheduled work'), ('not_applicable', 'Not applicable')], max_length=20),
        ),
        migrations.AlterField(
            model_name='participation',
            name='served_as',
            field=models.CharField(blank=True, choices=[('volunteer', 'Volunteering'), ('work', 'Scheduled work'), ('not_applicable', 'Not applicable')], max_length=20),
        ),
        migrations.AddConstraint(
            model_name='participation',
            constraint=models.CheckConstraint(condition=models.Q(models.Q(('served_as', 'not_applicable'), _negated=True), ('hours__isnull', True), _connector='OR'), name='participation_no_hours_when_not_applicable', violation_error_code='participation_hours_when_not_applicable', violation_error_message='A place somebody attends does not record hours — they were not giving their time.'),
        ),
    ]
