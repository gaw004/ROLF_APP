"""Give the audience M2M a reverse name, so the audience filter can be one Exists.

⚠️ **No SQL at all.** `related_name` lives in Django's state, not in the
   database — this migration exists so the state matches the model, and running
   it changes nothing about any table.

Why it is a separate migration rather than an edit to 0019: 0019 is already
applied and already committed, and this project's rule (0003's own docstring)
is that an applied migration is not amended. Here it would in fact be harmless,
because the change emits nothing — which is precisely the sort of "harmless
this time" that erodes a rule worth keeping.

What it buys: with the reverse disabled ("+") the natural form of the audience
filter does not exist —

    FieldError: Unsupported lookup 'event_audience'

— and the working alternative is a subquery over the through table carrying two
nested OuterRefs. Same answer, far worse to read. See EventQuerySet.for_audience()
and 06-roadmap.md L2.2.

⚠️ Ministry now has two reverse entrances whose names are close and whose
   meanings are not: `ministry.events` (the ones it runs) and
   `ministry.event_audience` (the ones it is allowed to see).
"""


from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('events', '0019_event_audience'),
        ('org', '0008_kind_to_staff_and_compensation'),
    ]

    operations = [
        migrations.AlterField(
            model_name='event',
            name='visible_to_ministries',
            field=models.ManyToManyField(blank=True, limit_choices_to={'is_active': True}, related_name='%(class)s_audience', to='org.ministry', verbose_name="Only these ministries' staff"),
        ),
        migrations.AlterField(
            model_name='eventrole',
            name='visible_to_ministries',
            field=models.ManyToManyField(blank=True, limit_choices_to={'is_active': True}, related_name='%(class)s_audience', to='org.ministry', verbose_name="Only these ministries' staff"),
        ),
    ]
