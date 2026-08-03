"""What the shared navigation is allowed to know about the person reading it.

Every management page in this project was reachable only by typing its URL:
event_create and org:ministry_admins appeared in no template at all, and
event_roles — which links onward to registrations, attendance, the report and
the notice page — was reached only by the one redirect after creating an event.
The pages and their permission checks were all correct; nothing pointed at them.

⚠️ Everything here asks org.permissions and nothing else. Reading MinistryRole
   directly would put a second copy of the scoping rule in the navigation, and
   the copy that drifts is always the one nobody remembers is there — that is
   D20's whole argument, and there is a grep guard enforcing it.

Cheap by construction: ministry_ids_administered_by() is one query returning a
set of ids, and can_grant_ministry_admin() is one group lookup. Both run per
request because base.html draws the nav on every page.
"""

from org.permissions import can_grant_ministry_admin, ministry_ids_administered_by


def navigation(request):
    """Which management entrances this account should see."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {"administered_ministry_ids": set(), "can_grant_ministry_admin": False}

    administered = ministry_ids_administered_by(user)
    return {
        # A set of ids, not a queryset: the nav only asks "any?", and handing
        # templates a queryset invites somebody to iterate it into a menu and
        # add a query to every page in the project.
        "administered_ministry_ids": administered,
        "is_ministry_admin": bool(administered),
        "can_grant_ministry_admin": can_grant_ministry_admin(user),
    }
