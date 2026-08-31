"""Nothing is registered here, and that is the decision rather than an omission.

The same call `gallery/admin.py` made, for the same reason, and it is worth
writing out again because the reason is about permissions rather than about
photographs.

⚠️ Registering `Notice` would add a **second** way to edit or delete one, gated
   on Django's `change_notice` / `delete_notice` rather than on
   org/permissions.py. That is two answers to "may they touch this", and the
   admin's answer knows nothing about ministry scope — a ministry admin with
   the permission ticked could rewrite or remove every other ministry's
   notices. `can_manage_notice()` is the only answer, and it is scoped.
   D18's boundary, arriving as a concrete risk rather than as a principle.

⚠️ There is also no read-only registration. The audit question ("who put this
   up, and what did it say before?") is already answerable: `owner` is on the
   row, `/notices/manage/` shows the foundation tier everything, and
   simple-history keeps every version. A read-only registration is one
   checkbox away from a writable one.

⚠️ And it means `FOUNDATION_ADMIN_PERMISSIONS` needs no notices entry, which is
   the shape to keep: one list of what the foundation tier may reach through
   the admin, containing only things that genuinely have no page of their own.
"""
