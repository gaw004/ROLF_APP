from django.urls import path

from . import views

app_name = "events"

urlpatterns = [
    # B9 — the volunteer's own pages
    # ⚠️ This one now lists **today's events and everything after**, whatever
    #    their status — not just what is open (2026-08-17). `events/past/` was
    #    removed in the same change; nothing replaced the route, so an old link
    #    to it 404s rather than landing on a page that answers a different
    #    question while looking like the one they bookmarked.
    path("events/", views.event_list, name="event_list"),
    path("events/<int:pk>/", views.event_detail, name="event_detail"),
    path("events/<int:pk>/signup/", views.event_signup, name="event_signup"),
    path("me/participations/", views.my_participations, name="my_participations"),
    # D28 — the two halves of a scan. `confirm` comes first for the same reason
    # `new` does below: the token pattern matches any string, so the other order
    # would read the word "confirm" as a token and refuse it as expired.
    path("events/checkin/confirm/", views.checkin_confirm, name="checkin_confirm"),
    path("events/checkin/<str:token>/", views.checkin_scan, name="checkin_scan"),
    path(
        "me/participations/<int:pk>/cancel/",
        views.participation_cancel,
        name="participation_cancel",
    ),
    # B10 — the ministry admin's pages. `new` before `<int:pk>` is not an
    # accident: the other order would try to read "new" as a primary key.
    # C0.2.4 — the entrance to everything below. `manage` before `<int:pk>`.
    path("events/manage/", views.event_manage_list, name="event_manage_list"),
    # D27 — the full report, same filters, nothing truncated. `manage/report`
    # before `manage/<int:pk>` would matter if the latter existed; it does not,
    # and this comment is here so that adding it does not break this route.
    path("events/manage/report/", views.ministry_report_page, name="ministry_report"),
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
    # D28 — the iPad page and the endpoint that feeds it. ⚠️ The token endpoint
    # is gated on can_manage_event; without that check the whole rotating-code
    # scheme is decoration, because any volunteer could fetch a live code.
    path("events/<int:pk>/checkin-qr/", views.checkin_display, name="checkin_display"),
    path(
        "events/<int:pk>/checkin-qr/token/",
        views.checkin_token,
        name="checkin_token",
    ),
    path("events/<int:pk>/report/", views.event_report, name="event_report"),
    # B11 — P6. Same permission as attendance: sending is a write.
    path("events/<int:pk>/notify/", views.event_notify, name="event_notify"),
]
