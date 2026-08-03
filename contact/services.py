"""Cross-table writes for the contact app. Permanent asset — see goal.md D18.

Nothing here knows about the admin, and nothing here is a form or a view. Phase
C's HTMX views import the same functions unchanged; that is the whole point of
the file existing before it has any content.
"""

from django.db import IntegrityError, transaction

from core.timeutils import local_today

from .models import Contact


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
        raise MergeConflict("A record cannot be merged into itself.")

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
                f"Both records have a {model._meta.verbose_name}, so this cannot be "
                f"merged automatically — "
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
                f"Conflicting unique values on {model._meta.verbose_name}, so this "
                f"cannot be merged automatically: "
                f"{error}"
            ) from error

    _fill_blanks(keep, drop)

    # Two trails: simple_history already records the edit, and this line is
    # visible to a human reading the record months later.
    stamp = f"Merged #{drop.pk} ({local_today().isoformat()})"
    if actor:
        stamp += f", by {actor}"
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
