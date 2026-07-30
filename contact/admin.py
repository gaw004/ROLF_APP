from django.contrib import admin, messages
from django.urls import reverse
from django.utils.html import format_html
from simple_history.admin import SimpleHistoryAdmin

from core.admin import InEffectFilter

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


class PossibleDuplicateFilter(admin.SimpleListFilter):
    """Contacts sharing a name and a phone number with another row.

    Presentation only: it calls one QuerySet method and computes nothing itself
    (D18). Merging happens on /contacts/merge/, which is a page of our own — an
    admin action would have to inherit admin templates for its confirmation step.
    """

    title = "疑似重复（同名同号）"
    parameter_name = "duplicates"

    def lookups(self, request, model_admin):
        return [("yes", "只看疑似重复")]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.possible_duplicates()
        return queryset


class MinorFilter(admin.SimpleListFilter):
    """Minor / adult / birth date unknown.

    Three options, and the third is not optional: unknown has to be visible
    rather than swallowed into "adult", or a minor with no date on file drops
    silently off the list of people whose parents need calling.

    ⚠️ Every option calls exactly one QuerySet method. No date arithmetic here
       — the 18-year threshold, the leap-year case and the D16 timezone rule are
       written once, on ContactQuerySet, so Phase C's "notify every minor's
       parents" reuses them instead of recomputing them (D18).

    list_filter = ["is_minor"] is not an option: is_minor is a property and
    cannot be resolved by the ORM. Nor can an annotation replace this filter —
    list_filter resolves names against model fields and raises admin.E116 for an
    annotation, in every Django version.
    """

    title = "未成年"
    parameter_name = "minor"

    def lookups(self, request, model_admin):
        return [("yes", "未成年"), ("no", "成年"), ("unknown", "生日未知")]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.minors()
        if self.value() == "no":
            return queryset.adults()
        if self.value() == "unknown":
            return queryset.birth_date_unknown()
        return queryset


@admin.register(Contact)
class ContactAdmin(SimpleHistoryAdmin):
    """SimpleHistoryAdmin rather than ModelAdmin: adds the History button."""

    form = ContactAdminForm
    # merge_link is not here — get_list_display() appends it, because it needs
    # the whole page's duplicate pairs looked up once. See there.
    list_display = ["__str__", "contact_type", "email", "phone", "is_active"]
    list_filter = [
        "contact_type", "is_active", "gender", "address_country",
        PossibleDuplicateFilter, MinorFilter,
    ]
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

    def get_list_display(self, request):
        """Appends the merge column, with the page's duplicate pairs read once.

        merge_link used to be an ordinary list_display entry that called
        find_exact_duplicates() for every row — one query per row, on every
        changelist, whether or not the duplicates filter was on. At Django's
        default hundred rows a page that is a hundred queries to render a
        column that is almost always blank.

        Which contact pairs with which is a QuerySet method (D18); this only
        renders it. The result is cached on the request because Django asks for
        list_display more than once while building a changelist.
        """
        if not hasattr(request, "_contact_duplicate_partners"):
            request._contact_duplicate_partners = Contact.objects.duplicate_partners()
        partners = request._contact_duplicate_partners

        @admin.display(description="合并")
        def merge_link(obj):
            other_pk = partners.get(obj.pk)
            if other_pk is None:
                return ""
            return format_html(
                '<a href="{}?keep={}&drop={}">合并掉 #{}</a>',
                reverse("contact:contact_merge"), obj.pk, other_pk, other_pk,
            )

        return [*super().get_list_display(request), merge_link]

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
