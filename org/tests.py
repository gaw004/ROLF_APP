import datetime

from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from contact.models import Contact
from core.timeutils import local_today

from .models import Assignment, EmploymentType, Ministry, Position
from .services import build_org_tree

TODAY = local_today()
YESTERDAY = TODAY - datetime.timedelta(days=1)
LAST_YEAR = TODAY - datetime.timedelta(days=365)


def make_person(last_name):
    return Contact.objects.create(
        contact_type=Contact.ContactType.INDIVIDUAL, legal_last_name=last_name)


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


class VacancyTests(TestCase):
    """Why Position was split out of Assignment in the first place."""

    def setUp(self):
        self.ministry = make_ministry()
        self.position = make_position(
            "coordinator", "Coordinator", self.ministry, kind=Position.Kind.VOLUNTEER)
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
        self.assertEqual(vacant.kind, Position.Kind.VOLUNTEER)
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

    def test_employment_type_on_a_volunteer_position_is_refused_by_clean(self):
        # Spans two tables (kind is on Position), so no CheckConstraint can see
        # it — a hint layer, and recorded as one. See goal.md D14.
        full_time = EmploymentType.objects.create(code="full_time", name="Full time")
        assignment = Assignment(
            contact=self.person, position=self.cook, employment_type=full_time)
        with self.assertRaises(ValidationError) as caught:
            assignment.full_clean()
        self.assertIn("employment_type", caught.exception.message_dict)


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
