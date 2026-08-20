"""Constraint code -> field name, plus the mixin that moves the error there.

See goal.md D14. The rule itself lives in exactly one place — the database
constraint. Django, however, reports every constraint violation against
NON_FIELD_ERRORS (the top of the form), and the person in the admin needs to
see *which box* is wrong. That is a presentation problem, so the fix only
touches presentation.

This table is pure presentation metadata and holds no business logic: change
the rule (say, 18 to 16) and only the constraint moves — not a word here.

Adding a constraint means adding three things, and core/tests.py fails loudly
if you forget any of them:
  1. violation_error_message  — the sentence a human reads
  2. violation_error_code     — the key used below
  3. an entry in CONSTRAINT_FIELD
"""

from django.core.exceptions import NON_FIELD_ERRORS, ValidationError

# violation_error_code: the field the message should appear under.
#
# ⚠️ One code, one field. A constraint covering two rules with two different
#    offending fields cannot be mapped — split the constraint instead. That is
#    why contact_name_matches_type became three constraints; see contact/models.py.
CONSTRAINT_FIELD = {
    # accounts.User
    "user_email_taken": "email",
    # contact.Contact
    "individual_needs_last_name": "legal_last_name",
    "individual_needs_first_name": "legal_first_name",
    "organization_needs_name": "organization_name",
    "contact_type_unknown": "contact_type",
    # contact.RelationshipType
    "reltype_name_taken": "name_a_to_b",
    "reltype_code_taken": "code",
    # contact.EmergencyContact
    "emergency_contact_duplicate": "name",
    # org.Ministry / org.EmploymentType
    "ministry_code_taken": "code",
    "employmenttype_code_taken": "code",
    # org.Position
    "position_code_taken": "code",
    "position_reports_to_self": "reports_to",
    # org.Assignment
    "assignment_end_before_start": "end_date",
    "assignment_duplicate_tenure": "start_date",
    # org.MinistryRole
    "ministryrole_duplicate_grant": "start_date",
    "ministryrole_end_before_start": "end_date",
    # events.EventType / events.ParticipationRole
    "eventtype_code_taken": "code",
    "participationrole_code_taken": "code",
    # events.Event
    "event_end_before_start": "end_time",
    # events.EventRole
    "eventrole_duplicate": "role",
    "eventrole_needed_count_not_positive": "needed_count",
    # events.Participation
    "participation_duplicate": "contact",
    "participation_hours_negative": "hours",
    "participation_hours_without_attendance": "hours",
    "participation_checkout_before_checkin": "checked_out_at",
    "participation_absent_after_checkin": "status",
    # core.HomePage
    #
    # ⚠️ One constraint covers both halves of the framing, so the message has to
    #    land on one of the two fields; the horizontal one is where the eye
    #    starts. If that ever reads wrong the fix is two constraints, not a
    #    cleverer mapping.
    "homepage_focus_out_of_range": "hero_focus_x",
}


def _reattach_to_fields(error):
    """Rewrite a ValidationError so coded constraint errors sit on their field."""
    if hasattr(error, "error_dict"):
        incoming = error.error_dict
    else:
        incoming = {NON_FIELD_ERRORS: error.error_list}

    remapped = {}
    for field, errors in incoming.items():
        for item in errors:
            code = getattr(item, "code", None)
            # Errors Django already attached to a real field stay where they are;
            # only the NON_FIELD_ERRORS pile gets redistributed.
            target = CONSTRAINT_FIELD.get(code, field) if field == NON_FIELD_ERRORS else field
            remapped.setdefault(target, []).append(item)
    return ValidationError(remapped)


class ConstraintErrorFieldMixin:
    """Moves constraint errors from NON_FIELD_ERRORS onto the offending field.

    Django has no built-in way to make a CheckConstraint / UniqueConstraint
    failure land on a field, and there is no hook narrower than this one:
    validate_constraints() is where full_clean() collects them.

    ⚠️ A code may only be mapped to a field that the form actually renders.
       ModelForm._update_errors raises ValueError for a field it does not have,
       so mapping a constraint onto a field hidden from the form would turn a
       tidy form error into a 500. Every mapping here points at a field that is
       on the admin form.
    """

    def validate_constraints(self, exclude=None):
        try:
            super().validate_constraints(exclude=exclude)
        except ValidationError as error:
            raise _reattach_to_fields(error) from None
