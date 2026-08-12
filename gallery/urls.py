from django.urls import path

from . import views

app_name = "gallery"

urlpatterns = [
    path("", views.wall, name="wall"),
    path("manage/", views.manage, name="manage"),
    path("<int:pk>/delete/", views.delete, name="delete"),
]
