"""Disposable configuration (D18). Every judgement here is a QuerySet call.

Nothing in this file works anything out: the counts come from
with_signup_counts(), the shortfall from understaffed(). The test is whether
deleting this file would lose any business logic — it must not.
"""

from django.contrib import admin
from django.db.models import Count
from simple_history.admin import SimpleHistoryAdmin

from .models import Event, EventRole, EventType, Participation, ParticipationRole


@admin.register(EventType)
class EventTypeAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name", "code"]

    def get_readonly_fields(self, request, obj=None):
        # Same split as Ministry and RelationshipType: editable while adding,
        # frozen afterwards. clean() covers everything that is not the admin.
        return ["code"] if obj else []


@admin.register(ParticipationRole)
class ParticipationRoleAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["name", "code"]

    def get_readonly_fields(self, request, obj=None):
        return ["code"] if obj else []


class EventRoleInline(admin.TabularInline):
    """Open the jobs while setting the event up — P2's "say how many you need"."""

    model = EventRole
    extra = 0
    fields = ["role", "needed_count", "notes"]
    autocomplete_fields = ["role"]
    show_change_link = True


@admin.register(Event)
class EventAdmin(SimpleHistoryAdmin):
    """SimpleHistoryAdmin: what the time and place used to be, and who moved them.

    ⚠️ No Participation inline. Participation hangs off EventRole now, so
       registering people here would need a nested inline, which Django cannot
       do without a third-party package — squarely inside D18's shape trigger.
       Signing people in happens on the attendance page instead, which had to
       exist anyway.
    """

    list_display = ["name", "ministry", "event_type", "status", "start_time", "end_time"]
    list_filter = ["status", "ministry", "event_type"]
    search_fields = ["name", "location"]
    date_hierarchy = "start_time"
    autocomplete_fields = ["event_type", "ministry", "owner"]
    list_select_related = ["ministry", "event_type"]
    inlines = [EventRoleInline]

    def get_list_display(self, request):
        """Appends role and signup counts, counted by the database once per page.

        Not obj.roles.count() per row: a method in list_display runs once for
        every row rendered, which is the N+1 the Contact changelist had to be
        rescued from. Built here rather than by overriding get_queryset, which
        the layering guard forbids.
        """
        if not hasattr(request, "_event_counts"):
            request._event_counts = {
                event.pk: (event.role_total, event.registered_total)
                for event in Event.objects.annotate(
                    role_total=Count("roles", distinct=True),
                    registered_total=Count("roles__participations", distinct=True),
                )
            }
        counts = request._event_counts

        @admin.display(description="工种 / 报名")
        def roles_and_signups(obj):
            roles, registered = counts.get(obj.pk, (0, 0))
            return f"{roles} / {registered}"

        return [*super().get_list_display(request), roles_and_signups]


class UnderstaffedFilter(admin.SimpleListFilter):
    """Short of people / not. One QuerySet call per branch, no arithmetic here.

    Only two states this time, and they really are two: a role either asked for
    a number and has fewer, or it did not. Roles with no needed_count are not
    short — "no limit" is not "short by infinity".
    """

    title = "缺人"
    parameter_name = "understaffed"

    def lookups(self, request, model_admin):
        return [("yes", "还缺人"), ("no", "不缺人")]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.understaffed()
        if self.value() == "no":
            return queryset.exclude(pk__in=queryset.model.objects.understaffed())
        return queryset


@admin.register(EventRole)
class EventRoleAdmin(SimpleHistoryAdmin):
    list_display = ["role", "event", "needed_count"]
    list_filter = ["event__ministry", "role", UnderstaffedFilter]
    search_fields = ["event__name", "role__name"]
    autocomplete_fields = ["event", "role"]
    list_select_related = ["event", "role"]


@admin.register(Participation)
class ParticipationAdmin(SimpleHistoryAdmin):
    """SimpleHistoryAdmin because hours can be rewritten by hand.

    It is the only authoritative value in the system a human may overwrite, and
    it may end up attached to recognition: whoever turned 3 hours into 8 has to
    be answerable for it.
    """

    list_display = [
        "contact", "event_role", "status", "checked_in_at", "checked_out_at", "hours",
    ]
    list_filter = ["status", "event_role__event", "event_role__role"]
    search_fields = [
        "contact__legal_last_name", "contact__legal_first_name",
        "contact__preferred_name", "event_role__event__name",
    ]
    autocomplete_fields = ["contact", "event_role", "consent_relationship"]
    list_select_related = ["contact", "event_role__event", "event_role__role"]
