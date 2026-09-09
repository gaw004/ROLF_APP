"""Disposable configuration (D18). Every judgement here is a QuerySet call.

Nothing in this file works anything out: the counts come from
with_signup_counts(), the shortfall from understaffed(). The test is whether
deleting this file would lose any business logic — it must not.
"""

from django.contrib import admin
from django.db.models import Count
from simple_history.admin import SimpleHistoryAdmin

from .forms import AudienceAdminForm
from org.audience import Audience

from .models import (
    Event,
    EventRole,
    Participation,
    ParticipationRole,
    Session,
    SessionAttendance,
)


@admin.register(ParticipationRole)
class ParticipationRoleAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "nature", "is_active"]
    list_filter = ["nature", "is_active"]
    search_fields = ["name", "code"]

    def get_readonly_fields(self, request, obj=None):
        # ⚠️ `nature` is deliberately not frozen here — unlike Participation's
        #    served_as, whose rule could only be enforced by making the admin
        #    form unable to touch it. This one's rule is in
        #    ParticipationRole.clean(), which the admin's ModelForm calls, so
        #    it refuses exactly the change that matters (flipping a role people
        #    have already signed up through) and allows the one that should be
        #    allowed: correcting a role opened under the wrong kind.
        return ["code"] if obj else []


class EventRoleInline(admin.TabularInline):
    """Open the jobs while setting the event up — P2's "say how many you need"."""

    model = EventRole
    form = AudienceAdminForm
    extra = 0
    fields = ["role", "needed_count", "notes", *Audience.AUDIENCE_FIELDS]
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

    # R1 comes from date_hierarchy plus the row count, R2 from the ministry
    # column, R3 from duration — which is why all three are here rather than on
    # a page of their own. The foundation-wide role in the acceptance walk reads
    # them from this changelist.
    form = AudienceAdminForm
    list_display = [
        "name", "ministry", "status", "start_time", "end_time", "duration",
    ]
    list_filter = ["status", "ministry", "visible_to_outsiders"]
    search_fields = ["name", "location"]
    date_hierarchy = "start_time"
    autocomplete_fields = ["ministry", "owner"]
    list_select_related = ["ministry"]
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

        @admin.display(description="Roles / signups")
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

    title = "Short of people"
    parameter_name = "understaffed"

    def lookups(self, request, model_admin):
        return [("yes", "Still short"), ("no", "Fully signed up")]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.understaffed()
        if self.value() == "no":
            return queryset.exclude(pk__in=queryset.model.objects.understaffed())
        return queryset


@admin.register(EventRole)
class EventRoleAdmin(SimpleHistoryAdmin):
    form = AudienceAdminForm
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
    # ⚠️ `event_role` is **not** here any more: it is readonly below, and
    #    configuring a picker for a field nobody can edit is a control that
    #    does nothing — the shape this project keeps deleting.
    autocomplete_fields = ["contact", "consent_relationship"]
    list_select_related = ["contact", "event_role__event", "event_role__role"]
    # ⚠️ The admin is a write path that exists without anybody writing code for
    #    it, and the guard that keeps served_as to one setter greps source —
    #    it cannot see a form somebody fills in here. What comes out of that
    #    form is not a wrong value, it is a value with **no declared_by on it**:
    #    reports file it under "identity not recorded", the FLSA prompt cannot
    #    say who claimed it, and the page looks completely normal. D38 §4.
    #
    # ⚠️ checked_in_method is on this list for the same reason and always
    #    should have been (D28 §4) — the same rule, applied to the second fact
    #    on this table that records *who said so*.
    #
    #    Corrections go through the action on the signups page, which calls
    #    services.set_served_as() and stamps declared_by=admin.
    #
    # 🔴 `event_role` too, since 2026-09-08, and for a different reason from the
    #    three above. Those are frozen because they record *who said so*; this
    #    one is frozen because moving a row between roles changes **which fact
    #    it states**, and does it past every rule that would object. A signup
    #    carrying served_as=volunteer and 3.5 hours, re-pointed at an attending
    #    role, passes full_clean() — the no-hours constraint keys on served_as,
    #    and this row does not claim not_applicable — and afterwards the
    #    ministry report counts 0 hours for it (it filters on the role's nature)
    #    while /me/ counts 3.5 (it filters on served_as). One row, two ledgers,
    #    no error. Signing somebody up for a different role is a new signup.
    readonly_fields = ["served_as", "served_as_declared_by", "checked_in_method",
                       "event_role"]


@admin.register(Session)
class SessionAdmin(SimpleHistoryAdmin):
    """The meetings inside a run — L5.1's table, reachable at last.

    ⚠️ It went in on 2026-09-05 without this, so the table existed and nothing
       but the tests could reach it. Batch three's file list had promised the
       registration; the omission is the sort that has no symptom, because a
       table nobody can open looks exactly like a table nobody needs.

    SimpleHistoryAdmin for the reason the model keeps history at all: moving
    week seven from Tuesday to Thursday changes a set of people's attendance,
    so who did it is part of the record.
    """

    list_display = ["event", "start_time", "end_time", "source"]
    # ⚠️ RelatedOnlyFieldListFilter, not a bare "event": the default loads every
    #    row of the related table into the dropdown on every page view, so a
    #    foundation with four hundred past events gets four hundred options of
    #    which a handful have meetings. Scoped to what this table actually holds.
    list_filter = ["source", ("event", admin.RelatedOnlyFieldListFilter)]
    search_fields = ["event__name"]
    autocomplete_fields = ["event"]
    list_select_related = ["event"]
    date_hierarchy = "start_time"
    # Teaching order, matching the model's own — presentation, so it belongs
    # here rather than in a Meta the aggregates would inherit.
    ordering = ["start_time"]

    def get_readonly_fields(self, request, obj=None):
        """`event` freezes once anybody is on this meeting's register.

        🔴 The same reasoning that froze `Participation.event_role` on
           2026-09-08, arriving one table over: re-pointing a meeting at another
           run changes **which fact its register states**, and does it past
           every rule that would object. `Session.clean()` only checks the
           meeting against its run's own dates, so the move passes — and
           afterwards each attendance row sits in a state its own `full_clean()`
           rejects ("that meeting belongs to another run"), while
           hours_received() carries on counting it.

        ⚠️ Conditional rather than always readonly, because a meeting typed
           against the wrong run and noticed immediately is an ordinary
           correction — and nothing has been said about it yet.
        """
        frozen = list(super().get_readonly_fields(request, obj))
        if obj is not None and obj.attendances.exists():
            frozen.append("event")
        return frozen


@admin.register(SessionAttendance)
class SessionAttendanceAdmin(SimpleHistoryAdmin):
    """One person at one meeting. SimpleHistoryAdmin for the same reason.

    ⚠️ Two rules on this table are `clean()` rules rather than constraints —
       the meeting must belong to the run somebody signed up for, and a place
       people attend records no hours — and a ModelForm calls `full_clean()`,
       so both of them are live here. This form is currently the only door a
       person has to those rules; the bulk one arrives with L5.3.

    ⚠️ `hours` stays editable: this table is the paper-register path for a run,
       the same way Participation.hours is for a single occasion. What is not
       editable is the column recording **who** filled the row in.
    """

    list_display = ["participation", "session", "status", "hours"]
    list_filter = ["status", ("session__event", admin.RelatedOnlyFieldListFilter)]
    search_fields = [
        "participation__contact__legal_last_name",
        "participation__contact__legal_first_name",
        "session__event__name",
    ]
    autocomplete_fields = ["participation", "session"]
    # 🔴 Four joins, not two, and the two extra ones are not decoration: the
    #    `participation` column renders `Participation.__str__`, which reads
    #    `event_role` → `EventRole.__str__`, which reads both `role.name` and
    #    `event.name`. Measured on 2026-09-08: without them a fifteen-row page
    #    costs 46 queries and grows with the register. ParticipationAdmin above
    #    already spells out the same chain one join shallower; this copied the
    #    table's rules and not its select_related.
    list_select_related = [
        "participation__contact",
        "participation__event_role__event",
        "participation__event_role__role",
        "session__event",
    ]
    # ⚠️ Same rule as ParticipationAdmin's, applied to the same fact: this
    #    column says whether the volunteer filled the row in or an admin did,
    #    and an admin editing it is rewriting a piece of evidence about
    #    themselves. D28 §4.
    readonly_fields = ["checked_in_method"]
    ordering = ["-session__start_time"]
