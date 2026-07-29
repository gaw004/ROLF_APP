"""Cross-table writes for the contact app. Permanent asset — see goal.md D18.

Nothing here knows about the admin, and nothing here is a form or a view. Phase
C's HTMX views import the same functions unchanged; that is the whole point of
the file existing before it has any content.

Coming residents:
  direction_choices(subject)              B3.1b — the two readings of each type
  orient(*, subject, other, subject_is_a) B3.1b — which contact ends up as A
  merge_contacts(keep, drop, *, actor)    B4.4  — repoint every FK, then retire

Created empty on purpose (B1): by the time there is a reason to write orient(),
the tempting place to put it is Form.save(), and moving it afterwards costs more
than having the file already sitting here.
"""
