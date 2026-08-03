"""Seed the relationship vocabulary emergency contacts and consent draw on.

EmergencyContact.relationship_type is a required FK, and Participation's
consent_relationship offers the same list. On a fresh database that table is
empty, which means not "the dropdown looks sparse" but "nobody can record an
emergency contact at all" — the form has a required field with no valid choice.
That makes these rows an invariant of the schema, the same argument as
events/0003_seed_general_participation_role.py, and the same reason they are
not left to seed_demo (which refuses to run with DEBUG off) or to a line on a
manual setup checklist.

Direction, once, so nobody enters these backwards: a is the emergency contact,
b is the volunteer. "parent" reads "this emergency contact is the volunteer's
parent" — the same reading EmergencyContact.relationship_type spells out in its
help_text, and the reason the labels are nouns rather than "parent of".

⚠️ There is deliberately no "child" row, and adding one later will be rejected
   by RelationshipType.clean(): a forward name may not repeat another type's
   reverse name, and "child" is already the reverse of "parent". That rule
   exists so half the emergency contacts do not end up filed under one row and
   half under its mirror. "relative" covers an adult child standing as
   somebody's emergency contact.

Display names are English because the interface is (goal.md D23); the codes are
what code matches on and never change (D5). The foundation can rename any of
these in the admin, and add more, without touching this file.
"""

from django.db import migrations, transaction
from django.db.models import ProtectedError

# (code, name_a_to_b, name_b_to_a, is_symmetric)
# Symmetric types leave the reverse label empty on purpose: is_symmetric is the
# flag that carries the meaning, not the emptiness. See the model's comment.
# ⚠️ Nouns, not "parent of". Both places that display these put the grammar in
#    front themselves ("They are the volunteer's …"), so a label carrying it too
#    says it twice. 0005 renames any database seeded before this was understood;
#    the two files have to agree or a fresh install and an upgraded one differ.
RELATIONSHIP_TYPES = [
    ("parent", "parent", "child", False),
    ("guardian", "legal guardian", "ward", False),
    ("grandparent", "grandparent", "grandchild", False),
    ("spouse", "spouse", "", True),
    ("sibling", "sibling", "", True),
    ("relative", "relative", "", True),
    ("friend", "friend", "", True),
]


def add_relationship_types(apps, schema_editor):
    RelationshipType = apps.get_model("contact", "RelationshipType")
    for code, forward, reverse, symmetric in RELATIONSHIP_TYPES:
        # get_or_create so this is a no-op on any database where somebody has
        # already typed these in by hand. Matching on code rather than name is
        # the whole point of D5's stable identifier.
        RelationshipType.objects.get_or_create(
            code=code,
            defaults={
                "name_a_to_b": forward,
                "name_b_to_a": reverse,
                "is_symmetric": symmetric,
                # The reason these rows exist at all.
                "usable_as_emergency_contact": True,
            },
        )


def remove_relationship_types(apps, schema_editor):
    """Take back only what is not in use, and say nothing about the rest.

    Both referring columns are PROTECT (EmergencyContact.relationship_type and
    Participation.consent_relationship), so a row somebody has used will refuse
    to go. Participation lives in `events`, which already depends on `contact`,
    so this migration cannot import it to check first — hence asking the
    database instead of asking the model layer. Each delete gets its own
    savepoint: without one, the first ProtectedError would poison the whole
    transaction and the remaining rows would not even be attempted.
    """
    RelationshipType = apps.get_model("contact", "RelationshipType")
    for code, *_ in RELATIONSHIP_TYPES:
        row = RelationshipType.objects.filter(code=code).first()
        if row is None:
            continue
        try:
            with transaction.atomic():
                row.delete()
        except ProtectedError:
            # Somebody's emergency contact or consent record points at it. That
            # is data, not leftovers.
            continue


class Migration(migrations.Migration):

    dependencies = [
        ("contact", "0003_historicalcontact"),
    ]

    operations = [
        migrations.RunPython(add_relationship_types, remove_relationship_types),
    ]
