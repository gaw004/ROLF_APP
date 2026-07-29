"""The project's first business routes, included from config/urls.py."""

from django.urls import path

from . import views

app_name = "contact"

urlpatterns = [
    path("relationships/add/", views.relationship_create, name="relationship_add"),
]
