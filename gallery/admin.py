"""Nothing is registered here, and that is the decision rather than an omission.

`GalleryPhoto` is reached through `/memories/manage/` and nowhere else. Both
tiers that may touch it — a ministry's admins and the foundation tier — already
have that page, and neither needs `is_staff` to use it.

⚠️ Registering it would add a **second** way to delete a photo, gated on Django's
   `delete_galleryphoto` permission rather than on org/permissions.py. That is
   two answers to "may they take this down", and the admin's answer knows
   nothing about ministry scope — a ministry admin with the permission ticked
   could delete every other ministry's photographs, and these are the files no
   backup brings back. D18's boundary, arriving as a concrete risk rather than
   as a principle.

⚠️ There is also no read-only registration, which was considered for the audit
   question ("who published this?"). `uploaded_by` is on the row and the
   foundation tier sees every photo on the manage page, so the question is
   already answerable — and a read-only registration is one checkbox away from
   a writable one.
"""
