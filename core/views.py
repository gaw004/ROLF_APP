"""The pages that belong to no single app: the public front page, and a pulse."""

from django.http import HttpResponse
from django.shortcuts import render

from .health import HEALTH_PATH  # noqa: F401 — re-exported for the URLconf
from .models import HomePage


def healthz(request):
    """A pulse for the platform. 200 and nothing else.

    ⚠️ **It touches no database and renders no template, deliberately.** Render
       restarts an instance whose health check stops answering, so anything this
       depends on becomes a thing that can take the whole site down. A database
       hiccup would kill every instance rather than showing a slow page — and it
       would take down the very pages that could report the trouble. The
       question the platform is asking is "can this process answer?", not "is
       everything downstream perfect".

    ⚠️ And it exists at all because of SECURE_SSL_REDIRECT. The platform checks
       the instance directly over plain HTTP, with no X-Forwarded-Proto on the
       request, so Django rightly answers 301 to https — which the check reads
       as a failed deploy. prod.py exempts this one path from that redirect
       (SECURE_REDIRECT_EXEMPT); the front page keeps redirecting, as it must.
    """
    return HttpResponse("ok", content_type="text/plain")


def home(request):
    """The front page. **Public, and the same page for everybody.**

    ⚠️ This replaces what 03-roadmap.md's C3.1 planned, and the change is
       recorded in D25 rather than made quietly. That plan was a router: send a
       volunteer to the event list, a ministry admin to their management list,
       a foundation admin to the ministry list. Useful, and not a front page —
       a link shared with somebody who had never heard of the foundation would
       have opened a login form.

       Everybody sees the same thing now, signed in or not (2026-08-05 拍板).
       What changes with the account is one word in the top-right corner and
       which entries the menu offers.

    ⚠️ The only public pages are this one, login and register. Everything else
       still requires a session — the boundary did not move, it grew by one.

    ⚠️ Not `load()`. That one is a get_or_create, and this is a public page —
       putting a write on the busiest read path in the site means every
       anonymous visitor takes a write lock on the same single row. Only the
       admin creates it, which is the one place creating means something.

    ⚠️ `for_request()` rather than `current()` (2026-08-13) because the
       `site_appearance` context processor wants the same row and runs on every
       page in the site, this one included. Two identical SELECTs on the front
       page, and neither call site could see the other. The method caches on the
       request, so whichever runs first pays.
    """
    return render(request, "core/home.html", {"page": HomePage.for_request(request)})
