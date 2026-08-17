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
    # C3.2 — the way back in. Four routes because Django's flow is four pages
    # (ask, told, set, done), and the two in the middle are the ones that must
    # not be merged: "we have sent a link" is shown to somebody who typed an
    # address that may not exist, and merging it with the form would say
    # whether it did.
    #
    # ⚠️ The names are ours, but the **shape** of the confirm route is Django's:
    #    `<uidb64>/<token>` is what PasswordResetConfirmView reads, and its own
    #    email template builds the link from exactly those two names.
    path("password-reset/", views.VolunteerPasswordResetView.as_view(),
         name="password_reset"),
    path("password-reset/sent/", views.VolunteerPasswordResetDoneView.as_view(),
         name="password_reset_done"),
    path("password-reset/<uidb64>/<token>/",
         views.VolunteerPasswordResetConfirmView.as_view(),
         name="password_reset_confirm"),
    path("password-reset/done/", views.VolunteerPasswordResetCompleteView.as_view(),
         name="password_reset_complete"),
]
