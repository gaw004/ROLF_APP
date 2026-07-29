from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import EmploymentType, Ministry, Position


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
    list_filter = ["ministry", "kind", "is_leader", "is_active"]
    # Needed by the autocomplete on Position.reports_to, and later by the one on
    # Assignment.position.
    search_fields = ["name", "code"]
    autocomplete_fields = ["ministry", "reports_to"]
    # Both foreign keys are in list_display, so both have to be joined or the
    # changelist runs two extra queries per row.
    list_select_related = ["ministry", "reports_to"]

    def get_readonly_fields(self, request, obj=None):
        return ["code"] if obj else []
