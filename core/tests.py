"""Project-wide guards. These live in core because they police every app.

Most of what follows is lint dressed up as tests — the pattern this project
keeps reaching for when a rule has to hold everywhere and no linter enforces it
(migration guard, D16 time, D18 layering, D14 mappings, org-tree traversal).
"""

import datetime
import re
from pathlib import Path
from unittest import mock

from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.test import TestCase

from core.constraints import CONSTRAINT_FIELD
from core.querysets import DateRangeQuerySet
from core.timeutils import local_today

# core/tests.py borrowing a contact model is the one direction D17 forbids in
# production code. It is deliberate and test-only: .active() needs a model with
# start_date/end_date, Relationship is the only one that has them until B5, and
# the alternative (a test-only model) would need a migration of its own.
from contact.models import Contact, Relationship, RelationshipType

# Apps we wrote, as opposed to Django's and the third-party ones.
OUR_APPS = {"core", "contact", "accounts", "org", "events", "volunteer", "finance", "payroll"}

SKIPPED_DIRS = {".venv", "venv", "migrations", "__pycache__", "staticfiles", "node_modules"}


def project_python_files(skip=()):
    """Every .py file we wrote, as (relative path, source text) pairs."""
    root = Path(settings.BASE_DIR)
    skip = {Path(p) for p in skip}
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if SKIPPED_DIRS & set(relative.parts):
            continue
        if relative in skip:
            continue
        yield relative, path.read_text(encoding="utf-8")


def offending_lines(pattern, skip=(), only_filenames=None):
    """Lines matching `pattern`, as 'path:line: text' strings ready to print.

    The patterns passed in are regexes with escaped dots and parens, so they
    never match their own source in this file — which is what lets these guards
    scan the whole project including themselves.
    """
    regex = re.compile(pattern)
    hits = []
    for relative, source in project_python_files(skip=skip):
        if only_filenames and relative.name not in only_filenames:
            continue
        for number, line in enumerate(source.splitlines(), start=1):
            if regex.search(line):
                hits.append(f"{relative}:{number}: {line.strip()}")
    return hits


class NoMissingMigrationsTests(TestCase):
    """Guards the whole project, which is why it lives in core rather than in one app."""

    def test_no_model_changes_are_missing_a_migration(self):
        # makemigrations --check exits non-zero when a model change has no
        # migration yet, which surfaces here as SystemExit. Without this test a
        # forgotten makemigrations only shows up at deploy time.
        call_command("makemigrations", "--check", "--dry-run", verbosity=0)


class ActiveQuerySetTests(TestCase):
    """The four date boundaries of .active() — the most reused predicate we have.

    Exercised against Relationship, the only model with start/end dates until
    B5. The queryset is built directly rather than through a manager: B3.3 is
    where Relationship.objects gets it, and these boundaries have to hold from
    the moment the predicate exists.
    """

    def setUp(self):
        self.alice = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL, legal_last_name="Alice")
        self.bob = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL, legal_last_name="Bob")
        self.parent_of = RelationshipType.objects.create(
            code="parent_of", name_a_to_b="parent of", name_b_to_a="child of")

    def relationships(self):
        return DateRangeQuerySet(model=Relationship)

    def make(self, start_date=None, end_date=None):
        return Relationship.objects.create(
            contact_a=self.alice, contact_b=self.bob,
            relationship_type=self.parent_of,
            start_date=start_date, end_date=end_date,
        )

    def test_active_includes_a_row_ending_today(self):
        row = self.make(end_date=local_today())
        self.assertIn(row, self.relationships().active())

    def test_active_excludes_a_row_that_ended_yesterday(self):
        row = self.make(end_date=local_today() - datetime.timedelta(days=1))
        self.assertNotIn(row, self.relationships().active())

    def test_active_excludes_a_row_that_starts_in_the_future(self):
        # The half of the definition that is easiest to leave out, and leaving
        # it out does not raise — it just counts people who have not started yet.
        row = self.make(start_date=local_today() + datetime.timedelta(days=1))
        self.assertNotIn(row, self.relationships().active())

    def test_active_includes_a_row_with_no_dates_at_all(self):
        row = self.make()
        self.assertIn(row, self.relationships().active())

    def test_active_accepts_an_explicit_date(self):
        row = self.make(
            start_date=datetime.date(2020, 1, 1), end_date=datetime.date(2020, 12, 31))
        self.assertNotIn(row, self.relationships().active())
        self.assertIn(row, self.relationships().active(on=datetime.date(2020, 6, 1)))

    def test_active_is_not_frozen_at_import_time(self):
        # `def active(self, on=local_today())` would evaluate once, at import,
        # and a long-lived gunicorn worker would drift further off every day.
        # A default argument holding a date object is the tell.
        self.assertEqual(DateRangeQuerySet.active.__defaults__, (None,))


class TimezoneTests(TestCase):
    """D16: "today" is the foundation's today — not the server's, not UTC."""

    def test_local_today_uses_the_foundation_timezone_not_utc(self):
        # 8pm Pacific on 27 July is already 28 July in UTC. Getting this wrong
        # does not raise; it just ends people's tenure a day early every evening.
        evening_pacific = datetime.datetime(2026, 7, 28, 3, 0, tzinfo=datetime.timezone.utc)
        with mock.patch("django.utils.timezone.now", return_value=evening_pacific):
            self.assertEqual(local_today(), datetime.date(2026, 7, 27))
        # The wrong spelling, shown next to the right one so the difference is
        # on the record rather than in a comment.
        self.assertEqual(evening_pacific.date(), datetime.date(2026, 7, 28))

    def test_the_project_timezone_is_the_foundation_timezone(self):
        # local_today() is only correct because of this setting; reset it to UTC
        # and the test above would still pass, silently meaning nothing.
        self.assertEqual(settings.TIME_ZONE, "America/Los_Angeles")
        self.assertTrue(settings.USE_TZ)


class TimeSourceGuardTests(TestCase):
    """Lint-as-test: nobody computes "today" outside core/timeutils.py (D16)."""

    # The two wrong spellings of "today", as regexes. They are written escaped
    # and only here, so this file never contains the literal text it hunts for —
    # which is what lets the guard scan itself. Do not spell either of them out
    # in a comment; the first version of this test failed on its own docstring.
    NAIVE_TODAY = r"\bdate\.today\(\)"          # follows the server's timezone
    UTC_DATE = r"\btimezone\.now\(\)\.date\(\)"  # the UTC date, a day early after 5pm PT

    def test_nobody_computes_today_outside_core_timeutils(self):
        # ruff's DTZ catches the first pattern but not the second: that one is
        # tz-aware and looks perfectly legitimate to a linter. Hence this guard.
        hits = offending_lines(
            f"{self.NAIVE_TODAY}|{self.UTC_DATE}",
            skip=["core/timeutils.py"],
        )
        self.assertEqual(
            hits, [], "Use core.timeutils.local_today() instead:\n" + "\n".join(hits))


class LayeringGuardTests(TestCase):
    """Lint-as-test: business logic never imports the admin (D18)."""

    def test_business_logic_does_not_import_admin(self):
        # models.py / forms.py / services.py are permanent assets: the ORM and
        # django.forms sit inside Django's backwards-compatibility promise, and
        # Phase C's views import the same classes unchanged. admin.py is one-off
        # configuration that gets deleted when the front end arrives. This test
        # turns "the forms are reusable" from a promise into a checked fact.
        #
        # ⚠️ views.py is deliberately out of scope — the merge page needs
        #    staff_member_required (B4.4).
        hits = offending_lines(
            r"django\.contrib\.admin|from django\.contrib import admin",
            only_filenames={"models.py", "forms.py", "services.py"},
        )
        self.assertEqual(
            hits, [], "Business logic must not import admin:\n" + "\n".join(hits))


class OrgTreeGuardTests(TestCase):
    """Lint-as-test: the reporting chain is walked in exactly one place."""

    # Split across three lines on purpose: any single line holding both the
    # chain field and a loop keyword would match this guard itself.
    CHAIN_FIELD = r"\breports_to\b"
    FIELD_DEFINITION = r"reports_to\s*="
    LOOP = r"\b(for|while)\b"

    def test_nobody_traverses_reports_to_outside_org_services(self):
        # build_org_tree() is the only code that walks reports_to. The cycle
        # guard and the N+1 avoidance both live inside it, so callers receive a
        # tree and never need to know the data might contain a cycle. The
        # earlier plan — "every traversal carries its own visited set" — was
        # discipline, the kind this project has already convicted twice.
        # See goal.md「汇报线的环」.
        #
        # ⚠️ No-op until B5 creates the org app. B9 has a checklist item to
        #    confirm it really does go red once Position exists.
        # A field definition (a plain assignment) is not a traversal, and
        # neither is a filter() on it. Only loops walk the chain.
        hits = [
            hit
            for hit in offending_lines(self.CHAIN_FIELD, skip=["org/services.py"])
            if re.search(self.LOOP, hit) and not re.search(self.FIELD_DEFINITION, hit)
        ]
        self.assertEqual(
            hits,
            [],
            "Walk the reporting chain via org.services.build_org_tree():\n" + "\n".join(hits),
        )


class ConstraintMappingGuardTests(TestCase):
    """Lint-as-test: every business constraint is wired for field-level errors (D14).

    This replaces the comment discipline old D14 relied on ("change one, change
    both"): a missing code or mapping goes red here, instead of surfacing months
    later as a message stranded at the top of an admin form.
    """

    def our_constraints(self):
        for model in apps.get_models():
            if model._meta.app_label not in OUR_APPS:
                continue
            # simple_history's Historical* copies carry no constraints of their
            # own; skipping them keeps the counts honest either way.
            if model.__name__.startswith("Historical"):
                continue
            for constraint in model._meta.constraints:
                yield model, constraint

    def test_every_business_constraint_has_a_code_and_a_field_mapping(self):
        missing_code = []
        missing_mapping = []
        for model, constraint in self.our_constraints():
            label = f"{model._meta.label}.{constraint.name}"
            code = getattr(constraint, "violation_error_code", None)
            if not code:
                missing_code.append(label)
            elif code not in CONSTRAINT_FIELD:
                missing_mapping.append(f"{label} (code={code})")
        self.assertEqual(
            missing_code, [],
            "Constraints without violation_error_code:\n" + "\n".join(missing_code))
        self.assertEqual(
            missing_mapping, [],
            "Codes missing from CONSTRAINT_FIELD:\n" + "\n".join(missing_mapping))

    def test_every_business_constraint_has_a_message_a_human_can_read(self):
        # Without one, the mapping faithfully places Django's default
        # "Constraint 'contact_type_is_known' is violated." next to the field.
        unfriendly = [
            f"{model._meta.label}.{constraint.name}"
            for model, constraint in self.our_constraints()
            if not getattr(constraint, "violation_error_message", None)
        ]
        self.assertEqual(
            unfriendly, [],
            "Constraints without violation_error_message:\n" + "\n".join(unfriendly))

    def test_constraint_field_has_no_stale_entries(self):
        # The other direction: dropping a constraint and leaving its mapping
        # behind is silent, and the leftover row makes the table look as though
        # it knows about a rule that no longer exists.
        live_codes = {
            getattr(constraint, "violation_error_code", None)
            for _, constraint in self.our_constraints()
        }
        stale = sorted(set(CONSTRAINT_FIELD) - live_codes)
        self.assertEqual(stale, [], f"CONSTRAINT_FIELD entries with no constraint: {stale}")

    def test_mapped_fields_exist_on_their_model(self):
        # A typo here surfaces as a ValueError out of ModelForm._update_errors
        # at the moment someone submits bad data — i.e. never during development.
        wrong = []
        for model, constraint in self.our_constraints():
            code = getattr(constraint, "violation_error_code", None)
            field = CONSTRAINT_FIELD.get(code)
            if field is None:
                continue
            try:
                model._meta.get_field(field)
            except Exception:
                wrong.append(f"{model._meta.label}: {code} -> {field}")
        self.assertEqual(
            wrong, [], "Mapped to a field that does not exist:\n" + "\n".join(wrong))
