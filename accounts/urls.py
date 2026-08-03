from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("register/", views.register, name="register"),
    path("login/", views.VolunteerLoginView.as_view(), name="login"),
    path("logout/", views.VolunteerLogoutView.as_view(), name="logout"),
    # C0.2.5 — until this existed a wrong birth date, email or phone was
    # uncorrectable from outside the admin site.
    path("me/profile/", views.profile, name="profile"),
]
