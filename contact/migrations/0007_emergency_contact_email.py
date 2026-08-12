"""EmergencyContact gains a required email address (2026-08-05).

Why required rather than optional: this address is what P6 falls back to when a
minor has no consent address on file, and until now that fallback could only
send SMS — the table had no email column at all. Email costs essentially
nothing to send; SMS does not.

⚠️ `preserve_default=False`, so the empty string is used **once**, to fill the
   column on rows that already exist, and is not left on the field afterwards.
   Rows written before today therefore come out with an empty email while the
   model says one is required — a state nothing reports until somebody next
   edits that row and the form refuses to save it.

   That is the honest state rather than a bug: those records really are
   incomplete now. There are no such rows in a fresh production database, and
   the local demo data is rebuilt by seed_demo.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("contact", "0006_alter_emergencycontact_relationship_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="emergencycontact",
            name="email",
            field=models.EmailField(default="", max_length=254),
            preserve_default=False,
        ),
    ]
