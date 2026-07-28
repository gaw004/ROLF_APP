from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Django's stock UserAdmin plus the link to Contact."""

    # Autocomplete rather than a plain dropdown: ContactAdmin already declares
    # search_fields, and the contact table will be far too long to scroll.
    autocomplete_fields = ["contact"]

    fieldsets = BaseUserAdmin.fieldsets + (
        ("Foundation record", {
            "fields": ["contact"],
            "description": "The person this account belongs to. Leave empty for "
                           "technical accounts that do not correspond to a real person.",
        }),
    )
