from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from contact.admin import ContactAdmin
from contact.models import Contact
from core.admin import InEffectFilter

from .models import Assignment, EmploymentType, Ministry, MinistryRole, Position


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
        # Same split as every other code column: editable while adding, frozen after.
        # The admin covers the admin; Ministry.clean() covers everything else.
        return ["code"] if obj else []


@admin.register(EmploymentType)
class EmploymentTypeAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name", "code"]

    def get_readonly_fields(self, request, obj=None):
        return ["code"] if obj else []


class StaffingFilter(admin.SimpleListFilter):
    """Vacant / occupied / retired — three states, and the third is not optional.

    A vacancy nobody can see is the same as not having built this table, which
    is why the filter ships alongside the query.

    ⚠️ "Occupied" used to be implemented as "everything that is not vacant",
       which quietly swept up every retired post: an abolished post is not a
       vacancy either, so the complement collected it and the list claimed the
       post was staffed. Three states need three queries, not two and a
       negation. Same shape as MinorFilter, for the same reason.

    Every branch is one call to a QuerySet method — the rules live on
    PositionQuerySet so the front end reuses them (D18).
    """

    title = "Staffing"
    parameter_name = "staffing"

    def lookups(self, request, model_admin):
        return [("vacant", "空缺"), ("occupied", "有人在任"), ("retired", "已撤销")]

    def queryset(self, request, queryset):
        if self.value() == "vacant":
            return queryset.vacant()
        if self.value() == "occupied":
            return queryset.occupied()
        if self.value() == "retired":
            return queryset.retired()
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
    list_filter = ["ministry", "kind", "is_leader", "is_active", StaffingFilter]
    # Needed by the autocomplete on Position.reports_to, and by the one on
    # Assignment.position.
    search_fields = ["name", "code"]
    autocomplete_fields = ["ministry", "reports_to"]
    # Both foreign keys are in list_display, so both have to be joined or the
    # changelist runs two extra queries per row.
    list_select_related = ["ministry", "reports_to"]

    def get_readonly_fields(self, request, obj=None):
        return ["code"] if obj else []

    def get_list_display(self, request):
        """Appends the headcount column, counted by the database in one query.

        Not a per-row obj.assignments.count(): that is a query per row, the
        same N+1 the merge column on Contact had to be rescued from.

        The counts arrive as a {pk: counts} map rather than as an annotation on
        the changelist queryset, because annotating it would mean overriding
        get_queryset, which B9 forbids and a grep guard enforces. The cost is
        that this column cannot be sorted on in the admin — accepted knowingly:
        the admin is scaffolding, and with_headcounts() hands the front end a
        real sortable, filterable column.

        Position is a table of a few dozen rows by design (it holds kinds of
        post, not seats), so fetching all of it per page is not the problem it
        would be on Contact.
        """
        if not hasattr(request, "_position_headcounts"):
            request._position_headcounts = {
                position.pk: (position.holder_count, position.serving_count)
                for position in Position.objects.with_headcounts()
            }
        counts = request._position_headcounts

        @admin.display(description="People in post")
        def headcount(obj):
            holders, serving = counts.get(obj.pk, (0, 0))
            if holders == 0:
                return "—"
            # Only worth splitting out when somebody is on leave or suspended.
            return holders if holders == serving else f"{holders} ({serving} available)"

        return [*super().get_list_display(request), headcount]


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

    @admin.display(boolean=True, description="In effect")
    def is_currently_active(self, obj):
        return obj.is_currently_active


@admin.register(MinistryRole)
class MinistryRoleAdmin(SimpleHistoryAdmin):
    """SimpleHistoryAdmin: who granted what authority, when, and who took it away.

    ⚠️ Revoke by filling in end_date, never by deleting the row. Ending is what
       the date says — the same rule Assignment lives by — and a deleted grant
       leaves no answer to "who could see the food pantry's signups last March".

    No automatic link to Position.is_leader in either direction. Somebody being
    a ministry's leader and being able to operate its pages are separate facts
    (D20); deriving one from the other would weld them back together.
    """

    list_display = ["contact", "ministry", "role", "start_date", "end_date",
                    "is_currently_active", "granted_by"]
    list_filter = [InEffectFilter, "role", "ministry"]
    search_fields = [
        "contact__legal_last_name", "contact__legal_first_name",
        "contact__preferred_name", "ministry__name",
    ]
    autocomplete_fields = ["contact", "ministry"]
    list_select_related = ["contact", "ministry", "granted_by"]

    @admin.display(boolean=True, description="In effect")
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
