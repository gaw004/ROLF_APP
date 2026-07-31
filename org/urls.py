from django.urls import path

from . import views

app_name = "org"

urlpatterns = [
    path("ministries/<int:pk>/admins/", views.ministry_admin_page, name="ministry_admins"),
]
