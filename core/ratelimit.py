"""Who is asking, and how often they may. Used by the registration page.

Registration is the one write on this site an anonymous stranger can do, and it
writes **two** rows every time (a User and a Contact — P1). A script pointed at
it fills the contact table with people who look exactly like real ones, which is
the property this project chose deliberately (D4) and cannot undo:
`merge_contacts()` treats duplicates, not rubbish.

⚠️ **What this does and does not buy.** It stops the automated case — a script
   wants thousands of accounts and gets twenty. It does **not** stop somebody
   registering by hand with an address they do not own; that needs the address
   to be confirmed before the account works, which needs real email delivery
   (SES, C3.3) and is therefore deferred rather than half-built. Written into
   phase-c.md's known gaps so the remaining hole is on the record.
"""

from django.conf import settings


def client_ip(request):
    """The address to count against. Wired up as RATELIMIT_IP_META_KEY.

    ⚠️ This function exists because the obvious version **fails silently in
       production**. Behind a reverse proxy — which is every managed platform,
       Render included — `REMOTE_ADDR` is the proxy, so every visitor on earth
       shares one bucket: the first twenty registrations of the hour exhaust the
       limit for everybody, and nothing reports that the limit is measuring the
       wrong thing.

    ⚠️ And the naive fix is worse than the bug: trusting the whole
       `X-Forwarded-For` header lets a caller invent a new client address per
       request, which is unlimited registrations wearing a rate limiter. The
       header is a list that each hop **appends** to, so the entry we may
       believe is the **last** one — added by our own proxy — and never the
       first, which is whatever the caller typed.

    Hence the switch: off by default, so a development machine and anything run
    without a proxy in front of it counts REMOTE_ADDR and is right. It has to be
    turned on deliberately, by the deployment that actually has a proxy.
    """
    if getattr(settings, "TRUST_PROXY_CLIENT_IP", False):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
        if hops:
            return hops[-1]
    return request.META.get("REMOTE_ADDR", "")


def site_wide(group, request):
    """A single shared bucket, for the "how many in total" limit.

    A constant key, which is the documented way to say "count every request
    together" — the per-IP limit cannot see a thousand addresses each asking
    once, and that is exactly what a botnet looks like.
    """
    return "site"


def _unlimited_for_signed_in(request):
    """Is this request exempt from the registration limits entirely?

    ⚠️ Somebody already signed in cannot register — the view redirects them —
       but the limit is counted by a decorator, which runs first. So without this
       an account holder could POST to /register/ in a loop and **exhaust the
       site-wide allowance for everybody else**: a denial of service on the
       registration page, from inside, costing one account to set up. Returning
       no rate at all is the library's documented way to skip a request, and it
       skips the counting as well as the check.
    """
    return getattr(request.user, "is_authenticated", False)


def registration_rate_per_ip(group, request):
    """Read the per-IP rate at request time, not at import time.

    ⚠️ A callable rather than the plain string the decorator also accepts, so
       that raising the limit for a signup drive is an environment variable and
       not a deploy. That is not a nicety: the day this bites will be the day
       forty volunteers are standing in a hall being told to sign up, and the
       person who has to fix it will be holding a phone.
    """
    if _unlimited_for_signed_in(request):
        return None
    return settings.REGISTRATION_RATELIMIT_PER_IP


def registration_rate_site(group, request):
    """The same, for the whole-site limit."""
    if _unlimited_for_signed_in(request):
        return None
    return settings.REGISTRATION_RATELIMIT_SITE


def password_reset_rate_per_ip(group, request):
    """Per-IP limit on asking for a reset link.

    ⚠️ What is being protected here is **not** the accounts — the reset link
       goes to the address on file, so guessing addresses gains nothing. It is
       the mail allowance. On an open-registration site anybody can make the
       application send mail to any address they like, as often as they like,
       and every one of those comes out of the same daily quota as the
       notifications and the resets that real people need. Two things break at
       once and neither of them errors: the quota runs out, and the domain's
       reputation goes with it, because mail nobody asked for is what a spam
       complaint is.
    """
    return settings.PASSWORD_RESET_RATELIMIT_PER_IP


def password_reset_rate_site(group, request):
    """The same, counted across everybody.

    The per-IP bucket cannot see a thousand addresses asking once each, and
    against a shared daily allowance that is the shape that actually empties it.
    """
    return settings.PASSWORD_RESET_RATELIMIT_SITE
