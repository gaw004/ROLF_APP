from django.apps import AppConfig
from django.db.models.signals import post_migrate


def _sync_foundation_admin_group(sender, **kwargs):
    """Bring the foundation_admin group in line with the list in permissions.py.

    ⚠️ Runs after **every** migrate, which is what makes
       FOUNDATION_ADMIN_PERMISSIONS true in production rather than only in the
       demo data.

       Before this, the group was reconciled only when something called
       `foundation_admin_group()` — and the only callers were `seed_demo` and
       the tests. Neither runs on a deployed site. So adding a permission to
       that list did nothing at all in production, silently, and the only
       symptom was a page missing from the admin index.

       ⚠️ This is the **second** time this exact shape has bitten. The first was
       `if created or not group.permissions.exists()`, which made the list a lie
       on any database that already had the group. Fixing that function was not
       enough, because **nothing was calling it**.

    ⚠️ Guarded on `sender` so it runs once per migrate rather than once per app.
       Permissions that do not exist yet are skipped by the function itself, and
       the next migrate picks them up — which is exactly what happens the first
       time a new model is added alongside a new permission for it.
    """
    if getattr(sender, "name", None) != "org":
        return
    from .permissions import foundation_admin_group

    foundation_admin_group()


class OrgConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'org'

    def ready(self):
        post_migrate.connect(
            _sync_foundation_admin_group, dispatch_uid="org.sync_foundation_admin")
