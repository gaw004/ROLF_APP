"""⚠️ Mounted under `notices/` by config/urls.py, so nothing here repeats it.

Same call `memories/` made and for the reason written there: this app owns a
small area including its own manage page, rather than a single distinct noun at
the root the way `events/` and `login/` are.
"""

from django.urls import path

from . import views

app_name = "notices"

urlpatterns = [
    path("", views.notice_list, name="notice_list"),
    # ⚠️ The literal segments come before `<int:pk>`, the rule events/urls.py
    #    follows: "manage" and "new" are not integers, but the ordering is what
    #    keeps that from being something a reader has to work out.
    path("manage/", views.notice_manage_list, name="notice_manage_list"),
    path("new/", views.notice_create, name="notice_create"),
    path("<int:pk>/edit/", views.notice_update, name="notice_update"),
    path("<int:pk>/put-up/", views.notice_publish, name="notice_publish"),
    path("<int:pk>/take-down/", views.notice_take_down, name="notice_take_down"),
]
