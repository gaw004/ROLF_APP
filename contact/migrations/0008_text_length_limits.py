"""Text length caps on the free-text columns (2026-08-06).

⚠️ **The SQL is a no-op**, verified with `sqlmigrate`. A Postgres `text` column
   stays `text`: `max_length` on a TextField is not a database constraint, and
   Django 5.2 does not even add a model validator for it. What it does do is
   travel through `TextField.formfield()` into a `forms.CharField`, so every
   ModelForm — our pages and the admin — refuses an overlong value and renders
   `maxlength`. The layers are spelled out in core/limits.py.

   The migration exists anyway because the field's declared state changed, and
   the migration guard in core/tests.py fails on a model that has drifted from
   its migrations. Existing rows are untouched and nothing can be too long for
   the column, so there is no data to fix up.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contact', '0007_emergency_contact_email'),
    ]

    operations = [
        migrations.AlterField(
            model_name='contact',
            name='notes',
            field=models.TextField(blank=True, max_length=2000),
        ),
        migrations.AlterField(
            model_name='historicalcontact',
            name='notes',
            field=models.TextField(blank=True, max_length=2000),
        ),
    ]
