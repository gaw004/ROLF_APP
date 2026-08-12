from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("register/", views.register, name="register"),
    # ⚠️ It **prefills** the registration form and creates nothing. The account
    #    is still made by register() above, with a password. See accounts/google.py.
    path("register/google/", views.register_with_google, name="register_with_google"),
    path("login/", views.VolunteerLoginView.as_view(), name="login"),
    path("logout/", views.VolunteerLogoutView.as_view(), name="logout"),
    # C0.2.5 — until this existed a wrong birth date, email or phone was
    # uncorrectable from outside the admin site.
    path("me/profile/", views.profile, name="profile"),
    # ⚠️ Both the modal on the profile page and a whole page of its own —
    #    one URL, one implementation. See the view.
    path("me/password/", views.password_change, name="password_change"),
]
