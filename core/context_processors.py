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

from core.models import HomePage
from org.permissions import (
    can_grant_ministry_admin,
    in_foundation_tier,
    ministry_ids_administered_by,
)


def navigation(request):
    """Which management entrances this account should see."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {
            "administered_ministry_ids": set(),
            "can_grant_ministry_admin": False,
            "is_ministry_admin": False,
            "can_see_all_events": False,
        }

    administered = ministry_ids_administered_by(user)
    foundation = in_foundation_tier(user)
    return {
        # A set of ids, not a queryset: the nav only asks "any?", and handing
        # templates a queryset invites somebody to iterate it into a menu and
        # add a query to every page in the project.
        "administered_ministry_ids": administered,
        "is_ministry_admin": bool(administered),
        # ⚠️ The foundation tier reaches the same page over every ministry, and
        #    without this it had **no link to it at all** — it holds no
        #    MinistryRole, so `is_ministry_admin` is False for it and the nav
        #    drew nothing. The view was already open; nothing pointed at it.
        #    That is the exact shape of the five gaps C0.2 closed, arriving for
        #    the seventh time, and it was caught by looking at a screenshot
        #    rather than by any test.
        "can_see_all_events": bool(administered) or foundation,
        "can_grant_ministry_admin": can_grant_ministry_admin(user),
    }


def site_appearance(request):
    """The front page's picture and the brand ramp derived from it.

    Both are needed by the **shared shell**, so they are here rather than in
    each view: the top bar is on every page, and in dark mode the background is
    that picture.

    ⚠️ One **read** query per request, on a single row that almost never
       changes. `current()` rather than `load()` on purpose: the latter is a
       get_or_create, which puts a write on the read path of every page in the
       site. Two query-count tests caught that within a minute of it landing.

    ⚠️ One query is cheap but not free, and it is the reason `brand_palette` is
       stored on the row rather than computed: quantising a photograph per page
       view would not be.

    ⚠️ Returns the **image** only, never the video. A video behind every page
       means every page decodes video, which on a phone is heat and battery for
       something nobody is looking at. A page whose only hero is a video falls
       back to the plain dark background.
    """
    page = HomePage.current()
    return {
        "site_hero_image": page.hero_image if page.hero_image else None,
        "site_brand_palette": page.brand_palette or None,
    }
