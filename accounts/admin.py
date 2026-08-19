from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Django's UserAdmin, rebuilt around the email address as the login name.

    ⚠️ Every list, fieldset and ordering below had to be restated rather than
       extended. `BaseUserAdmin` names `username` in `fieldsets`,
       `add_fieldsets`, `list_display`, `search_fields` and `ordering`, and a
       ModelAdmin naming a field the model does not have mostly fails loudly:
       `manage.py check` reports `admin.E033` for `ordering` and `admin.E108`
       for `list_display`, and the fieldsets blow up when the page is opened.

       **`search_fields` is the exception, and it is the one worth knowing
       about** — measured rather than assumed: a bogus entry there passes
       `manage.py check` completely and raises only when somebody types in the
       search box. Hence the test that actually searches.

    ⚠️ D14's ConstraintErrorFieldMixin is deliberately **not** applied to User.
       `email` is `unique=True` as well as covered by the `user_email_taken`
       constraint, so Django's own uniqueness validation catches a duplicate
       first and already reports it under the email box. The constraint is the
       floor under the paths that run no form at all, and a violation from there
       has no form to be moved onto.
    """

    # Autocomplete rather than a plain dropdown: ContactAdmin already declares
    # search_fields, and the contact table will be far too long to scroll.
    autocomplete_fields = ["contact"]

    ordering = ["email"]
    list_display = ["email", "first_name", "last_name", "email_verified", "is_staff"]
    # 想找出「注册了但没验证邮箱」的人，只有这一个入口 —— 那批人登不进来，
    # 也不会自己来问。
    list_filter = [*BaseUserAdmin.list_filter, "email_verified"]
    search_fields = ["email", "first_name", "last_name"]

    fieldsets = (
        (None, {"fields": ["email", "password"]}),
        ("Personal info", {"fields": ["first_name", "last_name"]}),
        ("Permissions", {
            # ⚠️ `email_verified` 在这里而不是在「Personal info」里：它决定这个
            #    账号能不能登录，那是一条权限。
            #    ⚠️ 它是**可改的**，而且必须是：唯一能把「注册时把地址打错了」
            #       的人救回来的办法，就是有人在这里改地址、然后让他重新验证。
            #       改动会留在 admin 的操作日志里。
            "fields": ["is_active", "email_verified", "is_staff", "is_superuser",
                       "groups", "user_permissions"],
        }),
        ("Important dates", {"fields": ["last_login", "date_joined"]}),
        ("Foundation record", {
            "fields": ["contact"],
            "description": "The person this account belongs to. Leave empty for "
                           "technical accounts that do not correspond to a real person.",
        }),
    )

    # ⚠️ `usable_password` is Django 5.1's field on the add form (the "set no
    #    password" option). Dropping it from a rewritten add_fieldsets removes
    #    the choice silently, so it is named here on purpose.
    add_fieldsets = (
        (None, {
            "classes": ["wide"],
            "fields": ["email", "usable_password", "password1", "password2"],
        }),
    )
