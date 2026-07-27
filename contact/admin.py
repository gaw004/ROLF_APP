from django.contrib import admin

from .forms import ContactAdminForm
from .models import Contact, Language, Relationship, RelationshipType


class RelationshipInline(admin.TabularInline):
    model = Relationship
    # Relationship has two FKs to Contact, so Django needs to be told which one
    # this inline hangs off of.
    fk_name = "contact_a"
    extra = 0
    autocomplete_fields = ["contact_b", "relationship_type"]


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    form = ContactAdminForm
    list_display = ["__str__", "contact_type", "email", "phone", "is_active"]
    list_filter = ["contact_type", "is_active", "gender", "address_country"]
    search_fields = [
        "legal_first_name", "legal_last_name", "preferred_name",
        "organization_name", "email",
    ]
    autocomplete_fields = ["preferred_language"]
    inlines = [RelationshipInline]

    fieldsets = [
        (None, {"fields": ["contact_type"]}),
        ("Name", {
            # Only the fields matching the selected contact type are shown; the
            # others are hidden by contact_type_toggle.js and cleared on save.
            "fields": [
                "legal_first_name", "legal_last_name",
                "preferred_name", "organization_name",
            ],
        }),
        ("Contact info", {"fields": ["email", "phone"]}),
        ("Demographics & preferences", {
            "fields": [
                "gender", "birth_date",
                "preferred_language", "preferred_communication_method",
            ],
        }),
        ("Address", {
            # Country comes first: it decides whether the state field is a US
            # dropdown or a free-text region box (see address_state_toggle.js).
            "fields": [
                "address_country",
                "address_street", "address_city",
                "address_state", "address_postal_code",
            ],
        }),
        ("Status", {"fields": ["is_active", "notes"]}),
    ]

    class Media:
        js = [
            "contact/admin/contact_type_toggle.js",
            "contact/admin/address_state_toggle.js",
        ]


@admin.register(Language)
class LanguageAdmin(admin.ModelAdmin):
    list_display = ["display_name", "code", "name", "language_type", "pin_rank"]
    list_filter = ["language_type"]
    # Also drives the searchable autocomplete on Contact.preferred_language.
    search_fields = ["display_name", "name", "alt_names", "code"]
    # Pinned languages first, so English / Mandarin / Cantonese head the dropdown.
    ordering = ["-pin_rank", "display_name"]


@admin.register(RelationshipType)
class RelationshipTypeAdmin(admin.ModelAdmin):
    list_display = ["name_a_to_b", "name_b_to_a", "description"]
    search_fields = ["name_a_to_b", "name_b_to_a"]


@admin.register(Relationship)
class RelationshipAdmin(admin.ModelAdmin):
    list_display = ["contact_a", "relationship_type", "contact_b", "is_active"]
    list_filter = ["is_active", "relationship_type"]
    autocomplete_fields = ["contact_a", "contact_b", "relationship_type"]
