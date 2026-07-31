"""The project's first business routes, included from config/urls.py."""

from django.urls import path

from . import views

app_name = "contact"

urlpatterns = [
    path("contacts/merge/", views.contact_merge, name="contact_merge"),
]
