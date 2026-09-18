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
from django.template.defaultfilters import date as date_filter
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from contact.models import Contact
from core.timeutils import local_date_of, local_now, local_today
from events.models import Event

from .admin import StaffingFilter
from .forms import PositionForm
from .models import Assignment, EmploymentType, Ministry, MinistryRole, Position
from .permissions import (
    FOUNDATION_ADMIN_GROUP,
    FOUNDATION_ADMIN_PERMISSIONS,
    can_grant_ministry_admin,
    can_manage_event,
    can_manage_staff_roster,
    can_publish_event,
    can_view_event_records,
    foundation_admin_group,
    in_foundation_tier,
    ministry_ids_administered_by,
    unresolved_permissions,
)
from .audience import on_the_books_q
from .services import (build_org_tree, end_assignment, grant_ministry_admin,
                       ministry_admins, refuse_a_second_live_tenure,
                       revoke_ministry_role)

TODAY = local_today()
YESTERDAY = TODAY - datetime.timedelta(days=1)
TOMORROW = TODAY + datetime.timedelta(days=1)
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
        "assignment_overlapping_tenure",
        "ministryrole_overlapping_grant",
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
        """🔴 这一条差点被排他约束变成一个 500（D51）。

        `ExclusionConstraint.validate()` 会把这一行的值代进 `daterange(...)`
        发给 Postgres，而 `daterange('2023-01-01','2020-01-01')` 是一个
        `DataError` —— 人话消息根本来不及产生。修法是给那条排他约束加一个
        `condition`（终点不早于起点），它在数据库里一行都不排除，
        只是让 `validate()` 走另一条分支。整段写在 `org/models.py` 上。
        """
        messages = self.assertFieldError(
            Assignment(contact=self.person, position=self.position,
                       start_date=datetime.date(2023, 1, 1),
                       end_date=datetime.date(2020, 1, 1)),
            "end_date",
        )
        self.assertIn("The end date cannot be before the start date.", messages)

    def test_an_overlapping_tenure_points_at_contact(self):
        """⚠️ 落在 `contact` 上，不是 `start_date`（D51）。

        约束问的是「这个人在这个岗位上有没有一段压着的时间」，而人是那张表单上
        唯一可选的格子 —— `AssignmentForm` 的 `position` 来自地址栏。
        """
        Assignment.objects.create(contact=self.person, position=self.position)
        messages = self.assertFieldError(
            Assignment(contact=self.person, position=self.position), "contact")
        self.assertIn("already holds this post", " ".join(messages))

    def test_an_inverted_range_does_not_blow_up_the_overlap_check(self):
        """🔴 **`refuse_a_second_live_tenure()` 自己会发一次 `daterange()`**
           （2026-09-18 review 抓到）。

        `daterange('2023-01-01','2020-01-01')` 在 Postgres 里是
        `DataError: range lower bound must be less than or equal to ...` ——
        一个连「哪一格错了」都说不出的 500。排他约束上那个 `condition=` 挡的是
        同一件事，但它挡不到这个函数：这个函数自己发查询。

        ⚠️ **这一条直接调那个函数，不走 `full_clean()`** —— 走 `full_clean()`
           的话落到的是排他约束的 `condition`，那条路上面已经有测试，
           而这个函数一个字都不会被执行到。第一版就是这么写的，验红时没红。

        ⚠️ 挡住之后，「结束早于开始」由**下一道关**回答（`end_date >=
           start_date` 那条 CheckConstraint，经 `ConstraintErrorFieldMixin`
           落到 `end_date` 那一格）。这个函数的职责只是别在它之前炸掉。
        """
        refuse_a_second_live_tenure(
            contact=self.person, position=self.position,
            start_date=datetime.date(2023, 1, 1),
            end_date=datetime.date(2020, 1, 1))  # 不抛，尤其不抛 DataError

    def test_two_overlapping_historical_tenures_are_refused(self):
        """🔴 **旧版的重复检查一条都拦不住这一格**（D51）。

        它查的是 `active(on=今天)`「两条今天都活着」，而这两段都在 2020 年 ——
        今天一条都不活。于是补录进去不报错，而 `with_headcounts(on=2020…)`
        在回溯报表里把这个人数成两个。现在问的是区间重叠，和数据库那条约束
        同一句话。
        """
        Assignment.objects.create(
            contact=self.person, position=self.position,
            start_date=datetime.date(2020, 1, 1), end_date=datetime.date(2021, 1, 1))
        messages = self.assertFieldError(
            Assignment(contact=self.person, position=self.position,
                       start_date=datetime.date(2020, 6, 1),
                       end_date=datetime.date(2020, 8, 1)),
            "contact")
        self.assertIn("already holds this post", " ".join(messages))

    def test_a_tenure_that_does_not_overlap_is_fine(self):
        """⭐ 而这一格正是旧约束办不到的：**同一个人、同一个岗位、回来第二段**。

        旧的 `UNIQUE(contact, position, start_date)` 只比日期是不是一样，
        于是「离职 → 回来」撞不撞全看那一格填了什么。区间不相交问的是真规则。
        """
        Assignment.objects.create(
            contact=self.person, position=self.position,
            start_date=datetime.date(2020, 1, 1), end_date=datetime.date(2021, 1, 1))
        again = Assignment(
            contact=self.person, position=self.position,
            start_date=datetime.date(2021, 1, 1))
        again.full_clean()  # 不抛 —— `[2020,2021)` 和 `[2021,)` 不重叠。
        again.save()
        self.assertEqual(
            Assignment.objects.filter(
                contact=self.person, position=self.position).count(), 2)

    def test_an_overlapping_grant_points_at_contact(self):
        MinistryRole.objects.create(contact=self.person, ministry=self.ministry)
        messages = self.assertFieldError(
            MinistryRole(contact=self.person, ministry=self.ministry), "contact")
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

    def test_a_post_is_still_held_on_its_holders_last_day(self):
        """⚠️ 边界那一格，隔着一层关系从来没有人问过（在这条之前）。

        上面每一条边界用的都是 `end_date=YESTERDAY`。而空缺查询走的是
        `_has_a_holder()` —— `active()` 套一个 `OuterRef("pk")`，和直接调
        `active()` **不是同一段 SQL**。这一条和下面那条各钉一条路径。

        ⚠️ `end_date=TOMORROW` 读作「最后一天是今天」（右开，D51），也正是
           `end_assignment(last_day=今天)` 会写下的那个值。
        """
        self.hold(start_date=LAST_YEAR, end_date=TOMORROW)
        self.assertNotIn(self.position, Position.objects.vacant())
        self.assertIn(self.position, Position.objects.occupied())
        # 而明天起它就空了。
        self.assertIn(self.position, Position.objects.vacant(on=TOMORROW))

    def test_a_tenure_on_its_last_day_still_counts_in_the_headcount(self):
        """⚠️ 第三条路径：`in_effect_on(prefix="assignments__")`（`org/models.py`
           的 `with_headcounts()`）。它跨的是关系前缀而不是子查询，同样没被问过
           这一格。
        """
        self.hold(start_date=LAST_YEAR, end_date=TOMORROW)
        counted = Position.objects.with_headcounts().get(pk=self.position.pk)
        self.assertEqual(counted.holder_count, 1)
        tomorrow = Position.objects.with_headcounts(on=TOMORROW).get(pk=self.position.pk)
        self.assertEqual(tomorrow.holder_count, 0)

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

    def test_a_second_tenure_covering_the_same_day_is_rejected(self):
        # ⚠️ 这一条从前叫 `test_duplicate_assignment_with_null_start_date_is_rejected`，
        #    钉的是 `nulls_distinct=False`（那时 `start_date` 可空、常常留空，
        #    而 Postgres 认 NULL != NULL）。D51 之后起始日期不可为空、两条都默认
        #    今天，拦下它们的是**区间重叠**。
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


class EndAssignmentTests(TestCase):
    """`end_assignment()` 写了哪一天，以及那一天当天他还算不算在编。

    🔴 **这个函数此前没有任何服务层测试** —— 只有一条视图测试
       （`OrgViewTests.test_ending_a_tenure_dates_it_rather_than_deleting_it`）
       断言它写了今天的日期。而「写下哪个日期」不是这个函数的行为，
       「从哪一天起他不在名单上」才是，那一半一直没有网。

    ⚠️ 这三条是**特征化测试**：它们记录当前行为，不主张当前行为是对的。
       `org/services.py` 的 `end_assignment()` docstring 写着「结束之后这个人
       **当场**不再算这个 ministry 的在编人员」—— 而第二条证明那句话是假的：
       他今天还在。两者之中错的是那句注释，不是代码。
    """

    def setUp(self):
        self.ministry = make_ministry()
        self.person = make_person("王强")
        self.position = make_position("cook", "Cook", self.ministry)
        self.assignment = Assignment.objects.create(
            contact=self.person, position=self.position, start_date=LAST_YEAR)

    def on_the_books(self, on):
        return Assignment.objects.filter(
            on_the_books_q(on), contact=self.person).exists()

    def test_ending_a_tenure_records_the_day_after_the_last_day(self):
        """⚠️ 存的是**次日**：`end_date` 右开，是第一个不算数的日子（D51）。

        参数也叫 `last_day` 而不是 `on` —— HR 手上有的日期是「最后一班是哪天」
        （Workday 的 termination effective date 就是 last day of employment），
        `+1` 只发生在服务层那一行。
        """
        end_assignment(self.assignment)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.end_date, TOMORROW)

    def test_an_explicit_last_day_is_stored_as_the_day_after(self):
        end_assignment(self.assignment, last_day=YESTERDAY)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.end_date, TODAY)
        self.assertFalse(self.on_the_books(TODAY))
        self.assertTrue(self.on_the_books(YESTERDAY))

    def test_somebody_ended_today_is_still_on_the_books_for_the_rest_of_today(self):
        end_assignment(self.assignment)
        self.assertIn(self.assignment, Assignment.objects.active())
        self.assertTrue(self.on_the_books(TODAY))

    def test_they_are_off_the_books_from_tomorrow(self):
        end_assignment(self.assignment)
        self.assertNotIn(self.assignment, Assignment.objects.active(on=TOMORROW))
        self.assertFalse(self.on_the_books(TOMORROW))


class MinistryRoleTests(TestCase):
    """The table. Its point is the scope — see PermissionTests below for that."""

    def setUp(self):
        self.pantry = make_ministry()
        self.wang = make_person("Wang")

    def grant(self, contact=None, ministry=None, **kwargs):
        return MinistryRole.objects.create(
            contact=contact or self.wang, ministry=ministry or self.pantry, **kwargs)

    def test_two_grants_covering_the_same_day_are_rejected(self):
        # 区间不相交（D51）。⚠️ 这一条从前叫
        # `test_duplicate_grant_with_no_start_date_is_rejected`，钉的是
        # `nulls_distinct=False` —— 那时 `start_date` 可空且常常留空，Postgres
        # 认 NULL != NULL，没有那个标志就会放行任意多条。现在起始日期不可为空，
        # 两条都默认今天，拦下它们的是区间重叠。
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
        # ⚠️ 「昨天还有效」现在写成 `end_date = 今天` —— 右开，结束日期是第一个
        #    不算数的日子（D51）。写 `end_date = 昨天` 的意思变成了「昨天就已经
        #    不算了」，那是另一句话。
        expired = self.grant(start_date=LAST_YEAR, end_date=TODAY)
        self.assertNotIn(expired, MinistryRole.objects.active())
        self.assertIn(expired, MinistryRole.objects.active(on=YESTERDAY))

    def test_granting_again_after_a_revoke_opens_a_second_row(self):
        """⚠️ 和 `events.tests.EventGrantTests` 那条对称 —— 两张表，一条规矩。

        「恢复」分支 2026-09-17 删掉了（D51）：撤销那一行是 `[…, 今天)`，
        再授权那一行是 `[今天, …)`，两段不重叠，所以是第二行，
        而中间断过那件事**留在当前表里**，不是只留在 simple-history 里。
        """
        grant = grant_ministry_admin(
            contact=self.wang, ministry=self.pantry, granted_by=None)
        revoke_ministry_role(grant)

        again = grant_ministry_admin(
            contact=self.wang, ministry=self.pantry, granted_by=None)
        self.assertNotEqual(again.pk, grant.pk)
        grant.refresh_from_db()
        self.assertEqual(grant.end_date, TODAY, "旧行不许被改回来")
        self.assertEqual(MinistryRole.objects.filter(
            contact=self.wang, ministry=self.pantry).count(), 2)

    def test_the_two_rows_are_listed_in_a_total_order(self):
        """⚠️ 同 `events.tests.EventGrantTests.test_the_two_rows_are_listed_in_a_total_order`
           —— 两张授权页一样的形状，一样的并列。

        ⚠️ 只断言「这一次顺序对不对」是抓不住的（那边记着验红的经过）；
           钉住它的是「排序键是不是全序」。
        """
        first = grant_ministry_admin(
            contact=self.wang, ministry=self.pantry, granted_by=None)
        revoke_ministry_role(first)
        second = grant_ministry_admin(
            contact=self.wang, ministry=self.pantry, granted_by=None)

        rows = ministry_admins(self.pantry)
        self.assertEqual([row.pk for row in rows], [second.pk, first.pk])

        keys = list(rows.query.order_by) or list(MinistryRole._meta.ordering)
        self.assertIn(
            keys[-1].lstrip("-"), {"pk", "id"},
            f"排序键 {keys} 不是全序 —— 并列的两行先后由数据库随手定")

    def test_revoking_a_row_that_already_ended_changes_nothing(self):
        """🔴 **不挡的话这是一个无人接管的 500**（2026-09-18 review 抓到）。

        `find_grant()` 只按 ministry 收窄，拿得到**已经结束的**行，而 D51 之后
        「同一把钥匙上两行」是常态。一个带旧 pk 的 POST（页面开着没刷新，别人
        同时撤了又发了一次）会把旧行的 `end_date` 重写成今天 —— 那一段区间
        因此**撑进**新行里，撞上排他约束。

        ⚠️ 顺带还抹掉了这一轮特意保住的那段断档，而那是静默的那一半。
        """
        old = self.grant(start_date=LAST_YEAR,
                         end_date=LAST_YEAR + datetime.timedelta(days=30))
        live = self.grant(start_date=YESTERDAY)

        revoke_ministry_role(old)  # 不抛

        old.refresh_from_db()
        self.assertEqual(old.end_date, LAST_YEAR + datetime.timedelta(days=30),
                         "旧行的结束日期不许被重写")
        live.refresh_from_db()
        self.assertIsNone(live.end_date)

    def test_revoking_a_grant_that_has_not_started_yet_cancels_it(self):
        """🔴 **不夹住的话这是一个点两下就能撞到的 500**（2026-09-18，用户拍板）。

        「起始日期」那一格收将来的日子（「她下周一接手」是正常需求），而那一行
        发出来之后旁边就渲染着撤销键（模板只问 `end_date` 空不空）。点它，
        服务层写下「到今天为止」—— 一个**终点早于起点**的区间，
        `end_date >= start_date` 那条 CheckConstraint 当场拒绝，
        而这里不走 `full_clean()`，于是那是一个无人接管的 `IntegrityError`。

        ⚠️ 夹成 `start_date` 之后它是一个**空区间**：什么都不覆盖、永不生效，
           而两个日期都如实留着 —— 「从下周一起，下周一撤销」读作「还没开始
           就被取消了」。行不删，审计线完整。
        """
        future = TODAY + datetime.timedelta(days=7)
        grant = self.grant(start_date=future)
        self.assertFalse(grant.is_currently_active, "它本来就还没生效")

        revoke_ministry_role(grant)  # 不抛

        grant.refresh_from_db()
        self.assertEqual(grant.end_date, future, "夹到起始日期，不是今天")
        self.assertFalse(grant.is_currently_active)
        # ⭐ 空区间 —— 到了那一天也不生效。
        self.assertNotIn(grant, MinistryRole.objects.active(on=future))
        self.assertNotIn(
            grant, MinistryRole.objects.active(on=future + datetime.timedelta(days=1)))

    def test_granting_to_somebody_who_already_has_it_is_refused(self):
        grant_ministry_admin(
            contact=self.wang, ministry=self.pantry, granted_by=None)
        with self.assertRaises(ValidationError) as caught:
            grant_ministry_admin(
                contact=self.wang, ministry=self.pantry, granted_by=None)
        self.assertIn("contact", caught.exception.message_dict)


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
        return Event.objects.create(
            name="Distribution", ministry=ministry,
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
        # ⚠️ 起止一起给：`start_date` 自 D51 起不可为空、默认今天，而
        #    「今天开始、昨天结束」过不了 `end_date >= start_date` —— 那条约束
        #    是对的，这一行本来就是胡说，只是从前 `start_date` 为空所以看不见。
        MinistryRole.objects.update(start_date=LAST_YEAR, end_date=YESTERDAY)
        self.assertFalse(can_publish_event(self.user, self.pantry))

    def test_a_future_grant_does_not_confer_permission_yet(self):
        # The other half of .active(), and the half most often left out.
        #
        # 🔴 `local_today()` **here**, not the module-level TODAY. That constant is
        #    frozen at import time, and "tomorrow" measured from it stops being
        #    tomorrow the moment the suite crosses local midnight — the grant
        #    starts today, `.active()` says yes, and this assertion fails with
        #    `True is not false` on a change that touched nothing near it.
        #    That is not hypothetical: CI ran 2026-08-28 23:48 PT, took 11 minutes,
        #    and printed this failure at 00:00:40 PT.
        #    ⚠️ The other TODAY users are safe by shape: they anchor rows that are
        #       already active or already expired, and both stay true a day later.
        #       Only a *future* date computed from a frozen "today" can flip.
        MinistryRole.objects.update(
            start_date=local_today() + datetime.timedelta(days=1))
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

    def test_revoking_takes_effect_at_once_not_tomorrow(self):
        """🔴 **撤销当场生效**（2026-09-15，用户拍板）。

        ⭐ 撤销不是一段事实，是一个**即时动作**：按钮说「撤销」，发生的就该是
           「现在起撤销」。写进去的是**今天**，而 `end_date` 右开 —— 今天是第一个
           不算数的日子，所以他今天就什么都做不了了。
           钉这条读法的是 `core.tests.ActiveQuerySetTests
           .test_active_excludes_a_row_ending_today`。

        ⚠️ 这一条在 2026-09-15 之前**不存在**，而那正是问题：这个行为一直是
           右闭的（明天才失效），没有任何东西确认过它是有意的。是 D47 落地时
           撞上的。

        ⚠️ 2026-09-15 到 09-17 之间，它靠的是一条**第二谓词**（`in_force()`）；
           D51 把语义轴消掉之后，靠的是**写进去的是哪一天** ——
           而下面那条任职测试就是同一条读法的另一半。
        """
        grant = MinistryRole.objects.get(contact=self.zhang, ministry=self.pantry)
        self.assertEqual(ministry_ids_administered_by(self.user), {self.pantry.pk})

        revoke_ministry_role(grant)
        self.assertEqual(ministry_ids_administered_by(self.user), set())

        # ⚠️ 而那一行**还在**，日期也还是今天 —— 「去年三月谁能看这个 ministry
        #    的报名」仍然答得出来。
        grant.refresh_from_db()
        self.assertEqual(grant.end_date, TODAY)
        self.assertTrue(MinistryRole.objects.filter(pk=grant.pk).exists())

    def test_a_tenure_whose_last_day_is_today_still_counts_today(self):
        """⚠️ 和上面那条撤销测试并排 —— 两种事实，**一条读法**（D51）。

        差别全在**写进去的是哪一天**：撤销存「今天」（今天起失效），任职结束
        存「最后一天的次日」（`end_assignment(last_day=...)`）。读的都是右开的
        `active()`。

        🔴 2026-09-17 之前这两句话是靠**两条谓词**表达的（`active()` 右闭、
           `in_force()` 右开），而那正是四种写法走散的根。少了这一条，下一个人
           会以为任职也该在最后一天当天就下名单。
        """
        post = make_position(None, "Greeter", ministry=self.pantry)
        tenure = Assignment.objects.create(
            contact=self.zhang, position=post, end_date=TOMORROW)
        self.assertTrue(tenure.is_currently_active)
        self.assertIn(tenure, Assignment.objects.active())
        self.assertNotIn(tenure, Assignment.objects.active(on=TOMORROW))

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

    def test_an_unsaved_account_is_answered_not_raised(self):
        """🔴 `is_authenticated` **拦不住一个没存过的 User**。

        Django 的 `AbstractBaseUser.is_authenticated` 是一个硬编码的 `True`，
        所以 `in_foundation_tier(User())` 走得到 `user.groups` —— 而那个关系对
        一个没有主键的实例直接抛 `ValueError`，也就是一个 500 而不是一句「不是」。

        ⚠️ 这不是编出来的输入：2026-09-16 `PublishFormMixin.__init__` 开始问这个
           问题之后，`core.tests.TextLengthLimitTests` 那两条（用一个空 `User()`
           构造 `EventForm`，只为了验一个字数上限）当场变成 ERROR。

        ⚠️ 它的兄弟 `ministry_ids_administered_by()` 早就兜住了同一种输入。
           两个并排的谓词对同一个输入一个答 False、一个 500，是这个模块最不该
           有的那种不一致 —— 这一条钉的是它们答得一样。
        """
        nobody = get_user_model()()
        self.assertFalse(in_foundation_tier(nobody))
        self.assertEqual(ministry_ids_administered_by(nobody), set())

    def test_the_group_grants_what_the_list_says(self):
        group = foundation_admin_group()
        granted = {
            f"{p.content_type.app_label}.{p.codename}" for p in group.permissions.all()
        }
        # Subset rather than equality: a permission named in the list but absent
        # from this database (an app not installed yet) is skipped by design.
        #
        # ⚠️ Which is why this assertion can never catch a name that resolves to
        #    nothing — it passes more easily the more of the list is broken.
        #    That is what test_every_named_permission_resolves below is for; the
        #    two are a pair and neither is sufficient alone.
        self.assertTrue(granted <= set(FOUNDATION_ADMIN_PERMISSIONS))
        self.assertIn("org.add_ministry", granted,
                      "A production database starts with no ministries and nothing "
                      "else can create one.")

    #: Registered in the admin and deliberately **not** granted to this tier.
    #: Every entry needs a reason, because the guard below turns "forgot" into a
    #: failure and this list is the only way to say "meant it".
    SUPERUSER_ONLY = {
        # Credentials and identity. Being able to administer a ministry is not
        # being able to read the account table.
        "accounts.user": "sign-in accounts, not foundation data",
        # 🔴 The people themselves. This tier administers *ministries*; reading
        #    every contact in the foundation is a different power, and D21's
        #    self-service pages are how a person reaches their own record.
        "contact.contact": "everybody's personal details — a different power",
        # Employment records: pay band adjacent, and D32's axes live here.
        "org.assignment": "employment records",
        # ⚠️ These two are lookup tables, and their three siblings
        #    (events.view_participationrole, org.view_position,
        #    org.view_employmenttype) **are** granted a few lines above under
        #    "the other lookup tables: read-only for now". Nobody has ever
        #    written down why these two differ, and the honest reading is that
        #    they were missed the same way L5.2's two tables were. Parked here
        #    rather than granted, because widening this tier is the foundation's
        #    call and not a tidy-up — but parked **visibly**, which is the whole
        #    point of this list.
        "contact.language": "⚠️ undecided — see note above",
        "contact.relationshiptype": "⚠️ undecided — see note above",
    }

    def test_every_registered_model_is_granted_or_named(self):
        """🔴 Registering a model in admin.py does **not** make it reachable.

        Django hides a model from the admin index entirely when the account
        holds no permission on it, so a table can be registered, tested and
        invisible to every non-superuser at once — and what that looks like is
        a page that was never built, not a page that refuses.

        This has now happened three times in this file's history: `add_ministry`
        (the note above), and both of L5.2's tables on 2026-09-08. The first two
        fixes were to add the missing label; this is the fix that makes the
        *next* one fail here instead of in front of somebody.

        ⚠️ It walks the admin registry rather than asserting a list of names —
           the same shape as events.tests' guard on audience-editing admins, and
           for the same reason: a hardcoded assertion only ever re-detects the
           incident it was written for. Adding a model to admin.py now forces a
           decision, and either answer (grant it, or name it below) is one line.
        """
        from django.contrib import admin as django_admin

        # ⚠️ Imported here rather than re-derived: core/tests.py holds the one
        #    definition of "an app in this repository", and a second copy would
        #    drift the first time a new app lands. Deferred into the method
        #    because that is how this project's tests reach across apps.
        from core.tests import OUR_APPS

        granted = {
            f"{p.content_type.app_label}.{p.codename}"
            for p in foundation_admin_group().permissions.all()
        }
        unreachable = []
        for model in django_admin.site._registry:
            meta = model._meta
            if meta.app_label not in OUR_APPS:
                continue
            name = f"{meta.app_label}.{meta.model_name}"
            if name in self.SUPERUSER_ONLY:
                continue
            if f"{meta.app_label}.view_{meta.model_name}" not in granted:
                unreachable.append(name)
        self.assertEqual(
            sorted(unreachable), [],
            "Registered in the admin but nobody in the foundation tier holds "
            "view on it, so it is not on their admin index at all — it looks "
            "exactly like a page that was never built. Either add "
            "`<app>.view_<model>` to FOUNDATION_ADMIN_PERMISSIONS, or name it "
            "in SUPERUSER_ONLY with the reason:\n" + "\n".join(sorted(unreachable)))

    def test_the_superuser_only_list_has_no_stale_entries(self):
        """The other direction, the same as CONSTRAINT_FIELD's stale check.

        A model that leaves the admin, or that later gets granted, must not
        leave a line behind claiming a decision nobody is making any more.
        """
        from django.contrib import admin as django_admin

        registered = {
            f"{m._meta.app_label}.{m._meta.model_name}"
            for m in django_admin.site._registry
        }
        granted = {
            f"{p.content_type.app_label}.{p.codename}"
            for p in foundation_admin_group().permissions.all()
        }
        stale = sorted(
            name for name in self.SUPERUSER_ONLY
            if name not in registered
            or f"{name.split('.')[0]}.view_{name.split('.')[1]}" in granted
        )
        self.assertEqual(
            stale, [],
            f"SUPERUSER_ONLY names something no longer registered, or something "
            f"that is now granted after all: {stale}")

    def test_every_named_permission_resolves(self):
        """🔴 A label that names nothing grants nothing, silently.

        `events.view_eventtype` sat in the list for three days after its model
        was deleted, and nothing went red: the builder skips what it cannot
        resolve, and the subset assertion above gets *easier* to satisfy as the
        list rots. A mistyped codename fails exactly the same way and looks
        correct in every listing.
        """
        self.assertEqual(unresolved_permissions(), [])

    def test_it_never_grants_delete_ministry(self):
        # "We stopped running it" is is_active=False — an ending is a date, not
        # a deletion. ⚠️ The comment here used to say deleting cascades into
        # the ministry's events; it does not (Event.ministry is PROTECT). What
        # cascades is the audience many-to-many — see the test below, and
        # org/permissions.py for the corrected reason.
        granted = {p.codename for p in foundation_admin_group().permissions.all()}
        self.assertNotIn("delete_ministry", granted)

    def test_deleting_a_ministry_empties_the_audiences_that_named_it(self):
        """🔴 What actually cascades, pinned because the comments named the wrong thing.

        A ministry that owns events cannot be deleted at all — Event.ministry is
        PROTECT. A ministry that owns nothing but is *ticked into* somebody
        else's event is a different row: the audience many-to-many has no
        on_delete of its own, so the tick goes, and an event left with an empty
        audience is invisible to everybody. That is exactly the state
        refuse_empty_audience() exists to prevent, reached by a path it cannot
        see.

        ⚠️ Not a defence — this documents the consequence. Deleting needs a
           superuser and the foundation tier is not granted it, which is the
           protection; this test is here so the next person reads a true reason.
        """
        owner = make_person("Owner")
        pantry = make_ministry()
        spare = make_ministry(code="spare", name="Spare")
        event = Event.objects.create(
            name="Joint briefing", ministry=pantry, owner=owner,
            start_time=local_now(), end_time=local_now(),
            visible_to_outsiders=False, visible_to_all_staff=False)
        event.visible_to_ministries.set([spare])
        self.assertFalse(event.audience_is_empty)

        spare.delete()

        event.refresh_from_db()
        self.assertTrue(event.audience_is_empty)

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


class StaffRosterTests(TestCase):
    """`/org/staff/` —— 两档权限、两格收窄，以及这一整轮真正要的那件事。

    ⭐ **最后一条（`test_a_new_assignment_makes_staff_only_events_visible`）才是这
       一页存在的理由。** 受众轴早就能按 ministry 判「在编」了；在这一页之前，
       录那两行的唯一入口是 Django admin，而 ministry admin 被
       `StaffOnlyAdminMiddleware` 挡在外面。缺的是入口，不是机制 ——
       所以验收点是「从这一页录进去的人，看得见发给本部门的活动」。
    """

    def setUp(self):
        self.pantry = make_ministry()
        self.tax = make_ministry(code="tax_help", name="Tax Help")

        self.zhang = make_person("Zhang")
        self.admin = get_user_model().objects.create_user(
            email="zhang@example.com", password="x", contact=self.zhang)
        MinistryRole.objects.create(contact=self.zhang, ministry=self.pantry)

        self.li = make_person("Li")
        self.boss = get_user_model().objects.create_user(
            email="li@example.com", password="x", contact=self.li)
        self.boss.groups.add(foundation_admin_group())

        self.wang = make_person("Wang")

    def as_admin(self):
        self.client.force_login(self.admin)

    def as_boss(self):
        self.client.force_login(self.boss)

    def create_post(self, **overrides):
        # ⚠️ `compensation` 在这里，因为 2026-09-15 起 ministry admin **填得了它**
        #    （而且它必填 —— 那一格没有「不知道」这个答案）。foundation tier 还多
        #    一个 `kind`，那一档的测试自己传。
        payload = {"ministry": self.pantry.pk, "name": "Pantry Coordinator",
                   "compensation": Position.Compensation.UNPAID,
                   "description": "", "is_leader": ""}
        payload.update(overrides)
        return self.client.post(reverse("org:position_create"), payload)

    # --- review 2026-09-16 抓到的两条 -----------------------------------

    def test_the_new_post_button_preselects_the_ministry_it_came_from(self):
        """🔴 后果不只是多点一下。

        `roster_index` 拼了 `?ministry=<pk>`、那颗「New post」带着它，而
        `position_create` **从不读 `request.GET`** —— 预选静默地什么都没做。
        而对 foundation tier，那一格的空值是**合法**的「Foundation-wide
        (no ministry)」：于是从某个 ministry 页点进来建出的岗位可以
        **不属于任何 ministry**，而没有任何东西说过这件事。
        """
        self.as_boss()
        page = self.client.get(
            reverse("org:position_create") + f"?ministry={self.pantry.pk}")
        self.assertEqual(
            str(page.context["form"]["ministry"].value()), str(self.pantry.pk))

    def test_a_ministry_with_only_a_retired_post_waiting_still_gets_a_card(self):
        """🔴 **那条岗位在界面上够不着，而菜单红点在数它。**

        卡片从 `is_active=True` 的岗位来，而 `awaiting` 数全部 —— 后者是
        **有意的**（`positions_awaiting_review()`：「撤销」不能变成一条绕过确认
        的路）。而待确认的那份名单画在单个 ministry 那一页上，去那一页的唯一
        入口就是这里的卡片。

        于是一个 ministry 唯一的待确认岗位被撤销时：红点说「1 waiting」，
        点进去一张卡片都没有。
        """
        retired = Position.objects.create(
            ministry=self.tax, name="Built by mistake",
            compensation=Position.Compensation.UNPAID,
            needs_foundation_review=True, is_active=False)
        self.as_boss()
        page = self.client.get(reverse("org:staff_roster"))
        cards = {card.ministry: card for card in page.context["cards"]}
        self.assertIn(self.tax, cards, "红点在数它，而这一页上没有任何路到得了")
        self.assertEqual(cards[self.tax].awaiting, 1)
        # ⚠️ 三个 0 是诚实的：这个 ministry 确实一个在办岗位都没有。
        self.assertEqual(cards[self.tax].posts, 0)
        self.assertContains(page, retired.ministry.name)

    # --- 两格收窄 ------------------------------------------------------

    def test_a_ministry_admin_is_not_offered_the_foundation_column(self):
        """`kind` 那一格对他**不存在**，不是灰的。

        ⚠️ 2026-09-15 从三格收到一格：薪酬档和汇报线还给他填了（用户定的），
           而 `kind` 留着 —— 理事席位是基金会的事（D32）。名单读
           `FOUNDATION_ONLY_FIELDS`，不在这里抄第二份。
        """
        self.as_admin()
        form = self.client.get(reverse("org:position_create")).context["form"]
        # ⚠️ 读 `FOUNDATION_ONLY_FIELDS` 而不是在这里再抄一份名单 —— 抄一份的话，
        #    「哪几格收给 foundation」就有两个答案，而它们走散时这条测试照样绿。
        #    （顺带绕开 `OrgTreeGuardTests`：那条守卫按 `reports_to` 扫全仓库，
        #    而一个字符串字面量在它看来和一次遍历长得一样。）
        for name in PositionForm.FOUNDATION_ONLY_FIELDS:
            self.assertNotIn(name, form.fields)
        self.assertIn("name", form.fields)

    def test_the_foundation_tier_is_offered_them(self):
        self.as_boss()
        form = self.client.get(reverse("org:position_create")).context["form"]
        for name in PositionForm.FOUNDATION_ONLY_FIELDS:
            self.assertIn(name, form.fields)

    def test_he_sets_the_pay_himself_but_a_forged_kind_does_not_reach_the_row(self):
        """两半：薪酬档**是他填的**，而 `kind` 伪造不进来。

        ⭐ 前一半 2026-09-15 改口（用户定的）：薪酬档和汇报线还给 ministry admin
           填，foundation tier 改成事后核验。这比原来好，而且治掉一个真问题 ——
           那两格原来留着默认值，**没有任何人声明过它们**。

        🔴 后一半是「删字段而不是 `disabled`」买到的东西，一个字没变：
           `disabled=True` 会让 Django 拿 initial 顶替提交上来的值 —— 对**改**
           安全，对**建**不安全（新建时 initial 就是字段默认值，于是这个 POST 会
           被悄悄换成 `staff`，看起来像挡住了）。删掉字段之后，那个值连进
           `cleaned_data` 的机会都没有。
        """
        self.as_admin()
        self.create_post(compensation=Position.Compensation.PAID,
                         kind=Position.Kind.BOARD)
        post = Position.objects.get(name="Pantry Coordinator")
        self.assertEqual(post.compensation, Position.Compensation.PAID)
        self.assertEqual(post.kind, Position.Kind.STAFF)

    def test_a_ministry_admin_is_only_offered_his_own_ministries(self):
        self.as_admin()
        form = self.client.get(reverse("org:position_create")).context["form"]
        self.assertEqual(list(form.fields["ministry"].queryset), [self.pantry])

    def test_the_ministry_box_does_not_tell_him_to_leave_it_empty(self):
        """🔴 模型的 help_text 对这一档是一句假话（2026-09-15 在浏览器里看到的）。

        `Position.ministry` 的 help_text 写着「Leave empty for foundation-wide
        posts」—— 对 foundation tier 成立，而 ministry admin 这一格是**必填**的，
        照做会被拒绝。⚠️ curl 抓不到：HTML 一个字不差，错的是那句话和这张表单的
        关系。
        """
        self.as_admin()
        form = self.client.get(reverse("org:position_create")).context["form"]
        self.assertNotIn("Leave empty", form.fields["ministry"].help_text)

        self.as_boss()
        form = self.client.get(reverse("org:position_create")).context["form"]
        self.assertIn("Leave empty", form.fields["ministry"].help_text)

    def test_a_ministry_admin_cannot_leave_the_ministry_empty(self):
        """空着的 `ministry` 意思是「基金会级岗位」，而那只有 foundation tier 建得了。

        ⚠️ 表单把这一格设成必填，正是因为**不设的话它是可空的** ——
           `Position.ministry` 允许 null，所以留空提交会静静地建出一个他没有权限
           建的东西。
        """
        self.as_admin()
        response = self.create_post(ministry="")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Position.objects.filter(name="Pantry Coordinator").exists())

    def test_a_forged_ministry_id_creates_nothing(self):
        """别人 ministry 的 id 提交上来 —— 什么都没建出来。

        ⚠️ **实际拦住它的是表单那道收窄**（`ministry` 的 queryset 只有他那几个），
           所以这里是 200 加一条字段报错，不是 403。视图里那句
           `can_manage_staff_roster()` 是**纵深防御**，今天走不到 ——
           `events.views.event_create` 里那句 `can_publish_event()` 是同一个形状、
           同一个处境。规则本身由下面那条测试直接钉。
        """
        self.as_admin()
        response = self.create_post(ministry=self.tax.pk)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Position.objects.exists())

    def test_who_may_manage_which_ministrys_roster(self):
        """规则本身，直接问 —— 不经过任何一张表单。

        ⚠️ 这条测试存在，是因为上面那条走不到视图里那句检查：没有它，
           `can_manage_staff_roster()` 删掉也不会有任何测试变红。
        """
        self.assertTrue(can_manage_staff_roster(self.admin, self.pantry))
        self.assertFalse(can_manage_staff_roster(self.admin, self.tax))
        # ⭐ `None` = 基金会级岗位，只有 foundation tier 过得去。
        self.assertFalse(can_manage_staff_roster(self.admin, None))
        self.assertTrue(can_manage_staff_roster(self.boss, None))
        self.assertTrue(can_manage_staff_roster(self.boss, self.tax))

    # --- 待确认标记 ----------------------------------------------------

    def test_a_post_a_ministry_admin_created_waits_for_the_foundation(self):
        self.as_admin()
        self.create_post()
        self.assertTrue(Position.objects.get().needs_foundation_review)

    def test_a_post_the_foundation_created_does_not_wait(self):
        self.as_boss()
        self.create_post(kind=Position.Kind.STAFF,
                         compensation=Position.Compensation.PAID)
        self.assertFalse(Position.objects.get().needs_foundation_review)

    def test_the_foundation_saving_an_edit_counts_as_verifying_it(self):
        """⭐ **「改完就算核验过」**（用户 2026-09-15 定的）。

        他打开一个待核验的岗位、把填错的薪酬档改对、保存 —— 那一下既是修正也是
        核验。再让他点第二颗「Verified」只会制造「已经改对了却还赖在待办里」。
        """
        self.as_admin()
        self.create_post()
        post = Position.objects.get()
        self.assertTrue(post.needs_foundation_review)

        self.as_boss()
        self.client.post(reverse("org:position_detail", kwargs={"pk": post.pk}), {
            "ministry": self.pantry.pk, "name": post.name,
            "kind": Position.Kind.STAFF,
            "compensation": Position.Compensation.PAID,
            "reports_to": "", "description": "", "is_leader": "", "is_active": "on"})
        post.refresh_from_db()
        self.assertEqual(post.compensation, Position.Compensation.PAID)
        self.assertFalse(post.needs_foundation_review)

    def test_changing_the_pay_afterwards_sends_it_back_for_verification(self):
        """🔴 核验不是一次性的 —— 改那几格就重新待核验（用户定的）。

        不这么做的话，核完之后那几格再也没人看，而**改一格比建一个新岗位容易
        得多**。
        """
        self.as_boss()
        self.create_post(kind=Position.Kind.STAFF,
                         compensation=Position.Compensation.UNPAID)
        post = Position.objects.get()
        self.assertFalse(post.needs_foundation_review)

        self.as_admin()
        self.client.post(reverse("org:position_detail", kwargs={"pk": post.pk}), {
            "ministry": self.pantry.pk, "name": post.name,
            "compensation": Position.Compensation.PAID,
            "reports_to": "", "description": "", "is_leader": "", "is_active": "on"})
        post.refresh_from_db()
        self.assertEqual(post.compensation, Position.Compensation.PAID)
        self.assertTrue(post.needs_foundation_review)

    def test_renaming_a_post_does_not_send_it_back_for_verification(self):
        """⚠️ 只有 `VERIFIED_FIELDS` 触发。

        一张被无关改动塞满的待办列表，正是让人开始无视它的原因 —— 同
        `deferred.md` 里那条「下周班表已生成」的周期通知被否掉的理由。
        """
        self.as_boss()
        self.create_post(kind=Position.Kind.STAFF,
                         compensation=Position.Compensation.UNPAID)
        post = Position.objects.get()

        self.as_admin()
        self.client.post(reverse("org:position_detail", kwargs={"pk": post.pk}), {
            "ministry": self.pantry.pk, "name": "Weekend Coordinator",
            "compensation": Position.Compensation.UNPAID,
            "reports_to": "", "description": "", "is_leader": "", "is_active": "on"})
        post.refresh_from_db()
        self.assertEqual(post.name, "Weekend Coordinator")
        self.assertFalse(post.needs_foundation_review)

    def test_confirming_takes_it_off_the_list(self):
        self.as_admin()
        self.create_post()
        post = Position.objects.get()

        self.as_boss()
        self.client.post(reverse("org:position_detail", kwargs={"pk": post.pk}),
                         {"confirm": "1"})
        post.refresh_from_db()
        self.assertFalse(post.needs_foundation_review)

    def test_a_ministry_admin_cannot_confirm(self):
        self.as_admin()
        self.create_post()
        post = Position.objects.get()
        response = self.client.post(
            reverse("org:position_detail", kwargs={"pk": post.pk}), {"confirm": "1"})
        self.assertEqual(response.status_code, 403)
        post.refresh_from_db()
        self.assertTrue(post.needs_foundation_review)

    def roster_of(self, ministry):
        return self.client.get(
            reverse("org:ministry_roster", kwargs={"pk": ministry.pk}))

    def test_the_review_panel_is_drawn_for_the_foundation_and_nobody_else(self):
        """⚠️ 给 ministry admin 画这块面板就是给他一张他做不了的待办。"""
        self.as_admin()
        self.create_post()

        self.assertIsNone(self.roster_of(self.pantry).context["awaiting"])
        self.as_boss()
        self.assertEqual(len(self.roster_of(self.pantry).context["awaiting"]), 1)

    def test_the_review_note_says_somebody_set_them_not_that_they_are_defaults(self):
        """🔴 文案跟着流程走（2026-09-15 当天改过一次）。

        上一版写的是「still at their defaults — nobody has said they are right」，
        对应的是「那两格收给 foundation tier 填」那一版设计。用户推翻它之后，
        那两格**是 ministry admin 声明过的**，那句话成了假话。
        ⚠️ curl 抓不到这种东西：HTML 一个字不差，错的是那句话和流程的关系。
        """
        self.as_admin()
        self.create_post()
        post = Position.objects.get()

        self.as_boss()
        html = self.client.get(
            reverse("org:position_detail", kwargs={"pk": post.pk})).content.decode()
        self.assertIn("A ministry admin set", html)
        self.assertNotIn("still at their defaults", html)

    def test_the_index_counts_what_is_waiting_per_ministry(self):
        """⭐ 索引页上每张卡片右上角那颗红点（用户 2026-09-15 定的）。"""
        self.as_admin()
        self.create_post()
        self.create_post(name="Pantry Greeter")

        self.as_boss()
        cards = {c.ministry: c for c in
                 self.client.get(reverse("org:staff_roster")).context["cards"]}
        self.assertEqual(cards[self.pantry].awaiting, 2)

    def test_the_index_lists_one_card_per_ministry_and_no_posts(self):
        """⭐ **索引页不列任何一个岗位** —— 先选一个 ministry，点进去才看。

        初版是一页列全部、按 ministry 分段，而这个基金会的 ministry 只会变多。
        """
        make_position(None, "Tax Lead", ministry=self.tax)
        make_position(None, "Greeter", ministry=self.pantry)
        self.as_boss()
        page = self.client.get(reverse("org:staff_roster"))
        self.assertEqual([c.ministry for c in page.context["cards"]],
                         [self.pantry, self.tax])
        self.assertNotIn("sections", page.context)
        self.assertNotIn("Greeter", page.content.decode())

    def test_a_ministry_admin_only_sees_his_own_card(self):
        make_position(None, "Tax Lead", ministry=self.tax)
        make_position(None, "Greeter", ministry=self.pantry)
        self.as_admin()
        cards = self.client.get(reverse("org:staff_roster")).context["cards"]
        self.assertEqual([c.ministry for c in cards], [self.pantry])

    def test_another_ministrys_roster_does_not_exist_for_him(self):
        """404 —— 不归他管的 ministry 的名册对他**不存在**，同活动那道门的口径。"""
        make_position(None, "Tax Lead", ministry=self.tax)
        self.as_admin()
        self.assertEqual(self.roster_of(self.tax).status_code, 404)

    def test_the_foundation_wide_roster_is_only_for_the_foundation(self):
        """🔴 基金会级岗位那一页没有 pk 可挂，所以它是一个词形的地址。

        ⚠️ 而挡住 ministry admin 的**不是**这一页自己的判断：`_scoped_positions()`
           给他的 queryset 里根本没有 `ministry` 为空的那些行。
        """
        make_position(None, "Executive Director")
        self.as_admin()
        self.assertEqual(
            self.client.get(reverse("org:foundation_wide_roster")).status_code, 404)
        self.as_boss()
        page = self.client.get(reverse("org:foundation_wide_roster"))
        self.assertEqual(page.status_code, 200)
        self.assertIn("Executive Director", page.content.decode())

    # --- 作用域 --------------------------------------------------------

    def test_another_ministrys_post_does_not_exist_for_him(self):
        """404，不是 403 —— 不归他管的岗位对他**不存在**，同活动那道门的口径。"""
        other = make_position("tax-lead", "Tax Lead", ministry=self.tax)
        self.as_admin()
        response = self.client.get(
            reverse("org:position_detail", kwargs={"pk": other.pk}))
        self.assertEqual(response.status_code, 404)

    def test_a_foundation_wide_post_is_only_on_the_foundations_page(self):
        """🔴 `Position.ministry` 可空，空着的意思是「基金会级岗位」。

        按 ministry 收窄天然把它们排除在 ministry admin 之外 —— 而那正是想要的
        （Executive Director 不是食物银行的岗位）。这一条钉的是**另一半**：
        foundation tier 那边它们必须在，否则这批人一个页面都进不去而且不报错
        （D2a.10 给 `on_duty()` 记的就是这个症状）。
        """
        top = make_position("exec-director", "Executive Director")
        self.as_admin()
        self.assertEqual(
            self.client.get(reverse("org:position_detail"
                                    , kwargs={"pk": top.pk})).status_code, 404)
        self.as_boss()
        self.assertEqual(
            self.client.get(reverse("org:position_detail",
                                    kwargs={"pk": top.pk})).status_code, 200)

    def test_somebody_with_neither_hat_is_refused(self):
        outsider = get_user_model().objects.create_user(
            email="nobody@example.com", password="x", contact=self.wang)
        self.client.force_login(outsider)
        self.assertEqual(
            self.client.get(reverse("org:staff_roster")).status_code, 403)

    # --- code：默认没有，需要时才手设一个 --------------------------------

    def test_a_new_post_has_no_code(self):
        """⭐ 建出来的岗位**没有** code，而那是它的正常状态。

        一个格子做不了两件事：标识符不许变，描述不许过期。所以这一列不再试图
        从名字派生任何东西 —— 空的意思是「还没有任何系统需要指向这个岗位」。
        """
        self.as_admin()
        self.create_post()
        self.assertIsNone(Position.objects.get().code)

    def test_the_form_never_offers_a_code_to_anybody(self):
        """两档都没有这一格：「什么时候该填」是技术判断，不是业务判断。"""
        self.as_admin()
        self.assertNotIn(
            "code", self.client.get(reverse("org:position_create")).context["form"].fields)
        self.as_boss()
        self.assertNotIn(
            "code", self.client.get(reverse("org:position_create")).context["form"].fields)

    def test_any_number_of_posts_can_have_no_code(self):
        """⚠️ `position_code_ci_unique` 是 `UniqueConstraint(Lower("code"))`。

        这一条钉的是「多个空 code 不算重复」—— Postgres 认多个 NULL 互不相等，
        所以约束一个字不用改。**而它只在归一化把 `""` 收成 `None` 的前提下成立**：
        两个 `""` 数据库是认作重复的，那会让建第二个没有 code 的岗位撞上一个
        莫名其妙的唯一性冲突（`core.models.ImmutableCodeMixin.save()`）。
        """
        self.as_admin()
        self.create_post()
        self.create_post(name="Pantry Greeter")
        self.create_post(name="Pantry Driver")
        self.assertEqual(Position.objects.filter(code__isnull=True).count(), 3)

    def test_an_anchor_can_be_set_once_and_then_not_changed(self):
        """空着可以设；设过之后不可改 —— 不可改保护的是**已经有人在引用**的值。"""
        post = make_position(None, "Coordinator", ministry=self.pantry)
        post.code = "pantry_coordinator"
        post.full_clean()                      # 第一次设：放行
        post.save()

        post.code = "something_else"
        with self.assertRaises(ValidationError) as caught:
            post.full_clean()
        self.assertIn("code", caught.exception.error_dict)

    # --- 名册的分组 ------------------------------------------------------

    def test_a_vacant_leader_post_is_listed_once_and_only_under_vacant(self):
        """🔴 `PositionQuerySet` 那条三态不变量，画在页面上的那一半。

        把「空缺」写成一个附加标记的话，一个没人的组长岗位会**同时**出现在
        Leaders 和 Vacant 两组里 —— 同一行列两遍，而顶上那两个数跟着对不上。
        """
        make_position(None, "Lead", ministry=self.pantry, is_leader=True)
        self.as_admin()
        section = self.roster_of(self.pantry).context["section"]
        groups = dict(section.groups)
        self.assertEqual([row.position.name for row in groups["Vacant"]], ["Lead"])
        self.assertNotIn("Leaders", groups)
        self.assertEqual(section.posts, 1)
        self.assertEqual(section.holders, 0)

    def test_a_stipend_post_is_grouped_with_the_paid_ones(self):
        """⚠️ `stipend` 归在拿钱那一档，是 `Compensation` 自己的注释定的政策。"""
        post = make_position(None, "Greeter", ministry=self.pantry,
                             compensation=Position.Compensation.STIPEND)
        Assignment.objects.create(contact=self.wang, position=post)
        self.as_admin()
        self.assertIn("Staff — paid", dict(self.roster_of(self.pantry).context["section"].groups))

    # --- 写入路径上的两道拦 ---------------------------------------------

    def test_a_post_somebody_still_holds_cannot_be_retired(self):
        post = make_position("greeter", "Greeter", ministry=self.pantry)
        Assignment.objects.create(contact=self.wang, position=post)
        self.as_admin()
        response = self.client.post(
            reverse("org:position_detail", kwargs={"pk": post.pk}),
            {"ministry": self.pantry.pk, "name": "Greeter", "description": "",
             "is_leader": "", "is_active": ""})
        self.assertEqual(response.status_code, 200)
        post.refresh_from_db()
        self.assertTrue(post.is_active)

    def test_the_same_person_cannot_hold_one_post_twice_at_once(self):
        """⚠️ 不和「允许交接期重叠」冲突 —— 那说的是**两个人**并存。"""
        post = make_position("greeter", "Greeter", ministry=self.pantry)
        Assignment.objects.create(contact=self.wang, position=post, start_date=LAST_YEAR)
        self.as_admin()
        response = self.client.post(
            reverse("org:position_detail", kwargs={"pk": post.pk}),
            {"assign": "1", "contact": self.wang.pk, "start_date": TODAY})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Assignment.objects.filter(position=post).count(), 1)

    def test_ending_a_tenure_dates_it_rather_than_deleting_it(self):
        post = make_position("greeter", "Greeter", ministry=self.pantry)
        tenure = Assignment.objects.create(contact=self.wang, position=post)
        self.as_admin()
        self.client.post(reverse("org:position_detail", kwargs={"pk": post.pk}),
                         {"end": tenure.pk})
        tenure.refresh_from_db()
        # 最后一天是今天 → 存明天（右开，D51）。他今天还在名单上。
        self.assertEqual(tenure.end_date, TOMORROW)
        self.assertIn(tenure, Assignment.objects.active())

    def test_the_page_shows_the_last_day_not_the_stored_one(self):
        """⚠️ 存的和印的差一天，而印错不报错 —— 页面看上去完全正常。

        守卫（`core.tests.EndDateIsNeverShownRawGuardTests`）只证明模板没有裸印
        `end_date`；它证明不了印出来的是对的。这一条走一遍真页面。
        """
        post = make_position("greeter", "Greeter", ministry=self.pantry)
        tenure = Assignment.objects.create(
            contact=self.wang, position=post, start_date=LAST_YEAR)
        end_assignment(tenure, last_day=YESTERDAY)

        self.as_admin()
        page = self.client.get(
            reverse("org:position_detail", kwargs={"pk": post.pk})).content.decode()
        # ⚠️ 按模板实际用的格式比对（Django 的默认 `DATE_FORMAT`），不是 ISO ——
        #    这一条第一版拿 `isoformat()` 比，红了一次才发现页面印的是
        #    「Sept. 16, 2026」。
        self.assertIn(date_filter(YESTERDAY), page)
        self.assertNotIn(date_filter(TODAY), page, "印的是存的那一天，晚了一天")

    def test_the_post_page_lists_tenures_in_a_total_order(self):
        """🔴 **这一页的两个 `order_by()` 覆盖 `Meta.ordering`**（2026-09-18
           review 抓到），所以那边补的 `-pk` 在这条路上不生效。

        并列是可达的，而最容易忽略的是 `past`：一段**未来才生效**的任职不算
        active，于是它落进 past 而 `end_date` 是空的 —— 那一栏里每一条这样的
        行彼此全部并列。
        """
        post = make_position("greeter", "Greeter", ministry=self.pantry)
        # 两条未来才生效的任职：都不 active，都没有结束日期。
        one = Assignment.objects.create(
            contact=self.wang, position=post,
            start_date=TODAY + datetime.timedelta(days=10))
        two = Assignment.objects.create(
            contact=self.li, position=post,
            start_date=TODAY + datetime.timedelta(days=20))
        self.as_admin()
        context = self.client.get(
            reverse("org:position_detail", kwargs={"pk": post.pk})).context

        for name in ("holders", "past"):
            keys = list(context[name].query.order_by)
            self.assertIn(
                keys[-1].lstrip("-"), {"pk", "id"},
                f"{name} 的排序键 {keys} 不是全序 —— 并列时先后由数据库随手定")
        self.assertEqual({row.pk for row in context["past"]}, {one.pk, two.pk})

    def test_a_tenure_pk_from_another_post_is_not_reachable(self):
        """来自表单的 pk 不许够得着别的岗位的行，同 `find_grant()` 的作用域。"""
        mine = make_position("greeter", "Greeter", ministry=self.pantry)
        theirs = make_position("tax-lead", "Tax Lead", ministry=self.tax)
        tenure = Assignment.objects.create(contact=self.wang, position=theirs)
        self.as_admin()
        response = self.client.post(
            reverse("org:position_detail", kwargs={"pk": mine.pk}), {"end": tenure.pk})
        self.assertEqual(response.status_code, 404)
        tenure.refresh_from_db()
        self.assertIsNone(tenure.end_date)

    # --- ⭐ 这一整轮真正要的那件事 ---------------------------------------

    def staff_only_event(self):
        event = Event.objects.create(
            name="Staff briefing", ministry=self.pantry,
            start_time=local_now() + datetime.timedelta(days=3),
            end_time=local_now() + datetime.timedelta(days=3, hours=1),
            owner=self.zhang, status=Event.Status.OPEN,
        )
        event.visible_to_ministries.add(self.pantry)
        return event

    def test_a_new_assignment_makes_staff_only_events_visible(self):
        """⭐ 从这一页录进去的人，当场看得见发给本部门的活动。

        受众判断走 `Position(kind=staff, is_active)` + 一条在效期内的
        `Assignment` —— 也就是这一页建的那两行，一个新字段都没有。
        """
        event = self.staff_only_event()
        self.assertNotIn(event, Event.objects.for_audience(self.wang))

        post = make_position("greeter", "Greeter", ministry=self.pantry)
        tenure = Assignment.objects.create(contact=self.wang, position=post)
        self.assertIn(event, Event.objects.for_audience(self.wang))

        # 而结束任职收回可见性 —— `end_assignment()` 的注释写着这一条，
        # 因为它看起来会像一个 bug。
        # ⚠️ 走服务而不是手填 `end_date`：今天入职、昨天结束过不了
        #    `end_date >= start_date`（D51 起 `start_date` 不可为空）。而那场
        #    活动在三天后，所以今天结束就够了。
        end_assignment(tenure)
        self.assertNotIn(event, Event.objects.for_audience(self.wang))

    def test_a_tenure_ending_on_the_event_day_still_sees_that_event(self):
        """⚠️ 第四条路径：`on=OuterRef(...)` —— 受众判断里那个相关子查询。

        `for_audience()` 给每一行标上**它自己的那一天**（`Event.AUDIENCE_DAY`），
        再把那一列当作 `on` 交给 `in_effect_on()`（`org.audience.on_the_books_q`）。
        同一个谓词、第四种 SQL 形状，而「结束日期正好等于判定那一天」这一格
        从来没有被问过。

        🔴 **判的是活动那一天，不是今天** —— 这条测试第一版写成「今天结束任职、
           今天还看得见」，红了：那场活动在三天后，而他到那天已经走了。
           边界必须踩在**活动当天**，这也正是这条路径要单独有一张网的理由。

        ⚠️ 这一条 Step 1 写下时是**特征化**的（当时存的是活动当天），D51 之后
           存的值变成次日而**答案一个字没变** —— 那正是「行为不变」的意思。
        """
        event = self.staff_only_event()
        event_day = local_date_of(event.start_time)
        post = make_position("greeter", "Greeter", ministry=self.pantry)
        tenure = Assignment.objects.create(contact=self.wang, position=post)
        self.assertIn(event, Event.objects.for_audience(self.wang))

        # 最后一天正好是活动当天 → 存次日（右开，D51）。
        tenure.end_date = event_day + datetime.timedelta(days=1)
        tenure.save(update_fields=["end_date"])
        self.assertIn(event, Event.objects.for_audience(self.wang))

        # 而前一天就走了，那场活动就看不见了 —— 两格并排，边界才说得清。
        tenure.end_date = event_day
        tenure.save(update_fields=["end_date"])
        self.assertNotIn(event, Event.objects.for_audience(self.wang))

    def test_a_board_seat_does_not_count_as_being_on_the_books(self):
        """⚠️ `kind=board` 的人**不算在编**（`on_the_books_q` 只认 STAFF）。

        这正是 `kind` 被收给 foundation tier 的那个不显眼的理由：选错一格，
        这个人静默地看不见发给员工的活动，而他不会知道为什么。
        """
        event = self.staff_only_event()
        seat = make_position("trustee", "Trustee", ministry=self.pantry,
                             kind=Position.Kind.BOARD)
        Assignment.objects.create(contact=self.wang, position=seat)
        self.assertNotIn(event, Event.objects.for_audience(self.wang))
