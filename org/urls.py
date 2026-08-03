from django.urls import path

from . import views

app_name = "org"

urlpatterns = [
    # C0.2.4 — P5 had no entrance: the grant page needs a pk and nothing linked to it.
    path("ministries/", views.ministry_list, name="ministry_list"),
    path("ministries/<int:pk>/admins/", views.ministry_admin_page, name="ministry_admins"),
]
