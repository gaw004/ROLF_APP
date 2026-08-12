"""The pages that belong to no single app. So far: the public front page."""

from django.shortcuts import render

from .models import HomePage


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

    ⚠️ `current()`, not `load()`. The latter is a get_or_create, and this is a
       public page — putting a write on the busiest read path in the site means
       every anonymous visitor takes a write lock on the same single row.
       Only the admin creates it, which is the one place creating means
       something.
    """
    return render(request, "core/home.html", {"page": HomePage.current()})
