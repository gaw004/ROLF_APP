"""Disposable configuration (D18). Every judgement here is a QuerySet call.

Nothing in this file works anything out: the counts come from
with_signup_counts(), the shortfall from understaffed(). The test is whether
deleting this file would lose any business logic — it must not.
"""

from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.template.response import TemplateResponse
from simple_history.admin import SimpleHistoryAdmin

from .forms import AudienceAdminForm, EventSeriesAdminForm, SessionForm
from .services import (
    generate_occasions,
    register_kept_at,
    split_series,
    stop_series_today,
    undo_preview,
    undo_series,
)
from org.audience import Audience

from .models import (
    Event,
    EventRole,
    EventSeries,
    EventSeriesRole,
    Participation,
    ParticipationRole,
    Session,
    SessionAttendance,
)


def _admin_contact(request):
    """The Contact behind the logged-in admin, or None.

    ⚠️ None is a normal answer rather than a failure: a superuser matches no
       real person by design (D12), which is why `undone_by` is nullable. Named
       once because three actions ask it, and `views._my_contact()` is the same
       question on the other side of the D18 line — this file may not import it.
    """
    return getattr(request.user, "contact", None)


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


class ShapeFilter(admin.SimpleListFilter):
    """Courses / one-off occasions. One QuerySet call per branch, as above.

    ⚠️ A `SimpleListFilter` rather than `list_filter = ["shape"]`, and the
       difference is not cosmetic: Django builds the plain version from the
       field's own choices and applies `filter(shape__exact=…)`, so it never
       reaches `EventQuerySet` at all. Here the two branches call the two
       predicates, which is what keeps a single definition of each half — and
       what gives them a reader from the day they land, months before
       `/programs/` (L5.8) becomes the second one.

    ⚠️ Cleaner than `UnderstaffedFilter` above in one respect worth noting: both
       branches have a predicate of their own, because the two are a partition.
       That one has to spell its "no" side as an exclusion.
    """

    title = "Kind of event"
    parameter_name = "shape"

    def lookups(self, request, model_admin):
        return [("program", "Courses and programs"),
                ("single", "One-off occasions")]

    def queryset(self, request, queryset):
        if self.value() == "program":
            return queryset.programs()
        if self.value() == "single":
            return queryset.single_occasions()
        return queryset


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
        "name", "ministry", "shape", "status", "start_time", "end_time",
        "duration", "series",
    ]
    # ⚠️ `series` in both, because a rule can put 52 rows on this page carrying
    #    the same name, the same ministry and the same shape — and this is the
    #    changelist staff actually live on. Without the column they are 52
    #    indistinguishable lines; without the filter there is no way to pick out
    #    the ones a rule made. `RelatedOnlyFieldListFilter` for the reason
    #    SessionAdmin states: the plain version loads every row of the related
    #    table into the dropdown on every page view.
    list_filter = ["status", ShapeFilter, "ministry", "visible_to_outsiders",
                   ("series", admin.RelatedOnlyFieldListFilter)]
    search_fields = ["name", "location"]
    date_hierarchy = "start_time"
    autocomplete_fields = ["ministry", "owner"]
    list_select_related = ["ministry", "series"]
    inlines = [EventRoleInline]
    # 🔴 L5.4's two columns are shown and never typed, and `source` in
    #    particular is not a preference — it is what decides whether a rule may
    #    take this row back. An admin who set a hand-made event to `generated`
    #    would have it silently withdrawn by the next regeneration, taking any
    #    signups on it; one who moved an event to another series would hand that
    #    series a row it never made. Both go through
    #    `services.generate_occasions()` or not at all.
    #
    # ⚠️ It is also what stops the add form demanding them. `source` has a
    #    default but is not `blank`, so a ModelForm renders it required — which
    #    broke the ordinary admin create the moment the column landed, and a
    #    test caught it rather than a person.
    readonly_fields = ["series", "source"]

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

    # ⚠️ The form is not decoration: it is what makes adding a meeting here
    #    reach `services.open_registers_for()`, so somebody who signed up in
    #    week two lands on the register of a meeting added in week ten. The
    #    alternative hook, `save_model`, is one of the four hooks
    #    AdminHasNoLogicGuardTests refuses — see the form's own docstring.
    form = SessionForm
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

    def has_delete_permission(self, request, obj=None):
        """Refuses a meeting that has anybody on its register. D18: it asks, it
        does not work it out — `services.register_kept_at()` is the answer.

        🔴 **It asked about *hours* until 2026-09-10, and that made it blind to
           half this table.** A place somebody attends records no hours by
           design (decision 20), so a meeting with a full class register —
           twelve students, all marked present — reported nothing to lose and
           the delete button was enabled. The guard asked the volunteering
           question on a table built to hold both halves.

        🔴 `SessionAttendance` cascades from `session`, so deleting week seven
           here takes its whole register with it: who came, the hours an
           assistant gave that evening, and the meetings D43 reads for hours
           received. That is the same loss `role_delete` refuses one table
           lower, arriving by a door that guard cannot see — nothing about the
           role changes, so nothing about the role objects.

        ⚠️ Not one of the four hooks `AdminHasNoLogicGuardTests` refuses, and it
           holds no rule of its own: the question is asked in services, and this
           returns its answer.
        """
        if obj is not None and register_kept_at(obj):
            return False
        return super().has_delete_permission(request, obj)

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


class EventSeriesRoleInline(admin.TabularInline):
    """The jobs the rule opens on every occasion it makes. L5.4.

    ⚠️ `AudienceAdminForm` for the same reason `EventRoleInline` above carries
       it: without a form the admin runs **no** audience checks at all, and here
       what would go unchecked is the containment rule against the series — a
       template role open wider than its series produces twelve real breaches of
       the L2×L3 invariant in one press, silently.
    """

    model = EventSeriesRole
    form = AudienceAdminForm
    extra = 0
    fields = ["role", "needed_count", "stop_at_needed_count", "notes",
              *Audience.AUDIENCE_FIELDS]
    autocomplete_fields = ["role"]
    # ⚠️ No `show_change_link`, unlike `EventRoleInline` above — and the
    #    difference is not an oversight. Django only renders that link when the
    #    inline's model is registered in the admin (`has_registered_model`), and
    #    this one is not: everything about a template role is on this row.
    #    Setting it would have been a flag that silently does nothing, which is
    #    indistinguishable from a link somebody broke.


@admin.register(EventSeries)
class EventSeriesAdmin(SimpleHistoryAdmin):
    """A repeat rule and the batch of events it made. L5.4–L5.6.

    🔴 **Generating is an action, never a side effect of saving**, and there are
       two independent reasons — either one alone would settle it:

         · `ModelAdmin.save_related()` calls `form.save_m2m()` **before** it
           saves the inlines, so anything hooked onto a save runs while the
           series still has no roles and, on an add, no audience. It would
           cheerfully generate twelve events with nothing open on them;
         · `save_related` is one of the four hooks `AdminHasNoLogicGuardTests`
           refuses outright (D18).

       Which lands on the shape D40 wanted anyway: a batch is something somebody
       presses, looks at, and can undo — not something that happens to them
       while they were editing a description.

    ⚠️ Both actions are one line each into `events.services`. This file works
       nothing out; the arithmetic on the confirmation screen is
       `services.undo_preview()`, so the numbers under the button and the rows
       the button removes come from one place.

    🔴 **Both actions declare `permissions=["change"]`, and without that line
       the grant in org/permissions.py does not hold.** An earlier draft of this
       docstring claimed the page was "open to superusers only" because
       `FOUNDATION_ADMIN_PERMISSIONS` grants `view_` and no more. That is not
       how Django gates actions: `_filter_actions_by_permissions()` allows any
       action whose callable carries no `allowed_permissions`, and the
       changelist renders the action form for anybody who can open the page. So
       a view-only foundation admin saw both actions and could press them —
       measured: four events created and a whole batch withdrawn by an account
       holding `view_eventseries` alone.

       ⚠️ Written out because the docstring was **the only thing** asserting the
          lock, and a comment promising one is worse than an open door: it stops
          the next person looking. Same failure this repository records against
          `set_audience()`'s module note, which claimed a gate the services did
          not have.

    ⚠️ `change`, not a permission of its own, and it lands where D20 puts it:
       building a batch is an act on **one ministry's** events, so it belongs to
       the ministry tier. Until the series pages (L5.8) hand that tier a door,
       the only account with `change_eventseries` is a superuser — which is what
       the sentence above was trying to describe.
    """

    # ⚠️ Not the plain `AudienceAdminForm`: this one adds the picture pipeline
    #    (`normalise_event_image` — resize, WebP, **strip EXIF**), which the
    #    series upload went round the back of. A phone photo carries GPS.
    form = EventSeriesAdminForm
    list_display = ["name", "ministry", "rule", "starts_on", "start_time",
                    "status", "ended_on", "undone_at"]
    list_filter = ["status", "ministry"]
    search_fields = ["name", "rule"]
    autocomplete_fields = ["ministry", "owner"]
    list_select_related = ["ministry"]
    inlines = [EventSeriesRoleInline]
    # ⚠️ `ended_on` is readonly, and that is a correctness fix rather than
    #    tidiness. Typing a date into it stops **future generation** and
    #    withdraws nothing, so an admin who set it believed they had called the
    #    series off while four published evenings stood on the calendar, still
    #    signable. The action below is the only thing that does both, so it is
    #    the only way in. (`undone_at` / `undone_by` are readonly for the
    #    ordinary reason: they record what a service did.)
    readonly_fields = ["ended_on", "generated_at", "generated_by",
                       "undone_at", "undone_by"]
    actions = ["generate_occasions", "stop_today", "change_the_rule", "undo_batch"]

    def get_list_display(self, request):
        """Counts the occasions once for the page, not once per row.

        The same arrangement `EventAdmin.get_list_display` uses and for the same
        reason: a method in `list_display` runs per row, which is the N+1 the
        Contact changelist had to be rescued from. Built here rather than by
        overriding `get_queryset`, which the layering guard forbids.
        """
        if not hasattr(request, "_series_counts"):
            request._series_counts = dict(
                EventSeries.objects.annotate(made=Count("occasions"))
                .values_list("pk", "made"))
        counts = request._series_counts

        @admin.display(description="Occasions")
        def occasions_made(obj):
            return counts.get(obj.pk, 0)

        return [*super().get_list_display(request), occasions_made]

    @admin.action(description="Generate the occasions this rule calls for",
                  permissions=["change"])
    def generate_occasions(self, request, queryset):
        for series in queryset:
            try:
                made = generate_occasions(
                    series, generated_by=_admin_contact(request))
            except ValidationError as refused:
                # ⚠️ Reported, not raised — the same handling `undo_batch` below
                #    has. A series that reached the database without
                #    `full_clean()` (an import, a script, the seed) can hold an
                #    empty audience or a template role wider than itself, and
                #    both are refused deep inside the service. Uncaught that is
                #    a 500 on an admin page, with the batch silently rolled back
                #    by the service's own `atomic` — the admin sees a crash and
                #    has no idea whether anything was written.
                self.message_user(
                    request, f"“{series.name}”: {'; '.join(refused.messages)}",
                    level=messages.WARNING)
                continue
            self.message_user(
                request,
                f"“{series.name}”: {len(made)} occasion(s) generated. "
                f"They are {series.get_status_display().lower()} — nothing was "
                "removed from occasions people had already signed up for.")

    @admin.action(description="Stop this series from today",
                  permissions=["change"])
    def stop_today(self, request, queryset):
        """🔴 The door `stop_series_today()` did not have.

        Without it the only way to stop a series was to type a date into
        `ended_on` — which stops *generation* and withdraws *nothing*. An admin
        did that, saw the form save, and left four published evenings standing
        on the public calendar, open for signup. Two of the feature's own
        refusal messages ("Stop the series instead") pointed at this control
        while it did not exist.
        """
        for series in queryset:
            try:
                dropped = stop_series_today(series)
            except ValidationError as refused:
                self.message_user(
                    request, f"“{series.name}”: {'; '.join(refused.messages)}",
                    level=messages.WARNING)
            else:
                self.message_user(
                    request,
                    f"“{series.name}”: stopped as of today. "
                    f"{dropped} occasion(s) still to come were withdrawn; "
                    "everything that already happened was left alone.")

    @admin.action(description="Change the rule from today on",
                  permissions=["change"])
    def change_the_rule(self, request, queryset):
        """Stop this series and open an editable copy of it. 06-roadmap L5.6.

        ⭐ The shape is Google's calendar split, and it is what makes the rule
           freeze survivable: the successor has **no occasions yet**, so
           `_refuse_rewriting_the_rule()` does not apply to it and every
           generation column is editable. Change what you meant to change on the
           copy, then press Generate.

        ⚠️ Before this, the freeze's own refusal said "stop this series instead,
           and build the next one" and neither half had a control — so the only
           sanctioned way to change a rule was unreachable, and `split_series()`
           had no caller outside the tests.

        ⚠️ It deliberately does **not** ask what the new rule is. A form here
           would be a second place that knows how to edit a series, and the
           change page is already that place — this hands them the copy and gets
           out of the way.
        """
        for series in queryset:
            try:
                successor, withdrawn = split_series(
                    series, changed_by=_admin_contact(request))
            except ValidationError as refused:
                self.message_user(
                    request, f"“{series.name}”: {'; '.join(refused.messages)}",
                    level=messages.WARNING)
            else:
                self.message_user(
                    request,
                    f"“{series.name}”: stopped as of today — "
                    f"{withdrawn} occasion(s) still to come were withdrawn, "
                    "and what already happened stays. A copy is ready to edit "
                    f"(#{successor.pk}): change when it repeats there, then "
                    "generate its occasions.")

    @admin.action(description="Undo this batch", permissions=["change"])
    def undo_batch(self, request, queryset):
        """Two passes: show what would happen, then do it. D40 section 1.

        ⚠️ The confirmation screen is not politeness. Undo does **not** put the
           database back — three weeks on, some occasions have happened and some
           have people on them, and neither is ours to take back. Somebody told
           only "386 will be removed" believes it came out clean and meets the
           survivors next month, by which time they no longer remember undoing
           anything.
        """
        if request.POST.get("confirmed"):
            for series in queryset:
                try:
                    # ⚠️ None is a normal answer, not a failure: a superuser has
                    #    no Contact by design (D12), and `undone_by` is nullable
                    #    for that reason. Today the superuser is the only account
                    #    that can reach this page at all — see the class docstring.
                    dropped = undo_series(series, undone_by=_admin_contact(request))
                except ValidationError as refused:
                    # ⚠️ Named, like the sibling action above. Undo a
                    #    multi-select where one batch is already undone and a
                    #    bare "This batch has already been undone" leaves the
                    #    reader with no way to tell which.
                    self.message_user(
                        request,
                        f"“{series.name}”: {'; '.join(refused.messages)}",
                        level=messages.WARNING)
                else:
                    self.message_user(
                        request, f"“{series.name}”: {dropped} occasion(s) withdrawn.")
            return None
        batches = [(series, undo_preview(series)) for series in queryset]
        return TemplateResponse(request, "admin/events/eventseries/undo_confirm.html", {
            **self.admin_site.each_context(request),
            "title": "Undo these batches",
            "batches": batches,
            # ⚠️ Any, not all: a mixed selection still has something to do, and
            #    the screen says per batch which ones are closed.
            "offerable": any(preview.within_window for _, preview in batches),
            "queryset": queryset,
            "action_checkbox_name": admin.helpers.ACTION_CHECKBOX_NAME,
        })
