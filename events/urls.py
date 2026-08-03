from django.urls import path

from . import views

app_name = "events"

urlpatterns = [
    # B9 — the volunteer's own pages
    path("events/", views.event_list, name="event_list"),
    # C0.2.3 — R1. `past` before `<int:pk>`, same reason as `new` below.
    path("events/past/", views.past_events, name="past_events"),
    path("events/<int:pk>/", views.event_detail, name="event_detail"),
    path("events/<int:pk>/signup/", views.event_signup, name="event_signup"),
    path("me/participations/", views.my_participations, name="my_participations"),
    path(
        "me/participations/<int:pk>/cancel/",
        views.participation_cancel,
        name="participation_cancel",
    ),
    # B10 — the ministry admin's pages. `new` before `<int:pk>` is not an
    # accident: the other order would try to read "new" as a primary key.
    # C0.2.4 — the entrance to everything below. `manage` before `<int:pk>`.
    path("events/manage/", views.event_manage_list, name="event_manage_list"),
    path("events/new/", views.event_create, name="event_create"),
    # C0.2.2 — the only way to move an event, and the only way to mark one
    # completed. Its absence is what left services.reschedule() unreachable.
    path("events/<int:pk>/edit/", views.event_update, name="event_update"),
    path("events/<int:pk>/roles/", views.event_roles, name="event_roles"),
    path("events/roles/<int:pk>/delete/", views.role_delete, name="role_delete"),
    path(
        "events/<int:pk>/registrations/",
        views.event_registrations,
        name="event_registrations",
    ),
    path("events/<int:pk>/attendance/", views.event_attendance, name="event_attendance"),
    path("events/<int:pk>/report/", views.event_report, name="event_report"),
    # B11 — P6. Same permission as attendance: sending is a write.
    path("events/<int:pk>/notify/", views.event_notify, name="event_notify"),
]
