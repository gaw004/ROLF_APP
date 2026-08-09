"""The email address becomes the login name; `username` is dropped (2026-08-06).

Order matters and is not the order makemigrations produced. The data step has to
run **first**, while `username` still exists and before `email` becomes unique:

  1. fill in every missing address and lowercase the rest — needs `username`;
  2. only then drop `username` and make `email` unique.

Reversed, step 2 would hit the unique index on a table where three demo accounts
share the empty string.

⚠️ **Not reversible, and deliberately not faked into looking reversible.**
   Rolling back would re-add `username` as a unique NOT NULL column with no
   value to put in it, and would have to un-invent the addresses below. The
   backwards direction is `RunPython.noop` so that the data step does not
   pretend otherwise; `migrate accounts 0001` will fail at RemoveField, which is
   the honest outcome — restore from a dump instead.

⚠️ **This migration invents email addresses**, for accounts that had none. Say it
   plainly because inventing data is normally the wrong answer: the alternative
   was to refuse to migrate at all, and every affected row is demo data made by
   seed_demo (a production database has no users at this point, and the pilot has
   not started). The invented form is `<old username>@example.invalid` —
   `.invalid` is reserved by RFC 2606 and can never be a real address, so nothing
   is ever sent there and nobody can log in as one of these by accident. They
   match the addresses seed_demo already writes for its other accounts.

   Two accounts whose addresses collide once lowercased are **not** merged or
   renamed: the migration stops and names them. Two logins for one inbox is a
   question about people, and a migration is the wrong place to answer it.
"""

import re

import accounts.models
import django.db.models.functions.text
from django.db import migrations, models

# What may appear in the local part of an address we are making up. Anything else
# becomes a hyphen: `username` was validated as a Django username, which allows
# characters (`+`, `@`) that would produce a second at-sign or an unparseable
# address.
UNSAFE_IN_LOCAL_PART = re.compile(r"[^a-z0-9._-]+")


def fill_in_login_names(apps, schema_editor):
    User = apps.get_model("accounts", "User")

    proposed = {}
    for user in User.objects.order_by("pk"):
        address = (user.email or "").strip().lower()
        if not address:
            local = UNSAFE_IN_LOCAL_PART.sub("-", (user.username or "").strip().lower())
            address = f"{local or f'account-{user.pk}'}@example.invalid"
        proposed[user.pk] = address

    taken = {}
    for pk, address in proposed.items():
        taken.setdefault(address, []).append(pk)
    collisions = {a: pks for a, pks in taken.items() if len(pks) > 1}
    if collisions:
        raise RuntimeError(
            "Two or more accounts would end up with the same login address, and "
            "this migration will not choose between them. Decide which account "
            "keeps the address (and delete or re-address the others), then run "
            "migrate again:\n"
            + "\n".join(f"  {address}: user ids {pks}" for address, pks in collisions.items()))

    for pk, address in proposed.items():
        User.objects.filter(pk=pk).update(email=address)


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0001_initial'),
        ('auth', '0012_alter_user_first_name_max_length'),
        ('contact', '0008_text_length_limits'),
    ]

    operations = [
        migrations.RunPython(fill_in_login_names, migrations.RunPython.noop),
        migrations.AlterModelManagers(
            name='user',
            managers=[
                ('objects', accounts.models.UserManager()),
            ],
        ),
        migrations.RemoveField(
            model_name='user',
            name='username',
        ),
        migrations.AlterField(
            model_name='user',
            name='email',
            field=models.EmailField(
                error_messages={'unique': 'An account with that email address already exists.'},
                max_length=254, unique=True, verbose_name='email address'),
        ),
        migrations.AddConstraint(
            model_name='user',
            constraint=models.UniqueConstraint(
                django.db.models.functions.text.Lower('email'),
                name='user_email_taken',
                violation_error_code='user_email_taken',
                violation_error_message='An account with that email address already exists.'),
        ),
    ]
