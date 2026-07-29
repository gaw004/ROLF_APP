from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from contact.admin import ContactAdmin
from contact.models import Contact
from core.admin import InEffectFilter

from .models import Assignment, EmploymentType, Ministry, Position


class PositionInline(admin.TabularInline):
    """Set up a ministry's boxes while creating the ministry.

    Only the columns that make sense before anybody is hired; the manager line
    and the description are set on the position's own page, where the dropdown
    can search the whole chart.
    """

    model = Position
    extra = 0
    fields = ["name", "code", "kind", "is_leader", "is_active"]
    show_change_link = True


@admin.register(Ministry)
class MinistryAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "is_active", "founded_on"]
    list_filter = ["is_active"]
    # Also drives the autocomplete on Position.ministry.
    search_fields = ["name", "code"]
    inlines = [PositionInline]

    def get_readonly_fields(self, request, obj=None):
        # Same split as RelationshipType: editable while adding, frozen after.
        # The admin covers the admin; Ministry.clean() covers everything else.
        return ["code"] if obj else []


@admin.register(EmploymentType)
class EmploymentTypeAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name", "code"]

    def get_readonly_fields(self, request, obj=None):
        return ["code"] if obj else []


class VacantFilter(admin.SimpleListFilter):
    """Posts that still exist and have nobody in them today.

    A vacancy that nobody can see is the same as not having built this table,
    which is why the filter ships alongside the query. Both branches are one
    call to a QuerySet method — the 3 rules that make up "vacant" are written
    once, on PositionQuerySet (D18).
    """

    title = "空缺"
    parameter_name = "vacant"

    def lookups(self, request, model_admin):
        return [("yes", "空缺"), ("no", "有人在任")]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.vacant()
        if self.value() == "no":
            return queryset.exclude(pk__in=queryset.model.objects.vacant())
        return queryset


@admin.register(Position)
class PositionAdmin(SimpleHistoryAdmin):
    """SimpleHistoryAdmin: who moved which box under whom, and when."""

    list_display = [
        "name",
        "code",
        "ministry",
        "kind",
        "is_leader",
        "is_active",
        "reports_to",
    ]
    list_filter = ["ministry", "kind", "is_leader", "is_active", VacantFilter]
    # Needed by the autocomplete on Position.reports_to, and by the one on
    # Assignment.position.
    search_fields = ["name", "code"]
    autocomplete_fields = ["ministry", "reports_to"]
    # Both foreign keys are in list_display, so both have to be joined or the
    # changelist runs two extra queries per row.
    list_select_related = ["ministry", "reports_to"]

    def get_readonly_fields(self, request, obj=None):
        return ["code"] if obj else []


@admin.register(Assignment)
class AssignmentAdmin(SimpleHistoryAdmin):
    list_display = [
        "contact", "position", "employment_type", "status",
        "start_date", "end_date", "is_currently_active",
    ]
    list_filter = [InEffectFilter, "status", "position__ministry", "position__kind"]
    search_fields = [
        "contact__legal_last_name", "contact__legal_first_name",
        "contact__preferred_name", "position__name",
    ]
    autocomplete_fields = ["contact", "position", "employment_type"]
    # Both, not one: each of them is a column in the changelist.
    list_select_related = ["contact", "position"]

    @admin.display(boolean=True, description="生效中")
    def is_currently_active(self, obj):
        return obj.is_currently_active


class AssignmentInline(admin.TabularInline):
    """A person's tenures, on their own page."""

    model = Assignment
    extra = 0
    autocomplete_fields = ["position", "employment_type"]
    fields = ["position", "employment_type", "status", "start_date", "end_date"]


# Contact's page gains this app's inline here, in this app, rather than
# contact/admin.py importing org: the dependency runs org -> contact (D17), and
# reversing it in the admin would make contact impossible to install alone.
# Re-registering a subclass is the only hook Django offers for that, and both
# files are disposable configuration anyway (D18).
admin.site.unregister(Contact)


@admin.register(Contact)
class ContactWithAssignmentsAdmin(ContactAdmin):
    inlines = [*ContactAdmin.inlines, AssignmentInline]
