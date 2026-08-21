"""Project-wide request rules.

Just the one so far, and it exists because Django's default is the wrong answer
for this project rather than because anything is missing.
"""

from django.core.exceptions import PermissionDenied

# The admin is mounted here (config/urls.py). Written once so the two places
# that care cannot drift apart.
ADMIN_PREFIX = "/admin/"
# Leaving is always allowed. Somebody whose staff flag was taken away
# mid-session would otherwise be unable to log out of a site that refuses them
# every page.
ALWAYS_ALLOWED = {f"{ADMIN_PREFIX}logout/"}


class StaffOnlyAdminMiddleware:
    """A signed-in volunteer asking for /admin/ is refused, not sent to a login form.

    Django's default is to redirect an authenticated non-staff user to the admin
    login page, which then tells them to enter "the correct username and
    password for a staff account". They are already signed in correctly — the
    account simply is not staff — so the redirect is both a lie and a loop, and
    it reads as "log in again" rather than "this is not for you".

    D21's first requirement is explicit that this has to be a refusal rather
    than an absence of links, so a redirect does not satisfy it however
    effectively it keeps people out. See goal.md D21 and phase-b.md's
    acceptance list, both of which say 403 in as many words.

    ⚠️ Anonymous visitors are left alone deliberately: they may well be staff
       who have not signed in yet, and Django's redirect is exactly right for
       them. The rule is about accounts that are signed in and still not staff.

    Middleware rather than a custom AdminSite: subclassing AdminSite is the
    layer D18 marks as the most likely to break on a Django upgrade and the
    first thing thrown away when the front end arrives. This is one condition
    that happens to be about a URL prefix, and it survives both.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (
            request.path.startswith(ADMIN_PREFIX)
            and request.path not in ALWAYS_ALLOWED
            and request.user.is_authenticated
            and not request.user.is_staff
        ):
            raise PermissionDenied(
                "The admin is for staff accounts. Everyone else uses the "
                "self-service pages at /events/."
            )
        return self.get_response(request)
