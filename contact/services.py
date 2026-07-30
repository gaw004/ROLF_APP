"""Cross-table writes for the contact app. Permanent asset — see goal.md D18.

Nothing here knows about the admin, and nothing here is a form or a view. Phase
C's HTMX views import the same functions unchanged; that is the whole point of
the file existing before it has any content.
"""

from django.db import IntegrityError, transaction

from core.timeutils import local_today

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


class MergeConflict(Exception):
    """Refusal to merge, with the reason. Never a partial merge."""


# Fields never carried over from the retired record: identity, status and the
# audit columns. notes is handled separately — it gets appended to, not copied.
FIELDS_NOT_MERGED = {
    "id", "contact_type", "is_active", "notes", "created_at", "updated_at",
}


@transaction.atomic
def merge_contacts(keep, drop, *, actor=None):
    """Repoint everything that references `drop` at `keep`, then retire `drop`.

    Scope is minimum-viable on purpose: no field-by-field merge screen. The
    field rule is simply "keep wins, drop fills in the blanks".

    Walks Contact._meta.related_objects rather than a hand-written list of
    foreign keys. A hand-written list would certainly miss Phase C's
    Contribution, and the symptom of missing one is donations quietly vanishing
    along with the retired record.

    Refuses rather than guesses, in two situations — saying which one blocked is
    far safer than deleting one side on the caller's behalf.
    """
    if keep.pk == drop.pk:
        raise MergeConflict("不能把一条记录合并到它自己。")

    for relation in Contact._meta.related_objects:
        model = relation.related_model
        # simple_history rows record what happened at the time. Rewriting them
        # would be falsifying the audit trail, so history stays with whoever
        # it was written about.
        if model.__name__.startswith("Historical"):
            continue

        field_name = relation.field.name
        rows = model._base_manager.filter(**{field_name: drop})
        if not rows.exists():
            continue

        # Conflict 1: one-to-one. Both sides holding a User or a
        # VolunteerProfile is a question only a person can answer.
        if relation.one_to_one and model._base_manager.filter(
                **{field_name: keep}).exists():
            raise MergeConflict(
                f"两条记录都有 {model._meta.verbose_name}，无法自动合并 —— "
                "请先决定保留哪一个。"
            )

        # Conflict 2: unique constraints. Cheaper than reflecting over every
        # constraint on every related model, and it catches exactly the same
        # cases: let the database try, and take its answer.
        #
        # ⚠️ The inner atomic() is the savepoint, and it is not optional. Once
        #    Postgres raises, the transaction is aborted and no further query
        #    runs until something rolls back to a savepoint — catching
        #    IntegrityError without one turns the next statement into a
        #    TransactionManagementError, which is what this code did first.
        try:
            with transaction.atomic():
                rows.update(**{field_name: keep})
        except IntegrityError as error:
            raise MergeConflict(
                f"{model._meta.verbose_name} 上有冲突的唯一约束，无法自动合并："
                f"{error}"
            ) from error

    _fill_blanks(keep, drop)

    # Two trails: simple_history already records the edit, and this line is
    # visible to a human reading the record months later.
    stamp = f"已合并 #{drop.pk}（{local_today().isoformat()}）"
    if actor:
        stamp += f"，操作人 {actor}"
    keep.notes = f"{keep.notes}\n{stamp}".strip() if keep.notes else stamp
    keep.save()

    # Retired, not deleted: it is the tombstone that explains where the ids in
    # anybody's bookmarks and exports went.
    drop.is_active = False
    drop.save()
    return keep


def _fill_blanks(keep, drop):
    """keep's values win; drop only supplies what keep left empty."""
    for field in Contact._meta.fields:
        if field.name in FIELDS_NOT_MERGED or not field.editable:
            continue
        if not getattr(keep, field.name) and getattr(drop, field.name):
            setattr(keep, field.name, getattr(drop, field.name))
