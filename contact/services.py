"""Cross-table writes for the contact app. Permanent asset — see goal.md D18.

Nothing here knows about the admin, and nothing here is a form or a view. Phase
C's HTMX views import the same functions unchanged; that is the whole point of
the file existing before it has any content.

Still to come:
  merge_contacts(keep, drop, *, actor)    B4.4 — repoint every FK, then retire
"""

from .models import Contact, RelationshipType

# Suffixes on the choice value. The form only needs to know which way round the
# reading was, so the value is "<type id>:<direction>".
FORWARD = "fwd"
REVERSE = "rev"


def direction_choices(subject):
    """Both readings of every relationship type, phrased around `subject`.

    Returns pairs like ("3:fwd", "小明 是 ___ 的父亲"). Symmetric types appear
    once — "spouse of" read backwards is still "spouse of", and offering it
    twice would only invite recording the same relationship in both directions.

    Whoever enters the data picks a sentence; A and B never appear on screen.
    That is the whole reason this exists: "always enter from A's page" was a
    rule that turned a foreign key's direction into human discipline, and it
    could not express "王强 is my father" from 小明's page at all.
    """
    choices = []
    for relationship_type in RelationshipType.objects.all():
        forward = f"{subject} 是 ___ 的{relationship_type.name_a_to_b}"
        choices.append((f"{relationship_type.pk}:{FORWARD}", forward))
        if relationship_type.is_symmetric:
            continue
        reverse_label = relationship_type.name_b_to_a
        if reverse_label:
            choices.append((
                f"{relationship_type.pk}:{REVERSE}",
                f"{subject} 是 ___ 的{reverse_label}",
            ))
    return choices


def parse_direction_choice(value):
    """Split "<type id>:<direction>" back into (RelationshipType, subject_is_a)."""
    type_id, _, direction = value.partition(":")
    relationship_type = RelationshipType.objects.get(pk=type_id)
    return relationship_type, direction == FORWARD


def orient(*, subject, other, subject_is_a: bool) -> tuple[Contact, Contact]:
    """Return (contact_a, contact_b) for a relationship being recorded.

    The only copy of this routing. It is a plain function rather than part of
    RelationshipForm.save() because Phase C may well replace that form with an
    "all of this person's relationships" view — a different shape, so the form
    would not be reusable, and the routing would get copied. As a function it
    survives either way.

    ⚠️ Does NOT reorder symmetric types by id. That normalisation belongs to
       Relationship.save() and stays there: an import never goes through a form
       or through here. One normalisation, one place (goal.md D9).
    """
    return (subject, other) if subject_is_a else (other, subject)
