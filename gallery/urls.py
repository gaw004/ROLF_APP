from django.urls import path

from . import views

app_name = "gallery"

urlpatterns = [
    path("", views.wall, name="wall"),
    path("manage/", views.manage, name="manage"),
    # ⚠️ Before the `<int:pk>` route only for readability — the two cannot
    #    collide, because "remove" is not an integer.
    path("remove/", views.remove_selected, name="remove_selected"),
    path("<int:pk>/delete/", views.delete, name="delete"),
]
