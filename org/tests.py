import datetime
import inspect
from importlib import import_module

from django.apps import apps
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from contact.models import Contact
from core.timeutils import local_now, local_today
from events.models import Event, EventType

from .admin import StaffingFilter
from .models import Assignment, EmploymentType, Ministry, MinistryRole, Position
from .permissions import (
    FOUNDATION_ADMIN_GROUP,
    FOUNDATION_ADMIN_PERMISSIONS,
    can_grant_ministry_admin,
    can_manage_event,
    can_publish_event,
    can_view_event_records,
    foundation_admin_group,
    ministry_ids_administered_by,
)
from .services import build_org_tree

TODAY = local_today()
YESTERDAY = TODAY - datetime.timedelta(days=1)
LAST_YEAR = TODAY - datetime.timedelta(days=365)


def make_person(last_name, first_name="Ping"):
    # A first name is required of an individual (2026-08-19), same as the last.
    return Contact.objects.create(
        contact_type=Contact.ContactType.INDIVIDUAL,
        legal_first_name=first_name, legal_last_name=last_name)


def make_ministry(code="food_pantry", name="Food Pantry", **kwargs):
    return Ministry.objects.create(code=code, name=name, **kwargs)


def make_position(code, name, ministry=None, **kwargs):
    return Position.objects.create(code=code, name=name, ministry=ministry, **kwargs)


class MinistryTests(TestCase):
    def test_ministry_code_must_be_unique(self):
        make_ministry()
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_ministry(name="Food Pantry (Saturday)")

    def test_bulk_create_cannot_insert_a_ministry_code_differing_only_in_case(self):
        # The point of Lower("code") over unique=True: bulk_create never calls
        # save(), and the foundation's existing data arrives that way.
        make_ministry()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Ministry.objects.bulk_create([Ministry(code="Food_Pantry", name="Duplicate")])

    def test_ministry_code_cannot_be_changed_once_created(self):
        ministry = make_ministry()
        ministry.code = "pantry"
        with self.assertRaises(ValidationError) as caught:
            ministry.full_clean()
        self.assertIn("code", caught.exception.message_dict)

    def test_employment_type_code_must_be_unique(self):
        EmploymentType.objects.create(code="full_time", name="Full time")
        with self.assertRaises(IntegrityError), transaction.atomic():
            EmploymentType.objects.create(code="FULL_TIME", name="Full-time")


class ConstraintFieldErrorTests(TestCase):
    """Every org constraint, submitted violated, lands on its mapped field (D14).

    core/tests.py checks that each constraint *has* a code and a mapping. Only
    this checks the mapping actually fires: CheckConstraint.validate() skips
    silently when its expression raises FieldError, and a constraint that never
    validates at form time reaches the user as an IntegrityError 500 rather
    than a red box on a field. The IntegrityError tests elsewhere in this file
    prove the database refuses the row; these prove the admin says why.

    contact/tests.py has the matching class for that app's models.
    """

    COVERED = {
        "ministry_code_taken",
        "employmenttype_code_taken",
        "position_code_taken",
        "position_reports_to_self",
        "assignment_end_before_start",
        "assignment_duplicate_tenure",
        "ministryrole_duplicate_grant",
        "ministryrole_end_before_start",
    }

    def setUp(self):
        self.ministry = make_ministry()
        self.position = make_position("director", "Program Director", self.ministry)
        self.person = make_person("王强")

    def assertFieldError(self, instance, field):
        with self.assertRaises(ValidationError) as caught:
            instance.full_clean()
        self.assertIn(field, caught.exception.message_dict)
        return caught.exception.message_dict[field]

    def test_this_class_covers_every_constraint_in_the_app(self):
        """Adding a constraint without a case here goes red, which is the point.

        core/tests.py checks a mapping exists; nothing but a case in this class
        checks that it fires, so "somebody will remember" is exactly the
        discipline this project keeps replacing with a test.
        """
        live = {
            constraint.violation_error_code
            for model in apps.get_app_config("org").get_models()
            for constraint in model._meta.constraints
            if getattr(constraint, "violation_error_code", None)
        }
        missing = sorted(live - self.COVERED)
        self.assertEqual(missing, [], f"No field-error case for: {missing}")

    def test_a_duplicate_ministry_code_points_at_code(self):
        messages = self.assertFieldError(
            Ministry(code="FOOD_PANTRY", name="Food Pantry (Saturday)"), "code")
        self.assertIn("A ministry with this code already exists.", messages)

    def test_a_duplicate_employment_type_code_points_at_code(self):
        EmploymentType.objects.create(code="full_time", name="Full time")
        messages = self.assertFieldError(
            EmploymentType(code="Full_Time", name="Full-time"), "code")
        self.assertIn("An employment type with this code already exists.", messages)

    def test_a_duplicate_position_code_points_at_code(self):
        messages = self.assertFieldError(Position(code="DIRECTOR", name="Director"), "code")
        self.assertIn("A position with this code already exists.", messages)

    def test_a_self_reporting_position_points_at_reports_to(self):
        # validate_constraints() rather than full_clean(), and the reason is
        # worth writing down: clean()'s cycle check reaches the same verdict
        # first, and full_clean() excludes any field that already has an error
        # from constraint validation — so through that door the constraint's
        # own mapping never runs. The mapping still earns its place, because
        # any form that leaves reports_to off its field list calls
        # validate_constraints() directly, with no clean() in front of it.
        self.position.reports_to_id = self.position.pk
        with self.assertRaises(ValidationError) as caught:
            self.position.validate_constraints()
        self.assertIn("reports_to", caught.exception.message_dict)
        self.assertIn(
            "A position cannot report to itself.",
            caught.exception.message_dict["reports_to"],
        )

    def test_an_assignment_end_date_before_the_start_date_points_at_end_date(self):
        messages = self.assertFieldError(
            Assignment(contact=self.person, position=self.position,
                       start_date=datetime.date(2023, 1, 1),
                       end_date=datetime.date(2020, 1, 1)),
            "end_date",
        )
        self.assertIn("The end date cannot be before the start date.", messages)

    def test_a_duplicate_tenure_points_at_start_date(self):
        # nulls_distinct=False, so the pair collides even with no start date —
        # the case Postgres would otherwise wave through.
        Assignment.objects.create(contact=self.person, position=self.position)
        messages = self.assertFieldError(
            Assignment(contact=self.person, position=self.position), "start_date")
        self.assertIn("already has a tenure in this position", " ".join(messages))

    def test_a_duplicate_grant_points_at_start_date(self):
        MinistryRole.objects.create(contact=self.person, ministry=self.ministry)
        messages = self.assertFieldError(
            MinistryRole(contact=self.person, ministry=self.ministry), "start_date")
        self.assertIn("already have that role in this ministry", " ".join(messages))

    def test_a_grant_ending_before_it_starts_points_at_end_date(self):
        messages = self.assertFieldError(
            MinistryRole(contact=self.person, ministry=self.ministry,
                         start_date=datetime.date(2023, 1, 1),
                         end_date=datetime.date(2020, 1, 1)),
            "end_date",
        )
        self.assertIn("The end date cannot be before the start date.", messages)


class PositionTests(TestCase):
    def setUp(self):
        self.ministry = make_ministry()

    def test_position_code_must_be_unique(self):
        make_position("director", "Program Director", self.ministry)
        with self.assertRaises(IntegrityError), transaction.atomic():
            make_position("director", "Director", self.ministry)

    def test_bulk_create_cannot_insert_a_position_code_differing_only_in_case(self):
        make_position("director", "Program Director", self.ministry)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Position.objects.bulk_create([Position(code="Director", name="Director")])

    def test_position_code_cannot_be_changed_once_created(self):
        position = make_position("director", "Program Director", self.ministry)
        position.code = "boss"
        with self.assertRaises(ValidationError) as caught:
            position.full_clean()
        self.assertIn("code", caught.exception.message_dict)

    def test_the_admin_freezes_code_on_the_change_page_only(self):
        model_admin = admin.site._registry[Position]
        self.assertEqual(model_admin.get_readonly_fields(None, obj=None), [])
        position = make_position("director", "Program Director", self.ministry)
        self.assertEqual(model_admin.get_readonly_fields(None, obj=position), ["code"])

    def test_position_name_is_normalised_on_save(self):
        position = make_position("director", "  Program   Director  ", self.ministry)
        self.assertEqual(position.name, "Program Director")

    def test_position_str_carries_its_ministry(self):
        # Two ministries with a "Coordinator" each is legal, so the dropdown has
        # to be able to tell them apart.
        position = make_position("coordinator", "Coordinator", self.ministry)
        self.assertEqual(str(position), "Coordinator（Food Pantry）")

    def test_a_foundation_wide_position_has_no_ministry_in_its_name(self):
        self.assertEqual(str(make_position("ed", "Executive Director")), "Executive Director")

    def test_position_cannot_report_to_itself(self):
        # The database's half of the loop protection. It only reaches depth 1 —
        # A -> B -> A is what build_org_tree() has to survive.
        position = make_position("director", "Program Director", self.ministry)
        position.reports_to_id = position.pk
        with self.assertRaises(IntegrityError), transaction.atomic():
            position.save()

    def test_a_reporting_cycle_is_rejected_by_clean(self):
        first = make_position("director", "Program Director", self.ministry)
        second = make_position("coordinator", "Coordinator", self.ministry, reports_to=first)
        first.reports_to = second
        with self.assertRaises(ValidationError) as caught:
            first.full_clean()
        self.assertIn("reports_to", caught.exception.message_dict)

    def test_deleting_a_position_with_reports_is_blocked(self):
        # PROTECT, not SET_NULL: SET_NULL would silently promote the whole
        # subtree to the top of the chart and leave no trace that it happened.
        boss = make_position("director", "Program Director", self.ministry)
        make_position("coordinator", "Coordinator", self.ministry, reports_to=boss)
        with self.assertRaises(ProtectedError), transaction.atomic():
            boss.delete()

    def test_a_reporting_line_can_cross_kinds(self):
        # The executive director is staff and reports to the board chair, who is
        # not. Nothing may forbid that.
        chair = make_position("chair", "Board Chair", kind=Position.Kind.BOARD, is_leader=True)
        director = make_position(
            "ed", "Executive Director", kind=Position.Kind.STAFF,
            compensation=Position.Compensation.PAID, reports_to=chair)
        director.full_clean()
        self.assertEqual(director.reports_to, chair)


class WorkerAxisSplitTests(TestCase):
    """D32: `kind` narrowed to two values, pay moved onto its own column.

    ⚠️ Three of these four watch the *vocabulary*, not any particular row. The
       migration that rewrote the data ran once and will never run again on this
       database — what can still go wrong is somebody widening the enum back, or
       editing the mapping table in the migration long after the rows it
       described were written.
    """

    def test_the_forward_mapping_is_still_exactly_those_three_rows(self):
        # Read out of the migration, deliberately: this asserts that the table
        # somebody could edit has not been edited. It says nothing about data.
        # import_module, because the module name starts with a digit and so
        # cannot be written as an import statement.
        migration = import_module("org.migrations.0008_kind_to_staff_and_compensation")
        self.assertEqual(migration.FORWARD, {
            "employee": ("staff", "paid"),
            "volunteer": ("staff", "unpaid"),
            "board": ("board", "unpaid"),
        })

    def test_no_row_anywhere_still_carries_an_old_kind(self):
        """⚠️ Weaker than it looks, and the weakness is the point.

        Django builds the test database by running the migrations against an
        empty one, so this proves the data migration does not blow up — not
        that its mapping is right. The mapping is only really accepted by
        running it over a database that has old rows in it (seed_demo's), which
        is the first line of D1's acceptance list and cannot be a unit test.
        """
        old = ["employee", "volunteer"]
        self.assertFalse(Position.objects.filter(kind__in=old).exists())
        self.assertFalse(
            Position.history.model.objects.filter(kind__in=old).exists())

    def test_kind_offers_two_values_and_not_a_third(self):
        # A third value would be somebody folding a pay arrangement back into
        # this column, which is the mistake the split exists to undo.
        self.assertEqual(
            [value for value, _ in Position.Kind.choices], ["staff", "board"])

    def test_a_new_position_is_unpaid_until_somebody_says_otherwise(self):
        # The safe direction: a wrongly unpaid vacancy costs one question at
        # hiring time; the other way round invents salaried posts on a report.
        post = make_position("new_box", "New box", make_ministry())
        self.assertEqual(post.compensation, Position.Compensation.UNPAID)


class BuildOrgTreeTests(TestCase):
    """The one place that walks the reporting chain, and the three promises it makes."""

    def setUp(self):
        self.ministry = make_ministry()
        self.boss = make_position("director", "Program Director", self.ministry)
        self.staff = make_position(
            "coordinator", "Coordinator", self.ministry, reports_to=self.boss)

    def test_build_org_tree_nests_children_under_their_manager(self):
        roots = build_org_tree()
        self.assertEqual([position.pk for position in roots], [self.boss.pk])
        self.assertEqual([position.pk for position in roots[0].children], [self.staff.pk])
        self.assertEqual(roots[0].children[0].children, [])

    def test_build_org_tree_uses_a_single_query(self):
        # Nails down that nobody has quietly changed .reports_to_id back to
        # .reports_to, which reads fine and costs one query per row.
        with self.assertNumQueries(1):
            build_org_tree()

    def test_build_org_tree_survives_a_cycle_inserted_by_bulk_create(self):
        # clean() refuses loops, but bulk_create never calls clean(), so one can
        # exist in the table. Anything that recurses over it has to survive.
        first = Position(code="a", name="A", ministry=self.ministry)
        second = Position(code="b", name="B", ministry=self.ministry)
        Position.objects.bulk_create([first, second])
        Position.objects.filter(pk=first.pk).update(reports_to=second)
        Position.objects.filter(pk=second.pk).update(reports_to=first)

        with self.assertLogs("org.services", level="WARNING") as logs:
            roots = build_org_tree()

        self.assertIn("loop", logs.output[0])
        rooted = {position.pk for position in roots}
        self.assertLessEqual({first.pk, second.pk}, rooted)
        # Every position still appears exactly once, so a caller can render the
        # result without knowing a loop was ever possible.
        self.assertEqual(
            sum(1 + len(position.children) for position in roots),
            Position.objects.count(),
        )


class VacancyTests(TestCase):
    """Why Position was split out of Assignment in the first place."""

    def setUp(self):
        self.ministry = make_ministry()
        self.position = make_position(
            "coordinator", "Coordinator", self.ministry, kind=Position.Kind.STAFF,
            compensation=Position.Compensation.UNPAID)
        self.person = make_person("王强")

    def hold(self, **kwargs):
        return Assignment.objects.create(
            contact=self.person, position=self.position, **kwargs)

    def test_a_position_becomes_vacant_when_its_last_tenure_ends(self):
        assignment = self.hold(start_date=LAST_YEAR)
        self.assertNotIn(self.position, Position.objects.vacant())
        assignment.end_date = YESTERDAY
        assignment.save()
        self.assertIn(self.position, Position.objects.vacant())

    def test_a_vacant_position_still_reports_its_kind_ministry_and_reports(self):
        # The whole argument for the split: an empty box that cannot say what
        # it is would be no use to whoever is trying to fill it.
        junior = make_position("helper", "Helper", self.ministry, reports_to=self.position)
        vacant = Position.objects.vacant().get(pk=self.position.pk)
        self.assertEqual(vacant.kind, Position.Kind.STAFF)
        # ⚠️ The second half is new and is the reason D32 split the axis: a
        #    vacancy has to be able to say whether it is budgeted.
        self.assertEqual(vacant.compensation, Position.Compensation.UNPAID)
        self.assertEqual(vacant.ministry, self.ministry)
        self.assertEqual(list(vacant.direct_reports.all()), [junior])

    def test_an_inactive_position_is_not_listed_as_vacant(self):
        # Abolished is not the same as open for applications.
        self.position.is_active = False
        self.position.save()
        self.assertNotIn(self.position, Position.objects.vacant())

    def test_vacant_accepts_an_explicit_date(self):
        self.hold(start_date=LAST_YEAR, end_date=YESTERDAY)
        self.assertIn(self.position, Position.objects.vacant())
        self.assertNotIn(self.position, Position.objects.vacant(on=LAST_YEAR))

    def test_a_position_held_by_someone_on_leave_is_not_vacant(self):
        # vacant() is built on active(), not serving(): the post-holder is away,
        # not gone, and the box is not open.
        self.hold(start_date=LAST_YEAR, status=Assignment.Status.ON_LEAVE)
        self.assertNotIn(self.position, Position.objects.vacant())

    def test_replacing_a_position_holder_does_not_touch_the_reporting_lines(self):
        """The point of the entire second revision. Everything else can fail; not this."""
        junior = make_position("helper", "Helper", self.ministry, reports_to=self.position)
        line_before = junior.reports_to_id
        outgoing = self.hold(start_date=LAST_YEAR)

        outgoing.end_date = YESTERDAY
        outgoing.save()
        Assignment.objects.create(
            contact=make_person("李梅"), position=self.position, start_date=TODAY)

        junior.refresh_from_db()
        self.assertEqual(junior.reports_to_id, line_before)
        # And the person who left still has their service history.
        self.assertEqual(outgoing.contact.assignments.count(), 1)


class StaffingTests(TestCase):
    """A post is vacant, occupied or retired — exactly one of the three.

    "Occupied" was once defined as "not vacant", which put every retired post
    in it: an abolished post is not a vacancy either, so the complement
    collected it. These tests exist so that definition cannot come back.
    """

    def setUp(self):
        self.ministry = make_ministry()
        self.position = make_position(
            "pantry_vol", "Pantry Volunteer", self.ministry, kind=Position.Kind.STAFF,
            compensation=Position.Compensation.UNPAID)

    def hold(self, last_name, **kwargs):
        return Assignment.objects.create(
            contact=make_person(last_name), position=self.position, **kwargs)

    def test_one_position_can_be_held_by_several_people_at_once(self):
        """A Position is a kind of post, not a seat — the claim the whole split
        rests on, and nothing else in this file was pinning it down.

        Three pantry volunteers are one Position and three Assignments. There
        is deliberately no constraint against it: one would block both genuine
        co-holders and the overlap of a normal handover.
        """
        for last_name in ("张三", "李四", "王五"):
            self.hold(last_name, start_date=LAST_YEAR)
        self.assertEqual(self.position.assignments.active().count(), 3)
        self.assertNotIn(self.position, Position.objects.vacant())
        self.assertIn(self.position, Position.objects.occupied())

    def test_a_retired_position_is_neither_vacant_nor_occupied(self):
        # The bug this class is named after. Retired with nobody in it is the
        # case that used to be reported as staffed.
        self.position.is_active = False
        self.position.save()
        self.assertNotIn(self.position, Position.objects.vacant())
        self.assertNotIn(self.position, Position.objects.occupied())
        self.assertIn(self.position, Position.objects.retired())

    def test_a_retired_position_that_still_has_a_holder_is_not_occupied_either(self):
        # Abolished while somebody was still in it — the variant that would slip
        # through if retired() were the only thing that had been fixed.
        self.hold("张三", start_date=LAST_YEAR)
        self.position.is_active = False
        self.position.save()
        self.assertNotIn(self.position, Position.objects.occupied())
        self.assertIn(self.position, Position.objects.retired())

    def test_vacant_occupied_and_retired_partition_the_whole_table(self):
        make_position("empty", "Empty Post", self.ministry)
        make_position("gone", "Abolished Post", self.ministry, is_active=False)
        self.hold("张三", start_date=LAST_YEAR)

        vacant = set(Position.objects.vacant())
        occupied = set(Position.objects.occupied())
        retired = set(Position.objects.retired())
        # No overlap, and between them they account for every row.
        self.assertEqual(vacant & occupied, set())
        self.assertEqual(vacant & retired, set())
        self.assertEqual(occupied & retired, set())
        self.assertEqual(vacant | occupied | retired, set(Position.objects.all()))

    def test_headcounts_are_counted_by_the_database_in_one_query(self):
        # The reason this is an annotation and not a property: the count for
        # every post on the page arrives in the query that fetched the page.
        for last_name in ("张三", "李四"):
            self.hold(last_name, start_date=LAST_YEAR)
        make_position("empty", "Empty Post", self.ministry)

        with self.assertNumQueries(1):
            counts = {
                position.pk: position.holder_count
                for position in Position.objects.with_headcounts()
            }
        self.assertEqual(counts[self.position.pk], 2)
        self.assertEqual(counts[Position.objects.get(code="empty").pk], 0)

    def test_headcount_separates_holders_from_those_actually_serving(self):
        # Somebody on leave still holds the post: they count as a holder, so
        # the post is not vacant, but they are not on the duty roster.
        self.hold("张三", start_date=LAST_YEAR)
        self.hold("李四", start_date=LAST_YEAR, status=Assignment.Status.ON_LEAVE)
        position = Position.objects.with_headcounts().get(pk=self.position.pk)
        self.assertEqual(position.holder_count, 2)
        self.assertEqual(position.serving_count, 1)

    def test_headcounts_accept_an_explicit_date(self):
        # Same injectable clock as active() and vacant() (D16), which makes
        # "how many people held this post last June" free.
        self.hold("张三", start_date=LAST_YEAR, end_date=YESTERDAY)
        today = Position.objects.with_headcounts().get(pk=self.position.pk)
        back_then = Position.objects.with_headcounts(on=LAST_YEAR).get(pk=self.position.pk)
        self.assertEqual(today.holder_count, 0)
        self.assertEqual(back_then.holder_count, 1)

    def test_the_headcount_column_does_not_query_once_per_row(self):
        for number in range(10):
            make_position(f"post{number}", f"Post {number}", self.ministry)
        self.client.force_login(
            get_user_model().objects.create_superuser(email="staff@example.com", password="x"))
        url = reverse("admin:org_position_changelist")

        with CaptureQueriesContext(connection) as captured:
            self.assertEqual(self.client.get(url).status_code, 200)
        few = len(captured)

        for number in range(10, 40):
            make_position(f"post{number}", f"Post {number}", self.ministry)
        with CaptureQueriesContext(connection) as captured:
            self.client.get(url)
        self.assertEqual(len(captured), few)

    def test_the_admin_filter_offers_a_retired_option(self):
        # Three states, three options. Folding retired into either of the other
        # two is exactly the bug this filter was rewritten to remove.
        options = [
            value for value, _ in
            StaffingFilter(None, {}, Position, None).lookups(None, None)
        ]
        self.assertEqual(options, ["vacant", "occupied", "retired"])

    def test_the_admin_filter_does_no_counting_of_its_own(self):
        # D18: each branch calls one QuerySet method. The old "not vacant"
        # negation lived here, in admin.py, which is where it went wrong.
        source = inspect.getsource(StaffingFilter.queryset)
        for spelling in ["exclude", "is_active", "Exists", "Count"]:
            self.assertNotIn(spelling, source)


class AssignmentTests(TestCase):
    def setUp(self):
        self.ministry = make_ministry()
        self.person = make_person("王强")
        self.cook = make_position("cook", "Cook", self.ministry)
        self.driver = make_position("driver", "Driver", self.ministry)

    def test_one_person_can_hold_two_positions_in_the_same_ministry(self):
        # D11's core scenario. The old constraint needed `title` in it to allow
        # this; two positions make it fall out for free.
        Assignment.objects.create(contact=self.person, position=self.cook, start_date=TODAY)
        Assignment.objects.create(contact=self.person, position=self.driver, start_date=TODAY)
        self.assertEqual(self.person.assignments.count(), 2)

    def test_one_person_can_hold_the_same_position_in_two_separate_stints(self):
        # Left and came back. Different start dates, so the unique constraint
        # has no opinion.
        Assignment.objects.create(
            contact=self.person, position=self.cook,
            start_date=LAST_YEAR, end_date=YESTERDAY)
        Assignment.objects.create(contact=self.person, position=self.cook, start_date=TODAY)
        self.assertEqual(self.person.assignments.count(), 2)

    def test_two_positions_for_one_person_can_have_different_managers(self):
        head_cook = make_position("head_cook", "Head Cook", self.ministry, is_leader=True)
        chair = make_position("chair", "Board Chair", kind=Position.Kind.BOARD)
        self.cook.reports_to = head_cook
        self.cook.save()
        self.driver.reports_to = chair
        self.driver.save()
        Assignment.objects.create(contact=self.person, position=self.cook)
        Assignment.objects.create(contact=self.person, position=self.driver)
        # Reads one level for each of two positions, which is not walking the
        # chain. core/tests.py is broad enough to ask; this is the answer.
        # loop-guard-ok
        managers = {a.position.reports_to_id for a in self.person.assignments.all()}
        self.assertEqual(managers, {head_cook.pk, chair.pk})

    def test_duplicate_assignment_with_null_start_date_is_rejected(self):
        # nulls_distinct=False. Postgres's default NULL != NULL would let any
        # number of these through, which is what A7 got wrong.
        Assignment.objects.create(contact=self.person, position=self.cook)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Assignment.objects.create(contact=self.person, position=self.cook)

    def test_assignment_end_date_cannot_precede_start_date(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Assignment.objects.create(
                contact=self.person, position=self.cook,
                start_date=TODAY, end_date=YESTERDAY)

    def test_deleting_a_position_with_assignments_is_blocked(self):
        # PROTECT: with CASCADE, retiring one box would delete the service
        # history of everybody who ever held it.
        Assignment.objects.create(contact=self.person, position=self.cook)
        with self.assertRaises(ProtectedError), transaction.atomic():
            self.cook.delete()

    def test_deleting_a_contact_with_assignments_is_blocked(self):
        # PROTECT on the other end too. This one used to be CASCADE, on the
        # reasoning "the file is gone, the tenure means nothing" — but that
        # sentence reads just as well on MinistryRole.contact, and there we
        # chose PROTECT because a grant has to leave a trace. Assignment
        # carries simple-history and is the only support for R8, so the same
        # answer holds: a person who only ever worked (never volunteered) was
        # deletable, and deleting them took the employment history silently.
        Assignment.objects.create(contact=self.person, position=self.cook)
        with self.assertRaises(ProtectedError), transaction.atomic():
            self.person.delete()

    def test_employment_type_is_allowed_on_an_unpaid_position(self):
        """The rule that used to refuse this is gone, and its absence is tested.

        ⚠️ Deleting the old test would have left nothing saying which way this
           goes now — and "an unpaid part-timer" is a real arrangement at this
           foundation, not an edge case. See Assignment's docstring and D37 section 1:
           how much of a week somebody gives is `fte`'s answer; employment_type
           went back to meaning the shape of the engagement.
        """
        full_time = EmploymentType.objects.create(code="full_time", name="Full time")
        assignment = Assignment(
            contact=self.person, position=self.cook, employment_type=full_time)
        assignment.full_clean()      # no raise

class AssignmentStatusTests(TestCase):
    """status and the term are orthogonal — leave never edits the dates."""

    def setUp(self):
        self.ministry = make_ministry()
        self.person = make_person("王强")
        self.position = make_position("cook", "Cook", self.ministry)
        self.assignment = Assignment.objects.create(
            contact=self.person, position=self.position, start_date=LAST_YEAR)

    def test_going_on_leave_leaves_the_tenure_dates_untouched(self):
        self.assignment.status = Assignment.Status.ON_LEAVE
        self.assignment.save()
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.start_date, LAST_YEAR)
        self.assertIsNone(self.assignment.end_date)

    def test_a_person_on_leave_is_excluded_from_serving_but_still_in_active(self):
        # The roster of who is on the team, and the roster of who is on duty
        # today, are two different questions.
        self.assignment.status = Assignment.Status.ON_LEAVE
        self.assignment.save()
        self.assertIn(self.assignment, Assignment.objects.active())
        self.assertNotIn(self.assignment, Assignment.objects.serving())

    def test_coming_back_from_leave_needs_no_second_assignment_row(self):
        self.assignment.status = Assignment.Status.ON_LEAVE
        self.assignment.save()
        self.assignment.status = Assignment.Status.ACTIVE
        self.assignment.save()
        self.assertIn(self.assignment, Assignment.objects.serving())
        self.assertEqual(Assignment.objects.count(), 1)

    def test_a_stale_on_leave_status_on_an_ended_tenure_is_inert(self):
        # Why no "status must agree with the dates" constraint is needed: both
        # predicates AND the dates, so an out-of-date status cannot bring
        # anybody back.
        self.assignment.status = Assignment.Status.ON_LEAVE
        self.assignment.end_date = YESTERDAY
        self.assignment.save()
        self.assertNotIn(self.assignment, Assignment.objects.active())
        self.assertNotIn(self.assignment, Assignment.objects.serving())

    def test_assignment_status_has_no_ended_value(self):
        # Ending is said by end_date and nowhere else. A second place to say it
        # is a second answer, and one of them goes stale.
        self.assertNotIn("ended", Assignment.Status.values)


class MinistryRoleTests(TestCase):
    """The table. Its point is the scope — see PermissionTests below for that."""

    def setUp(self):
        self.pantry = make_ministry()
        self.wang = make_person("Wang")

    def grant(self, contact=None, ministry=None, **kwargs):
        return MinistryRole.objects.create(
            contact=contact or self.wang, ministry=ministry or self.pantry, **kwargs)

    def test_duplicate_grant_with_no_start_date_is_rejected(self):
        # nulls_distinct=False: start_date is nullable and routinely left empty,
        # and without it Postgres would wave every duplicate through.
        self.grant()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.grant()

    def test_the_same_person_can_be_granted_in_two_ministries(self):
        self.grant()
        self.grant(ministry=make_ministry(code="tax_help", name="Tax Help"))
        self.assertEqual(self.wang.ministry_roles.count(), 2)

    def test_end_date_cannot_precede_start_date(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.grant(start_date=TODAY, end_date=YESTERDAY)

    def test_deleting_a_ministry_with_grants_is_blocked(self):
        # PROTECT, not CASCADE. This table claims that changes of authority
        # leave a trace; letting a ministry deletion take a batch of grants with
        # it would contradict that claim silently.
        self.grant()
        with self.assertRaises(ProtectedError):
            self.pantry.delete()

    def test_deleting_a_person_with_grants_is_blocked(self):
        self.grant()
        with self.assertRaises(ProtectedError):
            self.wang.delete()

    def test_deleting_the_granting_user_keeps_the_grant(self):
        # SET_NULL. CASCADE here would revoke a batch of people's authority
        # because somebody's account was closed.
        granter = get_user_model().objects.create_user(email="boss@example.com", password="x")
        grant = self.grant(granted_by=granter)
        granter.delete()
        grant.refresh_from_db()
        self.assertIsNone(grant.granted_by)

    def test_grants_reuse_the_shared_active_predicate(self):
        # Not a permanent boolean: authority starts and ends, exactly like a
        # tenure, so it uses the one definition of "in effect".
        expired = self.grant(start_date=LAST_YEAR, end_date=YESTERDAY)
        self.assertNotIn(expired, MinistryRole.objects.active())
        self.assertIn(expired, MinistryRole.objects.active(on=YESTERDAY))


class PermissionTests(TestCase):
    """D20's acceptance point: authority is scoped, and nothing gets a bypass."""

    def setUp(self):
        self.pantry = make_ministry()
        self.tax = make_ministry(code="tax_help", name="Tax Help")
        self.zhang = make_person("Zhang")
        self.user = get_user_model().objects.create_user(
            email="zhang@example.com", password="x", contact=self.zhang)
        MinistryRole.objects.create(contact=self.zhang, ministry=self.pantry)

    def make_event(self, ministry):
        event_type, _ = EventType.objects.get_or_create(
            code="distribution", defaults={"name": "Distribution"})
        return Event.objects.create(
            name="Distribution", event_type=event_type, ministry=ministry,
            start_time=local_now(), end_time=local_now() + datetime.timedelta(hours=2),
            owner=self.zhang,
        )

    def test_a_ministry_admin_can_publish_for_their_own_ministry(self):
        self.assertTrue(can_publish_event(self.user, self.pantry))

    def test_a_ministry_admin_cannot_publish_for_another_ministry(self):
        # ⭐ D20 in one line. Fail this and scoped authority was not built:
        # a Django Group would have said yes here.
        self.assertFalse(can_publish_event(self.user, self.tax))

    def test_managing_another_ministrys_event_is_refused(self):
        self.assertTrue(can_manage_event(self.user, self.make_event(self.pantry)))
        self.assertFalse(can_manage_event(self.user, self.make_event(self.tax)))

    def test_an_expired_grant_stops_conferring_permission(self):
        MinistryRole.objects.update(end_date=YESTERDAY)
        self.assertFalse(can_publish_event(self.user, self.pantry))

    def test_a_future_grant_does_not_confer_permission_yet(self):
        # The other half of .active(), and the half most often left out.
        MinistryRole.objects.update(start_date=TODAY + datetime.timedelta(days=1))
        self.assertFalse(can_publish_event(self.user, self.pantry))

    def test_a_grant_on_a_retired_ministry_confers_nothing(self):
        self.pantry.is_active = False
        self.pantry.save()
        self.assertFalse(can_publish_event(self.user, self.pantry))

    def test_a_user_with_no_grants_is_denied_everything(self):
        # Deny by default, never "allowed unless forbidden".
        stranger = get_user_model().objects.create_user(
            email="stranger@example.com", password="x", contact=make_person("Stranger"))
        self.assertFalse(can_publish_event(stranger, self.pantry))
        self.assertFalse(can_view_event_records(stranger, self.make_event(self.pantry)))
        self.assertFalse(can_grant_ministry_admin(stranger))

    def test_a_user_with_no_contact_is_denied_everything_without_raising(self):
        # A normal state, not an error: MinistryRole hangs off Contact while the
        # entry point is a User, and User.contact must stay nullable (D12/D21).
        # Raising here would 500 every protected view for such an account.
        technical = get_user_model().objects.create_user(email="tech@example.com", password="x")
        self.assertEqual(ministry_ids_administered_by(technical), set())
        self.assertFalse(can_publish_event(technical, self.pantry))

    def test_a_superuser_gets_no_ministry_scope_either(self):
        # No exemption. One here would be a hole straight through the scoping
        # that D20 exists to create; a superuser has the admin already.
        root = get_user_model().objects.create_superuser(email="root@example.com", password="x")
        self.assertFalse(can_publish_event(root, self.pantry))
        self.assertFalse(can_grant_ministry_admin(root))

    def test_an_anonymous_visitor_is_denied_without_raising(self):
        self.assertFalse(can_publish_event(AnonymousUser(), self.pantry))
        self.assertFalse(can_grant_ministry_admin(AnonymousUser()))

    def test_ministry_admins_cannot_grant_ministry_admin(self):
        # P5 is genuinely global, so it reads the Group and never MinistryRole:
        # a ministry admin must not be able to recruit their own downline.
        self.assertFalse(can_grant_ministry_admin(self.user))
        self.user.groups.add(foundation_admin_group())
        self.assertTrue(can_grant_ministry_admin(self.user.__class__.objects.get(pk=self.user.pk)))

    def test_the_id_set_is_ids_not_objects(self):
        # The name says ids because the return value is ids. Two documents once
        # gave this function two names; it is the most-called one we have.
        self.assertEqual(ministry_ids_administered_by(self.user), {self.pantry.pk})


class FoundationAdminGroupTests(TestCase):
    """The global tier's permissions, and the reconciliation that keeps them true.

    ⚠️ 这一组存在的理由是一次真实的静默失败：`foundation_admin_group()` 原来写的是
       `if created or not group.permissions.exists()`，于是**在任何已经有这个组的
       库上，往清单里加一条权限都不会生效** —— 清单是对的，组是旧的，没有任何东西
       报告这个差别。症状是 admin 首页少一个模块，看起来像「页面没做」。
    """

    def test_the_group_grants_what_the_list_says(self):
        group = foundation_admin_group()
        granted = {
            f"{p.content_type.app_label}.{p.codename}" for p in group.permissions.all()
        }
        # Subset rather than equality: a permission named in the list but absent
        # from this database (an app not installed yet) is skipped by design.
        self.assertTrue(granted <= set(FOUNDATION_ADMIN_PERMISSIONS))
        self.assertIn("org.add_ministry", granted,
                      "A production database starts with no ministries and nothing "
                      "else can create one.")

    def test_it_never_grants_delete_ministry(self):
        # Deleting a ministry cascades into its events. "We stopped running it"
        # is is_active=False — an ending is a date, not a deletion.
        granted = {p.codename for p in foundation_admin_group().permissions.all()}
        self.assertNotIn("delete_ministry", granted)

    def test_a_migrate_is_what_keeps_the_group_true_in_production(self):
        """⚠️ The second half of the same bug, and the half that mattered more.

        Making `foundation_admin_group()` reconcile was not enough: the only
        things that called it were `seed_demo` and the tests, and **neither runs
        on a deployed site**. Adding a permission to the list therefore did
        nothing at all in production — silently — and the only symptom was a
        page missing from the admin index.

        It is now wired to `post_migrate`, and `migrate` runs on every deploy.
        This asserts the wiring, not the function: strip the group down, run
        migrate, and it has to come back.
        """
        group, _ = Group.objects.get_or_create(name=FOUNDATION_ADMIN_GROUP)
        group.permissions.clear()

        call_command("migrate", verbosity=0)

        granted = {p.codename for p in group.permissions.all()}
        self.assertIn("add_ministry", granted)
        self.assertIn("change_homepage", granted)

    def test_an_existing_group_is_brought_up_to_date(self):
        """The bug this whole class is about, as an assertion.

        Build the group with one stale permission, then call again: the second
        call has to reconcile it, not walk away because the group was non-empty.
        """
        group, _ = Group.objects.get_or_create(name=FOUNDATION_ADMIN_GROUP)
        stale = Permission.objects.get(
            content_type__app_label="org", codename="view_ministryrole")
        group.permissions.set([stale])

        refreshed = foundation_admin_group()
        granted = {p.codename for p in refreshed.permissions.all()}
        self.assertIn("add_ministry", granted)
        self.assertGreater(len(granted), 1)
