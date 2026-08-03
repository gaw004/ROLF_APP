"""Drop the trailing "of" from the seeded relationship labels.

The dropdown on the profile page reads "They are the volunteer's …", and every
option under it said "parent of", "spouse of". Filled in, that is "they are the
volunteer's parent of" — the label and the option each carry the grammar, so
together they say it twice. EmergencyContact.__str__ had the same problem:
"Wang Xiuying (parent of)".

Two conventions collided. RelationshipType's own docstring writes labels as
"A is parent of B", which is right for a sentence built from both names; but
nothing in this project builds that sentence — both places that show these
values put the label in front themselves. So the noun is the useful form, and
the direction is explained in the field's help text instead.

A second migration rather than an edit to 0004, because 0004 has already been
applied on the development database: get_or_create matches on code, so amending
it in place would silently leave old rows spelled the old way on any database
that had already run it, and the two spellings would then differ per machine.

Only rows still holding the exact seeded text are touched. Somebody who has
already renamed one in the admin meant it (D5: display names are editable, only
`code` is fixed).
"""

from django.db import migrations

# code -> (old forward, new forward, old reverse, new reverse)
RENAMES = [
    ("parent", "parent of", "parent", "child of", "child"),
    ("guardian", "legal guardian of", "legal guardian", "ward of", "ward"),
    ("grandparent", "grandparent of", "grandparent", "grandchild of", "grandchild"),
    ("spouse", "spouse of", "spouse", "", ""),
    ("sibling", "sibling of", "sibling", "", ""),
    ("relative", "relative of", "relative", "", ""),
    ("friend", "friend of", "friend", "", ""),
]


def _apply(apps, forward_index, reverse_index):
    RelationshipType = apps.get_model("contact", "RelationshipType")
    for code, old_a, new_a, old_b, new_b in RENAMES:
        was_a, will_a = (old_a, new_a) if forward_index else (new_a, old_a)
        was_b, will_b = (old_b, new_b) if reverse_index else (new_b, old_b)
        RelationshipType.objects.filter(
            code=code, name_a_to_b=was_a, name_b_to_a=was_b,
        ).update(name_a_to_b=will_a, name_b_to_a=will_b)


def to_nouns(apps, schema_editor):
    _apply(apps, True, True)


def back_to_phrases(apps, schema_editor):
    _apply(apps, False, False)


class Migration(migrations.Migration):

    dependencies = [
        ("contact", "0004_seed_relationship_types"),
    ]

    operations = [
        migrations.RunPython(to_nouns, back_to_phrases),
    ]
