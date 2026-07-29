from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from .models import EmploymentType, Ministry, Position
from .services import build_org_tree


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
            "ed", "Executive Director", kind=Position.Kind.EMPLOYEE, reports_to=chair)
        director.full_clean()
        self.assertEqual(director.reports_to, chair)


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
