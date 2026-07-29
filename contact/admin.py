from django.contrib import admin, messages
from django.urls import reverse
from django.utils.html import format_html
from simple_history.admin import SimpleHistoryAdmin

from .forms import ContactAdminForm
from .models import Contact, EmergencyContact, Language, Relationship, RelationshipType


class RelationshipAsAInline(admin.TabularInline):
    """This contact's relationships, read-only, as seen from their side.

    Read-only is what makes the pair of inlines work at all: with no forms there
    is no formset plumbing, and nothing needs the parent object. Entry happens on
    /relationships/add/ instead (goal.md D18 — an inline form cannot get at the
    contact whose page it is on, and Phase C's HTMX will not use formsets).
    """

    model = Relationship
    # Relationship has two FKs to Contact, so Django needs to be told which one
    # this inline hangs off of.
    fk_name = "contact_a"
    verbose_name_plural = "关系"
    extra = 0
    can_delete = True          # deleting needs no sense of direction
    fields = ["reading", "other_party", "start_date", "end_date"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="关系")
    def reading(self, obj):
        return obj.label_from(self.fk_name)

    @admin.display(description="对方")
    def other_party(self, obj):
        return obj.contact_b if self.fk_name == "contact_a" else obj.contact_a


class RelationshipAsBInline(RelationshipAsAInline):
    """The other half. Without it, recording "王强 parent of 小明" leaves 小明's
    page showing nothing at all — the row exists, the second label was just
    never read."""

    fk_name = "contact_b"
    verbose_name_plural = "关系（对方那一侧）"


class EmergencyContactInline(admin.TabularInline):
    """Three boxes: name, phone, relationship. No lookup, no matching, no jump.

    Everything the old design needed here — creating a reference-only Contact on
    the fly, preselecting a match, a safety valve for the ambiguous cases — is
    simply gone. With no identity to establish there is nothing to get wrong.
    """

    model = EmergencyContact
    extra = 0
    autocomplete_fields = ["relationship_type"]


@admin.register(Contact)
class ContactAdmin(SimpleHistoryAdmin):
    """SimpleHistoryAdmin rather than ModelAdmin: adds the History button."""

    form = ContactAdminForm
    list_display = ["__str__", "contact_type", "email", "phone", "is_active"]
    list_filter = ["contact_type", "is_active", "gender", "address_country"]
    search_fields = [
        "legal_first_name", "legal_last_name", "preferred_name",
        "organization_name", "email",
    ]
    autocomplete_fields = ["preferred_language"]
    inlines = [EmergencyContactInline, RelationshipAsAInline, RelationshipAsBInline]
    readonly_fields = ["add_relationship"]

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
        ("Status", {"fields": ["is_active", "notes", "force_save"]}),
        ("Relationships", {"fields": ["add_relationship"]}),
    ]

    def response_add(self, request, obj, post_url_continue=None):
        self._warn_about_namesakes(request, obj)
        return super().response_add(request, obj, post_url_continue)

    def response_change(self, request, obj):
        self._warn_about_namesakes(request, obj)
        return super().response_change(request, obj)

    def _warn_about_namesakes(self, request, obj):
        """Mention other contacts with this name. Never blocks — see D18.

        Rendering a message is presentation and belongs here; deciding who
        counts as a namesake is a model classmethod, so Phase C's page shows the
        same warning without recomputing anything. Only the stronger signal
        (same name AND phone) blocks, and that happens in the form.
        """
        if obj.contact_type != Contact.ContactType.INDIVIDUAL:
            return
        namesakes = Contact.find_same_name(
            last_name=obj.legal_last_name,
            first_name=obj.legal_first_name,
            exclude_pk=obj.pk,
        )
        if namesakes.exists():
            self.message_user(
                request,
                f"系统里还有 {namesakes.count()} 位同名的联系人（号码不同）。"
                "重名是合法的，这里只是提醒。",
                messages.WARNING,
            )

    @admin.display(description="")
    def add_relationship(self, obj):
        """Link to the entry page, carrying whose page we are on."""
        if obj is None or obj.pk is None:
            return "保存之后才能添加关系。"
        url = reverse("contact:relationship_add")
        return format_html(
            '<a class="button" href="{}?subject={}">添加关系</a>', url, obj.pk)

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
    list_display = [
        "name_a_to_b", "name_b_to_a", "code",
        "is_symmetric", "usable_as_emergency_contact",
    ]
    list_filter = ["is_symmetric", "usable_as_emergency_contact"]
    search_fields = ["name_a_to_b", "name_b_to_a", "code"]

    def get_readonly_fields(self, request, obj=None):
        # Editable when adding, frozen afterwards: code is the anchor every
        # lookup in the codebase matches on, so renaming it breaks them silently.
        # This only covers the admin — RelationshipType.clean() is what catches
        # a script or a shell doing the same thing.
        return ["code"] if obj else []


class InEffectFilter(admin.SimpleListFilter):
    """In effect / ended, derived from the dates. Replaces the is_active field.

    Every option here is one call to a QuerySet method — no date arithmetic in
    the filter itself (D18). The old list_filter = ["is_active"] is precisely
    what made that field dangerous: it could be filtered on independently of the
    dates, and confidently return the wrong rows.
    """

    title = "生效中"
    parameter_name = "in_effect"

    def lookups(self, request, model_admin):
        return [("yes", "生效中"), ("no", "已结束或未开始")]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.active()
        if self.value() == "no":
            return queryset.exclude(pk__in=queryset.model.objects.active())
        return queryset


@admin.register(Relationship)
class RelationshipAdmin(admin.ModelAdmin):
    list_display = [
        "contact_a", "relationship_type", "contact_b",
        "start_date", "end_date", "is_currently_active",
    ]
    list_filter = [InEffectFilter, "relationship_type"]
    autocomplete_fields = ["contact_a", "contact_b", "relationship_type"]
    list_select_related = ["contact_a", "contact_b", "relationship_type"]

    @admin.display(boolean=True, description="生效中")
    def is_currently_active(self, obj):
        return obj.is_currently_active
