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

from django.contrib.auth.models import AnonymousUser
from django.urls import reverse

from core.models import HomePage
from org.permissions import (
    can_grant_ministry_admin,
    in_foundation_tier,
    ministry_ids_administered_by,
)


def _link(label, url_name, query=""):
    return {"label": label, "url": reverse(url_name) + query}


def _menu_for(user, administered, foundation):
    """The site menu, in order, as data. Rendered by _site_menu.html.

    ⚠️ **Built here rather than as branches in the template**, and the reason is
       the entrance animation. Each entry's transition-delay is computed from a
       `--i` the template supplies, and those numbers were written by hand —
       which works exactly as long as the list is flat. With three conditional
       sections it stops working: whichever section is hidden leaves a hole in
       the numbering, one entry waits an extra beat for nothing, and the
       staggered entrance the whole effect exists for goes lumpy. Nothing errors,
       and it is invisible until you watch the right account open the menu.
       Generated here, `forloop.counter0` is the number and there is no hole to
       leave.

    ⚠️ The two admin sections are **labelled by tier**, because one person can
       hold both and the two mean different things: a ministry admin publishes
       and edits their own ministry's events, while the foundation tier appoints
       ministry admins and may read every ministry's records. Somebody who is
       both needs to see which hat each page belongs to.

    ⚠️ `Events I Manage` and `All Events` are the same view, and the foundation
       one carries `?scope=all` deliberately — see events/views._scoped_events.
       Somebody who is both keeps the managing view of their own ministries by
       default, so without the parameter the foundation-wide entry would land on
       a page showing only their own ministries while the label said "All".

    Headings are entries too, rather than a nested structure: a flat list is what
    lets one loop number every item, which is the whole point (above).
    """
    if not user.is_authenticated:
        return [
            _link("Events", "events:event_list"),
            _link("Past Events", "events:past_events"),
            _link("Log In", "accounts:login"),
            _link("Register", "accounts:register"),
        ]

    menu = [
        _link("Events", "events:event_list"),
        _link("Past Events", "events:past_events"),
        _link("My Signups", "events:my_participations"),
        _link("My Profile", "accounts:profile"),
    ]

    if administered:
        menu += [
            {"heading": "Ministry Admin"},
            # No "New event" entry: event_manage_list already carries a
            # "Publish a new event" button, gated on the same permission. A
            # second entrance means the same condition written in two places,
            # and those two eventually disagree.
            _link("Events I Manage", "events:event_manage_list"),
            # ⚠️ The **manage** page, not the wall. The wall's entrance is the
            #    feather (the drifting ones, and the still one in the top bar),
            #    and putting a second door to it in the menu would give away the
            #    one thing that page is built around — you find it by noticing
            #    something. This entry is here for the opposite reason: without
            #    it, the upload page is reachable only by typing its URL, which
            #    is precisely the shape of the five gaps this module exists to
            #    close.
            _link("Memories Photos", "gallery:manage"),
        ]

    if foundation:
        menu += [
            {"heading": "Foundation Admin"},
            _link("All Events", "events:event_manage_list", "?scope=all"),
        ]
        # ⚠️ Only when they are not already a ministry admin, so that somebody
        #    holding both tiers gets one entry rather than two identical ones.
        #    Unlike `Events I Manage` / `All Events` — which carry different
        #    query strings and land on genuinely different lists — this is the
        #    same URL either way; the page itself widens for the foundation
        #    tier. Two entries pointing at one page reads as a bug.
        if not administered:
            menu.append(_link("Memories Photos", "gallery:manage"))
        if can_grant_ministry_admin(user):
            menu.append(_link("Ministry Admins", "org:ministry_list"))

    if user.is_staff:
        # ⚠️ Its own section, and not folded into either tier above. `is_staff`
        #    is a different axis: it says "may open the Django admin", which is
        #    neither of the two ministry-scoped tiers and is held by neither by
        #    default. Filing it under "Foundation admin" would state something
        #    untrue about who has it.
        # ⚠️ `new_tab` — the only entry in this menu that gets it, and the only
        #    one that leaves this interface. Somebody opens the Django admin to
        #    look something up or fix a row **while** they are in the middle of
        #    whatever brought them here; replacing the tab throws that away and
        #    the way back is the browser's Back button through a page that may
        #    have been a POST. Every other entry is a page of this site, and
        #    opening those in new tabs would just accumulate them.
        menu += [{"heading": "Staff"},
                 {"label": "Admin Site", "url": "/admin/", "new_tab": True}]

    return menu


def navigation(request):
    """Which management entrances this account should see."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {
            "administered_ministry_ids": set(),
            "can_grant_ministry_admin": False,
            "is_ministry_admin": False,
            "can_see_all_events": False,
            "site_menu": _menu_for(AnonymousUser(), set(), False),
        }

    administered = ministry_ids_administered_by(user)
    foundation = in_foundation_tier(user)
    return {
        "site_menu": _menu_for(user, administered, foundation),
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
       changes. `for_request()` rather than `load()` on purpose: the latter is a
       get_or_create, which puts a write on the read path of every page in the
       site. Two query-count tests caught that within a minute of it landing.

    ⚠️ `for_request()` rather than `current()` as of 2026-08-13, and it is the
       front page that was paying: `core.views.home` needs the same row for the
       verse, so that one page — the busiest public URL in the site — ran this
       SELECT twice. Neither call site could see the other, which is why the
       caching is on the model rather than a note asking people to be careful.

    ⚠️ One query is cheap but not free, and it is the reason `brand_palette` is
       stored on the row rather than computed: quantising a photograph per page
       view would not be.

    ⚠️ Returns the **image** only, never the video. A video behind every page
       means every page decodes video, which on a phone is heat and battery for
       something nobody is looking at. A page whose only hero is a video falls
       back to the plain dark background.
    """
    page = HomePage.for_request(request)
    hero_image = page.hero_image if page.hero_image else None
    return {
        "site_hero_image": hero_image,
        "site_brand_palette": page.brand_palette or None,
        # ⚠️ The same string the front page uses, out of the same property.
        #    Every page crops this one photograph to a different shape, and the
        #    focus is what keeps all of those framings agreeing with each other.
        #    Formatting it separately here is how they would drift apart.
        "site_hero_focus": page.hero_focus,
        # The `<html>` class for every page that carries the shell. Computed
        # here for one reason, and it is written on the two files that used to
        # do it themselves:
        #
        # ⚠️ `has-hero` is what selects the dark glass — every rule for it is
        #    `.dark.has-hero ...`. Without the class the backdrop photograph is
        #    still painted but the 62% black over it is not, so the picture comes
        #    through at nearly full strength and the whole page goes bright and
        #    busy. **It does not error and it does not look like a missing
        #    class.** `wall.html` said exactly that in a comment while holding
        #    the second copy of the condition; it has been hit once already.
        "site_root_class": "h-full has-hero" if hero_image else "h-full",
    }
