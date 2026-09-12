"""A pulse for the platform, and nothing else.

⚠️ `home()` lived here until 2026-09-11 and has moved to `dashboard.views.front`
   (D44). It moved because `/` now carries the dashboard below the fold for
   anybody signed in, and rendering that here would mean the root of the
   dependency chain importing its own three downstream apps — the exact import
   D17 forbids and D42 第一节 opened a whole app to avoid. The URL name `home`
   did not change, so every `{% url 'home' %}` in the templates is untouched.
"""

from django.http import HttpResponse


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
