"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from core.health import HEALTH_PATH
from core.views import healthz, home

urlpatterns = [
    # The public front page. ⚠️ No login_required: this is the one page a link
    # shared with a stranger has to open. See D25.
    path('', home, name='home'),
    # ⚠️ Before anything that could shadow it, and outside every app: the
    #    platform's health check has to answer even while the rest of the site
    #    is having a bad day. See core/views.py::healthz.
    path(HEALTH_PATH, healthz, name='healthz'),
    path('admin/', admin.site.urls),
    # Staff-only pages pushed out of the admin by D18's shape trigger
    # (the contact merge page).
    path('', include('contact.urls')),
    # The outward-facing half: volunteers register, browse and sign up; a
    # ministry's admins publish, check people in and notify. None of it goes
    # through the admin — volunteers must not be able to reach it at all (D21).
    path('', include('accounts.urls')),
    path('', include('events.urls')),
    path('', include('org.urls')),
    # ⚠️ Prefixed, unlike the four above. Those all mount at the root because
    #    their paths are already distinct nouns ("events/", "login/"); this one
    #    owns a whole small area including its own manage page, and "memories/"
    #    is the thing people will type.
    path('memories/', include('gallery.urls')),
    # ⚠️ Prefixed for the same reason as the line above, and by the same test:
    #    it owns a small area with its own manage page, rather than being one
    #    distinct noun at the root.
    path('notices/', include('notices.urls')),
    # ⚠️ **After accounts**, which owns `me/profile/`. The two do not actually
    #    collide — this one matches `me/` exactly — but the order is what saves
    #    the next reader from having to prove that to themselves.
    #
    # It is the signed-in landing page, and it is deliberately *not* `/`:
    # D25 kept `/` public and gave the test for it ("would you send this link to
    # somebody with no account?"). This one needs a session, so it is not that
    # page — and `/` still does not redirect anybody anywhere.
    path('me/', include('dashboard.urls')),
]

# ⚠️ Development only, and django.conf.urls.static.static() enforces that by
#    returning nothing when DEBUG is off. In production these files are served
#    by the object store, not by Django — serving user uploads through the
#    application is both slow and the shape of problem this project has no
#    reason to take on.
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
