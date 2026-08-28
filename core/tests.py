"""Project-wide guards. These live in core because they police every app.

Most of what follows is lint dressed up as tests — the pattern this project
keeps reaching for when a rule has to hold everywhere and no linter enforces it
(migration guard, D16 time, D18 layering, D14 mappings, org-tree traversal).
"""

import colorsys
import datetime
import io
import os
import ast
import re
import shutil
import smtplib
import tempfile
from pathlib import Path
from unittest import mock

from django.apps import apps
from django.core.mail.backends.base import BaseEmailBackend
from django.http import HttpResponse
from django.middleware.security import SecurityMiddleware
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.db import IntegrityError, connection, models
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from core.constraints import CONSTRAINT_FIELD
from core.health import HEALTH_PATH
from core.management.commands.check_deployment import DEMO_ADDRESS_SUFFIX
from core.limits import LONG_TEXT
from core.models import HomePage
from core.services import orphaned_home_media
from core.palette import dominant_colour, ramp_from, relative_luminance
from core.querysets import DateRangeQuerySet
from core.timeutils import local_today

# core/tests.py borrowing an app's models is the one direction D17 forbids in
# production code. It is deliberate and test-only: .active() needs a model with
# start_date/end_date, and the alternative (a test-only model) would need a
# migration of its own.
from contact.models import Contact
from org.models import Assignment, Position

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


def project_markdown_files():
    """Every .md file we wrote, as (relative path, text) pairs.

    Discovered by walking, not listed — a new document is covered the moment it
    exists, which is the whole point of a guard.
    """
    root = Path(settings.BASE_DIR)
    for path in sorted(root.rglob("*.md")):
        relative = path.relative_to(root)
        if SKIPPED_DIRS & set(relative.parts) or ".git" in relative.parts:
            continue
        yield relative, path.read_text(encoding="utf-8")


LOOP_OPENER = re.compile(r"^\s*(async\s+for|for|while)\b")
SCOPE_OPENER = re.compile(r"^\s*(async\s+def|def|class)\s+(\w+)")
# A loop keyword anywhere on the line, which is how a comprehension iterates.
INLINE_LOOP = re.compile(r"\b(for|while)\b")


def _innermost_block_is_a_loop(stack):
    """Walking outwards, is a loop reached before a function or class body?

    A def inside a loop is its own scope: its lines run once per call, not once
    per iteration, so the loop's reach stops there.
    """
    for _, kind, _name in reversed(stack):
        if kind == "loop":
            return True
        if kind == "scope":
            return False
    return False


def _calls_its_own_scope(stack, line):
    """Does this line call the function it is written inside? I.e. recursion."""
    return any(
        kind == "scope" and name and f"{name}(" in line
        for _, kind, name in stack
    )


def repeated_uses(pattern, skip=(), exempt="loop-guard-ok"):
    """Lines matching `pattern` that could run more than once: loops, recursion.

    Three signals, because walking a chain can be spelled three ways:

      1. inside a for/while body — indentation-based, and that part matters.
         The first version of this guard wanted the loop keyword and the
         pattern on the *same* line, and the ordinary spelling walks past it:

             for _ in range(20):
                 nxt = node.<the field>      # never matched, never caught

      2. on a line that iterates by itself — a comprehension;
      3. on a line that calls the function it sits in — recursion.

    Broad on purpose: a false positive costs one `loop-guard-ok` comment — on
    the line or just above it, so the reason has room to be written out — and a
    miss costs a hung page in Phase C.
    """
    regex = re.compile(pattern)
    hits = []
    for relative, source in project_python_files(skip=skip):
        lines = source.splitlines()
        stack = []  # (indent, "loop" | "scope", name), innermost last
        for number, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            indent = len(line) - len(line.lstrip())
            while stack and stack[-1][0] >= indent:
                stack.pop()
            scope = SCOPE_OPENER.match(line)
            opens_a_loop = bool(LOOP_OPENER.match(line))
            exempted = exempt in line or (number > 1 and exempt in lines[number - 2])
            if regex.search(line) and not exempted:
                if (INLINE_LOOP.search(line)
                        or _innermost_block_is_a_loop(stack)
                        or _calls_its_own_scope(stack, line)):
                    hits.append(f"{relative}:{number}: {stripped}")
            if opens_a_loop:
                stack.append((indent, "loop", None))
            elif scope:
                stack.append((indent, "scope", scope.group(2)))
    return hits


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

    Exercised against Assignment. The queryset is built directly rather than
    through Assignment.objects, so these boundaries are pinned to the shared
    predicate itself and not to whichever manager happens to expose it.
    """

    def setUp(self):
        self.alice = Contact.objects.create(
            contact_type=Contact.ContactType.INDIVIDUAL,
            legal_first_name="Ann", legal_last_name="Alice")
        self.post = Position.objects.create(code="greeter", name="Greeter")

    def tenures(self):
        return DateRangeQuerySet(model=Assignment)

    def make(self, start_date=None, end_date=None):
        return Assignment.objects.create(
            contact=self.alice, position=self.post,
            start_date=start_date, end_date=end_date,
        )

    def test_active_includes_a_row_ending_today(self):
        row = self.make(end_date=local_today())
        self.assertIn(row, self.tenures().active())

    def test_active_excludes_a_row_that_ended_yesterday(self):
        row = self.make(end_date=local_today() - datetime.timedelta(days=1))
        self.assertNotIn(row, self.tenures().active())

    def test_active_excludes_a_row_that_starts_in_the_future(self):
        # The half of the definition that is easiest to leave out, and leaving
        # it out does not raise — it just counts people who have not started yet.
        row = self.make(start_date=local_today() + datetime.timedelta(days=1))
        self.assertNotIn(row, self.tenures().active())

    def test_active_includes_a_row_with_no_dates_at_all(self):
        row = self.make()
        self.assertIn(row, self.tenures().active())

    def test_active_accepts_an_explicit_date(self):
        row = self.make(
            start_date=datetime.date(2020, 1, 1), end_date=datetime.date(2020, 12, 31))
        self.assertNotIn(row, self.tenures().active())
        self.assertIn(row, self.tenures().active(on=datetime.date(2020, 6, 1)))

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
    # The third spelling, added after it was found in shipped code: taking the
    # day off a stored DateTimeField. Every datetime column in this project is
    # named *_time or *_at, so that is what it looks for.
    STORED_INSTANT_DATE = r"\b\w+_(time|at)\.date\(\)"

    def test_nobody_computes_today_outside_core_timeutils(self):
        # ruff's DTZ catches the first pattern but not the others: those are
        # tz-aware and look perfectly legitimate to a linter. Hence this guard.
        hits = offending_lines(
            f"{self.NAIVE_TODAY}|{self.UTC_DATE}",
            skip=["core/timeutils.py"],
        )
        self.assertEqual(
            hits, [], "Use core.timeutils.local_today() instead:\n" + "\n".join(hits))

    def test_nobody_takes_the_day_off_a_stored_instant_directly(self):
        # A DateTimeField comes back in UTC, so an event at 6pm Pacific on the
        # 31st has .date() == the 1st. R8 asks "who was employed on the day of
        # the event" with that value — off by one, and silent. R8 shipped with
        # exactly this bug and a month-boundary test caught it; the guard is
        # here so the next one is caught at the point of writing.
        hits = offending_lines(self.STORED_INSTANT_DATE, skip=["core/timeutils.py"])
        self.assertEqual(
            hits,
            [],
            "That is the UTC day. Use core.timeutils.local_date_of():\n" + "\n".join(hits),
        )


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

    # Both spellings: build_org_tree() itself reads .reports_to_id (the
    # attribute version costs a query per row), so anybody copying it would
    # copy that too, and \b does not treat the _id suffix as a new word.
    CHAIN_FIELD = r"\breports_to(_id)?\b"
    # An assignment is not a traversal — that covers the field declaration on
    # the model and reports_to=<something> passed as a keyword argument.
    FIELD_DEFINITION = r"reports_to(_id)?\s*="

    def test_nobody_traverses_reports_to_outside_org_services(self):
        # build_org_tree() is the only code that walks reports_to. The cycle
        # guard and the N+1 avoidance both live inside it, so callers receive a
        # tree and never need to know the data might contain a cycle. The
        # earlier plan — "every traversal carries its own visited set" — was
        # discipline, the kind this project has already convicted twice.
        # See goal.md「汇报线的环」.
        hits = [
            hit
            for hit in repeated_uses(self.CHAIN_FIELD, skip=["org/services.py"])
            if not re.search(self.FIELD_DEFINITION, hit)
        ]
        self.assertEqual(
            hits,
            [],
            "Walk the reporting chain via org.services.build_org_tree():\n" + "\n".join(hits),
        )


class PermissionGuardTests(TestCase):
    """Lint-as-test: ministry-scoped authority is judged in exactly one place."""

    # Written escaped and only here, so this file never contains the literal
    # text it hunts for and the guard can scan itself.
    DIRECT_QUERY = r"MinistryRole\.objects"

    def test_only_permissions_py_queries_ministryrole(self):
        """views.py / admin.py / forms.py never touch the grant table themselves.

        Same argument as the org-tree guard, one notch more serious. Checks
        scattered across views and admin means one of them eventually forgets
        .active() or ministry__is_active — and where a missed traversal hangs
        the page (loud), a missed permission check is silent: nothing raises,
        somebody merely sees what they should not.

        Two places may touch the table, and the split is worth stating:
        permissions.py *judges* ("may they?"), services.py *writes* (P5's page
        grants and revokes, which by its nature edits grants). A view does
        neither — it calls one of them. This guard was first written to skip
        only permissions.py and immediately went red on P5's own page; the
        answer was not to widen the exemption but to move the writes into
        org/services.py, which is where the roadmap's version of this guard
        pointed all along.
        """
        hits = offending_lines(
            self.DIRECT_QUERY, only_filenames={"views.py", "admin.py", "forms.py"})
        self.assertEqual(
            hits,
            [],
            "Ask org.permissions to judge, org.services to write:\n" + "\n".join(hits),
        )


class ReportFigureNamesGuardTests(TestCase):
    """Lint-as-test: no figure on the ministry report is named after volunteering
    unless somebody put it on the list below on purpose.

    ⚠️ The risk this watches is specific, and it is a naming risk rather than a
       computation one. `ministry_report()` counts distinct contacts over every
       participation — **paid staff included** — and that figure was called
       `volunteers` until 2026-08-20. D1.4 adds a genuine volunteer figure
       (`served_as=volunteer`) to the same report. Two numbers on one screen,
       one name, two definitions, and neither of them raising: that is the
       failure this project has already convicted three times.

    So the rule is not "never say volunteer" — D1.4's figure has every right to
    the word. The rule is that claiming it has to be a **visible act**: adding a
    row to ALLOWED below, in a diff somebody reviews.

    ⚠️ Deliberately narrow. A guard that scanned the whole file for the word
       would be red every day (the docstrings discuss it constantly) and would
       be widened until it meant nothing — the failure mode
       05-roadmap.md warns about for whitelists.
    """

    #: Figure keys that may carry the word, and why. Empty today; D1.4's
    #: volunteer-hours figure is the first legitimate entry, and adding it here
    #: is how that decision becomes visible rather than incidental.
    ALLOWED: set[str] = set()

    def test_no_figure_key_says_volunteer_without_being_listed(self):
        from events.services import ministry_report
        from events.models import Event

        figures = ministry_report(Event.objects.none())["figures"]
        offenders = sorted(
            key for key in figures
            if "volunteer" in key.lower() and key not in self.ALLOWED
        )
        self.assertEqual(
            offenders,
            [],
            "A report figure named after volunteering has to be one on purpose "
            "— add it to ALLOWED with a reason, or name it for what it counts:\n"
            + "\n".join(offenders),
        )


class AudienceContainmentGuardTests(TestCase):
    """Lint-as-test: "is this role wider than its event" is compared in one place.

    ⭐ The invariant that keeps requirement 7 honest — somebody who cannot see
       an event must not be able to sign up for a job inside it. It is three
       comparisons of three different kinds (two implications and a set
       containment, with "all staff" sitting above every ministry while being a
       boolean), which is exactly the shape that grows a second, subtly
       different implementation the first time somebody needs the answer
       somewhere else.

    ⚠️ It watches Audience.Spec's three attribute names rather than the model
       fields. The fields are read all over the place — templates draw them,
       the admin filters on them — and a guard that went red on those would be
       widened until it meant nothing. These three names belong to the value
       object alone, so seeing one outside the two files below means somebody
       is doing the comparison by hand.

    ⚠️ forms.py is allowed because it *builds* Specs and hands them over;
       models.py is allowed because refuse_wider_than_event() lives there.
       Anywhere else is a second implementation.
    """

    SPEC_ATTRIBUTE = r"\.(outsiders|all_staff|ministries)\b"
    ALLOWED = ["events/models.py", "events/forms.py"]

    def test_only_one_place_compares_two_audiences(self):
        hits = offending_lines(self.SPEC_ATTRIBUTE, skip=self.ALLOWED)
        self.assertEqual(
            hits,
            [],
            "Comparing audiences is events.models.refuse_wider_than_event()'s "
            "job — one rule, one implementation:\n" + "\n".join(hits),
        )


class AudienceIsAskedGuardTests(TestCase):
    """Lint-as-test: nobody narrows an event list by status and forgets the person.

    ⭐ L3's whole risk in one sentence. `visible_to_participants()` answers "is
       it published"; `for_audience()` answers "is it for them". They are two
       predicates on purpose, and the failure mode of using only the first is
       the exact hole participants.md section 1 found: every signed-in account
       seeing every published event, with nothing raising and every page
       looking normal.

    ⚠️ Deliberately narrow — it reads **function bodies**, not whole files. A
       file-wide version would go red on every docstring that discusses the two
       predicates (there are several, including this one), and a guard that is
       red every day gets whitelisted until it means nothing. Same reasoning
       ReportFigureNamesGuardTests writes out above.
    """

    NARROWS = "visible_to_participants("
    ASKS = "for_audience("
    #: Docstrings discuss this pair at length, so they are stripped before the
    #: search — matching prose would make the guard lie in both directions.
    DOCSTRING = re.compile(r'("""|\x27\x27\x27).*?\1', re.S)

    #: Named exemptions. Each is a decision, not an oversight — see the
    #: docstring at each site, and 06-roadmap.md L2.2.
    ALLOWED = {
        # The rows somebody already holds. Narrowing an audience afterwards must
        # not take away a signup they made while it was still open to them.
        "my_participations",
        # The two predicates defining themselves.
        "visible_to_participants",
        "for_audience",
        # ⚠️ Somebody standing in front of the iPad, having already scanned the
        #    code. It refuses anybody without a signup two lines later, and a
        #    signup is proof enough that the event was once theirs to join —
        #    an audience test here could only stop a person who is physically
        #    present from checking in. Same rule as my_participations above:
        #    narrowing takes away discovery, never a row somebody holds.
        "checkin_confirm",
    }

    def functions(self):
        """(where, name, source) for every function in our own non-test code."""
        for relative, source in project_python_files(skip=["tests.py"]):
            if relative.name == "tests.py":
                continue
            try:
                tree = ast.parse(source)
            except SyntaxError:  # pragma: no cover - caught by check, not here
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    body = ast.get_source_segment(source, node) or ""
                    yield f"{relative}:{node.lineno} {node.name}()", node.name, body

    def test_narrowing_by_status_always_asks_who_is_looking(self):
        offenders = [
            where
            for where, name, body in self.functions()
            if name not in self.ALLOWED
            and self.NARROWS in self.DOCSTRING.sub("", body)
            and self.ASKS not in self.DOCSTRING.sub("", body)
        ]
        self.assertEqual(
            offenders,
            [],
            "Published is not the same question as for-them. Add "
            "for_audience(contact), or name the function in ALLOWED with a "
            "reason:\n" + "\n".join(offenders),
        )


class ServedAsWriteGuardTests(TestCase):
    """Lint-as-test: the identity on a signup is written in exactly one place.

    D38's invariant. Three write paths were planned for this column (the signup
    form, an admin's correction, and D3's invitation reply) and each of them
    arrives with its own idea of what the default should be — so the default
    rules end up in three places, disagreeing about the board member and about
    the person whose tenure ended last year, silently.

    ⚠️ The grep half of this guard cannot see the fourth path, and the fourth
       path is the one that exists without anybody writing a line of code:
       Django's admin. Hence the second test below, which is an assertion
       rather than a search.
    """

    # Escaped and written only here, so this file never contains the literal
    # text it hunts for and the guard can scan itself.
    ASSIGNMENT = r"\.served_as(_declared_by)?\s*="
    # Where it is legitimately assigned: the setter, and the backfill that
    # predates it. Both are named, so widening this list is a visible act.
    ALLOWED = [
        "events/services.py",
        "events/migrations/0014_backfill_served_as.py",
    ]

    def test_only_set_served_as_writes_the_identity_columns(self):
        hits = offending_lines(self.ASSIGNMENT, skip=self.ALLOWED)
        self.assertEqual(
            hits,
            [],
            "Write the identity through events.services.set_served_as(), which "
            "stamps who said so at the same time:\n" + "\n".join(hits),
        )

    def test_the_admin_cannot_edit_the_identity_columns(self):
        """⚠️ An assertion, not a grep, because there is nothing to grep.

        A field is editable in the admin by default — the write path is created
        by *not* writing code, which is precisely what a source search cannot
        see. And what comes out of it is not a wrong value but a value with no
        declared_by beside it: reports file the row under "identity not
        recorded", the FLSA prompt cannot say who claimed it, and every page
        looks entirely normal.
        """
        from events.admin import ParticipationAdmin

        for field in ["served_as", "served_as_declared_by", "checked_in_method"]:
            with self.subTest(field=field):
                self.assertIn(
                    field, ParticipationAdmin.readonly_fields,
                    f"{field} records who said something; the admin form must "
                    "not be able to change it without saying who.",
                )


class NotificationBackendGuardTests(TestCase):
    """Lint-as-test: a delivery backend knows an address, a channel and words.

    Nothing else. The moment one of them knows that minors are notified through
    a guardian, changing provider means rewriting that rule — and it is a rule
    about this foundation, which no notification platform has ever heard of.
    Who to tell lives in events/services.py::resolve_recipients(); this package
    only puts bytes on a wire. See goal.md D22.
    """

    BUSINESS_NAMES = r"\b(Contact|Participation|is_minor|EventRole|guardian)\b"

    def test_the_backend_never_imports_contact_or_participation(self):
        hits = [
            hit for hit in offending_lines(self.BUSINESS_NAMES)
            if hit.startswith("core/notifications/")
        ]
        self.assertEqual(
            hits,
            [],
            "Delivery adapters take (address, channel, content) and nothing "
            "else:\n" + "\n".join(hits),
        )


class ViewsAreThinGuardTests(TestCase):
    """Lint-as-test: statistics live in QuerySets and services, not in views.

    R4–R8 are the answers this whole phase exists to produce. Computed in a
    view, they get rewritten along with the templates the first time the
    interface changes — which is scheduled, not hypothetical (D18).
    """

    # Aggregates, capitalised: the ORM functions, not queryset.count().
    AGGREGATES = r"\b(Sum|Count|Avg|Max|Min)\("
    # Date arithmetic of any kind belongs to core.timeutils or a QuerySet.
    DATE_MATHS = r"\btimedelta\(|\bmonth_bounds\(|\blocal_today\(|\blocal_now\("

    def test_views_contain_no_statistics_or_date_arithmetic(self):
        hits = offending_lines(
            f"{self.AGGREGATES}|{self.DATE_MATHS}", only_filenames={"views.py"})
        self.assertEqual(
            hits,
            [],
            "Views are thin shells — put this on a QuerySet or in services.py:\n"
            + "\n".join(hits),
        )


class AdminHasNoLogicGuardTests(TestCase):
    """Lint-as-test: the four admin hooks that would hide business logic (D18).

    The executable form of "delete admin.py and every business rule is still
    there". get_queryset in particular is the tempting one — it is where an
    annotation would go, and an annotation there is a rule the front end cannot
    reach.
    """

    HOOKS = r"def (save_model|save_related|get_queryset|get_formset)\b"

    def test_admin_does_not_override_the_four_hooks(self):
        hits = offending_lines(self.HOOKS, only_filenames={"admin.py"})
        self.assertEqual(
            hits,
            [],
            "Move this to models.py / services.py — admin.py renders, it does "
            "not decide:\n" + "\n".join(hits),
        )


class QuotaEmailBackend(BaseEmailBackend):
    """A mail server that accepts `allowance` messages and then refuses.

    Which is what a free tier's daily limit looks like from inside the process:
    not a rejection at connect time, but the same call working and then not.
    Class attributes rather than instance ones because Django builds this from
    a dotted path in settings and hands it no arguments.
    """

    allowance = 1
    sent: list = []
    opened = 0

    @classmethod
    def reset(cls, allowance=1):
        cls.allowance, cls.sent, cls.opened = allowance, [], 0

    def open(self):
        type(self).opened += 1
        return True

    def close(self):
        pass

    def send_messages(self, email_messages):
        for message in email_messages:
            if len(type(self).sent) >= type(self).allowance:
                raise smtplib.SMTPDataError(554, b"Daily sending quota exceeded")
            type(self).sent.append(message)
        return len(email_messages)


class NotificationBackendTests(TestCase):
    """The adapters themselves. No network is touched anywhere in here."""

    def message(self, channel="email", to="lisi@example.com"):
        from core.notifications.base import Message

        return Message(to=to, channel=channel, subject="Subject", body="Body")

    def test_the_configured_backend_is_the_one_that_gets_used(self):
        from core.notifications.base import get_backend
        from core.notifications.locmem import LocmemBackend

        with self.settings(NOTIFICATION_BACKEND="core.notifications.locmem.LocmemBackend"):
            self.assertIsInstance(get_backend(), LocmemBackend)

    def test_the_email_backend_reports_an_sms_as_not_accepted(self):
        # Rather than dropping it. With this backend configured, an SMS-only
        # recipient is a real gap — and D22's whole complaint is about gaps
        # that nobody can see.
        from core.notifications.django_email import DjangoEmailBackend

        results = DjangoEmailBackend().send([self.message(channel="sms", to="+14085550100")])
        self.assertFalse(results[0].accepted)

    def test_the_email_backend_sends_email(self):
        from django.core import mail

        from core.notifications.django_email import DjangoEmailBackend

        with self.settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
            results = DjangoEmailBackend().send([self.message()])
        self.assertTrue(results[0].accepted)
        self.assertEqual(len(mail.outbox), 1)

    def test_the_quota_running_out_is_reported_per_message_not_raised(self):
        # ⭐ The one that matters for a free tier. The provider takes the first
        #    message and refuses the second; the caller has to come out of this
        #    holding a verdict for both, because it is about to write down who
        #    was told — and the first one really was.
        from core.notifications.django_email import DjangoEmailBackend

        QuotaEmailBackend.reset(allowance=1)
        with self.settings(EMAIL_BACKEND="core.tests.QuotaEmailBackend"):
            results = DjangoEmailBackend().send([
                self.message(to="first@example.com"),
                self.message(to="second@example.com"),
                self.message(to="third@example.com"),
            ])
        self.assertEqual([r.accepted for r in results], [True, False, False])
        self.assertIn("quota", results[1].detail.lower())

    def test_a_batch_shares_one_connection(self):
        # A hundred signups used to mean a hundred connect/authenticate/quit
        # cycles, which providers rate-limit in their own right — and the
        # failure arrives as refused connections partway down a list that was
        # fine a moment earlier.
        from core.notifications.django_email import DjangoEmailBackend

        QuotaEmailBackend.reset(allowance=10)
        with self.settings(EMAIL_BACKEND="core.tests.QuotaEmailBackend"):
            DjangoEmailBackend().send([self.message(to=f"v{n}@example.com")
                                       for n in range(5)])
        self.assertEqual(QuotaEmailBackend.opened, 1)

    def test_a_mail_server_that_will_not_open_fails_every_message(self):
        # Nothing went out, so nothing may be recorded as sent. Reported rather
        # than raised for the same reason as above: the caller writes it down.
        from core.notifications.django_email import DjangoEmailBackend

        with self.settings(EMAIL_BACKEND="core.tests.QuotaEmailBackend"):
            with mock.patch.object(QuotaEmailBackend, "open",
                                   side_effect=OSError("connection refused")):
                results = DjangoEmailBackend().send(
                    [self.message(), self.message(to="other@example.com")])
        self.assertEqual([r.accepted for r in results], [False, False])
        self.assertIn("connection refused", results[0].detail)

    def test_the_novu_backend_posts_to_the_api(self):
        # Mocked on purpose: there is no domain and no sender identity on a
        # laptop, so a live integration could be neither sent nor verified.
        # Connecting it for real belongs to Phase C; what is being pinned here
        # is that the seam is in the right place and the call has the shape.
        import io
        import json
        from unittest import mock

        from core.notifications.novu import NovuBackend

        response = io.BytesIO(json.dumps({"data": {"transactionId": "abc"}}).encode())
        response.__enter__ = lambda self=response: self
        response.__exit__ = lambda *args: False
        with mock.patch("urllib.request.urlopen", return_value=response) as opened:
            results = NovuBackend(api_key="k").send([self.message()])
        self.assertTrue(results[0].accepted)
        self.assertEqual(results[0].provider_ref, "abc")
        self.assertTrue(opened.called)

    def test_a_novu_failure_is_reported_rather_than_raised(self):
        # One bad address must not stop the rest of the batch; the caller
        # records what happened instead.
        import urllib.error
        from unittest import mock

        from core.notifications.novu import NovuBackend

        with mock.patch("urllib.request.urlopen",
                        side_effect=urllib.error.URLError("nope")):
            results = NovuBackend(api_key="k").send([self.message()])
        self.assertFalse(results[0].accepted)


class DocTestReferenceGuardTests(TestCase):
    """Lint-as-test: every **qualified** test name in the docs points at a real one.

    Sibling of MarkdownLinkGuardTests, and for the same reason: the planning
    documents cite guards constantly ("守卫：`core.tests.XTests.test_y`"), and a
    citation that no longer resolves fails silently. The reader goes looking for
    the test that supposedly pins a rule, does not find it, and is left unable
    to tell which of the two is stale — the rule or the pointer.

    Found two real breaks the day it was written, both from renames months
    earlier: a class renamed (`StripLayoutTests` → `StripDrawTests`) and a
    method renamed under a class that still existed.

    ⚠️ **Only qualified references are checked** — `SomeTests.test_x`. The bare
       `def test_x(self)` lines in 01/02-roadmap are Phase A/B's *checklists of
       what to pin*, written before the code, and about fifteen of those names
       drifted during implementation. Those are history, not pointers; the
       convention is written at the top of 02-roadmap. Checking them here would
       either fail forever or force a rewrite of a record this project keeps on
       purpose.

    ⚠️ The method has to live **in the class named**, not merely somewhere. A
       method that moved class is exactly the case the reader cannot resolve
       alone, and it is one of the two this test caught.

    2026-08-18 — it now reads the **source** as well as the documents. The gap
    was found the way these usually are: three dangling citations were written
    into `assets/app.css` and `events/schedule.py` in one afternoon, by somebody
    who had just read the commit that added this guard. Docs were checked;
    comments were not, and a comment saying "守卫：X" is the same promise to the
    same reader. Everything outside this session's three was already clean, so
    turning it on cost nothing.
    """

    REFERENCE = re.compile(r"(?:[a-z_]+\.tests\.)?(\w*Tests)\.(test_[a-z0-9_]+)")

    def suites(self):
        """{class name: {method names}} across every app's tests.py.

        ⚠️ Not named `test_suites`: the runner collects anything starting with
           `test_`, so a helper by that name is silently reported as a passing
           test. Caught here by the count going from 1 to 2.
        """
        suites = {}
        for path in sorted(Path(settings.BASE_DIR).glob("*/tests.py")):
            source = path.read_text()
            for block in re.finditer(
                    r"^class (\w+)\(.*?(?=^class |\Z)", source, re.S | re.M):
                suites.setdefault(block.group(1), set()).update(
                    re.findall(r"^    def (test_[a-z0-9_]+)\(", block.group(0), re.M))
        return suites

    def cited_files(self):
        """The documents, plus the source files that cite guards in comments.

        ⚠️ `*/tests.py` is excluded, and not as an optimisation: those files are
           full of bare `test_x` names being *defined*, and a test that renames
           its own neighbour would then have to update a citation of itself.
        """
        root = Path(settings.BASE_DIR)
        skip = {"node_modules", "staticfiles", ".venv", "migrations", "__pycache__"}
        found = list(root.glob("docs/**/*.md"))
        for pattern in ("*/*.py", "*/**/*.html", "assets/*.css", "assets/**/*.js"):
            found += [path for path in root.glob(pattern)
                      if not skip & set(path.parts) and path.name != "tests.py"]
        return sorted(set(found))

    def test_every_cited_guard_exists(self):
        suites = self.suites()
        self.assertIn("MarkdownLinkGuardTests", suites, "no test suites were found")

        problems = []
        for path in self.cited_files():
            for number, line in enumerate(path.read_text().split("\n"), 1):
                for suite, name in self.REFERENCE.findall(line):
                    where = f"{path.relative_to(settings.BASE_DIR)}:{number}"
                    if suite not in suites:
                        problems.append(f"{where}  {suite} — no such test class")
                    elif name not in suites[suite]:
                        problems.append(f"{where}  {suite}.{name} — no such test")
        self.assertEqual(
            problems, [],
            "A doc points at a guard that does not exist. Either the guard was "
            "renamed (fix the citation) or it is gone (say so, and say what "
            "pins the rule now):\n" + "\n".join(problems))


class MarkdownLinkGuardTests(TestCase):
    """Lint-as-test: every link in every .md file resolves — file and anchor.

    The planning documents are the memory of this project, and they are dense
    with cross-references: a few hundred of them across goal.md, decisions/,
    phase-b.md and the roadmaps. A broken one fails the way this project keeps
    convicting: silently. Nothing errors, the reader just lands nowhere — and the
    2026-07-30 split of goal.md into a hub plus one file per decision moved every
    single target, so "check it by hand" stopped being an option.

    Found four real breaks the day it was written, one of them minutes old.
    """

    # ``` or ~~~, possibly inside a blockquote. Headings inside a fence are code
    # comments (`# settings/base.py`), not headings, and GitHub gives them no
    # anchor — counting them would make this guard accept links that 404.
    FENCE = re.compile(r"^\s*(?:>\s*)*(?:```|~~~)")
    HEADING = re.compile(r"^(?:>\s*)*(#{1,6})\s+(.*?)\s*$")
    # [text](target) and [text](target#anchor). Bare #anchor means this file.
    LINK = re.compile(r"\]\(([^)\s]*?)(#[^)\s]*)?\)")
    EXTERNAL = re.compile(r"^(https?:|mailto:|tel:|//)")

    @staticmethod
    def slug(heading):
        """GitHub's anchor for a heading: strip markup, drop punctuation, hyphenate.

        CJK survives because \\w is unicode-aware, which is what makes this work
        on documents written in Chinese.
        """
        text = re.sub(r"[`*~]", "", heading)
        text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
        return text.lower().replace(" ", "-")

    @classmethod
    def anchors(cls, text):
        """Every anchor a document offers, duplicates suffixed the way GitHub does."""
        found, seen = set(), {}
        in_fence = False
        for line in text.split("\n"):
            if cls.FENCE.match(line):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            match = cls.HEADING.match(line)
            if not match:
                continue
            base = cls.slug(match.group(2))
            count = seen.get(base, 0)
            seen[base] = count + 1
            found.add(base if count == 0 else f"{base}-{count}")
        return found

    def test_every_markdown_link_resolves(self):
        root = Path(settings.BASE_DIR)
        documents = dict(project_markdown_files())
        anchors = {path: self.anchors(text) for path, text in documents.items()}

        broken = []
        for path, text in documents.items():
            in_fence = False
            for number, line in enumerate(text.split("\n"), 1):
                if self.FENCE.match(line):
                    in_fence = not in_fence
                    continue
                if in_fence:  # links inside code samples are illustrations
                    continue
                for target, anchor in self.LINK.findall(line):
                    anchor = anchor.lstrip("#")
                    if self.EXTERNAL.match(target):
                        continue
                    where = path
                    if target:
                        resolved = (root / path).parent / target
                        if not resolved.exists():
                            broken.append(f"{path}:{number}  no such file: {target}")
                            continue
                        where = resolved.resolve().relative_to(root.resolve())
                    if not anchor:
                        continue
                    if where not in anchors:  # a real file, but not markdown
                        continue
                    if anchor not in anchors[where]:
                        broken.append(f"{path}:{number}  no such heading: {target}#{anchor}")

        self.assertEqual(
            broken,
            [],
            f"{len(broken)} markdown link(s) point nowhere. Anchors follow the "
            "heading text, so renaming a heading breaks every link to it:\n"
            + "\n".join(broken),
        )


class DecisionSectionReferenceGuardTests(TestCase):
    """Lint-as-test: prose references like "D33 第七节" point at a real section.

    MarkdownLinkGuardTests checks the file and the anchor of a link. It cannot
    check the *text* of one — and this project cites decisions by section number
    in prose far more often than by anchor: "见 D36 第四节", "同 D33 第七节".

    That gap has already cost it. Splitting D32 into D33–D38 on 2026-08-14
    renumbered every section, and two references to "D32 第七节" survived,
    pointing into a file that now ends at 第五节. Both sat there for a day with
    every guard green — which is exactly goal.md 约定 3's "假引用比没引用更糟":
    a reader follows it, finds nothing, and stops trusting the other few hundred.

    ⚠️ Mention is not use. A reference inside 「」 is the project quoting a
       reference — most often the broken one it is in the middle of correcting.
       Same rule the emphasis guard already applies to ⭐ inside backticks.
    """

    FENCE = re.compile(r"^\s*(?:>\s*)*(?:```|~~~)")
    # 「…」 quotes a reference rather than making one; backticks name a symbol.
    QUOTED = re.compile(r"「[^」\n]*」|`[^`\n]*`")
    # "D33 第七节" / "D9 第一节". The numeral is CJK: 一, 十, 十一 …
    REFERENCE = re.compile(r"\bD(\d{1,2})\s*第([一二三四五六七八九十]+)节")
    # "## 七、打卡：不做" — the numbered sections a decision actually offers.
    # Lazy prefix so an ornament (## ⭐ 四、…) does not swallow the numeral.
    SECTION = re.compile(r"^##\s+.*?([一二三四五六七八九十]+)、")

    DIGITS = "零一二三四五六七八九"

    @classmethod
    def value(cls, numeral):
        """一 → 1, 十 → 10, 十一 → 11. Only ever used to sort a message."""
        if "十" not in numeral:
            return cls.DIGITS.find(numeral)
        tens, _, ones = numeral.partition("十")
        return (cls.DIGITS.find(tens) if tens else 1) * 10 + (
            cls.DIGITS.find(ones) if ones else 0
        )

    @classmethod
    def sections_by_decision(cls, documents):
        """{33: {"一", "二", …}} for every decisions/D*.md we can parse."""
        found = {}
        for path, text in documents.items():
            if path.parent.name != "decisions" or not path.name.startswith("D"):
                continue
            number = re.match(r"D(\d{1,2})", path.name)
            if not number:
                continue
            sections, in_fence = set(), False
            for line in text.split("\n"):
                if cls.FENCE.match(line):
                    in_fence = not in_fence
                    continue
                if in_fence:
                    continue
                match = cls.SECTION.match(line)
                if match:
                    sections.add(match.group(1))
            if sections:
                found[int(number.group(1))] = sections
        return found

    def test_every_section_reference_resolves(self):
        documents = dict(project_markdown_files())
        sections = self.sections_by_decision(documents)

        broken = []
        for path, text in documents.items():
            in_fence = False
            for number, line in enumerate(text.split("\n"), 1):
                if self.FENCE.match(line):
                    in_fence = not in_fence
                    continue
                if in_fence:
                    continue
                for decision, section in self.REFERENCE.findall(
                    self.QUOTED.sub("", line)
                ):
                    decision = int(decision)
                    # A decision with no numbered sections at all (or no file of
                    # its own yet) is not something this guard can rule on.
                    if decision not in sections:
                        continue
                    if section not in sections[decision]:
                        have = "/".join(sorted(sections[decision], key=self.value))
                        broken.append(
                            f"{path}:{number}  D{decision} 第{section}节 does not "
                            f"exist — D{decision} has 第{have}节"
                        )

        self.assertEqual(
            broken,
            [],
            f"{len(broken)} reference(s) name a section that is not there. "
            "Section numbers move when a decision is split or a section is "
            "inserted, and goal.md 约定 3 rates a false reference as worse than "
            "no reference at all:\n" + "\n".join(broken),
        )


class EmphasisGuardTests(TestCase):
    """Lint-as-test: emphasis is a budget, not a tone of voice (goal.md 约定 6).

    These documents had drifted to 35% of lines carrying bold, 46 ⭐ and 206 ⚠️,
    with each symbol standing for three or four different things. Emphasis
    everywhere is emphasis nowhere: the reader cannot tell which line is the one
    that matters. This guard does not police taste — it polices the three misuses
    that are wrong by definition, so the cleanup cannot quietly undo itself.
    """

    FENCE = re.compile(r"^\s*(?:>\s*)*(?:```|~~~)")
    # A whole table cell in bold: | **...** |. A bold column says nothing.
    BOLD_CELL = re.compile(r"\|\s*\*\*[^*|\n]+\*\*\s*(?=\|)")
    # A whole line in bold. If the line is the point, it is a heading.
    BOLD_LINE = re.compile(r"^(?:\s*(?:[-*]\s+|>\s+|\d+\.\s+)?)\*\*[^*\n]{15,}\*\*[ \t]*$")
    # ⚠️ on a revision note. A changelog entry is history, not a trap.
    WARN_ON_NOTE = re.compile(
        r"⚠️[ \t]*(?=\*\*(?:20\d\d-\d\d-\d\d|原文|原方案|原来|本条|本节|整节))"
    )
    # ⭐ means "the single acceptance point": roughly one per decision or per
    # step, so even a long document holds a handful.
    STARS_PER_FILE = 4
    # `⭐` in backticks is the symbol being *named* — as the convention table in
    # goal.md has to do — rather than used. Mention is not use.
    CODE_SPAN = re.compile(r"`[^`\n]*`")

    def test_no_bold_cells_bold_lines_or_warnings_on_changelog_entries(self):
        problems = []
        for path, text in project_markdown_files():
            in_fence = False
            for number, line in enumerate(text.split("\n"), 1):
                if self.FENCE.match(line):
                    in_fence = not in_fence
                    continue
                if in_fence:
                    continue
                if self.BOLD_CELL.search(line):
                    problems.append(f"{path}:{number}  whole table cell in bold")
                if self.BOLD_LINE.match(line):
                    problems.append(f"{path}:{number}  whole line in bold — make it a heading")
                if self.WARN_ON_NOTE.search(line):
                    problems.append(
                        f"{path}:{number}  ⚠️ on a revision note — it is history, not a trap"
                    )
        self.assertEqual(
            problems,
            [],
            "goal.md「强调的用法」: bold marks the load-bearing half of a sentence, "
            "never a whole cell, line or sentence:\n" + "\n".join(problems),
        )

    def test_stars_stay_scarce(self):
        # ⭐ is the acceptance point: if this one fails, that whole piece of design
        # was pointless. Roughly one per decision — a file full of them has none.
        heavy = []
        for path, text in project_markdown_files():
            used = self.CODE_SPAN.sub("", text).count("⭐")
            if used > self.STARS_PER_FILE:
                heavy.append(f"{path}: {used} ⭐")
        self.assertEqual(
            heavy,
            [],
            f"⭐ marks the one test that must pass; at most {self.STARS_PER_FILE} "
            "per file:\n" + "\n".join(heavy),
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


class AnonymousReachabilityTests(TestCase):
    """C0.5.1 — the first thing a first-time visitor does had better not 404.

    Django's LOGIN_URL defaults to "/accounts/login/"; accounts/urls.py is
    mounted at the root prefix, so the login page really lives at "/login/".
    Nothing about that mismatch is loud: the redirect is issued correctly, the
    target simply is not there.

    ⚠️ 404 个测试没有一个抓到它，成因和 C0.2 那五处缺口是同一个：既有的测试要么
       先登录、要么只断言 302 不跟随。**这一条的价值全在 follow=True 上** ——
       不跟随的断言证明的是「跳了」，而坏掉的是「跳到哪」。
    """

    def test_following_the_login_redirect_lands_on_a_real_page(self):
        response = self.client.get("/events/", follow=True)
        self.assertEqual(response.status_code, 200)
        # Named rather than literal so a future move of the login page keeps
        # this honest: what is asserted is "the redirect chain ends at the
        # login page", not "it ends at some URL that happens to answer".
        self.assertRedirects(self.client.get("/events/"), f"{settings.LOGIN_URL}?next=/events/")

    def test_every_link_the_anonymous_navigation_draws_resolves(self):
        """The acceptance criterion, as a test: click every nav link, no 404.

        Scanning the rendered HTML rather than listing the URLs here on purpose
        — a link added to base.html is covered the moment it exists, which is
        the only version of this guard worth having.

        ⚠️ `<a href>` only, not every href on the page. The first version matched
           any href= and started failing the moment base.html grew a
           `<link rel="stylesheet">`: static files are served by whitenoise out
           of STATIC_ROOT, which is a build product and legitimately absent
           here. That was the guard being wrong, not the page — what this
           asserts is "click every link", and a stylesheet is not something a
           visitor clicks.
        """
        page = self.client.get(settings.LOGIN_URL)
        self.assertEqual(page.status_code, 200)
        hrefs = {
            href for href in re.findall(r'<a\b[^>]*\bhref="([^"]+)"', page.content.decode())
            if href.startswith("/")
        }
        self.assertIn("/events/", hrefs, "The nav stopped drawing its own links.")
        dead = [
            href for href in sorted(hrefs)
            if self.client.get(href, follow=True).status_code == 404
        ]
        self.assertEqual(dead, [], "Anonymous nav links that 404:\n" + "\n".join(dead))


class ErrorPageTests(TestCase):
    """C0.5.2 — without these templates every refusal explains nothing.

    Django renders a bare page with an empty `details` when 403.html is absent,
    so the string in PermissionDenied() never reaches a human. That string is
    the mitigation: org/permissions.py writes SCOPED_DENIAL once precisely so
    the next person fixes the account rather than deleting the check.
    """

    def test_the_403_page_prints_the_reason_it_was_refused(self):
        from org.permissions import SCOPED_DENIAL

        user = get_user_model().objects.create_user(email="nobody@example.com", password="x")
        self.client.force_login(user)
        response = self.client.get(reverse("events:event_create"))
        self.assertEqual(response.status_code, 403)
        self.assertTemplateUsed(response, "403.html")
        # ⚠️ 断言的是原文，不是「页面非空」。没有这一条，模板以后被换掉、
        #    {{ exception }} 被删掉，都不会有任何东西变红。
        self.assertIn(SCOPED_DENIAL, response.content.decode())

    def test_the_404_page_is_ours(self):
        response = self.client.get("/no-such-page-exists/")
        self.assertEqual(response.status_code, 404)
        self.assertTemplateUsed(response, "404.html")

    def test_the_500_template_renders_with_no_request_at_all(self):
        """The exact condition Django puts it in: template.render(), no context.

        django.views.defaults.server_error renders this template without a
        request, so context processors do not run and `user` is empty. Anything
        the template needed from a request would raise here — and a 500 page
        that raises leaves the visitor with Django's plain-text fallback and
        leaves us unable to see what this page said.
        """
        from django.template import loader

        html = loader.get_template("500.html").render()
        self.assertIn("Something went wrong", html)


def _theme_colors():
    """The --color-* tokens in assets/app.css, as {name: "#rrggbb"}.

    Parsed from the stylesheet rather than repeated here: that file is the one
    definition point for colour, and a table of hex values in a test is a
    second one — the copy that drifts is always the one nobody remembers.
    """
    source = (Path(settings.BASE_DIR) / "assets" / "app.css").read_text(encoding="utf-8")
    return {
        name: value.lower()
        for name, value in re.findall(r"--color-([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;", source)
    }


def _relative_luminance(hex_colour):
    """WCAG 2.x relative luminance."""
    channels = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(one, other):
    """How far apart two colours are, 1.0 (identical) to 21.0 (black on white)."""
    a, b = _relative_luminance(one), _relative_luminance(other)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


class ContrastGuardTests(TestCase):
    """design-system.md 那张对比度表，做成跑得出来的数字。

    ⚠️ 这条守卫存在的理由是**目测查不出来**：`ink-500` 在白底上是 4.34:1，
       差 4.5 那条线一点点，而 4.34 和 4.5 在屏幕上看不出任何区别。
       写这份规范的第一版就凭感觉写错了两条（见 design-system.md 对比度那节）。

    颜色从 assets/app.css 解析，不在这里抄一份 —— 抄一份就是第二个真相。
    """

    # (前景, 背景, 至少几比几, 这是干什么用的)。改色值改到不达标会红。
    PAIRS = [
        ("brand-700", "white",     4.5, "链接文字"),
        ("white",     "brand-600", 4.5, "主按钮：白字配品牌底"),
        ("ink-900",   "ink-50",    4.5, "浅色下的正文"),
        ("ink-600",   "white",     4.5, "浅色下的次要文字 / 帮助文字"),
        ("ink-50",    "ink-950",   4.5, "深色下的正文"),
        ("brand-300", "ink-950",   4.5, "深色下的链接"),
        ("success-fg", "success-bg", 4.5, "状态标签 success"),
        ("warning-fg", "warning-bg", 4.5, "状态标签 warning"),
        ("danger-fg",  "danger-bg",  4.5, "状态标签 danger"),
        ("info-fg",    "info-bg",    4.5, "状态标签 info"),
        ("success-fg-dark", "success-bg-dark", 4.5, "深色下的 success"),
        ("warning-fg-dark", "warning-bg-dark", 4.5, "深色下的 warning"),
        ("danger-fg-dark",  "danger-bg-dark",  4.5, "深色下的 danger"),
        ("info-fg-dark",    "info-bg-dark",    4.5, "深色下的 info"),
        # 分类徽章（ministry 名）。⚠️ 它用的是 brand 而不是语义色 ——
        # 一个不表示任何状态的标签借了「信息」那一档，读起来就有了语气。
        ("brand-800", "brand-50",  4.5, "分类徽章，浅色下"),
        ("brand-200", "brand-900", 4.5, "分类徽章，深色下"),
        # 焦点环只要看得见就行，按「大字号和图标」那档 3:1。
        ("brand-500", "white",   3.0, "焦点环，浅色下"),
        ("brand-500", "ink-950", 3.0, "焦点环，深色下"),
    ]

    def test_every_documented_pair_meets_its_ratio(self):
        palette = _theme_colors() | {"white": "#ffffff", "black": "#000000"}
        failures = []
        for fg, bg, minimum, purpose in self.PAIRS:
            missing = [n for n in (fg, bg) if n not in palette]
            if missing:
                failures.append(f"{purpose}: 令牌不存在 {missing}")
                continue
            got = contrast_ratio(palette[fg], palette[bg])
            if got < minimum:
                failures.append(
                    f"{purpose}: {fg} on {bg} = {got:.2f}, 要求 {minimum}")
        self.assertEqual(
            failures, [],
            "design-system.md 的对比度表对不上 assets/app.css：\n" + "\n".join(failures))

    def test_ink_500_is_not_usable_as_body_text(self):
        """规范里那条最容易破的规矩，写成断言。

        `ink-500` 看起来就像个「次要文字」色，而它在白底上是 4.34:1 —— 过不了线。
        ⚠️ 这条断言的方向是**反的**（断言它 < 4.5），所以有人把 ink-500 调深到
        达标时它会红。那不是误报：调深了就该回到规范里把「只做边框」那条删掉，
        而不是留着一条已经不成立的规矩。
        """
        palette = _theme_colors()
        self.assertLess(
            contrast_ratio(palette["ink-500"], "#ffffff"), 4.5,
            "ink-500 现在够做正文了 —— 去 design-system.md 删掉「只做边框」那条")


TEMPLATE_COMMENT = re.compile(r"\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}", re.S)
INLINE_TEMPLATE_COMMENT = re.compile(r"\{#.*?#\}", re.S)
# Han, hiragana/katakana, Hangul. Built from ranges rather than written out, so
# this module contains no character it is looking for — the guards scan the
# project, and four earlier ones in this repo matched their own source.
CJK_RANGES = (
    (0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0x3040, 0x30FF),
    (0xAC00, 0xD7AF), (0x3000, 0x303F), (0xFF00, 0xFF65),
)


def _is_cjk(character):
    return any(low <= ord(character) <= high for low, high in CJK_RANGES)


def project_template_files():
    """Every .html file we wrote, as (relative path, text) pairs.

    Walked, not listed: a template added tomorrow is covered the moment it
    exists, which is the only version of a guard worth having.
    """
    root = Path(settings.BASE_DIR)
    for path in sorted(root.rglob("*.html")):
        relative = path.relative_to(root)
        if SKIPPED_DIRS & set(relative.parts) or ".git" in relative.parts:
            continue
        # ⚠️ Django templates only — an app's templates/ directory. Everything
        #    else that happens to end in .html is not interface: the diagrams
        #    under docs/planning/ are planning documents, and D23 keeps those
        #    in Chinese along with the rest of docs/. Scanning by extension
        #    alone made the language guard fail on a diagram, which is the
        #    guard being wrong rather than the document.
        if "templates" not in relative.parts:
            continue
        yield relative, path.read_text(encoding="utf-8")


def _blank_out_comments(text):
    """Replace template comments with blank lines, keeping line numbers honest."""
    def blanks(match):
        return "\n" * match.group(0).count("\n")
    return INLINE_TEMPLATE_COMMENT.sub(blanks, TEMPLATE_COMMENT.sub(blanks, text))


class InterfaceLanguageGuardTests(TestCase):
    """D23: the interface is English. Templates carry no CJK outside comments.

    Stronger than the guard the abandoned bilingual plan would have needed.
    That one could only ask "is this string wrapped in a translation tag?",
    which leaves a middle state — wrapped but never translated — that looks
    fine and ships the wrong language. This one asks "is there any of it?",
    and there is no middle state to hide in.

    ⚠️ 分界线是「这句话会不会出现在浏览器里」，不是「它写在哪个文件里」。
       所以 {% comment %} 块里的中文不算 —— 那是本项目推理的落点，
       翻成英文是纯损失。注释之外的一个汉字都不许有。
    """

    def test_no_cjk_outside_template_comments(self):
        offenders = []
        for relative, source in project_template_files():
            for number, line in enumerate(_blank_out_comments(source).splitlines(), 1):
                found = [c for c in line if _is_cjk(c)]
                if found:
                    offenders.append(f"{relative}:{number}: {''.join(found)}")
        self.assertEqual(
            offenders, [],
            "D23: interface text is English. Move prose into a template comment "
            "or translate it:\n" + "\n".join(offenders))


# Attribute names Alpine reads: the x- prefix plus the two shorthands.
ALPINE_ATTRIBUTE = re.compile(r'(?:\bx-[\w:.-]+|\s@[\w:.-]+|\s:[\w:.-]+)\s*=\s*"([^"]*)"')
# What may never be decided in the browser. Split by why, because the
# consequences differ.
ALPINE_FORBIDDEN = {
    # Permission. The server is the only place that knows, and a button drawn
    # for somebody who may not press it has already leaked the information.
    "can_manage", "can_publish", "can_view", "can_grant", "is_admin",
    "is_staff", "is_superuser", "perms", "has_perm",
    # Arithmetic the server has already done. A second answer is a second
    # truth, free to disagree and with nothing to report that it has.
    "hours", "total_hours", "amount", "subtotal", "salary",
    # Dates. D16: "today" has one spelling, and the browser's timezone is not
    # the foundation's.
    "new Date", "Date.now", "getFullYear", "getMonth", "setDate", "toISOString",
}
ALPINE_EXEMPT = "alpine-guard-ok"


class AlpineStaysUiOnlyGuardTests(TestCase):
    """D24: x- attributes hold UI state, never business decisions.

    ⚠️ 越界不会报错。把一个权限判断写进 x-show，页面照常渲染、测试照常绿，
       只是那个按钮对不该看见它的人也画出来了。服务端仍然会 403，
       所以泄露的是**信息**不是权限 —— 但那也是泄露，而且没有任何东西会告诉你
       它发生了。这条守卫就是为这一件事存在的。

    False positives cost one `alpine-guard-ok` comment on the line or just
    above it — the same escape hatch the loop guard uses, and for the same
    reason: a miss here is silent and a false positive is not.
    """

    def test_no_business_logic_in_alpine_attributes(self):
        offenders = []
        for relative, source in project_template_files():
            lines = _blank_out_comments(source).splitlines()
            for number, line in enumerate(lines, 1):
                exempted = (ALPINE_EXEMPT in line
                            or (number > 1 and ALPINE_EXEMPT in lines[number - 2]))
                if exempted:
                    continue
                for expression in ALPINE_ATTRIBUTE.findall(line):
                    hit = sorted(w for w in ALPINE_FORBIDDEN if w in expression)
                    if hit:
                        offenders.append(f"{relative}:{number}: {hit} in {expression[:60]}")
        self.assertEqual(
            offenders, [],
            "D24: Alpine holds UI state only — permissions, arithmetic and date "
            "maths belong on the server:\n" + "\n".join(offenders))


class AssetPathsComeFromTemplatesGuardTests(TestCase):
    """Lint-as-test: no static file path is ever written in JavaScript (2026-08-06).

    Production serves static files through
    `whitenoise.storage.CompressedManifestStaticFilesStorage`, which puts a
    content hash into every filename — `feather-1.webp` is served as
    `feather-1.<hash>.webp`. Only `{% static %}` knows the hashed name, and only
    the template can call it.

    ⚠️ This is on the list of failures this project keeps convicting itself for:
       a path assembled in JS resolves perfectly in development and **404s on
       every request after deploy**, with nothing in the console, because a
       failed `new Image().src` raises nothing and logs nothing. The visible
       symptom is a feature that silently stopped existing.

    So the URLs travel from the template into a data attribute, and the script
    only ever reads them back out. Written as an extension test rather than as
    "no feather paths", because the trap belongs to static files in general —
    the next person to hardcode one will not be doing it to a feather.

    ⚠️ Comments are stripped first: this file's own explanation names the very
       filenames it forbids, and so does the code it guards.
    """

    #: Any file extension that would only ever appear in an asset path.
    ASSET = re.compile(r"\.(?:webp|png|jpe?g|svg|gif|avif|woff2?)\b", re.I)
    LINE_COMMENT = re.compile(r"//.*$", re.M)
    BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)

    def test_no_javascript_source_hardcodes_an_asset_path(self):
        root = Path(settings.BASE_DIR) / "assets" / "js"
        offenders = []
        for path in sorted(root.rglob("*.js")):
            source = self.BLOCK_COMMENT.sub("", path.read_text())
            source = self.LINE_COMMENT.sub("", source)
            for number, line in enumerate(source.splitlines(), 1):
                if self.ASSET.search(line):
                    relative = path.relative_to(settings.BASE_DIR)
                    offenders.append(f"{relative}:{number}: {line.strip()}")
        self.assertEqual(
            offenders,
            [],
            "Production hashes static filenames, so only {% static %} knows "
            "them. Pass the URL in from the template:\n" + "\n".join(offenders),
        )


# Tags may wrap onto a second line, so these are matched against the whole
# file rather than line by line; the line number is counted back from the offset.
TEXTAREA_TAG = re.compile(r"<textarea\b", re.I)
INPUT_TAG = re.compile(r"<input\b[^>]*>", re.I | re.S)
INPUT_TYPE = re.compile(r'\btype\s*=\s*"([^"]*)"', re.I)
#: Input types somebody types **prose** into. Deliberately an allowlist: `type`
#: is optional in HTML and defaults to text, so "anything not on the exempt
#: list" would be the wrong default — it flags nothing when the attribute is
#: missing, which is exactly the case that matters.
TYPED_TEXT_INPUTS = {"", "text", "email", "search", "url", "tel", "password"}


class SelfHostedFontTests(TestCase):
    """Jost is served by us, and every part of that has to hold together.

    ⚠️ The failure this class exists for is a **deploy** failure, not a wrong
       pixel. Production uses ManifestStaticFilesStorage, which parses `url()`
       out of every collected stylesheet and looks the target up in STATIC_ROOT —
       a path that does not resolve makes `collectstatic` raise and the deploy
       stop. That is the same mechanism that forced the CSS sources out of
       `static/` in the first place (see prod.py), so it has bitten this project
       once already.
    """

    FONT_DIR = "core/static/core/fonts"

    def test_the_font_files_are_in_the_repository(self):
        root = Path(settings.BASE_DIR) / self.FONT_DIR
        for name in ["jost-latin.woff2", "jost-latin-ext.woff2"]:
            with self.subTest(file=name):
                self.assertTrue((root / name).exists(), f"{name} is missing")

    def test_the_licence_ships_with_the_font(self):
        # ⚠️ Jost is OFL, and the OFL requires the licence to travel with the
        #    font. Self-hosting a typeface without its licence text is the one
        #    part of this that is not a matter of taste.
        licence = Path(settings.BASE_DIR) / self.FONT_DIR / "OFL.txt"
        self.assertTrue(licence.exists(), "OFL.txt is missing next to the font")
        self.assertIn("SIL Open Font License", licence.read_text())

    def test_every_font_url_in_the_built_css_resolves(self):
        # ⭐ The deploy check, done here rather than at deploy time. Each url() is
        #    resolved the way ManifestStaticFilesStorage will resolve it:
        #    relative to the stylesheet's own place in the collected tree.
        built = Path(settings.BASE_DIR) / "static" / "css" / "app.css"
        if not built.exists():
            self.skipTest("static/css/app.css is a build product; run npm run build")
        broken = []
        for url in re.findall(r"url\(\s*[\"']?([^\"')]+)", built.read_text()):
            if url.startswith(("data:", "http:", "https:", "//")):
                continue
            # The stylesheet is collected to STATIC_ROOT/css/app.css, so a
            # relative url() is resolved from STATIC_ROOT/css/ — which is exactly
            # what normpath("css/" + url) gives.
            target = os.path.normpath(os.path.join("css", url.split("?")[0]))
            if finders.find(target) is None:
                broken.append(f"{url} → {target}")
        self.assertEqual(
            broken, [],
            "url() targets that collectstatic will not find — this is a failed "
            "deploy, not a missing picture:\n" + "\n".join(broken))

    def test_the_stack_still_falls_back_to_a_font_with_cjk(self):
        # ⚠️ Jost has no CJK at all, and this system stores people's real names.
        #    Dropping the system stack after it would send a Chinese name to
        #    whatever the browser picks last.
        source = (Path(settings.BASE_DIR) / "assets" / "app.css").read_text()
        stack = re.search(r"--font-sans:(.*?);", source, re.S).group(1)
        self.assertIn("Jost", stack)
        self.assertIn("Noto Sans", stack)


class HoverUnderlineTests(TestCase):
    """The event-name underline is painted inside a **clipped** box.

    ⚠️ This exists because of a failure that produced no error of any kind. The
       underline is an `::after` on an element that carries `line-clamp-*`, and
       that utility sets `overflow: hidden`. The first version positioned the line
       at `bottom: -0.125rem` — outside the padding box, so it was clipped and
       never painted. Every signal said it was fine: the rule was in the served
       stylesheet, the selector matched, and the computed style reported
       `visibility: visible`, `height: 1px` and the right colour. There was simply
       nothing on the screen.

       So the rule these tests encode is: **as long as the name is clamped, the
       underline may not sit outside it.** Both halves are asserted, because
       either one alone is meaningless — a negative offset is fine on an unclipped
       element, and clamping is fine as long as nothing hangs outside.
    """

    def stylesheet(self):
        return (Path(settings.BASE_DIR) / "assets" / "app.css").read_text()

    def after_block(self):
        match = re.search(r"\.event-name::after\s*\{(.*?)\n  \}", self.stylesheet(), re.S)
        self.assertIsNotNone(match, ".event-name::after is gone from app.css")
        # Comments carry the words "bottom: -0.125rem" as a warning; strip them.
        return re.sub(r"/\*.*?\*/", "", match.group(1), flags=re.S)

    def test_the_underline_never_hangs_outside_the_clipped_box(self):
        offsets = re.findall(r"\b(top|bottom|left|right)\s*:\s*(-[\d.]+\w*)",
                             self.after_block())
        self.assertEqual(
            offsets, [],
            "The name is clamped, so `overflow: hidden` applies and a negative "
            "offset means the underline is clipped away — visible in no way, and "
            "reported by nothing:\n" + str(offsets))

    def test_the_name_is_still_the_clamped_element(self):
        # The reason the rule above exists. If the clamp ever moves off this
        # element, a negative offset becomes harmless again and this pair of tests
        # should be revisited rather than worked around.
        # ⚠️ One template since 2026-08-17 — Past Events had the second one.
        template = "events/_event_list_results.html"
        markup = (Path(settings.BASE_DIR) / "events" / "templates" / template).read_text()
        name_element = re.search(r'<span class="event-name[^"]*"', markup)
        self.assertIsNotNone(name_element, f"no .event-name in {template}")
        self.assertIn("line-clamp", name_element.group(0))

    def test_hovering_the_row_is_what_reveals_it(self):
        # ⚠️ `.event-row:hover`, not `.event-name:hover`. The whole row is one link,
        #    so an underline that only lit up under the text would make the rest of
        #    the row look unclickable.
        css = self.stylesheet()
        self.assertIn(".event-row:hover .event-name::after", css)
        self.assertIn(".event-row:focus-within .event-name::after", css)

    def test_it_grows_from_the_left_and_retracts_to_the_right(self):
        # The two transform-origins are the entire mechanism: one on the resting
        # rule, the other on the hover rule. Equal origins would make it retract
        # the way it arrived.
        resting = self.after_block()
        hover = re.search(
            r"\.event-row:hover \.event-name::after,\s*"
            r"\.event-row:focus-within \.event-name::after \{(.*?)\}",
            self.stylesheet(), re.S).group(1)
        self.assertIn("transform-origin: right", resting)
        self.assertIn("transform-origin: left", hover)


class HomeVerseEntranceTests(TestCase):
    """The verse rises into place on load (2026-08-06).

    ⚠️ A CSS `animation`, not a `transition`: there is no state change to drive a
       transition, and what is wanted is "play once when the page appears" —
       which is exactly the line between the two. So it replays on refresh with
       no JavaScript involved.
    """

    def stylesheet(self):
        return (Path(settings.BASE_DIR) / "assets" / "app.css").read_text()

    def test_both_lines_carry_the_animation(self):
        # ⚠️ The verse block is only rendered when there is a verse. A fresh
        #    database has none, so without this the assertion would be checking
        #    an empty page and passing for the wrong reason.
        page_row = HomePage.load()
        page_row.verse_text = "Whatever you do, work at it with all your heart."
        page_row.verse_reference = "Colossians 3:23"
        page_row.save(update_fields=["verse_text", "verse_reference"])
        page = self.client.get(reverse("home")).content.decode()
        # Two classes, not one on the container: the reference line comes in
        # 180ms later, and a container can only move as a block.
        self.assertIn("home-verse", page)
        self.assertIn("home-verse-ref", page)

    def test_the_animation_fills_backwards(self):
        # ⚠️ Without `both`, the element sits at its *final* state until the
        #    animation starts — so the first frame flashes the finished text and
        #    then jumps back to the start. That flash is worse than no animation.
        css = self.stylesheet()
        block = re.search(r"\.home-verse \{(.*?)\}", css, re.S).group(1)
        self.assertIn("both", block)

    def test_it_only_animates_opacity_and_transform(self):
        # Anything else (top, margin, height) relayouts the block every frame,
        # which is visible stutter on a phone.
        keyframes = re.search(r"@keyframes home-verse-rise \{(.*?)\n  \}",
                              self.stylesheet(), re.S).group(1)
        properties = set(re.findall(r"^\s*([a-z-]+):", keyframes, re.M))
        self.assertEqual(properties, {"opacity", "transform"})

    def test_reduced_motion_still_shows_the_text(self):
        """⭐ The one that matters. Turning the animation off must not turn the
        text off — and with a backwards-filling animation, "off" is not the same
        as "back to normal", so both properties are written back explicitly.
        """
        css = self.stylesheet()
        block = re.search(
            r"@media \(prefers-reduced-motion: reduce\) \{\s*\.home-verse,\s*"
            r"\.home-verse-ref \{(.*?)\}", css, re.S)
        self.assertIsNotNone(block, "no reduced-motion fallback for the verse")
        self.assertIn("opacity: 1", block.group(1))
        self.assertIn("transform: none", block.group(1))


def _composite(top, alpha, bottom):
    """`top` at `alpha` over `bottom`, both #rrggbb, result #rrggbb."""
    def channels(colour):
        colour = colour.lstrip("#")
        return [int(colour[i:i + 2], 16) for i in (0, 2, 4)]
    mixed = [round(t * alpha + b * (1 - alpha))
             for t, b in zip(channels(top), channels(bottom))]
    return "#" + "".join(f"{c:02x}" for c in mixed)


class GlassMaterialContrastTests(TestCase):
    """The dark-mode background and glass, and what they cost in contrast.

    🔴 **This class changed shape on 2026-08-06 and the change is a loss.** It used
       to check the whole stack against a **pure white photograph** — the worst
       possible upload — and everything passed with room to spare. It can no longer
       promise that, because the darkening was deliberately made lighter for how it
       looks, and "looks right" won over "provably safe on any photo".

       What it can still do, and now does:
         · pin the scrim so nobody weakens it further by accident;
         · pin the two figures that were knowingly accepted, so a regression trips;
         · keep the structural rules that have each already been broken once.

    ⚠️ The three darkenings this one spot has had, because the third is a step back
       to something like the first and that is easy to mistake for a mistake:
         ① flat `ink-950 / 0.68`     — additive, crushed the photo's tones
         ② the photo's own `filter`  — multiplicative, kept the texture, contrast
                                       passed everywhere (worst photo 6.68:1),
                                       **but looked too dark**
         ③ the home page's scrim     — current. Looks right; the numbers below are
                                       the price.
       ② was better on every measurement and still lost. Worth remembering that a
       change which improves every metric can still be the wrong change.
    """

    #: The two text colours that end up on those two surfaces.
    OUTSIDE_CARD_TEXT = "ink-50"    # page headings, counts, section intros
    INSIDE_CARD_TEXT = "ink-100"    # body text on a card

    #: What was accepted on 2026-08-06, measured against a **pure white** photo at
    #: the brightest point of the scrim (33% down). Both are below AA.
    #: ⚠️ These are not targets. They are a record of a decision, so that making
    #:    things worse is a test failure rather than a silent drift.
    ACCEPTED_WORST_OUTSIDE = 1.73
    ACCEPTED_WORST_INSIDE = 4.11

    #: And on the photo actually uploaded today, same point.
    CURRENT_PHOTO_OUTSIDE = 4.03

    def scrim(self):
        """The two gradient layers, as [[stop alphas], [stop alphas]].

        Parsed out of the shared component so that changing the markup is what
        this test sees. ⚠️ Read from **one** file on purpose — the whole reason the
        scrim became a component is that the home page and the inner pages have to
        stay identical, and two copies of a gradient drift.
        """
        markup = (Path(settings.BASE_DIR) / "core" / "templates" / "core"
                  / "components" / "_hero_scrim.html").read_text()
        layers = []
        for line in markup.splitlines():
            if "bg-gradient-to-" not in line:
                continue
            stops = re.findall(r"(?:from|via|to)-black/(\d+)", line)
            if "to-transparent" in line:
                stops.append("0")
            layers.append([int(one) / 100 for one in stops])
        self.assertEqual(len(layers), 2,
                         "the scrim no longer has exactly two layers")
        return layers

    def background_under(self, photo, position):
        """The page background at `position` (0 top, 1 bottom) over a grey `photo`.

        ⚠️ Both layers are black, so they **multiply**: each one keeps `1 - alpha`
           of what is under it. Adding the alphas instead would overstate the
           darkening badly at the bottom (0.40 + 0.75 is not 0.85 of black).
        """
        full, lower = self.scrim()

        def along(stops, t):
            # Evenly spaced stops, linear between them — what the browser does for
            # `from-* via-* to-*` with no explicit positions.
            span = 1 / (len(stops) - 1)
            index = min(int(t / span), len(stops) - 2)
            local = (t - index * span) / span
            return stops[index] + (stops[index + 1] - stops[index]) * local

        alpha_full = along(full, position)
        # The second layer covers the bottom two thirds and runs bottom-to-top,
        # so its stop list is reversed relative to screen order.
        if position < 1 / 3:
            alpha_lower = 0.0
        else:
            alpha_lower = along(list(reversed(lower)), (position - 1 / 3) / (2 / 3))

        value = photo * (1 - alpha_full) * (1 - alpha_lower)
        return "#" + f"{max(0, min(255, round(value * 255))):02x}" * 3

    def card_alpha(self):
        """How much black the card lays over that."""
        found = re.search(
            r"background-color:\s*rgb\(0 0 0 / ([\d.]+)\)",
            self.declarations(".dark.has-hero .card:not(.border)"))
        self.assertIsNotNone(found, "the glass no longer sets a translucent black")
        return float(found.group(1))

    def declarations(self, selector):
        """One rule's declarations, **with its comments stripped**.

        🔴 The stripping is not tidiness. `test_the_blur_is_not_what_keeps_the_text
           _readable` asserts the glass rule contains `background-color`, and the
           rule's own comment contains the words "background-color" — so deleting
           the actual declaration left that guard green. Found by breaking it on
           purpose. Every one of these greps has to read the CSS, not the prose
           about the CSS.
        """
        css = re.sub(r"/\*.*?\*/", "",
                     (Path(settings.BASE_DIR) / "assets" / "app.css").read_text(),
                     flags=re.S)
        block = re.search(re.escape(selector) + r" \{(.*?)\n  \}", css, re.S)
        self.assertIsNotNone(block, f"`{selector}` is gone from app.css")
        return block.group(1)

    def worst_point(self, photo=1.0):
        """(position, outside ratio, inside ratio) where the scrim is thinnest."""
        palette = _theme_colors()
        readings = []
        for step in range(101):
            position = step / 100
            page = self.background_under(photo, position)
            surface = _composite("#000000", self.card_alpha(), page)
            readings.append((
                position,
                contrast_ratio(palette[self.OUTSIDE_CARD_TEXT], page),
                contrast_ratio(palette[self.INSIDE_CARD_TEXT], surface),
            ))
        return min(readings, key=lambda one: one[1])

    def test_the_accepted_worst_case_does_not_get_worse(self):
        """⚠️ Not an AA check — **both of these numbers fail AA**, by decision.

        The point is that they were 1.73:1 and 4.11:1 when that decision was made,
        and anything that lowers them further is a change nobody chose. A thinner
        scrim, a more transparent card, or a lighter body-text colour all show up
        here.
        """
        position, outside, inside = self.worst_point()
        self.assertGreaterEqual(
            outside, self.ACCEPTED_WORST_OUTSIDE - 0.01,
            f"At {position:.0%} down, a white photograph now leaves text that is "
            f"not on a card at {outside:.2f}:1, below the {self.ACCEPTED_WORST_OUTSIDE}:1 "
            f"that was accepted. Nothing about the scrim was supposed to get "
            f"weaker — see _hero_scrim.html.")
        self.assertGreaterEqual(
            inside, self.ACCEPTED_WORST_INSIDE - 0.01,
            f"Body text on a card is down to {inside:.2f}:1 under a white "
            f"photograph, below the {self.ACCEPTED_WORST_INSIDE}:1 on record. The card's "
            f"own black is the second half of this — raise it rather than the scrim "
            f"if the cards are what changed.")

    def test_the_gap_is_recorded_where_somebody_will_look(self):
        """⚠️ A guard that only lives in a test file protects the code and nobody's
        expectations. The person who swaps the front-page photo will not run this
        suite; they will read the known gaps.
        """
        for path, needle in [
            (Path("docs") / "planning" / "phase-c.md", "hero"),
            (Path("core") / "templates" / "core" / "components" / "_hero_scrim.html",
             "4.02"),
        ]:
            with self.subTest(path=str(path)):
                text = (Path(settings.BASE_DIR) / path).read_text()
                self.assertIn(needle.lower(), text.lower())

    # Every template directory an {% include %} in this app could resolve against.
    _TEMPLATE_ROOTS = ("core/templates", "events/templates", "gallery/templates",
                       "accounts/templates", "org/templates", "contact/templates")

    def _reaches_scrim(self, path, seen=None):
        """Does this template reach _hero_scrim.html, directly or through includes?

        ⚠️ Follows the chain rather than looking for the literal string. The
        backdrop was pulled out into its own fragment on 2026-08-09, which put
        one {% include %} between base.html and the scrim — and a guard that
        only greps the top file reads that refactor as "the scrim was removed".
        """
        seen = seen if seen is not None else set()
        if path in seen:
            return False
        seen.add(path)
        full = Path(settings.BASE_DIR) / path
        if not full.exists():
            return False
        markup = full.read_text()
        if "core/components/_hero_scrim.html" in markup:
            return True
        for name in re.findall(r"""\{%\s*include\s+["']([^"']+)["']""", markup):
            for root in self._TEMPLATE_ROOTS:
                if self._reaches_scrim(Path(root) / name, seen):
                    return True
        return False

    def test_the_scrim_is_shared_and_not_copied(self):
        """⚠️ The requirement was "the same intensity as the home page". Two copies
        of a gradient are the one way to guarantee that stops being true.

        ⚠️ `wall.html` is in this list as of 2026-08-09. It was the one page that
        actually held a second copy — and it said so in its own comment ("两处要是
        分叉了…") while this guard, which exists to catch exactly that, never
        looked at it. A guard that omits the known offender is decoration.
        """
        for path in [Path("core") / "templates" / "core" / "home.html",
                     Path("core") / "templates" / "core" / "base.html",
                     Path("gallery") / "templates" / "gallery" / "wall.html"]:
            with self.subTest(path=str(path)):
                markup = (Path(settings.BASE_DIR) / path).read_text()
                self.assertTrue(self._reaches_scrim(path),
                                "this page does not use the shared scrim")
                body = re.sub(r"\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}",
                              "", markup, flags=re.S)
                self.assertNotIn(
                    "bg-gradient-to-b from-black", body,
                    "this page hand-writes the scrim gradients instead of "
                    "including the component, so the two will drift apart")

    def test_the_dark_backdrop_is_shared_and_not_copied(self):
        """The layer *under* the scrim — the fixed, darkened photo itself.

        ⚠️ Same failure as the scrim and a real one: base.html and wall.html held
        it verbatim twice, so "内页和 Memories 的深色底不一样" was one edit away
        at all times, with both copies looking correct on their own.
        """
        for path in [Path("core") / "templates" / "core" / "base.html",
                     Path("gallery") / "templates" / "gallery" / "wall.html"]:
            with self.subTest(path=str(path)):
                markup = (Path(settings.BASE_DIR) / path).read_text()
                self.assertIn("core/components/_hero_backdrop.html", markup,
                              "this page does not use the shared dark backdrop")
                body = re.sub(r"\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}",
                              "", markup, flags=re.S)
                self.assertNotIn(
                    "site_hero_image.url", body,
                    "this page hand-writes the backdrop instead of including "
                    "the fragment, so the two will drift apart")

    def test_nothing_darkens_the_photo_with_a_filter_any_more(self):
        # ⚠️ Reintroducing one would double up with the scrim: the page would go
        #    very dark and the fix would look like "the scrim is too strong".
        css = re.sub(r"/\*.*?\*/", "",
                     (Path(settings.BASE_DIR) / "assets" / "app.css").read_text(),
                     flags=re.S)
        self.assertNotIn("site-hero-photo", css,
                         "the photo is being filtered in CSS again; the darkening "
                         "belongs to _hero_scrim.html and only there")

    def test_the_blur_is_not_what_keeps_the_text_readable(self):
        """⚠️ `backdrop-filter` is ignored outright by browsers that do not
        support it, so the contrast may not depend on it. Darkening is the solid
        translucent black; the blur is only there to look like glass.
        """
        self.assertIn(
            "background-color",
            self.declarations(".dark.has-hero .card:not(.border)"),
            "the glass leans on backdrop-filter alone for its darkening")

    def hairline(self):
        """The `::after` that draws the 1px lit edge."""
        return self.declarations(".dark.has-hero .card:not(.border)::after")

    def test_the_hairline_does_not_change_the_box(self):
        """⚠️ An absolutely-positioned overlay, never a `border`. The event rows
        have a fixed height, so a 1px border would shrink the content box by 2px
        and could clip the text — and the box would differ between light and dark
        by a pixel.
        """
        self.assertIn("position: absolute", self.hairline())
        for selector in [".dark.has-hero .card:not(.border)",
                         ".dark.has-hero .card:not(.border)::after"]:
            self.assertNotRegex(self.declarations(selector), r"\n\s*border:")

    def test_the_hairline_does_not_nest_a_backdrop_filter_inside_the_glass(self):
        """🔴 The edge was first written as `backdrop-filter: brightness(2.4)`, so
        that it would sample the photo and be bright where the photo was bright.
        **It cannot work in this position, and it failed silently.**

        The card has its own `backdrop-filter`, which makes the card a *backdrop
        root*. The edge sits inside that root, so its backdrop is **empty** —
        `brightness()` multiplied nothing, and the edge came out **darker** than
        the card. Measured, on the same gradient and the same ring:

            card with backdrop-filter    → edge 12 darker (dim end), 46 (bright end)
            card without backdrop-filter → edge 44 brighter,          138

        ⚠️ This is the second time a `backdrop-filter` has been defeated by an
           ancestor rather than by its own declaration (the first was an `opacity`
           keyframe — see EventRowEntranceTests). Both were invisible in the
           stylesheet and both needed a screenshot to find. That is the reason
           this guard is written as a **ban** rather than as a check that it works:
           there is no way to tell from the CSS alone that it does.

        ⚠️ And the multiplication was the wrong material anyway: dark photo × 2.4
           is still dark, so the edge would vanish exactly where it was already
           invisible — which is the complaint that started this. A translucent
           white still tracks the backdrop in absolute brightness (81 over the
           dim parts of the photo, 145 over the bright ones) and stays visible on
           both.
        """
        block = self.hairline()
        self.assertNotIn(
            "backdrop-filter", block,
            "the card's lit edge is inside the card's own backdrop root, so a "
            "backdrop-filter there samples nothing and comes out darker, not "
            "brighter. Use a translucent white instead.")
        self.assertRegex(
            block, r"rgb\(255 255 255 / [\d.]+\)",
            "the lit edge is no longer a translucent white, so it has nothing "
            "left that makes it follow the brightness of the photo behind it")

    def test_no_glass_rule_hardcodes_a_brand_colour(self):
        """🔴 This shipped. The glass primary button was written as
        `background-color: rgb(31 117 148 / 0.75)` — the literal value of
        `--color-brand-600` in this stylesheet.

        But the brand palette is **derived from the front-page photo** (D26) and
        written as `--color-brand-*` by `_appearance.html`. A literal takes the
        button out of the theme: with a photo that derives warm sand (#806856),
        every button in dark mode went back to the default teal (#1f7594) — and
        light mode stayed correct, so it was wrong in exactly one mode.

        ⚠️ Alpha over a custom property needs
           `color-mix(in srgb, var(--color-brand-N) X%, transparent)`, not a
           hand-expanded `rgb()`.
        """
        css = re.sub(r"/\*.*?\*/", "",
                     (Path(settings.BASE_DIR) / "assets" / "app.css").read_text(),
                     flags=re.S)
        palette = _theme_colors()
        brand = {name: value for name, value in palette.items()
                 if name.startswith("brand-")}
        glass = "\n".join(body for selector, body
                           in re.findall(r"([^{}]+?)\{([^{}]*?)\}", css, re.S)
                           if "has-hero" in selector)
        for name, hex_value in brand.items():
            channels = tuple(int(hex_value.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
            for literal in (hex_value, "rgb(%d %d %d" % channels,
                            "rgb(%d, %d, %d" % channels):
                with self.subTest(token=name, literal=literal):
                    self.assertNotIn(
                        literal, glass,
                        f"a dark-mode rule hardcodes {name} as `{literal}`, which "
                        f"takes it out of the palette derived from the photo. Use "
                        f"color-mix() over var(--color-{name}).")

    def test_the_primary_button_is_not_glass(self):
        """🔴 It was, and at **2.71:1**.

        The contrast I checked before shipping it was *white* text on that glass
        (6.51:1). This button does not use white text in dark mode — button.html
        sets `dark:text-ink-950`, near-black, because the solid dark-mode fill is
        the lighter `brand-500`. I measured the colour a primary button ought to
        have rather than the colour this one has.

        ⚠️ Changing it to white text would also have worked (6.75:1). It is solid
           instead because a primary button is the one thing on the screen telling
           somebody what to do, and it should not have a readability margin worth
           discussing.
        """
        css = re.sub(r"/\*.*?\*/", "",
                     (Path(settings.BASE_DIR) / "assets" / "app.css").read_text(),
                     flags=re.S)
        for selector, body in re.findall(r"([^{}]+?)\{([^{}]*?)\}", css, re.S):
            if "has-hero" not in selector or "btn-primary" not in selector:
                continue
            with self.subTest(selector=selector.strip()):
                self.assertNotIn(
                    "backdrop-filter", body,
                    "the primary button is glass again. Its dark-mode text is "
                    "ink-950, so a translucent fill puts it near 2.7:1.")

    def test_alert_boxes_are_not_glass(self):
        # A notice that shows the photograph through it has been demoted to
        # decoration. They are excluded by the `border` marker the alert boxes
        # already carry — see BorderlessCardTests.
        # ⚠️ Via declarations(), so the comments are gone: this file *talks* about
        #    `:not(.border)` in several places, and a grep over the raw text would
        #    be satisfied by the prose long after the rule itself had changed.
        self.declarations(".dark.has-hero .card:not(.border)")
        self.declarations(".dark.has-hero .card:not(.border)::after")


class CardShadowIsNotClippedTests(TestCase):
    """Nothing above a card may clip, because the shadow is outside the card.

    🔴 The bug: `.event-row` had `overflow: hidden` to keep content inside the fixed
       row height. Measured geometry says why that was wrong —

           row   864 x 112 at x=528  → right edge 1392
           card  756 x 112 at x=636  → right edge 1392

       the card's top, bottom and right edges **coincide with the row's**. So the
       row clipped the card's drop shadow on three sides, leaving it only in the
       gap on the left. Worse at the corners: the clip line cuts across the corner
       curve, so the shadow that falls outside the rounded corner but inside the
       row box survives as a **dark wedge** on each corner. Measured below the
       card: 248/248/248 (the page colour — no shadow at all) before, 201/212/222
       after.

    ⚠️ An element's own `overflow` does not clip its own `box-shadow`; only an
       **ancestor's** does. So the fix is to move the clip down onto the card,
       which keeps the content clipped and lets the shadow paint.

    ⭐ Third time this shape has cost something: **which element a declaration sits
       on decides what it breaks, and reading the declaration never shows it.** The
       other two were `backdrop-filter` (killed by an ancestor's opacity animation,
       then by the card being its own backdrop root).

    ⚠️ Past Events used to be the same rule with the opposite placement — there
       the `<li>` **was** the card, so the clip belonged on it. That page was
       deleted on 2026-08-17; only the one placement is checked below now.
    """

    def rules(self):
        css = re.sub(r"/\*.*?\*/", "",
                     (Path(settings.BASE_DIR) / "assets" / "app.css").read_text(),
                     flags=re.S)
        return re.findall(r"([^{}]+?)\{([^{}]*?)\}", css, re.S)

    def declarations_for(self, selector):
        return "\n".join(body for sel, body in self.rules()
                          if selector in {one.strip() for one in sel.split(",")})

    def test_the_row_does_not_clip_the_card_it_contains(self):
        self.assertNotRegex(
            self.declarations_for(".event-row"), r"overflow:\s*hidden",
            "`.event-row` is the card's ancestor and its box coincides with the "
            "card's on three sides, so clipping here cuts the card's shadow off "
            "and leaves a dark wedge on every corner. Put the clip on "
            "`.event-row > .card` instead.")

    def test_something_still_clips_the_fixed_height(self):
        # ⚠️ The pair matters: dropping the clip entirely would let a long title
        #    spill out of the fixed row height, which is the thing the row height
        #    exists to prevent.
        # ⚠️ One selector since 2026-08-17. `.event-row-past` was the other, and
        #    it is gone with the page it dressed — not merely unused: a rule
        #    nothing can match is a signpost to a page that no longer exists.
        selector = ".event-row > .card"
        self.assertRegex(
            self.declarations_for(selector), r"overflow:\s*hidden",
            f"nothing clips `{selector}` any more, so content longer than "
            f"the fixed row height will spill out of it")


class EventThumbnailIsStructurallySquareTests(TestCase):
    """The thumbnail's width and height must come from **one** variable.

    🔴 This geometry has been wrong three separate times, always the same way: the
       image stopped being square, and nobody noticed by looking — it took
       measuring in a browser. Once the width came from a Tailwind utility that
       silently beat the stylesheet (64×112). Once it was pinned to the card's
       natural height, which had two values. Once it was pinned to the other one.

    🔴 And then it happened a **fourth** time, in the change that shrank the
       thumbnail: `width` and `height` were both rewritten as the literal
       `5.25rem`. That looks fine and is square today, but "two numbers that
       happen to match" is precisely the state the variable existed to leave
       behind — and this test did not exist to catch it.

    ⚠️ So the guard is not "is it square". It is **"is squareness structural"** —
       one custom property feeding both sides, which cannot diverge.
    """

    def declarations(self, selector):
        """Every declaration for `selector`, from **all** of its rules.

        ⚠️ `.event-row-thumb` has more than one rule (the radius and the overflow
           live apart from the sizes, and the sizes are inside a media query), so
           a `re.search` for the first block reads a rule that never mentions
           width. The stylesheet already records this exact mistake happening to
           `.event-row`; making it again one file over is why this reads all of
           them and concatenates.
        ⚠️ Comments stripped first — a comment contains no braces, so a `[^{}]`
           selector pattern otherwise swallows the whole comment above the rule.
        """
        css = re.sub(r"/\*.*?\*/", "",
                     (Path(settings.BASE_DIR) / "assets" / "app.css").read_text(),
                     flags=re.S)
        blocks = [body for sel, body in re.findall(r"([^{}]+?)\{([^{}]*?)\}", css, re.S)
                  if selector in {one.strip() for one in sel.split(",")}]
        self.assertTrue(blocks, f"`{selector}` is gone from app.css")
        return "\n".join(blocks)

    def test_both_sides_read_the_same_variable(self):
        block = self.declarations(".event-row-thumb")
        sides = dict(re.findall(r"(width|height):\s*([^;]+);", block))
        self.assertEqual(set(sides), {"width", "height"},
                         "the thumbnail no longer sets both of its sides here")
        variables = {re.search(r"var\((--[\w-]+)\)", value) for value in sides.values()}
        self.assertNotIn(
            None, variables,
            f"The thumbnail's sides are written as literals ({sides}), so nothing "
            f"stops them from drifting apart. Square has to come from one "
            f"variable used twice — that is what this rule's comment promises.")
        self.assertEqual(
            len({found.group(1) for found in variables}), 1,
            f"width and height read two *different* variables ({sides}), which is "
            f"the same failure as two literals with an extra step")

    def test_the_template_sets_no_size_of_its_own(self):
        # ⚠️ Tailwind's utilities layer beats the components layer, so a stray
        #    `w-16` in the template silently wins over this stylesheet. That is
        #    exactly how the 64×112 version happened.
        markup = (Path(settings.BASE_DIR) / "events" / "templates" / "events"
                  / "_event_list_results.html").read_text()
        thumb = re.search(r'class="([^"]*event-row-thumb[^"]*)"', markup)
        self.assertIsNotNone(thumb, "the thumbnail lost its `event-row-thumb` hook")
        for utility in re.findall(r"(?:^|\s)((?:sm:)?[wh]-\S+)", thumb.group(1)):
            self.fail(
                f"`{utility}` sizes the thumbnail from the template. Tailwind's "
                f"utilities layer outranks the components layer, so this wins over "
                f"app.css and the image stops being square — move it into the "
                f"stylesheet with the rest of the geometry.")


class ScheduleShellTests(TestCase):
    """The room the schedule opens into (2026-08-17).

    Two facts about the stylesheet, both of which are invisible on the machine
    they were written on and expensive on somebody else's.
    """

    def declarations(self, selector):
        css = re.sub(r"/\*.*?\*/", "",
                     (Path(settings.BASE_DIR) / "assets" / "app.css").read_text(),
                     flags=re.S)
        blocks = [body for sel, body in re.findall(r"([^{}]+?)\{([^{}]*?)\}", css, re.S)
                  if selector in {one.strip() for one in sel.split(",")}]
        self.assertTrue(blocks, f"`{selector}` is gone from app.css")
        return "\n".join(blocks)

    def test_the_closed_shell_is_exactly_the_page_it_replaced(self):
        """⚠️ Everybody who has not pressed the button sees this state, so it has
           to be `width: 100%` and nothing else — no width of its own, no
           margin. Anything else silently redesigns the default view of the
           busiest page in the app in exchange for a feature nobody asked to
           have on.
        """
        block = self.declarations(".events-shell")
        self.assertRegex(block, r"width:\s*100%")
        self.assertNotRegex(
            block, r"margin-left:\s*-",
            "the pull belongs in the `.is-open` rules, not on the resting state")

    def test_the_open_widths_never_come_from_vw(self):
        """🔴 `100vw` includes the vertical scrollbar; a media query does not.

        So `min(78rem, calc(100vw - 2rem))` overflows by the width of the
        scrollbar on the viewports where the clamp is doing anything at all —
        and the symptom is the whole page sliding sideways, which looks like a
        broken card rather than like a width. This file already records the same
        bug happening once, on the filter row.

        The widths therefore live inside media queries, which have the room
        already, and are written as plain rem.
        """
        css = re.sub(r"/\*.*?\*/", "",
                     (Path(settings.BASE_DIR) / "assets" / "app.css").read_text(),
                     flags=re.S)
        shell = "\n".join(
            block for block in re.findall(r"\.events-shell[^{]*\{[^}]*\}", css, re.S))
        self.assertNotIn("vw", shell,
                         "a viewport unit in the shell's geometry — see the "
                         "docstring, this is the scrollbar trap")

    def test_the_panel_is_one_screen_and_stays_put(self):
        """日程是一屏，钉在那儿 —— 不是一根跟着列表长的长条。

        ⚠️ 三条声明缺一不可，而少任何一条都**不报错**：
           `position: sticky` 少了 → 日历跟着卡片滚出屏幕；
           显式高度少了 → flex 项被拉成整行高，和包含块一样高，
                          于是 sticky 没有可粘的行程，看起来像没写；
           `align-self: flex-start` 少了 → 同上，stretch 会盖掉高度。
        """
        block = self.declarations(".schedule-panel")
        self.assertRegex(block, r"position:\s*sticky")
        self.assertRegex(block, r"align-self:\s*flex-start")
        self.assertRegex(block, r"height:\s*calc\(100svh")

    def test_the_panel_clears_the_sticky_top_bar(self):
        # `top: 0` would slide the panel's first rows under the white bar —
        # on a calendar that is exactly the row naming the days.
        block = self.declarations(".schedule-panel")
        self.assertNotRegex(block, r"top:\s*0")
        self.assertRegex(block, r"top:\s*calc\(var\(--top-bar-h\)")

    def test_the_top_bar_is_still_the_height_this_assumes(self):
        """🔴 `--top-bar-h` is a **measured** number, and the first version of it
        was a derived one that was wrong.

        Derived from "the bar is `text-sm`, so 20px of text plus its padding" it
        came out at 52 / 60px. The browser says 68 / 76: the tallest thing in
        that row is not the text at all, it is the **theme toggle** — `p-2`
        around an `h-5 w-5` icon, 8+20+8 = 36px. Plus the nav's `py-4` /
        `sm:py-5`: 36+32 and 36+40.

        So this pins the three utilities the number actually comes from. Pin
        only the padding and you are guarding a derivation that does not hold —
        which is exactly how it was wrong the first time. When it is wrong
        nothing errors; the calendar just has a strip cut off its top, behind a
        white bar that looks like it is supposed to be there.
        """
        markup = (Path(settings.BASE_DIR) / "core" / "templates" / "core"
                  / "components" / "_top_bar.html").read_text()
        nav = re.search(r"<nav[^>]*>", markup).group(0)
        self.assertIn("py-4", nav)
        self.assertIn("sm:py-5", nav)

        # The theme toggle: the tallest child, and therefore the real owner of
        # the bar's height.
        toggle = re.search(r'<button[^>]*x-data="themeToggle"[^>]*>', markup)
        self.assertIsNotNone(toggle, "the theme toggle moved; re-measure the bar")
        self.assertIn("p-2", toggle.group(0))
        icon = re.search(r'<svg class="h-5 w-5[^"]*"', markup)
        self.assertIsNotNone(icon, "the toggle's icon is no longer h-5; re-measure")

        css = re.sub(r"/\*.*?\*/", "",
                     (Path(settings.BASE_DIR) / "assets" / "app.css").read_text(),
                     flags=re.S)
        self.assertIn("--top-bar-h: 4.25rem", css)   # 36 + 2 × 16
        self.assertIn("--top-bar-h: 4.75rem", css)   # 36 + 2 × 20

        # ⚠️ 2026-08-18 起有**两处**声明它：日程面板，和日程开着时钉住的筛选卡。
        #    两块并排的东西钉在不同的高度上是一眼看得出来的错位，而各改一处
        #    不会报错。所以这里钉的是「所有声明只有这两个值」。
        declared = set(re.findall(r"--top-bar-h:\s*([\d.]+rem)", css))
        self.assertEqual(declared, {"4.25rem", "4.75rem"},
                         "有人给 --top-bar-h 加了第三个值，钉在顶上的两块会错开")

    def test_the_panel_is_hidden_rather_than_merely_narrow(self):
        # A zero-width panel is still in the accessibility tree and still in the
        # tab order — the calendar's buttons would be focusable and invisible.
        self.assertRegex(self.declarations(".schedule-panel"),
                         r"visibility:\s*hidden")

    def test_the_column_can_actually_shrink(self):
        # flex items default to `min-width: auto` ("never narrower than your
        # content"), and this column holds fixed-width thumbnails and a row of
        # buttons that does not wrap. Without this, opening the schedule
        # squeezes the panel instead of the list.
        self.assertRegex(self.declarations(".events-col"), r"min-width:\s*0")

    def test_reduced_motion_keeps_the_layout_and_drops_only_the_travel(self):
        """⚠️ Not `display: none` on the panel, not a disabled button: making
           room is layout, and somebody who has asked for less motion still
           wants the schedule. Only the 560ms of travel goes.
        """
        css = (Path(settings.BASE_DIR) / "assets" / "app.css").read_text()
        block = re.search(
            r"@media \(prefers-reduced-motion: reduce\) \{\s*\.events-shell.*?\n  \}",
            css, re.S)
        self.assertIsNotNone(block, "the shell has no reduced-motion rule")
        self.assertIn("transition: none", block.group(0))


class EventRowEntranceTests(TestCase):
    """Cards come in one after another, like the menu items (2026-08-06).

    🔴 The reason this class exists is one line in the stylesheet: the entrance
       must animate `translate`, **never** `transform`. These same `<li>` elements
       carry `scroll-breathe`, and the scroll-inertia JS writes
       `style.transform` on them every frame. Two things writing one property is
       the pattern app.css already convicted once — "whoever writes last wins, and
       that depends on frame timing". `translate` is a separate property that
       *composes* with `transform`, so the two coexist instead of fighting.

       Measured rather than assumed: with the entrance at its start (translate
       12px) and an inline `transform` of 7px applied at the same time, the row
       ends up displaced by both, and finishing the entrance moves it exactly
       12px while the 7px stays.
    """

    def stylesheet(self):
        return (Path(settings.BASE_DIR) / "assets" / "app.css").read_text()

    def keyframes(self):
        return re.search(r"@keyframes event-row-in \{(.*?)\n  \}",
                         self.stylesheet(), re.S).group(1)

    def rules(self):
        """(selector, body) for every rule, comments removed.

        ⚠️ Comments **must** come out first. A CSS comment contains no braces, so
           a `[^{}]*` selector pattern happily swallows the whole 30-line comment
           block above a rule — which is how the first version of this reported a
           paragraph of Chinese prose as a selector.
        """
        css = re.sub(r"/\*.*?\*/", "", self.stylesheet(), flags=re.S)
        return re.findall(r"([^{}]+?)\{([^{}]*?)\}", css, re.S)

    def entrance_rules(self):
        """Every rule that applies the entrance, as (selector, body).

        ⚠️ Matched by "the body mentions `event-row-in`" rather than by a
           hard-coded selector. The selector has already moved once (off the row
           and onto its children, see the test below) and a guard keyed to the old
           one goes quietly vacuous instead of failing.
        """
        found = [(selector, body) for selector, body in self.rules()
                 if "event-row-in" in body and "animation" in body]
        self.assertTrue(found, "nothing applies the entrance animation any more")
        return found

    def entrance_selectors(self):
        return {one.strip()
                for selector, _ in self.entrance_rules()
                for one in selector.split(",") if one.strip()}

    def entrance_blocks(self):
        return [body for _, body in self.entrance_rules()]

    def row_template(self):
        """The one template that draws event rows.

        ⚠️ There were two until 2026-08-17 (Past Events had its own, where the
           `<li>` itself was the card). Both loops that used to walk the pair
           are single-file now, and the two tests below still exist for the same
           reason: they pin the *premises* the animation rules depend on.
        """
        return (Path(settings.BASE_DIR) / "events" / "templates" / "events"
                / "_event_list_results.html").read_text()

    def test_the_animation_is_not_on_an_ancestor_of_the_glass(self):
        """🔴 The bug this exists for: **the entrance silently switched the glass
        off on the whole Events page**, and neither rule looked wrong.

        `event-row-in` fades `opacity`. An element with an animated `opacity`
        becomes a *backdrop root*, which permanently disables `backdrop-filter` on
        everything inside it — not just during the animation. So while the
        animation sat on the `<li class="event-row">`, the `.card` inside it had a
        `backdrop-filter` that never did anything. Proved by rendering with
        `backdrop-filter: none` forced: **0 pixels changed**. After moving the
        animation onto the card itself: 27,294 pixels changed.

        ⚠️ The card animating its *own* opacity is fine — `backdrop-filter`
           applies before the element's own opacity. It is only *ancestors* that
           destroy it. That distinction is the whole rule, and it is the reason
           the fix was "move it down one level" rather than "drop the fade".

        ⚠️ Past Events used to be the counter-example here — there the `<li>`
           **was** the card, so the animation sat on the glass itself and that
           page never broke. The page is gone (2026-08-17); the observation is
           kept because it is what proved the diagnosis.
        """
        for selector in self.entrance_selectors():
            with self.subTest(selector=selector):
                self.assertNotEqual(
                    selector, ".event-row",
                    "The entrance fades opacity, and an animated opacity on the row "
                    "makes it a backdrop root — which kills the `backdrop-filter` on "
                    "the card inside it for good. Put the animation on "
                    "`.event-row > .card` and `.event-row > .event-row-thumb`.")

    def test_the_entrance_never_touches_transform(self):
        properties = set(re.findall(r"^\s*([a-z-]+):", self.keyframes(), re.M))
        self.assertNotIn(
            "transform", properties,
            "The scroll-inertia JS writes `transform` on these same rows. Use "
            "`translate`, which composes with it instead of overwriting it.")
        self.assertEqual(properties, {"opacity", "translate"})

    def test_the_rows_still_carry_the_scroll_inertia_class(self):
        # The reason the rule above exists. If `scroll-breathe` ever leaves these
        # rows, `transform` becomes free again and this pair should be revisited
        # rather than worked around.
        row = re.search(r'<li class="event-row[^"]*"', self.row_template()).group(0)
        self.assertIn("scroll-breathe", row)

    def test_every_row_is_numbered(self):
        # Without `--i` the whole list shares one delay and arrives as a block.
        self.assertIn("--i: {{ forloop.counter0 }}", self.row_template())

    def test_the_delay_is_capped(self):
        """⚠️ A page holds 20 cards. Uncapped, the last waits 1.05s — and every
        card past the tenth is off screen anyway, so the only thing the extra
        delay buys is a screenful of still-fading blanks for somebody who scrolls
        straight down.
        """
        self.assertTrue(
            any(re.search(r"animation-delay:\s*calc\(min\(", b)
                for b in self.entrance_blocks()),
            "the entrance delay is not capped — a 20-card page would end with a "
            "1.05s wait on cards that are off screen anyway")

    def test_reduced_motion_clears_the_delay_and_not_just_the_duration(self):
        """⭐ The subtle one. The global reduced-motion reset in this file caps
        `animation-duration`, but **not** `animation-delay` — so on its own it
        would leave a card invisible for up to half a second and then flash it
        into place. That is a worse experience than the animation it was meant to
        remove. `animation: none` is the shorthand, so it resets the delay too.
        """
        # ⚠️ Keyed to "a reduced-motion block that names the same elements the
        #    entrance is applied to", not to a literal selector — the selector has
        #    moved once already.
        css = re.sub(r"/\*.*?\*/", "", self.stylesheet(), flags=re.S)
        reset = set()
        for selector, body in re.findall(
                r"@media \(prefers-reduced-motion: reduce\) \{\s*([^{}]+?)\{([^{}]*?)\}",
                css, re.S):
            # ⚠️ Only the rows. There are other reduced-motion resets in this file
            #    and they legitimately reset `transform` instead of `translate` —
            #    the row pair is the one that has to use `translate`, because
            #    `scroll-breathe` owns `transform` on these elements.
            if "event-row" not in selector or "animation: none" not in body:
                continue
            self.assertIn("opacity: 1", body)
            self.assertIn("translate: none", body)
            reset.update(one.strip() for one in selector.split(",") if one.strip())
        self.assertTrue(
            reset,
            "the reduced-motion fallback caps the duration but never resets the "
            "delay — a card would stay invisible for up to half a second and then "
            "flash into place, which is worse than the animation it removes")
        # ⚠️ **Every** selector the entrance is applied to has to be in that reset.
        #    Missing one leaves that one element blank-then-flashing while the rest
        #    of the row is already in place — and the thumbnail was very nearly
        #    exactly that, because it only became a separate animated element when
        #    the animation moved off the row.
        self.assertLessEqual(self.entrance_selectors(), reset)


class NativeControlsFollowTheThemeTests(TestCase):
    """`color-scheme`, which is the only handle we have on the OS's own popups.

    ⚠️ A `<select>`'s open list is drawn by the operating system, not the page —
       CSS cannot reach its background, its highlight or its corners, and
       `<option>` colours are ignored outright on macOS. What `color-scheme` does
       is tell the browser which way the page is painted, so the OS draws that
       popup (and the date picker, and the scrollbars) to match instead of
       defaulting to light on a dark page.

    ⚠️ Keyed off `.dark`, not `prefers-color-scheme`: the theme here is manually
       overridable, and this has to agree with what the page **actually** looks
       like rather than with the system preference.
    """

    def stylesheet(self):
        return (Path(settings.BASE_DIR) / "assets" / "app.css").read_text()

    def test_both_directions_are_declared(self):
        css = self.stylesheet()
        self.assertRegex(css, r"html \{[^}]*color-scheme:\s*light")
        self.assertRegex(css, r"html\.dark \{[^}]*color-scheme:\s*dark")

    def test_it_does_not_follow_the_system_preference_directly(self):
        # A `@media (prefers-color-scheme: dark) { color-scheme: dark }` would
        # disagree with the page whenever somebody used the theme toggle.
        css = self.stylesheet()
        for block in re.findall(r"@media \(prefers-color-scheme[^{]*\{(.*?)\n  \}", css, re.S):
            self.assertNotIn("color-scheme", block)


class ButtonCursorTests(TestCase):
    """按钮 hover 上去要变成手（2026-08-19）。

    🔴 起因是用户报的「Log out、Menu、太阳月亮，鼠标碰上去没变成 click」，而真正
       的范围比那三个大得多：**Tailwind v4 的 preflight 不再给 `<button>` 设
       `cursor: pointer`**（v3 设过），浏览器对 `<button>` 的默认值是箭头。于是
       全站每一颗 `<button>` 都是箭头 —— 密码框的「看一眼」、所有 Save、报名、
       翻页。`<a href>` 天生是手型，所以这件事看起来只发生在几个地方。

    ⚠️ 所以修法是一条 base 层规则，不是给那三个各加一个工具类。挨个加的下场是
       漏掉的那一颗谁也不会发现 —— 这个 bug 本身就是这么活下来的。
    """

    def stylesheet(self):
        return (Path(settings.BASE_DIR) / "assets" / "app.css").read_text()

    def built(self):
        path = Path(settings.BASE_DIR) / "static" / "css" / "app.css"
        if not path.exists():
            self.skipTest("static/css/app.css is a build product; run npm run build")
        return path.read_text()

    def test_the_rule_covers_buttons_and_things_dressed_as_buttons(self):
        rule = re.search(r"\n  button,\n(.*?)\{\s*\n?\s*cursor: pointer;",
                         self.stylesheet(), re.S)
        self.assertIsNotNone(rule, "assets/app.css 里没有那条 cursor: pointer 的 base 规则")
        # `[role="button"]` 是一个**看起来是按钮的 div**：它连 <button> 的默认值
        # 都没有，所以必须一起管。
        self.assertIn('[role="button"]', rule.group(1))

    def test_a_disabled_button_says_not_allowed_instead(self):
        """手型的意思是「点我」，而停用的控件恰恰点不了。

        ⚠️ 断言的是**这条规则盖住了哪几种停用**，不是选择器列表的字面文本
           （2026-08-27 改）。原来的正则把 `button:disabled` 和
           `[aria-disabled="true"]` 之间的逗号也钉死了，于是往列表里**加**一条
           选择器 —— 也就是把这条规则变强 —— 会让守卫变红，而下一个人最省事的
           反应是回头去加一条组件局部规则，正是本类 docstring 反对的那件事。

        ⚠️ 四种都要在：真按钮、真输入框（受众那一组勾选框会被禁用）、
           包着停用输入框的 `label`（上面那条 `label[for]` 会把它变成手），
           以及画成停用的链接/div。
        """
        block = re.search(r"\n((?:\s*[^{};\n]+,\n)*[^{};\n]*)\{\s*cursor: not-allowed;",
                          self.stylesheet())
        self.assertIsNotNone(block, "assets/app.css 里没有那条 cursor: not-allowed 规则")
        selectors = block.group(1)
        for wanted in ("button:disabled", "input:disabled",
                       "label:has(input:disabled)", '[aria-disabled="true"]'):
            with self.subTest(selector=wanted):
                self.assertIn(wanted, selectors)

    def test_it_actually_survived_the_build(self):
        """⚠️ 页面读的是 static/css/app.css，不是 assets/app.css。

        源文件里写对了而没有重新 build，屏幕上一点变化都没有 —— 而两个文件
        单看都是对的。这条钉的就是那个空档。
        """
        self.assertRegex(self.built(), r"button,\[role=button\][^}]*cursor:pointer")
        self.assertRegex(self.built(), r"button:disabled[^}]*cursor:not-allowed")

    def test_the_three_controls_the_user_named_are_real_buttons(self):
        """那三个之所以中招，是因为它们是 `<button>` 而不是 `<a>` —— 钉住这一点。

        ⚠️ 如果哪天有人把 Log out 改成一个 `<a>` 去「修」手型，那是拿一个真正的
           bug 换一个显示问题：登出是写操作，必须是带 CSRF 的 POST 表单。
        """
        from accounts.services import register_account

        user = register_account(
            email="mei@example.com", password="a-good-long-password",
            legal_first_name="Ping", legal_last_name="Mei")
        self.client.force_login(user)
        html = self.client.get(reverse("events:event_list")).content.decode()
        self.assertRegex(html, r'<button type="submit"[^>]*>\s*Log out')
        self.assertRegex(html, r'<button type="button"[^>]*x-on:click="menu = true"')
        self.assertRegex(html, r'<button type="button" x-data="themeToggle"')


class BorderlessCardTests(TestCase):
    """Cards have no border line (2026-08-06). Two things had to move with that.

    ⚠️ The shadow **must** get stronger at the same time, and this is not taste: a
       white card on an `ink-50` page is nearly the same colour, so with the old
       `0 1px 2px / 5%` shadow and no line the cards would simply stop reading as
       objects — several blocks of content on one page merging into a wash.

    ⚠️ And the alert boxes were `.card` plus a `border-<colour>` utility, which
       sets colour only: their **width** came from `.card`. Removing it took their
       outline away too, so each of them now carries an explicit `border`. That is
       the regression this class is really guarding — a new alert box copied from
       the old pattern would come out with no outline at all, and nothing would say
       so.
    """

    def stylesheet(self):
        return (Path(settings.BASE_DIR) / "assets" / "app.css").read_text()

    def card_block(self):
        return re.search(r"\n  \.card \{(.*?)\n  \}", self.stylesheet(), re.S).group(1)

    def test_the_card_has_no_border(self):
        self.assertNotRegex(self.card_block(), r"\bborder(-width)?\s*:")

    def test_the_card_still_casts_a_shadow_in_light_mode(self):
        # Without the border, this is the only thing separating a white card from
        # a near-white page.
        self.assertIn("box-shadow", self.card_block())

    def test_the_table_container_keeps_its_border(self):
        # Decided explicitly, and the opposite of the cards: a table's row lines
        # need an outer frame to close them off.
        block = re.search(r"\.table-wrap \{(.*?)\n  \}", self.stylesheet(), re.S).group(1)
        self.assertIn("border:", block)

    def test_the_side_panel_swaps_the_shadow_for_a_hairline(self):
        """🔴 「卡片不画边框、靠落影读成一个物件」那条全站规矩，**在右面板里不成立**。

        面板里那一层（`.schedule-detail`）是 `overflow-y: auto`，而按 CSS 规范，
        一轴不是 `visible` 时另一轴的**使用值**也会变成裁剪 —— 横向同样裁。而卡片
        正好和它一样宽（量出来两边都是 1343），于是落影往左右扩散的部分被齐齐
        切掉，只剩往下那一条：屏幕上是一个硬边的方块，不是落影。

        ⚠️ 这个错在「样式对不对」这一层完全看不出来 —— `box-shadow` 的计算值是对的。

        试过让卡片 `margin-inline` 缩一点把地方让出来，撤了：卡片因此比「Roles」
        那些标题和下面的表格窄一圈（量出来错开 8px），读起来是没对齐。所以换的是
        **材质**不是尺寸：这一档不投影，用一条细线，而那条线和 `.table-wrap` 的
        **同一根** —— 那张表就在同一个面板里、紧挨着这些卡片。

        ⚠️ 关影子必须写 `0 0 #0000`，不能写 `none`：这个变量会被代进一串逗号分隔的
           影子列表，`none` 在列表中间是语法错误，浏览器丢掉整条声明。
        """
        css = self.stylesheet()
        block = re.search(r"\.schedule-detail \.card \{(.*?)\n  \}", css, re.S)
        self.assertIsNotNone(block, "面板里那条卡片规则不见了")
        body = block.group(1)
        self.assertIn("--card-elevation: 0 0 #0000", body,
                      "落影没关掉 —— 它会被裁成一条硬边")
        self.assertNotIn("none", body, "`none` 在影子列表里是语法错误")
        self.assertIn("border: 1px solid var(--panel-hairline)", body)
        self.assertNotIn("margin-inline", body,
                         "又靠缩卡片让位了 —— 那会让卡片和标题、表格错开")

        # 和 table-wrap 用同一根线：粗细和两个模式下的颜色都要对得上
        table = re.search(r"\.table-wrap \{(.*?)\n  \}", css, re.S).group(1)
        self.assertIn("1px solid var(--color-ink-200)", table)
        self.assertIn("--panel-hairline: var(--color-ink-200)", body)
        dark = re.search(
            r"\.dark \.schedule-detail \.card \{(.*?)\n  \}", css, re.S)
        self.assertIsNotNone(dark, "深色那一档的线色不见了")
        self.assertIn("--panel-hairline: var(--color-ink-700)", dark.group(1))

        # 深色 + 大图那一档的「暗晕」同样会被裁，也要关掉；而它的权重更高
        glow = re.search(
            r"\.dark\.has-hero \.schedule-detail \.card:not\(\.border\) \{(.*?)\n  \}",
            css, re.S)
        self.assertIsNotNone(glow, "深色+大图那一档没有跟着关掉暗晕")
        self.assertIn("--card-elevation: 0 0 #0000", glow.group(1))

    def test_every_coloured_alert_box_states_its_own_border_width(self):
        offenders = []
        for relative, source in project_template_files():
            for number, line in enumerate(_blank_out_comments(source).splitlines(), 1):
                for classes in re.findall(r'class="(card[^"]*)"', line):
                    coloured = re.search(r"\bborder-(warning|danger|info|success)", classes)
                    # ⚠️ Not `\sborder\s`: these class strings contain template tags,
                    #    so a bare `border` can sit directly after `%}` with no
                    #    space. The first version of this guard used whitespace as
                    #    the boundary and reported a box that was already correct.
                    bare = re.search(r"(?<![\w-])border(?![\w-])", classes)
                    if coloured and not bare:
                        offenders.append(f"{relative}:{number}: {classes[:70]}")
        self.assertEqual(
            offenders, [],
            "`border-<colour>` sets the colour only — the width used to come from "
            "`.card`, and cards no longer have one. Add a bare `border`, or this "
            "box has no outline at all:\n" + "\n".join(offenders))


class FilterSearchAlignmentTests(TestCase):
    """The search box lines up with the row of controls under it, structurally.

    ⚠️ It cannot be a fixed width. "Level with Clear" is a different number on
       each page — measured at 642px on Events and 790px on the management list,
       because that page has an extra `Generate report` button. A pixel value
       would be wrong on one of them, and wrong again the next time a button
       label changes.

       `width: fit-content` on a column wrapper sizes it to its widest child (the
       controls row), so the search stretches to exactly that. Nothing is written
       down, and it follows the buttons on its own.
    """

    def markup(self):
        return (Path(settings.BASE_DIR) / "events" / "templates" / "events"
                / "_period_filter.html").read_text()

    def test_the_wrapper_sizes_itself_to_the_controls_row(self):
        self.assertIn("w-fit", self.markup())

    def test_the_wrapper_cannot_push_the_page_sideways(self):
        # ⚠️ `fit-content` resolves to max-content, and the controls row unwrapped
        #    is wider than a phone. Without this cap the whole page scrolls
        #    sideways at 375px — the same bug this row caused once before.
        wrapper = re.search(r'class="flex w-fit ([^"]*)"', self.markup()).group(0)
        self.assertIn("max-w-full", wrapper)

    def test_the_search_box_does_not_span_the_whole_card(self):
        # `basis-full` was the first attempt and it reached the card's edge.
        markup = _blank_out_comments(self.markup())
        search_line = next(line for line in markup.splitlines() if "period.q" in line)
        self.assertNotIn("basis-full", search_line)
        self.assertNotIn("w-full", search_line)


class ReportPanelExitsTests(TestCase):
    """The panel's two exits sit in its title row, outside the scrolling area.

    ⚠️ The rule they obey has not changed since 2026-08-05: an exit you can only
       reach by scrolling to the bottom of a scrolling panel is not an exit. They
       moved from a bar under the panel to the title row — still outside the
       scroller, and now nearer where the eye lands.
    """

    def panel(self):
        return (Path(settings.BASE_DIR) / "events" / "templates" / "events"
                / "_report_panel.html").read_text()

    def test_the_exits_come_before_the_scrolling_area(self):
        markup = self.panel()
        exits = markup.index("View full report")
        scroller = markup.index("overflow-y-auto")
        self.assertLess(
            exits, scroller,
            "The exits are inside or after the scrolling area — an exit you have "
            "to scroll to find is not an exit.")

    def test_they_are_in_the_same_row_as_the_heading(self):
        markup = self.panel()
        heading = markup.index(">Report</h2>")
        exits = markup.index("View full report")
        between = markup[heading:exits]
        # Nothing but the wrapper div for the links may sit between them.
        self.assertNotIn("</div>", between.replace('<div class="flex shrink-0', ""))

    def test_the_labels_cannot_wrap_mid_phrase(self):
        # The panel is a fixed 24rem on wide screens. "View full report →"
        # breaking after "full" would read as two separate links.
        markup = self.panel()
        for label in ["View full report", "Save as PDF"]:
            with self.subTest(label=label):
                anchor = markup[markup.index("prose-link", markup.index(label) - 200):]
                self.assertIn("whitespace-nowrap", anchor[:40])


#: The three documents that carry a `<head>` of their own, and the URL that
#: serves each. Everything else on the site extends `base.html`.
#:
#: ⚠️ Memories and the event list are both behind a login, so tests over this
#:    list sign in. The front page is public and stays that way.
THREE_HEADS = [
    ("home", "home"),
    ("an inner page", "events:event_list"),
    ("Memories", "gallery:wall"),
]


class FaviconTests(TestCase):
    """There was no favicon at all until 2026-08-06 — the tab showed a globe."""

    def test_the_icons_exist_and_are_square(self):
        from PIL import Image

        root = Path(settings.BASE_DIR) / "core" / "static" / "core" / "img"
        expected = {"favicon-16.png": 16, "favicon-32.png": 32,
                    "favicon-48.png": 48, "apple-touch-icon.png": 180}
        for name, size in expected.items():
            with self.subTest(icon=name):
                path = root / name
                self.assertTrue(path.exists(), f"{name} is missing")
                self.assertEqual(Image.open(path).size, (size, size))

    def test_every_page_declares_them(self):
        """⚠️ **All three top-level templates**, not one of them.

        This test used to fetch a single inner page and carried the comment
        "Declared in base.html, so one assertion covers the whole site". That
        sentence was not true and the guard could not tell: there are three
        documents with a `<head>` of their own, and they had drifted —
        `base.html` declared four sizes, `wall.html` one, and **`home.html`
        none at all**, so the front page showed the browser's globe. Each file
        looked finished on its own. Fixed on 2026-08-13 by sharing `_head.html`;
        this is the guard that would have caught it.
        """
        # ⚠️ Signed in, because Memories is behind a login — and Memories is
        #    precisely one of the two pages that had drifted.
        self.client.force_login(get_user_model().objects.create_user(
            email="icons@example.com", password="a-good-long-password"))
        for label, url_name in THREE_HEADS:
            with self.subTest(page=label):
                response = self.client.get(reverse(url_name))
                self.assertEqual(response.status_code, 200)
                for name in ["favicon-16.png", "favicon-32.png",
                             "favicon-48.png", "apple-touch-icon.png"]:
                    self.assertContains(response, name)


class SharedHeadTests(TestCase):
    """The `<head>` every page has, written once (2026-08-13).

    ⚠️ Three documents carry one: `base.html` for the whole application,
       `home.html` because the front page is a full-screen picture rather than
       the app shell, and `wall.html` for the same reason. Three copies of the
       same six lines, and the copies had already diverged — see FaviconTests.

    ⚠️ The two **real** differences are parameters rather than duplication, and
       both are tested below: the front page does not follow dark mode (so no
       theme boot), and the three titles differ.
    """

    FRAGMENT = Path("core") / "templates" / "core" / "components" / "_head.html"
    TOP_LEVEL = [
        Path("core") / "templates" / "core" / "base.html",
        Path("core") / "templates" / "core" / "home.html",
        Path("gallery") / "templates" / "gallery" / "wall.html",
    ]

    def setUp(self):
        # Memories and the event list are both behind a login.
        self.client.force_login(get_user_model().objects.create_user(
            email="heads@example.com", password="a-good-long-password"))

    def body(self, path):
        markup = (Path(settings.BASE_DIR) / path).read_text()
        return re.sub(r"\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}", "",
                      markup, flags=re.S)

    def test_no_page_writes_the_shared_head_itself(self):
        for path in self.TOP_LEVEL:
            with self.subTest(path=str(path)):
                body = self.body(path)
                self.assertIn("core/components/_head.html", body,
                              "this page does not use the shared <head>")
                for copied in ["<meta charset", "rel=\"icon\"",
                               "rel=\"apple-touch-icon\"", "css/app.css",
                               "js/app.js", "_appearance.html"]:
                    self.assertNotIn(
                        copied, body,
                        f"this page hand-writes {copied} instead of including "
                        "the fragment, so the three will drift apart again — "
                        "and the last time they did, the front page lost its "
                        "favicon without anything failing")

    def test_charset_comes_before_the_title(self):
        """⚠️ It has to land inside the first 1024 bytes or the browser parses
        the document once with a guessed encoding and then starts over. The
        fragment leads with it, so the fragment has to be included before
        anything else the page writes into its own head.
        """
        for path in self.TOP_LEVEL:
            with self.subTest(path=str(path)):
                body = self.body(path)
                self.assertLess(body.index("core/components/_head.html"),
                                body.index("<title>"),
                                "the shared head is included after the title; "
                                "charset is no longer first in the document")

    def test_the_front_page_alone_skips_the_theme_boot(self):
        """⚠️ The one difference the parameter exists for, stated in both
        directions. The front page is white text over a photograph and does not
        follow dark mode — and if it did boot the theme, `.dark` on `<html>`
        would invert the top bar's whole white-text/navy-text rule set.
        """
        home = self.body(Path("core") / "templates" / "core" / "home.html")
        self.assertIn("_head.html", home)
        self.assertNotIn("themed", home,
                         "the front page now boots the theme; its top bar's "
                         "colours invert the moment <html> gets .dark")
        for path in [Path("core") / "templates" / "core" / "base.html",
                     Path("gallery") / "templates" / "gallery" / "wall.html"]:
            with self.subTest(path=str(path)):
                self.assertIn("themed=1", self.body(path))

    def test_the_theme_boot_still_reaches_the_pages_that_want_it(self):
        # Through the fragment now, so a grep for the include in the page files
        # would read the refactor as a removal. Assert on what is served.
        for label, url_name in [("an inner page", "events:event_list"),
                                ("Memories", "gallery:wall")]:
            with self.subTest(page=label):
                self.assertContains(self.client.get(reverse(url_name)),
                                    "localStorage")
        self.assertNotContains(self.client.get(reverse("home")), "localStorage")

    def test_the_three_titles_are_still_their_own(self):
        self.assertContains(self.client.get(reverse("home")),
                            "<title>River of Life Foundation</title>")
        self.assertContains(self.client.get(reverse("gallery:wall")),
                            "<title>Memories · ROLF</title>")
        self.assertContains(self.client.get(reverse("events:event_list")),
                            "· ROLF</title>")

    def test_the_fragment_says_why_it_exists(self):
        # ⚠️ Same rule as every other shared fragment: one pulled out of three
        #    copies is indistinguishable from one that was always a component,
        #    until somebody inlines it back.
        markup = (Path(settings.BASE_DIR) / self.FRAGMENT).read_text()
        self.assertIn("{% comment %}", markup)
        self.assertIn("⚠️", markup)


class RootClassTests(TestCase):
    """`has-hero` on `<html>`, decided in one place (2026-08-13).

    🔴 Why this is worth a class of its own: every dark-mode glass rule is
       selected by `.dark.has-hero ...`. Without the class the backdrop
       photograph is still painted but the 62% black over it is not, so the
       picture comes through at nearly full strength and the page goes bright
       and busy. **It does not error and it does not look like a missing
       class.** It has been hit once already, and `wall.html` was carrying a
       comment saying so while holding the second copy of the condition.
    """

    def setUp(self):
        self.client.force_login(get_user_model().objects.create_user(
            email="rootclass@example.com", password="a-good-long-password"))

    def pages(self):
        return [("an inner page", reverse("events:event_list")),
                ("Memories", reverse("gallery:wall"))]

    def test_the_condition_is_not_written_in_any_template(self):
        for path in [Path("core") / "templates" / "core" / "base.html",
                     Path("gallery") / "templates" / "gallery" / "wall.html"]:
            with self.subTest(path=str(path)):
                markup = (Path(settings.BASE_DIR) / path).read_text()
                body = re.sub(r"\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}",
                              "", markup, flags=re.S)
                self.assertIn("{{ site_root_class }}", body)
                self.assertNotIn(
                    "has-hero", body,
                    "this page decides `has-hero` for itself again; the two "
                    "copies drift and the symptom is a page that is simply too "
                    "bright in dark mode")

    def test_a_picture_puts_the_class_on_and_no_picture_takes_it_off(self):
        for label, url in self.pages():
            with self.subTest(page=label, hero=False):
                self.assertContains(self.client.get(url), 'class="h-full"')

        page = HomePage.load()
        page.hero_image = "home/example.webp"
        page.save()

        for label, url in self.pages():
            with self.subTest(page=label, hero=True):
                self.assertContains(self.client.get(url),
                                    'class="h-full has-hero"')

    def test_the_two_pages_always_agree(self):
        """⚠️ The failure this replaces was never "one page is wrong" — it was
        the two disagreeing, each looking correct on its own screen.
        """
        page = HomePage.load()
        page.hero_image = "home/example.webp"
        page.save()
        classes = {
            label: re.search(r"<html[^>]*class=\"([^\"]*)\"",
                             self.client.get(url).content.decode()).group(1)
            for label, url in self.pages()
        }
        self.assertEqual(len(set(classes.values())), 1, classes)


class SiteMenuTests(TestCase):
    """The menu's entries and its two admin headings (2026-08-06).

    ⚠️ Built as data in core/context_processors.py rather than as branches in the
       template, because the entrance animation numbers each entry and a hidden
       branch used to leave a hole in the numbering. These tests read the data,
       so a section added without a heading, or a heading shown to the wrong
       account, fails here rather than being noticed in a screenshot.
    """

    def menu(self, user=None):
        # ⚠️ Two different pages, because the login page **redirects** a signed-in
        #    visitor away and a redirect carries no context at all. Anything that
        #    renders for the account in question will do; the menu is on every
        #    page by construction, which is the thing being relied on.
        if user is None:
            return self.client.get(reverse("accounts:login")).context["site_menu"]
        self.client.force_login(user)
        return self.client.get(reverse("accounts:profile")).context["site_menu"]

    def labels(self, menu):
        return [item.get("label") or item.get("heading") for item in menu]

    def headings(self, menu):
        return [item["heading"] for item in menu if item.get("heading")]

    def volunteer(self):
        from accounts.services import register_account

        return register_account(
            email="mei@example.com", password="a-good-long-password",
            legal_first_name="Ann", legal_last_name="Mei")

    def test_a_stranger_sees_only_the_public_entries(self):
        # ⚠️ "Past Events" was between Events and Log In until 2026-08-17.
        self.assertEqual(
            self.labels(self.menu()),
            ["Events", "Log In", "Register"])

    def test_an_ordinary_volunteer_gets_no_admin_heading(self):
        # ⭐ The one that matters most: a heading called "Ministry admin" drawn
        #    for somebody who is not one tells them a page exists that will 403.
        self.assertEqual(self.headings(self.menu(self.volunteer())), [])

    def test_a_ministry_admin_gets_the_ministry_heading_only(self):
        from org.models import Ministry, MinistryRole

        user = self.volunteer()
        pantry = Ministry.objects.create(code="food_pantry", name="Food Pantry")
        MinistryRole.objects.create(contact=user.contact, ministry=pantry)
        menu = self.menu(user)
        self.assertEqual(self.headings(menu), ["Ministry Admin"])
        self.assertIn("Events I Manage", self.labels(menu))

    def test_a_foundation_admin_gets_the_foundation_heading_only(self):
        from org.permissions import foundation_admin_group

        user = self.volunteer()
        user.groups.add(foundation_admin_group())
        menu = self.menu(get_user_model().objects.get(pk=user.pk))
        self.assertEqual(self.headings(menu), ["Foundation Admin"])
        self.assertIn("All Events", self.labels(menu))
        self.assertIn("Ministry Admins", self.labels(menu))

    def test_somebody_with_both_hats_gets_both_headings_in_order(self):
        # ⭐ The whole reason the headings exist: one person, two kinds of
        #    authority, and the pages under each mean different things.
        from org.models import Ministry, MinistryRole
        from org.permissions import foundation_admin_group

        user = self.volunteer()
        pantry = Ministry.objects.create(code="food_pantry", name="Food Pantry")
        MinistryRole.objects.create(contact=user.contact, ministry=pantry)
        user.groups.add(foundation_admin_group())
        menu = self.menu(get_user_model().objects.get(pk=user.pk))
        self.assertEqual(self.headings(menu), ["Ministry Admin", "Foundation Admin"])

    def test_the_foundation_entry_asks_for_the_foundation_wide_view(self):
        # ⚠️ Without ?scope=all, somebody who also runs a ministry would follow a
        #    link labelled "All Events" onto a page showing only their own.
        from org.permissions import foundation_admin_group

        user = self.volunteer()
        user.groups.add(foundation_admin_group())
        menu = self.menu(get_user_model().objects.get(pk=user.pk))
        entry = next(i for i in menu if i.get("label") == "All Events")
        self.assertIn("scope=all", entry["url"])

    def test_the_admin_site_is_its_own_section_not_a_tier(self):
        # is_staff is a different axis from the two ministry tiers, so filing it
        # under either would state something untrue about who holds it.
        user = self.volunteer()
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        menu = self.menu(user)
        self.assertEqual(self.headings(menu), ["Staff"])
        self.assertIn("Admin Site", self.labels(menu))

    def test_every_entry_is_numbered_without_a_gap(self):
        # ⚠️ The reason this moved out of the template. `--i` drives each entry's
        #    transition-delay, and a hidden branch used to skip a number: one
        #    entry waits an extra beat for nothing and the stagger goes lumpy.
        #    Nothing errors, which is why it is asserted rather than watched.
        from org.permissions import foundation_admin_group

        user = self.volunteer()
        user.groups.add(foundation_admin_group())
        user.is_staff = True
        user.save(update_fields=["is_staff"])
        self.client.force_login(get_user_model().objects.get(pk=user.pk))
        page = self.client.get(reverse("accounts:profile")).content.decode()
        numbers = [int(n) for n in re.findall(r"--i: (\d+)", page)]
        self.assertEqual(numbers, list(range(len(numbers))))


class TextInputsComeFromFormsGuardTests(TestCase):
    """Free-text boxes go through a Form, so core/limits.py is enough.

    ⚠️ This guard is what makes that sentence true rather than true-for-now.
       The caps in core/limits.py are declared on models and forms; a
       ``<textarea>`` written straight into a template has no cap at all,
       accepts a pasted document, and **nothing about it looks wrong** — the
       page renders, the value saves, and the column quietly holds a megabyte.
       There is no error to notice, which is the only kind of gap worth a guard.

    ⚠️ Scoped to boxes somebody types **text** into, and that scope is the whole
       claim. `type="number"` is exempt because its bound is not a length:
       _attendance_row.html hand-writes one (deliberately — one input per row
       beats a form per row) and `HoursForm.hours` is what refuses 9999999,
       through `max_digits`. Widening this guard to cover it would be asking a
       length rule to police an arithmetic one, and the fix it demanded would
       make that page worse.
    """

    def test_no_hand_written_text_inputs(self):
        offenders = []
        for relative, source in project_template_files():
            blanked = _blank_out_comments(source)
            found = [(m.start(), "<textarea>") for m in TEXTAREA_TAG.finditer(blanked)]
            for match in INPUT_TAG.finditer(blanked):
                kind = INPUT_TYPE.search(match.group(0))
                kind = kind.group(1).strip().lower() if kind else ""
                if kind in TYPED_TEXT_INPUTS:
                    found.append((match.start(), f'<input type="{kind}">'))
            for offset, what in sorted(found):
                line = blanked.count("\n", 0, offset) + 1
                offenders.append(f"{relative}:{line}: {what}")
        self.assertEqual(
            offenders, [],
            "Free-text boxes are rendered by a Form so that core/limits.py "
            "applies to them. Put the field on the form rather than in the "
            "template:\n" + "\n".join(offenders))


class TextLengthLimitTests(TestCase):
    """The caps in core/limits.py, checked where they are actually enforced.

    ⚠️ Checked through **forms**, not through ``full_clean()``. A
       ``TextField(max_length=…)`` adds no model validator in Django 5.2 and
       the Postgres column stays ``text`` — the cap arrives via
       ``TextField.formfield()``. A test that asserted on the model would pass
       against a cap that no submitted page respects, or fail against one that
       every page does. core/limits.py's docstring spells the layers out.
    """

    def test_the_event_form_refuses_a_description_past_the_cap(self):
        from events.forms import EventForm

        form = EventForm(user=get_user_model()(), data={"description": "x" * (LONG_TEXT + 1)})
        self.assertIn("description", form.errors)

    def test_the_event_form_accepts_a_description_at_the_cap(self):
        # The boundary in the other direction: a cap that is off by one is a cap
        # that refuses the longest legitimate value, and nobody would guess why.
        from events.forms import EventForm

        form = EventForm(user=get_user_model()(), data={"description": "x" * LONG_TEXT})
        self.assertNotIn("description", form.errors)

    def test_the_notify_form_and_its_column_agree(self):
        # ⭐ The one pair that could drift: NotifyForm is a plain Form, so its
        #    cap is a second declaration of the same number.
        from events.forms import NotifyForm
        from events.models import EventNotification

        self.assertEqual(
            NotifyForm().fields["message"].max_length,
            EventNotification._meta.get_field("message").max_length)

    def test_every_text_field_we_wrote_has_a_cap(self):
        # Walked rather than listed, so a TextField added tomorrow is covered.
        # Historical models are excluded: simple-history copies the field it was
        # given, so a cap on the original is a cap on the copy.
        offenders = []
        for model in apps.get_models():
            if model._meta.app_label not in OUR_APPS:
                continue
            if model.__name__.startswith("Historical"):
                continue
            for field in model._meta.get_fields():
                if isinstance(field, models.TextField) and field.max_length is None:
                    offenders.append(f"{model._meta.label}.{field.name}")
        self.assertEqual(
            offenders, [],
            "An uncapped text box accepts a pasted document. Give it a cap from "
            "core/limits.py:\n" + "\n".join(offenders))


class HomePageTests(TestCase):
    """The public front page (D25).

    ⚠️ It replaced C3.1's role-router design. That plan sent each account to
       whichever list suited it, which is useful and is not a front page: a link
       shared with somebody who had never heard of the foundation opened a login
       form.
    """

    def test_it_opens_without_a_session(self):
        # ⭐ The one thing that makes it a front page rather than a dashboard.
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)

    def test_a_signed_in_visitor_sees_the_same_page(self):
        # Not a router: everybody gets the picture. What changes is one word in
        # the corner and which entries the menu offers.
        user = get_user_model().objects.create_user(email="v@example.com", password="x")
        self.client.force_login(user)
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "core/home.html")

    def test_the_menu_offers_signing_in_to_a_stranger(self):
        page = self.client.get(reverse("home")).content.decode()
        self.assertIn(reverse("accounts:login"), page)
        self.assertIn(reverse("accounts:register"), page)

    def test_the_menu_offers_the_volunteer_pages_once_signed_in(self):
        user = get_user_model().objects.create_user(email="v@example.com", password="x")
        self.client.force_login(user)
        page = self.client.get(reverse("home")).content.decode()
        self.assertIn(reverse("events:my_participations"), page)
        self.assertIn(reverse("accounts:profile"), page)

    def test_the_verse_is_shown_reference_last(self):
        """⚠️ Passage first, citation after — the ordering was specified.

        Asserted on position rather than on presence, because both being on the
        page says nothing about which one reads first.
        """
        page = HomePage.load()
        page.verse_text = "Whatever you do, work heartily."
        page.verse_reference = "Colossians 3:23-24"
        page.save()
        body = self.client.get(reverse("home")).content.decode()
        self.assertLess(body.index("Whatever you do"), body.index("COLOSSIANS"))

    def test_an_empty_home_page_still_renders(self):
        # No picture, no verse — a fresh production database. It must not 500.
        self.assertEqual(self.client.get(reverse("home")).status_code, 200)

    def test_the_logo_in_the_app_shell_points_here(self):
        user = get_user_model().objects.create_user(email="v@example.com", password="x")
        self.client.force_login(user)
        page = self.client.get(reverse("events:event_list")).content.decode()
        self.assertIn(f'href="{reverse("home")}"', page)


class HomePageSingletonTests(TestCase):
    """One row, always. A model that can hold two eventually holds two."""

    def test_load_creates_it_and_then_returns_the_same_one(self):
        first = HomePage.load()
        self.assertEqual(HomePage.load().pk, first.pk)
        self.assertEqual(HomePage.objects.count(), 1)

    def test_saving_a_second_one_overwrites_rather_than_adds(self):
        # ⚠️ save() forces the pk. Without that, a shell session or a fixture
        #    can create a second row, and load() then picks one by chance.
        HomePage.load()
        HomePage(verse_reference="Somewhere else").save()
        self.assertEqual(HomePage.objects.count(), 1)
        self.assertEqual(HomePage.load().verse_reference, "Somewhere else")

    def test_it_refuses_to_be_deleted(self):
        # The public front page cannot be "not there".
        with self.assertRaises(IntegrityError):
            HomePage.load().delete()

    def test_video_wins_over_image_when_both_are_set(self):
        """⚠️ Stated, because the alternative is invisible.

        Showing whichever was uploaded most recently would make the page's
        appearance depend on something nobody can see from the form.
        """
        page = HomePage.load()
        page.hero_image = "home/a.jpg"
        page.hero_video = "home/b.mp4"
        self.assertEqual(page.hero[1], "video")
        page.hero_video = ""
        self.assertEqual(page.hero[1], "image")
        page.hero_image = ""
        self.assertIsNone(page.hero)

    def test_the_answer_is_readable_by_name_and_still_unpacks(self):
        """⚠️ `hero.kind` is what makes the front page's branch checkable.

        The template asks this question now, and `{% if page.hero.1 == "video" %}`
        is a line nobody can verify by reading it. The tuple half is kept
        because anything that already indexed it — this file included — has no
        reason to be rewritten for a rename.
        """
        page = HomePage.load()
        page.hero_video = "home/b.mp4"
        self.assertEqual(page.hero.kind, "video")
        self.assertEqual(page.hero.file, page.hero_video)
        self.assertEqual(tuple(page.hero), (page.hero_video, "video"))

    def test_one_query_for_the_row_however_many_places_ask(self):
        """⚠️ The front page asked for this row twice on every single hit.

        `core.views.home` wants it for the verse and the picture, and the
        `site_appearance` context processor wants it for the shell — and that
        processor runs on every page in the site, this one included. Two
        identical SELECTs on the busiest public URL there is, with neither call
        site in a position to notice the other.
        """
        page = HomePage.load()
        page.hero_image = "home/example.webp"
        page.save()
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        reads = [q for q in captured.captured_queries if "core_homepage" in q["sql"]]
        self.assertEqual(
            len(reads), 1,
            "the front page reads the home page row more than once — "
            "HomePage.for_request caches on the request, so somebody has gone "
            f"back to current() at one of the two call sites. Got: {reads}")

    def test_the_row_is_cached_on_the_request_and_not_on_the_process(self):
        """⚠️ A module- or class-level cache would serve yesterday's picture to
        everybody until the worker restarted, and the symptom is somebody
        saving the page, seeing nothing change, and saving it again.
        """
        self.assertIsNone(HomePage.for_request(RequestFactory().get("/")).pk)

        page = HomePage.load()
        page.verse_reference = "Colossians 3:23"
        page.save()

        later = HomePage.for_request(RequestFactory().get("/"))
        self.assertEqual(later.verse_reference, "Colossians 3:23")


#: The four aliases on the local filesystem, so a test that stores a file never
#: reaches Cloudflare R2. Same override the gallery suite uses, and for the same
#: reason: base.py already keeps them local, and saying so out loud is what
#: stops a future change to the defaults quietly putting the test suite on the
#: network.
LOCAL_STORAGE = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "memories": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "public": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


class EmptyBucketTestCase(TestCase):
    """A TestCase whose bucket starts empty for **every method**.

    ⚠️ The usual `@override_settings(MEDIA_ROOT=tempfile.mkdtemp())` on the
       class makes the directory **once**, at import — so files written by one
       test are still there in the next. The database is rolled back between
       methods and the bucket is not, which is exactly the asymmetry these two
       classes are about: they assert on what is and is not in the bucket, and
       leftovers from an earlier method are indistinguishable from the orphans
       being hunted. Two of the tests below found this the honest way.
    """

    def setUp(self):
        super().setUp()
        root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        overridden = override_settings(MEDIA_ROOT=root, STORAGES=LOCAL_STORAGE)
        overridden.enable()
        self.addCleanup(overridden.disable)


class HomePageMediaReplacementTests(EmptyBucketTestCase):
    """Changing the front page's picture takes the old one out of the bucket.

    ⭐ **Until 2026-08-14 it did not, and nothing anywhere said so.** Django
       writes each upload under a new key and forgets the previous one, so every
       change of picture left a file behind that no row referred to, no page
       could reach, and nobody would think to look for. The front page is also
       the one upload that is **not** re-encoded — what arrives is what is
       stored — so this was the most expensive place in the project to leak a
       file.
    """

    def storage(self):
        return HomePage._meta.get_field("hero_image").storage

    def put(self, page, field="hero_image", name="hero.jpg", body=b"first"):
        """Store a file on one of the two media fields and return its key."""
        getattr(page, field).save(name, ContentFile(body), save=True)
        return getattr(page, field).name

    def test_replacing_the_picture_deletes_the_one_it_replaces(self):
        page = HomePage.load()
        first = self.put(page)
        self.assertTrue(self.storage().exists(first))

        with self.captureOnCommitCallbacks(execute=True):
            self.put(page, name="second.jpg", body=b"second")

        self.assertFalse(
            self.storage().exists(first),
            "the picture that was replaced is still in the public bucket — "
            "HomePage._superseded_media no longer finds it")
        self.assertTrue(self.storage().exists(page.hero_image.name))

    def test_the_old_file_is_only_deleted_once_the_save_has_committed(self):
        """⚠️ Deleting inline would mean a transaction that then rolls back
        leaves the row pointing at a file that is no longer there — a broken
        image on the front page, arrived at with nothing raised anywhere.

        Asserted by watching the boundary: inside the block the callback is
        captured but not yet run, so the old file must still be there.
        """
        page = HomePage.load()
        first = self.put(page)

        with self.captureOnCommitCallbacks(execute=True):
            self.put(page, name="second.jpg", body=b"second")
            self.assertTrue(
                self.storage().exists(first),
                "the old file was deleted before the save committed")

        self.assertFalse(self.storage().exists(first))

    def test_clearing_the_picture_deletes_the_file_too(self):
        """⚠️ "Remove the picture" means the picture is gone. A file left behind
        in the **public** bucket is still on a URL that works — which is the
        opposite of what was asked for. Decided 2026-08-14.
        """
        page = HomePage.load()
        first = self.put(page)

        with self.captureOnCommitCallbacks(execute=True):
            page.hero_image = ""
            page.save()

        self.assertFalse(self.storage().exists(first))

    def test_saving_without_touching_the_media_deletes_nothing(self):
        """⭐ The regression that would cost a photograph rather than leak one.

        Every save of this row runs the comparison, and most saves — the verse,
        the framing — do not touch a file at all. A comparison that answered
        "changed" for those would delete the live picture the first time
        somebody edited the verse.
        """
        page = HomePage.load()
        first = self.put(page)

        with self.captureOnCommitCallbacks(execute=True):
            page.verse_reference = "Colossians 3:23"
            page.hero_focus_x = 30
            page.save()

        self.assertTrue(self.storage().exists(first))
        self.assertEqual(HomePage.load().hero_image.name, first)

    def test_reloading_the_row_and_saving_it_deletes_nothing(self):
        """The same guard from the other direction: a fresh instance read out of
        the database, saved unchanged. This is what a management command or a
        shell session does, and it must be inert."""
        page = HomePage.load()
        first = self.put(page)

        with self.captureOnCommitCallbacks(execute=True):
            HomePage.load().save()

        self.assertTrue(self.storage().exists(first))

    def test_changing_the_video_leaves_the_picture_alone(self):
        """⚠️ Per field. `hero` prefers the video when both are set, but
        somebody uploading a video has said nothing about the picture — and the
        picture is what every *other* page in the site shows.
        """
        page = HomePage.load()
        picture = self.put(page)
        video = self.put(page, field="hero_video", name="loop.mp4", body=b"one")

        with self.captureOnCommitCallbacks(execute=True):
            self.put(page, field="hero_video", name="loop2.mp4", body=b"two")

        self.assertTrue(self.storage().exists(picture))
        self.assertFalse(self.storage().exists(video))

    def test_a_bucket_that_refuses_the_delete_does_not_break_the_save(self):
        """⚠️ Swallowed on purpose. This runs after the save has committed, so
        there is nothing to roll back and nothing useful to say: the picture
        *is* live. A 500 here would be read as "it did not save", and the next
        thing that happens is somebody pressing save again.
        """
        page = HomePage.load()
        self.put(page)

        with mock.patch.object(type(self.storage()), "delete",
                               side_effect=OSError("R2 said no")):
            with self.assertLogs("core.services", level="ERROR") as logged:
                with self.captureOnCommitCallbacks(execute=True):
                    self.put(page, name="second.jpg", body=b"second")

        self.assertEqual(HomePage.load().hero_image.name, page.hero_image.name)
        self.assertIn("Could not delete superseded media", "\n".join(logged.output))


class OrphanedHomeMediaTests(EmptyBucketTestCase):
    """The sweep for what was left behind before the replacement above existed.

    ⚠️ It **reports**; a person decides. Every assertion about the command below
       is really about that: the default run deletes nothing, and the number it
       prints as deleted is the number that actually went.
    """

    def storage(self):
        return HomePage._meta.get_field("hero_image").storage

    def orphan(self, name="stale.jpg"):
        """A file under the front page's prefix that no row points at."""
        return self.storage().save(f"{HomePage.MEDIA_DIR}/{name}",
                                   ContentFile(b"left behind"))

    def run_command(self, *args):
        out = io.StringIO()
        call_command("purge_orphaned_home_media", *args, stdout=out)
        return out.getvalue()

    def test_nothing_uploaded_yet_is_not_an_error(self):
        """A fresh install has no such prefix at all, which is a state and not
        a fault."""
        self.assertEqual(orphaned_home_media(), [])

    def test_a_file_no_row_points_at_is_listed(self):
        stale = self.orphan()
        self.assertEqual(orphaned_home_media(), [stale])

    def test_the_live_picture_and_video_are_never_listed(self):
        """⭐ The one bug in this feature that costs something irreversible."""
        page = HomePage.load()
        page.hero_image.save("live.jpg", ContentFile(b"live"), save=True)
        page.hero_video.save("live.mp4", ContentFile(b"live"), save=True)
        stale = self.orphan()

        self.assertEqual(orphaned_home_media(), [stale])

    def test_the_command_deletes_nothing_unless_told_twice(self):
        """⚠️ The opposite default to purge_event_images. Being wrong there
        costs a picture that was going to go anyway; being wrong here costs the
        front page's photograph, out of the bucket the pg_dump does not cover.
        """
        stale = self.orphan()
        output = self.run_command()

        self.assertIn(stale, output)
        self.assertIn("Nothing was deleted", output)
        self.assertTrue(self.storage().exists(stale))

    def test_with_delete_they_actually_go(self):
        stale = self.orphan()
        output = self.run_command("--delete")

        self.assertFalse(self.storage().exists(stale))
        self.assertIn("1 object(s) deleted", output)

    def test_it_reports_what_it_could_not_delete_rather_than_the_count_it_wanted(self):
        """⚠️ discard_media swallows a storage error, so the two numbers can
        differ — and printing the number asked for as the number deleted is how
        a bucket quietly stays full while the command says it emptied it.
        """
        self.orphan()
        with mock.patch.object(type(self.storage()), "delete",
                               side_effect=OSError("R2 said no")):
            with self.assertLogs("core.services", level="ERROR"):
                output = self.run_command("--delete")

        self.assertIn("0 object(s) deleted", output)
        self.assertIn("1 could not be deleted", output)


class DerivedPaletteTests(TestCase):
    """The brand ramp is built from the front page's photograph (D26).

    ⭐ **The one assertion that matters is the sweep.** A palette taken from an
       arbitrary photograph is a palette nobody reviewed, so the guarantee
       cannot be "we checked the teal one" — it has to hold for every hue and
       every saturation a camera can produce.

    It holds because `core/palette.py` pins the **relative luminance** of each
    step to the value the hand-tuned teal had. Contrast is a function of
    luminance alone, so every ratio in design-system.md's table survives
    unchanged.
    """

    #: The three ratios design-system.md publishes, and what they are measured
    #: against. Allowing 0.15 of slack: the generator lands on the target
    #: luminance by bisection and 8-bit rounding moves it a hair.
    PUBLISHED = [
        (700, (255, 255, 255), 4.5, "link text on white"),
        (600, (255, 255, 255), 4.5, "white on the primary button"),
        (300, (16, 21, 26), 4.5, "the dark-mode link on ink-950"),
    ]

    #: Pairs where **both** colours come out of the ramp, so both move with the
    #: photograph. The ministry badge is the one that matters: it is a category
    #: label, not a status, so it uses brand rather than borrowing a semantic
    #: colour.
    PUBLISHED_PAIRS = [
        (800, 50, 4.5, "the category badge, light"),
        (200, 900, 4.5, "the category badge, dark"),
    ]

    def contrast(self, hex_colour, other):
        rgb = tuple(int(hex_colour.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
        first, second = relative_luminance(rgb), relative_luminance(other)
        lighter, darker = max(first, second), min(first, second)
        return (lighter + 0.05) / (darker + 0.05)

    def test_every_hue_and_saturation_still_meets_the_published_ratios(self):
        """⭐ Twelve hues × four saturations. All of them, or the feature is unsafe."""
        failures = []
        for hue in range(0, 360, 30):
            for saturation in (0.25, 0.45, 0.70, 0.95):
                red, green, blue = colorsys.hls_to_rgb(hue / 360, 0.5, saturation)
                ramp = ramp_from((round(red * 255), round(green * 255), round(blue * 255)))
                self.assertIsNotNone(ramp, f"hue {hue} sat {saturation} produced nothing")
                for step, against, minimum, what in self.PUBLISHED:
                    got = self.contrast(ramp[step], against)
                    if got < minimum:
                        failures.append(
                            f"hue {hue} sat {saturation}: {what} = {got:.2f}, need {minimum}")
                for front, back, minimum, what in self.PUBLISHED_PAIRS:
                    rgb = tuple(int(ramp[back].lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
                    got = self.contrast(ramp[front], rgb)
                    if got < minimum:
                        failures.append(
                            f"hue {hue} sat {saturation}: {what} = {got:.2f}, need {minimum}")
        self.assertEqual(failures, [], "\n".join(failures))

    def test_a_grey_photograph_is_refused_rather_than_used(self):
        """⚠️ Fog and snow have no hue worth taking.

        A near-grey "brand" colour reads as broken rather than as restrained, so
        the generator declines and the built-in teal stands.
        """
        self.assertIsNone(ramp_from((128, 128, 130)))

    def test_the_ramp_keeps_the_photographs_hue(self):
        # Otherwise it is not derived from anything and the feature is a lie.
        ramp = ramp_from((196, 40, 44))            # a red barn
        red, green, blue = (int(ramp[600].lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
        self.assertGreater(red, green)
        self.assertGreater(red, blue)

    def test_a_muted_photograph_stays_muted(self):
        """Saturation is capped by the tuned ramp, not replaced by it.

        ⚠️ Taking the reference saturation outright would turn every photograph
           into the same vivid ramp — the colour would come from the picture and
           the character would not.
        """
        muted = ramp_from((120, 96, 84))
        vivid = ramp_from((255, 90, 20))
        self.assertLess(self.saturation_of(muted[600]), self.saturation_of(vivid[600]))

    def saturation_of(self, hex_colour):
        rgb = [int(hex_colour.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        return colorsys.rgb_to_hls(*rgb)[2]

    def test_a_large_photograph_is_never_decoded_at_its_full_size(self):
        """⚠️ 2026-08-12: this one took the production instance down.

        `convert()` decodes the whole picture before `thumbnail()` can shrink
        it — measured at **+94 MB of peak memory for one 6000×4000 photograph
        out of a 1.1 MB file**, against a 512 MB instance running two workers.
        Changing the front page picture a few times in a row was enough: the
        process was killed and every visitor got a 502. With `draft()` the same
        measurement is +3.3 MB.

        ⚠️ **The regression this guards is invisible in every other way.**
           Delete the `draft()` line and the colours that come out are
           identical (verified on three photographs), every other test stays
           green, and the only difference is a number nobody is looking at
           until the instance dies. So the assertion is on the *size of the
           image at the moment it is converted*, which is the actual rule.

        ⚠️ The 10 MB upload limit is not a defence and never was: what costs
           the memory is the pixel count after decoding, and a small file can
           hold a very large picture — the fixture here is proof.
        """
        import io

        from PIL import Image as PILImage

        source = PILImage.new("RGB", (6000, 4000), (178, 34, 34))
        buffer = io.BytesIO()
        source.save(buffer, "JPEG", quality=88)
        source.close()
        buffer.seek(0)
        self.assertLess(len(buffer.getvalue()), 10 * 1024 * 1024,
                        "the fixture has to be an upload the form would accept")

        real_convert = PILImage.Image.convert
        converted_at = []

        def spy(self, *args, **kwargs):
            converted_at.append(self.size)
            return real_convert(self, *args, **kwargs)

        with mock.patch.object(PILImage.Image, "convert", spy):
            dominant_colour(buffer)

        self.assertNotEqual(converted_at, [], "nothing was converted at all")
        # ⚠️ A quarter of the original, not "small": JPEG can only be decoded at
        #    1/2, 1/4 or 1/8 scale, so 6000×4000 comes out at 750×500 and no
        #    smaller however little is asked for. Asserting a fixed pixel count
        #    would encode that arithmetic; asserting a ratio says the rule.
        self.assertLessEqual(
            max(max(size) for size in converted_at), 6000 / 4,
            "the picture was still at full size when it was decoded — the "
            "draft() call in dominant_colour has gone, and with it the only "
            "thing keeping a phone photograph from costing ~94 MB of memory")


class AppearanceContextTests(TestCase):
    """What the shared shell is told about the front page, on every request."""

    def test_no_hero_means_no_override_and_no_background(self):
        # A fresh production database. The built-in teal has to stand, and dark
        # mode has to stay plain dark.
        response = self.client.get(reverse("home"))
        self.assertIsNone(response.context["site_brand_palette"])
        self.assertIsNone(response.context["site_hero_image"])

    def test_the_read_path_never_writes(self):
        """⚠️ `current()`, not `load()`.

        The shell asks for this on every page view of the whole site, and
        `load()` is a get_or_create — a write on the read path. Two query-count
        tests caught that within a minute of it landing; this states the rule
        so it does not come back.
        """
        self.client.get(reverse("home"))
        self.assertEqual(HomePage.objects.count(), 0)


class HeroFramingTests(TestCase):
    """Which part of the front page's photograph survives being cropped.

    ⚠️ The problem this answers is not "the crop is wrong", it is that **the
       crop is a different shape on every device** — a phone keeps a tall slice
       of the picture and a laptop a wide one. So what is stored is a point
       that must stay visible, not a rectangle: a rectangle would simply be
       cropped again on the next screen (2026-08-13).
    """

    def test_the_default_is_what_a_browser_does_on_its_own(self):
        """50/50 has to be the default, and it has to be the middle.

        ⚠️ Anything else would silently re-frame the picture already on the
           front page the moment this migration lands — a change nobody asked
           for, arriving as "the photo moved by itself".
        """
        self.assertEqual(HomePage().hero_focus, "50% 50%")

    def test_the_front_page_and_every_other_page_are_framed_by_one_number(self):
        """⚠️ The same photograph, two croppings, one instruction.

        The front page crops it with object-position and the shared dark
        backdrop with background-position. Letting each pick its own would come
        out as a picture framed one way on the front page and another way
        behind the rest of the site, both looking deliberate — which is the
        divergence `_hero_backdrop.html` was already carrying a warning about.
        """
        page = HomePage.load()
        page.hero_image = "home/example.webp"
        page.hero_focus_x, page.hero_focus_y = 30, 15
        page.save()

        front = self.client.get(reverse("home"))
        self.assertContains(front, "object-position: 30% 15%")
        self.assertEqual(front.context["site_hero_focus"], "30% 15%")

        inner = self.client.get(reverse("accounts:login"))
        self.assertContains(inner, "background-position: 30% 15%")

    def test_the_inner_pages_no_longer_pin_the_crop_to_the_centre(self):
        """`bg-center` was the hard-coded half of the same decision.

        ⚠️ Leaving it in the class list would beat the inline value silently —
           the focus would be stored, shown in the admin, applied on the front
           page, and do nothing at all on every other page.
        """
        page = HomePage.load()
        page.hero_image = "home/example.webp"
        page.hero_focus_x, page.hero_focus_y = 80, 20
        page.save()
        inner = self.client.get(reverse("accounts:login"))
        self.assertNotContains(inner, "bg-center")

    def test_the_video_wins_rule_has_exactly_one_implementation(self):
        """⚠️ It had two, and one of them was dead (fixed 2026-08-13).

        `HomePage.hero` carried the rule and a comment explaining it, and
        **nothing but this test file called it** — the front page decided the
        same thing again with a `{% if page.hero_video %}` of its own. So the
        suite was green about a property no visitor's page ever reached, and
        the two could have disagreed without a single failure.
        """
        markup = (Path(settings.BASE_DIR) / "core" / "templates" / "core"
                  / "home.html").read_text()
        body = re.sub(r"\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}", "",
                      markup, flags=re.S)
        self.assertIn("page.hero", body,
                      "the front page no longer asks HomePage.hero")
        self.assertNotIn(
            "hero_video", body,
            "the front page is deciding video-over-image for itself again; "
            "that rule belongs to HomePage.hero and nowhere else")

    def test_the_front_page_plays_the_video_while_the_rest_of_the_site_does_not(self):
        """Both halves of the split, in one place, because they are one decision.

        ⚠️ Every other page shows the **image** — a video behind every page
           means every page decodes video, which on a phone is heat and battery
           for something nobody is looking at.
        """
        page = HomePage.load()
        page.hero_image = "home/example.webp"
        page.hero_video = "home/clip.mp4"
        page.save()

        front = self.client.get(reverse("home"))
        self.assertContains(front, "home/clip.mp4")
        self.assertNotContains(front, "home/example.webp")

        self.client.force_login(get_user_model().objects.create_user(
            email="inner@example.com", password="a-good-long-password"))
        inner = self.client.get(reverse("events:event_list"))
        self.assertContains(inner, "home/example.webp")
        self.assertNotContains(inner, "home/clip.mp4")

    def test_with_nothing_uploaded_the_front_page_still_shows_the_logo(self):
        """⚠️ The front page's fallback, and it is **not** shared with the rest
        of the site: every other page falls back to plain dark. Deliberate —
        the front page cannot be a blank screen, and an inner page carrying the
        logo as a full-bleed background would be noise behind text.
        """
        front = self.client.get(reverse("home"))
        self.assertContains(front, "event-default")

        self.client.force_login(get_user_model().objects.create_user(
            email="nologo@example.com", password="a-good-long-password"))
        inner = self.client.get(reverse("events:event_list"))
        self.assertIsNone(inner.context["site_hero_image"])

    def test_the_admin_offers_the_picture_to_click_on(self):
        """The numbers are for the machine; the picture is for the person.

        "0 is the left edge, 100 the right" is a sentence somebody has to
        translate in their head every time. The widget renders the actual
        photograph with the current point drawn on it.
        """
        staff = get_user_model().objects.create_superuser(
            email="framer@example.com", password="a-good-long-password")
        self.client.force_login(staff)
        page = HomePage.load()
        page.hero_image = "home/example.webp"
        page.hero_focus_x, page.hero_focus_y = 25, 75
        page.save()

        response = self.client.get(
            reverse("admin:core_homepage_change", args=[page.pk]))
        self.assertContains(response, 'class="hero-focus"')
        self.assertContains(response, 'data-focus-x="25"')
        self.assertContains(response, "home/example.webp")

    def test_the_numbers_stay_in_the_form_behind_the_picture(self):
        """⚠️ The picker writes into these; it submits nothing of its own.

        So the framing is still editable when the script has not run — a
        stylesheet that hid these would hide them in exactly the case they are
        needed. D24's line about write paths, applied to a staff screen.
        """
        staff = get_user_model().objects.create_superuser(
            email="fallback@example.com", password="a-good-long-password")
        self.client.force_login(staff)
        page = HomePage.load()
        response = self.client.get(
            reverse("admin:core_homepage_change", args=[page.pk]))
        self.assertContains(response, 'name="hero_focus_x"')
        self.assertContains(response, 'name="hero_focus_y"')

    def test_with_no_picture_yet_the_widget_says_so_rather_than_breaking(self):
        """⚠️ The markup is **present and hidden**, not absent (2026-08-13).

        It used to be wrapped in an `{% if %}` and so did not exist until a file
        had been saved — which is exactly why choosing a picture and scrolling
        down to frame it found a sentence instead of the tool. The script fills
        this in from the file the browser is already holding, and it needs
        somewhere to put it. What somebody *sees* with an empty form is
        unchanged: the sentence, and no picture.
        """
        staff = get_user_model().objects.create_superuser(
            email="empty@example.com", password="a-good-long-password")
        self.client.force_login(staff)
        response = self.client.get(
            reverse("admin:core_homepage_change", args=[HomePage.load().pk]))
        self.assertEqual(response.status_code, 200)
        markup = response.content.decode()

        picker = re.search(r"<div class=\"hero-focus\"[^>]*>", markup)
        self.assertIsNotNone(picker, "the picker is not in the document at all, "
                                     "so there is nothing for the script to "
                                     "preview an unsaved file into")
        self.assertIn("hidden", picker.group(0),
                      "an empty picker is being shown rather than hidden")
        self.assertIn("the framing tool", markup)

    def test_the_saved_urls_are_handed_over_separately_from_the_preview(self):
        """⚠️ Stored and merely-chosen have to stay apart.

        The script draws an unsaved file over `src`; the saved URL has to
        survive that, or cancelling an upload leaves the widget claiming the
        old picture is gone.
        """
        staff = get_user_model().objects.create_superuser(
            email="urls@example.com", password="a-good-long-password")
        self.client.force_login(staff)
        page = HomePage.load()
        page.hero_image = "home/example.webp"
        page.save()
        response = self.client.get(
            reverse("admin:core_homepage_change", args=[page.pk]))
        self.assertContains(response, "data-image-url=")
        self.assertContains(response, "data-video-url=")

    def test_both_media_tags_are_always_rendered(self):
        """⚠️ Both, so a swap between a picture and a video is a `hidden` flip
        rather than markup the script has to build. Only one is ever shown, and
        the rule for which is `HomePage.hero`'s — video wins.
        """
        staff = get_user_model().objects.create_superuser(
            email="bothtags@example.com", password="a-good-long-password")
        self.client.force_login(staff)
        page = HomePage.load()
        page.hero_image = "home/example.webp"
        page.save()
        markup = self.client.get(
            reverse("admin:core_homepage_change", args=[page.pk])).content.decode()
        self.assertIn("hero-focus__video", markup)
        self.assertIn("hero-focus__image", markup)


class FocusPickerScriptTests(TestCase):
    """Lint-as-test over the framing widget's script and stylesheet.

    ⚠️ None of this runs in the test suite — there is no browser here. These
       are the handful of lines whose absence is silent: a drag that scrolls
       the page instead, a marker that trails the finger, a blob: URL nobody
       lets go of. Each one is a symptom somebody would report as "it feels
       broken" rather than as an error.
    """

    def script(self):
        return (Path(settings.BASE_DIR) / "core" / "static" / "core" / "admin"
                / "hero_focus_picker.js").read_text()

    def stylesheet(self):
        return (Path(settings.BASE_DIR) / "core" / "static" / "core" / "admin"
                / "hero_focus_picker.css").read_text()

    def test_dragging_is_pointer_events_and_captures(self):
        """⚠️ One pair of handlers for a mouse, a trackpad and a finger.

        And `setPointerCapture`, or a drag that leaves the picture stops being
        delivered — the ring freezes at the edge, which reads as the widget
        breaking rather than as a boundary being reached.
        """
        source = self.script()
        for needle in ["pointerdown", "pointermove", "pointerup",
                       "pointercancel", "setPointerCapture"]:
            with self.subTest(needle=needle):
                self.assertIn(needle, source)

    def test_clicking_is_still_the_same_gesture_and_not_a_second_handler(self):
        """⚠️ A click **is** a pointerdown, so the point lands on the way down
        and a drag only keeps updating it. A separate `click` listener would be
        a second implementation of the same thing, free to disagree.
        """
        self.assertNotIn('addEventListener("click"', self.script())

    def test_a_drag_does_not_scroll_the_page_instead(self):
        """⚠️ Without `touch-action: none` the browser claims the gesture as a
        pan the moment it looks like one, pointermove stops arriving, and the
        widget is simply dead to a finger — on the device where the framing
        matters most.
        """
        self.assertIn("touch-action: none", self.stylesheet())

    def test_the_marker_does_not_ease_while_it_is_being_dragged(self):
        # 60ms of easing is what makes a click land softly and a drag lag.
        self.assertIn(".hero-focus--dragging .hero-focus__marker",
                      self.stylesheet())
        self.assertIn("transition: none", self.stylesheet())

    def test_hidden_is_given_a_display_rule_of_its_own(self):
        """⚠️ `[hidden]`'s `display: none` comes from the UA stylesheet and is
        beaten by the `inline-block`/`block` rules on these very elements. The
        script hides things with the attribute, so without this the attribute
        is set and nothing disappears.
        """
        self.assertIn(".hero-focus[hidden]", self.stylesheet())
        self.assertIn(".hero-focus__media[hidden]", self.stylesheet())

    def test_object_urls_are_released(self):
        """⚠️ Each blob: URL pins the whole file in memory until it is revoked.
        Somebody trying four photographs in a row would hold all four.
        """
        self.assertIn("revokeObjectURL", self.script())

    def test_the_preview_follows_the_same_video_wins_rule_as_the_page(self):
        # A widget previewing the picture while the site plays the video would
        # be showing the wrong thing to aim at, and would look reasonable.
        self.assertIn("video.hidden ? image : video", self.script())

    def test_the_script_reads_the_clear_box_and_never_writes_it(self):
        """⚠️ Ticking Clear *and* choosing a file is a contradiction Django
        rejects on submit with a message of its own. Untangling it here would
        be deciding which half somebody meant — and this widget's whole
        contract is that it submits nothing.
        """
        source = self.script()
        self.assertIn("clears[kind].checked", source)
        self.assertNotIn(".checked =", source)

    def test_a_focus_outside_the_picture_is_refused_by_the_database(self):
        """⚠️ In the database, not only in the form (D9 / D14).

        `object-position: 300% 50%` is valid CSS: it slides the picture off its
        own frame, and the page comes out mostly empty background with nothing
        raised anywhere. The field type already refuses negatives; this is the
        other end of the range.
        """
        page = HomePage.load()
        page.hero_focus_x = 300
        with self.assertRaises(IntegrityError):
            page.save()


class SiteMenuShapeTests(TestCase):
    """Two things about the menu that are easy to get wrong and never raise."""

    def setUp(self):
        # ⚠️ `is_staff`, not a ministry role: the Admin Site entry hangs off
        #    that axis alone, and it is neither of the two ministry tiers.
        self.user = get_user_model().objects.create_user(
            email="staff@example.com", password="a-good-long-password",
            is_staff=True)
        self.client.force_login(self.user)

    def test_the_admin_site_opens_in_a_new_tab(self):
        """⚠️ The only entry in this menu that leaves this interface, and the
        only one that gets `target`. Somebody opens the Django admin to look
        something up **while** in the middle of whatever brought them here;
        replacing the tab throws that away, and the way back is the browser's
        Back button through a page that may have been a POST.
        """
        page = self.client.get(reverse("home")).content.decode()
        entry = page[page.index('href="/admin/"'):]
        self.assertIn('target="_blank"', entry[:200])
        self.assertIn('rel="noopener"', entry[:200])

    def test_no_other_menu_entry_opens_in_a_new_tab(self):
        """The rest are pages of this site. Opening those in new tabs would
        just accumulate them, and a link that behaves differently from its
        neighbours for no visible reason is worse than either behaviour."""
        page = self.client.get(reverse("home")).content.decode()
        menu = page[page.index('class="home-menu'):]
        self.assertEqual(menu.count('target="_blank"'), 1)

    def test_the_menu_panel_has_a_dark_pair_for_everything_it_paints(self):
        """⚠️ 2026-08-08: the panel was a hardcoded `bg-white`, so in dark mode
        it was one white slab. Changing the background is not enough on its own
        — the text, the hover colour, the dividers and the headings all had to
        gain a dark step with it, and **missing any one of them shows up as
        "this bit is hard to read", never as an error**.
        """
        page = self.client.get(reverse("home")).content.decode()
        # ⚠️ Sliced from the panel's opening tag, not from `aria-label` —
        #    `class` is written before it, so anchoring on the label would cut
        #    off most of what this test reads.
        menu = page[page.index('class="home-menu'):]
        # ⚠️ The panel's own background is **not** in this list: it moved into
        #    the stylesheet, and the test below says why it had to.
        for expected in ["dark:text-ink-300",    # the close button
                         "dark:text-ink-100",    # the links
                         "dark:border-ink-700"]:  # the dividers under them
            with self.subTest(rule=expected):
                self.assertIn(expected, menu)

    def test_the_headings_and_their_rule_have_a_dark_step_too(self):
        """The heading colour and the line above it live in app.css rather than
        on the tag, so they are checked there. ⚠️ Leaving the rule at ink-200
        would draw a bright line across a dark panel — more prominent than the
        two groups it separates."""
        css = Path("assets/app.css").read_text()
        block = css[css.index("  .dark .home-menu-heading {"):]
        self.assertIn("border-top-color: var(--color-ink-700);", block[:220])
        self.assertIn("color: var(--color-ink-300);", block[:220])

    def test_the_front_page_needs_no_exception_for_any_of_this(self):
        """⚠️ Worth pinning down because it looks like an oversight. The front
        page never carries `.dark` — it is a full-bleed photograph with white
        type and deliberately does not follow the theme — so every `dark:` step
        above simply never matches there, and the menu stays white on that page
        without a single rule saying so."""
        page = self.client.get(reverse("home")).content.decode()
        self.assertIn('<html lang="en" class="h-full">', page)


class SharedFragmentGuardTests(TestCase):
    """Fragments that exist because the same markup was written twice.

    ⚠️ These are all the same shape of bug and it is worth naming: a second copy
    never *breaks*. Both copies render, both look right in isolation, and the
    failure only appears later as "these two pages look slightly different" —
    with nothing in any diff pointing at the cause. Every fragment guarded here
    was a real copy that had already been sitting in the tree, and in two cases
    the copy carried a comment predicting exactly this.
    """

    def markup(self, path):
        return (Path(settings.BASE_DIR) / path).read_text()

    def body(self, path):
        """The template with its {% comment %} blocks stripped.

        ⚠️ Necessary, not tidiness: the comments in this project quote the very
        markup they are warning about, so a naive `assertNotIn` fails on the
        warning rather than on the offence.
        """
        return re.sub(r"\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}",
                      "", self.markup(path), flags=re.S)

    # ⚠️ 活动详情这一格指的是 `_event_detail_body.html`，不是 `event_detail.html`
    #    （2026-08-18 正文抽出去了，因为日程面板里就地打开的是同一份）。
    #    守的是那份**正文**，而不是外面那层壳 —— 壳里现在只有一个 include。
    SIGNUP_LISTS = [
        Path("events") / "templates" / "events" / "_event_detail_body.html",
        Path("events") / "templates" / "events" / "event_registrations.html",
    ]

    def test_the_signup_lists_share_one_status_badge(self):
        for path in self.SIGNUP_LISTS:
            with self.subTest(path=str(path)):
                self.assertIn("events/_status_badge.html", self.markup(path),
                              "this list hand-rolls the status badge again")
                self.assertNotIn(
                    'row.get_status_display tone="success"', self.body(path),
                    "this page decides the badge tone itself instead of going "
                    "through the fragment, so the two lists will drift apart")

    def test_the_other_two_status_mappings_stay_separate(self):
        """⚠️ The inverse guard, and the more important one. Three pages map
        status to a tone and **all three differ on purpose** — the colour answers
        whatever that page is asking ("did they sign up" / "did they turn up" /
        "what should I do next"). Consolidating them is the obvious-looking
        cleanup, and its result is that a no-show shows up green on the
        attendance page. See the comment in _status_badge.html.
        """
        attendance = self.body(
            Path("events") / "templates" / "events" / "_attendance_row.html")
        self.assertIn('row.status == "attended"', attendance)
        self.assertIn('tone="warning"', attendance)
        self.assertNotIn("events/_status_badge.html", attendance)

        mine = self.body(
            Path("events") / "templates" / "events" / "my_participations.html")
        self.assertIn('tone="info"', mine)
        self.assertNotIn("events/_status_badge.html", mine)

    #: ⚠️ 报名那一份 2026-08-19 从 `event_signup.html` 挪到了
    #:    `_event_signup_body.html`（整页和右面板共用同一份正文）。守的东西没变 ——
    #:    换的是那段标记现在住在哪个文件里。指着壳子的话这条守卫会一直是绿的，
    #:    而它该看的那段标记已经不在那儿了。
    NON_FIELD_ERROR_CALLERS = [
        Path("core") / "templates" / "core" / "components" / "form_fields.html",
        Path("events") / "templates" / "events" / "_event_signup_body.html",
    ]

    def test_non_field_errors_are_drawn_by_one_fragment(self):
        """⚠️ The signup page cannot use form_fields.html — its consent branch
        splits the fields into a <fieldset> — so it copied the error box. Two
        boxes drift, and the drift shows up on the one screen a volunteer sees
        only when something has already gone wrong.
        """
        for path in self.NON_FIELD_ERROR_CALLERS:
            with self.subTest(path=str(path)):
                self.assertIn("core/components/_non_field_errors.html",
                              self.markup(path))
                self.assertNotIn(
                    "form.non_field_errors", self.body(path),
                    "this template renders the non-field errors itself instead "
                    "of including the fragment")

    def test_every_shared_fragment_says_why_it_exists(self):
        """⚠️ A fragment pulled out of two copies is indistinguishable from one
        that was always a component — until somebody inlines it back "because it
        is only used twice". The reason has to survive in the file.
        """
        for name, path in [
            ("_hero_backdrop", Path("core") / "templates" / "core" /
             "components" / "_hero_backdrop.html"),
            ("_non_field_errors", Path("core") / "templates" / "core" /
             "components" / "_non_field_errors.html"),
            ("_status_badge", Path("events") / "templates" / "events" /
             "_status_badge.html"),
        ]:
            with self.subTest(fragment=name):
                self.assertIn("{% comment %}", self.markup(path))
                self.assertIn("⚠️", self.markup(path))


class OverlaysLiveInTheTopLayerGuardTests(TestCase):
    """Lint-as-test: every overlay opens with dialog.showModal() (2026-08-09).

    🔴 The rule and the whole reason for it:

        `position: fixed` does **not** mean "relative to the viewport". If any
        ancestor has `transform`, `filter`, `contain` or `backdrop-filter`, that
        ancestor becomes the containing block and `inset: 0` fills **it**.

    This is not hypothetical — it had already happened twice, and neither
    instance was caught by a test or by review:

      · `modal.html` sat inside `.card`, and `.dark.has-hero .card` carries
        `backdrop-filter` for the glass look. In dark mode the change-password
        dialog was trapped inside the Login Information card with its form
        clipped. Light mode was fine, so it read as "sometimes".
      · `wall-lightbox` sat inside `.wall`, which also carries `backdrop-filter`.
        That one **looked** correct purely by coincidence — `.wall` happens to be
        `100dvh` and full width — while being one padding change away from
        silently shrinking, with `overflow: hidden` waiting to clip it.

    ⚠️ The second one is why "just don't nest overlays in filtered elements" is
       not an acceptable fix: it was wrong for three days while looking right,
       and the call site was innocent — the offending CSS lives in another file
       and applies only in one theme.

    `showModal()` puts the element in the **top layer**, whose containing block
    is always the viewport. That is the only position in the platform no
    ancestor can reach, which is why it is a rule here rather than a preference.
    """

    TEMPLATE_DIRS = [Path("core") / "templates", Path("events") / "templates",
                     Path("accounts") / "templates", Path("org") / "templates",
                     Path("gallery") / "templates", Path("contact") / "templates"]

    #: The one full-viewport overlay that is deliberately **not** a <dialog>.
    #:
    #: ⚠️ It is exempt because of a condition, not because it is old: the menu is
    #:    a direct child of <body>, so its only ancestors are <body> and <html> —
    #:    and `test_nothing_turns_html_or_body_into_a_containing_block` below
    #:    pins the thing that would break it. An exception whose safety condition
    #:    is itself guarded is not a hole; an unguarded one is.
    #:
    #: ⚠️ It is a sliding nav drawer with an entrance transition, and converting
    #:    it would risk that animation for no bug that exists today. If a third
    #:    overlay ever wants the same exemption, convert this one instead.
    MENU_EXEMPTION = "core/templates/core/components/_site_menu.html"

    # `fixed inset-0` / `fixed inset-y-0` followed by a NON-negative z-index.
    # ⚠️ The sign matters: `_hero_backdrop.html` is `fixed inset-0 -z-10`, a
    #    background painted *under* the content. It is not an overlay and must
    #    not trip this.
    FULLSCREEN_OVERLAY = re.compile(r"fixed\s+inset-(?:0|y-0)[^\"']*?\sz-\d")

    def templates(self):
        for base in self.TEMPLATE_DIRS:
            root = Path(settings.BASE_DIR) / base
            if not root.exists():
                continue
            for path in sorted(root.rglob("*.html")):
                yield path, path.read_text()

    def body(self, markup):
        return re.sub(r"\{%\s*comment\s*%\}.*?\{%\s*endcomment\s*%\}", "",
                      markup, flags=re.S)

    def test_the_two_overlays_are_dialogs_driven_by_x_dialog(self):
        for path, needle in [
            (Path("core") / "templates" / "core" / "components" / "modal.html",
             "modal"),
            (Path("gallery") / "templates" / "gallery" / "wall.html",
             "wall-lightbox"),
        ]:
            with self.subTest(path=str(path)):
                markup = (Path(settings.BASE_DIR) / path).read_text()
                # The class must sit on a <dialog> tag, not a <div>.
                self.assertRegex(
                    markup, rf"<dialog[^>]*class=\"{needle}\"",
                    f"the {needle} overlay is not a <dialog> — it will be "
                    "captured by any filtered ancestor")
                self.assertIn(
                    "x-dialog=", markup,
                    "this overlay does not go through the x-dialog directive, "
                    "so nothing guarantees it is opened with showModal()")

    def test_no_dialog_is_opened_with_the_open_attribute(self):
        """⚠️ The sharpest trap in this whole area, because it *looks* right.

        `<dialog open>` displays the element **in place** — it does not enter the
        top layer. So it renders, it is visible, and it is captured by exactly
        the ancestors `showModal()` would have escaped. A reviewer sees a
        `<dialog>` and moves on.
        """
        offenders = []
        for path, markup in self.templates():
            for tag in re.findall(r"<dialog[^>]*>", self.body(markup)):
                # ⚠️ Strip every attribute **value** before looking for a bare
                #    `open`. Without this the guard fails on its own subject:
                #    the lightbox is `x-dialog="open"` — the Alpine boolean is
                #    named `open` — and a plain \bopen\b matches inside that
                #    string. A guard that cannot tell an attribute name from an
                #    attribute value reports the correct code as broken.
                names_only = re.sub(r"=\s*(\"[^\"]*\"|'[^']*')", "=", tag)
                if re.search(r"\bopen\b", names_only):
                    offenders.append(str(path.relative_to(settings.BASE_DIR)))
        self.assertEqual(
            offenders, [],
            "a <dialog> carries the `open` attribute; that renders it in place "
            "instead of the top layer. Open it with showModal():\n"
            + "\n".join(offenders))

    def test_the_javascript_only_ever_calls_showmodal(self):
        source = (Path(settings.BASE_DIR) / "assets" / "js" / "app.js").read_text()
        code = re.sub(r"//[^\n]*", "", source)
        self.assertIn("showModal()", code,
                      "nothing opens a dialog into the top layer any more")
        # ⚠️ `this.open` is excluded on purpose and it is not a loophole: the
        #    lightbox's Alpine state is *called* `open` (`this.open = true` in
        #    show()), which is a plain boolean on a component, not a dialog
        #    element. Matching it flagged correct code. What must stay banned is
        #    assigning `.open` on an **element** reference — `dlg.open = true`.
        for pattern, why in [
            (r"\.show\(\)",
             "dialog.show() is non-modal and stays out of the top layer"),
            (r"setAttribute\(\s*[\"']open[\"']",
             "the open attribute does not enter the top layer"),
            (r"(?<!this)\.open\s*=\s*true",
             "assigning .open on an element does not enter the top layer"),
        ]:
            with self.subTest(pattern=pattern):
                self.assertIsNone(
                    re.search(pattern, code),
                    f"{why} — found {pattern!r} in assets/js/app.js")

    def test_no_new_fullscreen_overlay_avoids_dialog(self):
        """Any future overlay has to be a <dialog>; this is what says so."""
        found = set()
        for path, markup in self.templates():
            body = self.body(markup)
            for line in body.split("\n"):
                if self.FULLSCREEN_OVERLAY.search(line) and "<dialog" not in line:
                    found.add(str(path.relative_to(settings.BASE_DIR)))
        self.assertEqual(
            found, {self.MENU_EXEMPTION},
            "a full-viewport overlay is being drawn with `fixed inset-0` on "
            "something that is not a <dialog>. It will be captured by any "
            "ancestor carrying transform/filter/contain/backdrop-filter — which "
            "in this codebase means any `.card` or `.wall` in dark mode. Use "
            "core/components/modal.html, or open your own <dialog> with "
            "x-dialog.")

    def test_nothing_turns_html_or_body_into_a_containing_block(self):
        """The condition that keeps the site menu's exemption honest.

        ⚠️ The menu is safe only because <body> and <html> are plain. The day
        somebody puts a `filter` or a `transform` on either — a page-wide dim, a
        "shake on error" effect, a zoom — the menu silently starts positioning
        against it, and this test is the only thing standing there.
        """
        css = (Path(settings.BASE_DIR) / "assets" / "app.css").read_text()
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        dangerous = re.compile(
            r"^\s*(?:backdrop-filter|transform|filter|contain|perspective)\s*:")
        offenders = []
        # Every rule whose selector targets html/body/:root as the subject.
        for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
            selector, block = match.group(1).strip(), match.group(2)
            subject = selector.split(",")[-1].strip().split()[-1] if selector.split() else ""
            if subject not in ("html", "body", ":root"):
                continue
            for line in block.split(";"):
                if dangerous.match(line + ":") or dangerous.match(line):
                    prop = line.strip().split(":")[0]
                    if prop in ("backdrop-filter", "transform", "filter",
                                "contain", "perspective"):
                        offenders.append(f"{selector.strip()} → {prop}")
        self.assertEqual(
            offenders, [],
            "html/body gained a property that creates a containing block for "
            "fixed descendants. The site menu positions against the viewport "
            "only while this is not true — convert it to a <dialog> first:\n"
            + "\n".join(offenders))


class DialogsStayHiddenWhenClosedGuardTests(TestCase):
    """Lint-as-test: no author rule may override the UA's `display: none`.

    🔴 A `<dialog>` hides itself through the UA stylesheet's
    `dialog:not([open]) { display: none }`. **Author styles beat the UA
    stylesheet**, so a single `display: flex` on the overlay's own class pins it
    open forever.

    ⚠️ This is not hypothetical. It shipped on 2026-08-09, in the very commit
       that moved the overlays to `<dialog>`: `display: flex` was carried over
       verbatim from the `position: fixed` version (where it did the centring),
       without noticing it had picked up a second job on a `<dialog>`. The
       Memories wall vanished behind a permanently-open lightbox.

    ⚠️ And the verification at the time did not cover it: the browser check
       opened the overlay and measured it (correct), and never asked whether it
       was invisible while closed. **A check that only exercises the open state
       is blind to this entire class of bug** — which is why the rule is now
       here, where it costs nothing to run.
    """

    #: Properties that override the UA's `display: none` when set unconditionally.
    #: `display` is the whole story; the others are listed because they are the
    #: near misses somebody will reach for next.
    RISKY = ("display", "visibility", "opacity")

    def test_overlay_classes_scope_their_display_to_open(self):
        css = (Path(settings.BASE_DIR) / "assets" / "app.css").read_text()
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)

        # The classes that sit on a <dialog>. Kept in one place so a third
        # overlay has to be added here consciously.
        overlay_classes = ["modal", "wall-lightbox"]

        offenders = []
        for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
            selector, block = match.group(1).strip(), match.group(2)
            for name in overlay_classes:
                # Does this rule target the overlay itself (not a descendant)?
                targets = any(
                    part.strip().endswith(f".{name}")
                    for part in selector.split(","))
                if not targets or "[open]" in selector or ":modal" in selector:
                    continue
                for declaration in block.split(";"):
                    prop = declaration.split(":")[0].strip()
                    if prop in self.RISKY:
                        offenders.append(
                            f"{selector.strip()} → {prop} "
                            f"(scope it to .{name}[open])")
        self.assertEqual(
            offenders, [],
            "an author rule sets `display`/`visibility`/`opacity` on a <dialog> "
            "unconditionally. Author styles beat the UA stylesheet's "
            "`dialog:not([open]) { display: none }`, so the overlay will be "
            "visible even when closed:\n" + "\n".join(offenders))

    def test_the_wall_lightbox_still_gets_its_layout_when_open(self):
        """⚠️ The other half. Scoping to `[open]` fixes the bug and would also
        "fix" it by deleting the centring entirely — the photo would sit in the
        top-left corner and nothing would fail. Pin what the open state owes.
        """
        css = re.sub(r"/\*.*?\*/", "",
                     (Path(settings.BASE_DIR) / "assets" / "app.css").read_text(),
                     flags=re.S)
        match = re.search(r"\.wall-lightbox\[open\]\s*\{([^}]*)\}", css)
        self.assertIsNotNone(
            match, "the lightbox has no [open] rule, so it never becomes a "
                   "flex container and the photo will not be centred")
        block = match.group(1)
        for declaration in ("display: flex", "align-items: center",
                            "justify-content: center"):
            with self.subTest(declaration=declaration):
                self.assertIn(declaration, block)


def _without_comments(text):
    """The same text with whole-line comments dropped.

    Every check below matches on `key: value` lines, and the prose above them
    is full of the very strings being searched for — `branch`, `free`,
    `migrate`. Stripping first is cheaper than making six regexes clever.
    """
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#"))


class RenderBlueprintGuardTests(TestCase):
    """Lint-as-test: the deployment settings whose failure mode is silence.

    ⚠️ Everything in here shares one property, and it is the reason these are
       tests rather than a checklist in C3.5: **none of them produce an error
       message naming the real cause.** Deploying the wrong branch succeeds.
       A free database is deleted on its expiry date, not on a deploy. A
       pg_dump major version mismatch is discovered on the day the backup is
       needed. This project's own record says a rule that lives only in an
       acceptance list does not survive the next round (the `views.py` Sum rule
       and the four admin hooks both had to be promoted here later); these
       start here.

    ⚠️ Deliberately parsed with regexes rather than PyYAML. `yaml` is not in
       requirements.txt — it is in the environment only as somebody else's
       transitive dependency, and a guard that stops running when an unrelated
       package is dropped is worse than no guard, because it stays green.
    """

    @property
    def blueprint(self):
        return _without_comments(
            (Path(settings.BASE_DIR) / "render.yaml").read_text(encoding="utf-8"))

    @property
    def ci_workflow(self):
        return (Path(settings.BASE_DIR) / ".github" / "workflows" / "ci.yml"
                ).read_text(encoding="utf-8")

    @property
    def services(self):
        """The `services:` section, as {name: block text}."""
        section = self.blueprint.split("\nservices:\n", 1)[1].split("\ndatabases:\n")[0]
        blocks = {}
        for chunk in re.split(r"^  - type:", section, flags=re.M)[1:]:
            name = re.search(r"^    name:\s*(\S+)", chunk, flags=re.M)
            self.assertIsNotNone(name, f"a service has no name:\n{chunk[:200]}")
            blocks[name.group(1)] = chunk
        self.assertNotEqual(blocks, {}, "no services parsed out of render.yaml")
        return blocks

    def test_every_service_deploys_from_the_deploy_branch(self):
        """⚠️ The one in deploy-branch.yml's warning, made executable.

        `main` has no static/css/app.css — the products are built by CI and
        pushed to `deploy`. Pointing a service at `main` gives two failures,
        and the bad one is the quiet one: "merged to main but the site did not
        change", with CI green and Render reporting a successful deploy, since
        it did successfully deploy the older code.
        """
        for name, block in self.services.items():
            with self.subTest(service=name):
                self.assertEqual(
                    re.findall(r"^\s*branch:\s*(\S+)", block, flags=re.M),
                    ["deploy"],
                    f"{name} must deploy from the branch CI pushes products to")

    def test_nothing_runs_on_a_free_plan(self):
        """Two different disasters wearing one word.

        On the database it is data loss — Render deletes a free database when
        it expires, and during the pilot that database holds the foundation's
        real records. On the web service it is the foundation's first
        impression: a free instance sleeps after minutes of quiet and takes
        tens of seconds to wake, which reads as "this thing is broken" and is
        invisible from a development machine.
        """
        plans = re.findall(r"^\s*plan:\s*(\S+)", self.blueprint, flags=re.M)
        self.assertNotEqual(plans, [], "no plan: lines found — has the file moved?")
        self.assertNotIn("free", plans, f"a free plan is declared: {plans}")

    def test_the_production_postgres_matches_the_major_version_ci_runs(self):
        """⚠️ pg_dump refuses outright when its client is older than the server.

        So the backup cron — the piece most likely to drift from the server —
        is the piece this protects, and C3.6's restore drill is what would
        otherwise discover it, on the day it is needed. CI already pins the
        same number for a related reason (a cross-version difference makes CI
        green while a laptop is red); one number, one place to change it.
        """
        declared = re.search(
            r'^\s*postgresMajorVersion:\s*"(\d+)"', self.blueprint, flags=re.M)
        self.assertIsNotNone(declared, "render.yaml pins no Postgres major version")
        in_ci = re.search(r"image:\s*postgres:(\d+)", self.ci_workflow)
        self.assertIsNotNone(in_ci, "ci.yml runs no postgres service image")
        self.assertEqual(
            declared.group(1), in_ci.group(1),
            "production Postgres and the one CI tests against are different "
            "major versions")

    def test_the_pinned_python_matches_the_one_ci_runs(self):
        """C3.5's first bullet, kept true after the day it was checked.

        Render's default moves on its own schedule; `.python-version` is what
        stops it moving under this deployment. It is worth nothing if CI is
        meanwhile testing a different one.
        """
        pinned = (Path(settings.BASE_DIR) / ".python-version"
                  ).read_text(encoding="utf-8").strip()
        in_ci = re.search(r'python-version:\s*"([\d.]+)"', self.ci_workflow)
        self.assertIsNotNone(in_ci, "ci.yml pins no Python version")
        self.assertEqual(
            pinned, in_ci.group(1),
            "the deployment and CI would run different Python versions")

    def test_every_variable_the_settings_refuse_to_start_without_is_declared(self):
        """⚠️ `required=True` means the process does not start. Not degrade.

        That is the correct behaviour and it was chosen deliberately (falling
        back to local disk would make photographs vanish at the next deploy,
        which looks like a bug rather than a missing setting). The cost of
        choosing it is that a variable missing from this blueprint is a service
        that will not boot — and the first place it bites is not the web
        service but `build.sh`, whose collectstatic imports prod.py and stops
        with "Missing required environment variable" before collecting
        anything.

        ⚠️ Checked **per service**, not against the file as a whole. Whether
           the web service happens to name a variable says nothing about the
           purge cron, and the two failures are not equally visible: the web
           one is caught by the first person to open the site, while the cron
           one is a job that dies at 2am with nobody watching, and whose
           absence looks exactly like "there was nothing to purge".

        Which services must satisfy this is derived rather than listed: it is
        exactly those pulling in the `rolf-django` group, because that group is
        what carries DJANGO_SETTINGS_MODULE. The backup cron is deliberately
        not one of them — it is a shell script and a URL, so that it still runs
        on the day Django cannot start.
        """
        required = set()
        for path in sorted((Path(settings.BASE_DIR) / "config" / "settings").glob("*.py")):
            required |= set(re.findall(
                r'env\(\s*"([A-Z0-9_]+)"\s*,\s*required=True',
                path.read_text(encoding="utf-8")))
        self.assertNotEqual(
            required, set(), "no required=True variables found — has env() changed?")

        group = self.blueprint.split("\nservices:\n", 1)[0]
        shared = set(re.findall(r"^\s*- key:\s*(\S+)", group, flags=re.M))

        django_services = {
            name: block for name, block in self.services.items()
            if "fromGroup: rolf-django" in block
        }
        self.assertGreaterEqual(
            len(django_services), 2,
            "expected the web service and the purge cron to run Django")
        for name, block in django_services.items():
            with self.subTest(service=name):
                declared = shared | set(
                    re.findall(r"^\s*- key:\s*(\S+)", block, flags=re.M))
                self.assertEqual(
                    required - declared, set(),
                    f"{name} imports settings that refuse to start without "
                    f"these, and nothing gives them to it: "
                    f"{sorted(required - declared)}")

    def test_the_ci_step_that_imports_prod_settings_has_every_required_variable(self):
        """⚠️ The same rule as the blueprint, aimed at the other file that imports
        these settings — and this one has already bitten once.

        CI runs `collectstatic` under the production settings on purpose (it is
        the only place ManifestStaticFilesStorage is exercised before a deploy).
        The day prod.py grew the seven R2 variables, that step went red on a
        missing variable rather than on the thing it guards, **and stayed red
        for weeks** — found by a repo audit on 2026-08-09, not by anyone reading
        the failure. Adding email credentials repeated the setup exactly, so the
        rule stops living in a comment.
        """
        required = set()
        for path in sorted((Path(settings.BASE_DIR) / "config" / "settings").glob("*.py")):
            required |= set(re.findall(
                r'env\(\s*"([A-Z0-9_]+)"\s*,\s*required=True',
                path.read_text(encoding="utf-8")))

        workflow = self.ci_workflow
        step = workflow.split("DJANGO_SETTINGS_MODULE: config.settings.prod", 1)
        self.assertEqual(
            len(step), 2,
            "no CI step runs under the production settings any more — if that "
            "is deliberate, this guard has nothing left to protect")
        # From that line to the end of the step's env block.
        step = step[1].split("\n        run:", 1)[0]
        # Job-level env counts too: SECRET_KEY and DATABASE_URL live there.
        job_env = workflow.split("\n    steps:", 1)[0]
        declared = set(re.findall(r"^\s*([A-Z0-9_]+):", step, flags=re.M))
        declared |= set(re.findall(r"^\s*([A-Z0-9_]+):", job_env, flags=re.M))
        self.assertEqual(
            required - declared, set(),
            "the CI step importing prod.py is missing variables it refuses to "
            f"start without, so it will fail on those instead of on what it "
            f"checks: {sorted(required - declared)}")

    def test_the_purge_cron_points_at_the_same_buckets_as_the_web_service(self):
        """⚠️ The duplication Render's own rules force, watched.

        `sync: false` cannot be declared in an environment group, so the seven
        R2 variables are written out twice. The dangerous half is not omission
        — prod.py reads all seven at import and refuses to start, so a missing
        one is loud. It is divergence: a purge job aimed at a different bucket
        deletes the wrong objects and reports success.
        """
        def r2_keys(block):
            return sorted(set(re.findall(r"^\s*- key:\s*(R2_\S+)", block, flags=re.M)))

        services = self.services
        web = r2_keys(services["rolf-app"])
        purge = r2_keys(services["rolf-purge-event-images"])
        self.assertNotEqual(web, [], "the web service declares no R2 variables")
        self.assertEqual(
            web, purge,
            "the web service and the purge cron name different R2 variables; "
            "the purge would sweep a different bucket than the one uploads go to")

    def test_migrate_runs_once_before_the_cutover_and_not_during_the_build(self):
        """⚠️ Both halves, because either one alone can be satisfied wrongly.

        Moved out of build.sh on 2026-08-10 (revisions.md 三十三). Three
        services build on the same push, so a migrate in build.sh is three
        concurrent runs of one migration set; and a build-time migrate has
        already changed the database by the time a later step fails the deploy,
        which leaves the schema ahead of the code that is still serving.
        """
        build = _without_comments(
            (Path(settings.BASE_DIR) / "build.sh").read_text(encoding="utf-8"))
        self.assertNotIn(
            "migrate", build,
            "migrate belongs in render.yaml's preDeployCommand — in build.sh "
            "it runs once per service, concurrently")
        self.assertRegex(
            self.blueprint, r"preDeployCommand:.*migrate",
            "nothing runs migrations any more: they left build.sh and no "
            "preDeployCommand picked them up")

    def test_the_shell_scripts_are_executable(self):
        """render.yaml invokes build.sh as `./build.sh`; the backup image CMDs
        into backup.sh.

        Without the mode bit committed, every build stops at "permission
        denied" — on the platform, never on the machine where the file was
        written, because creating a file and running it locally are the same
        act there.
        """
        for relative in ("build.sh", "scripts/backup/backup.sh"):
            with self.subTest(script=relative):
                self.assertTrue(
                    os.access(Path(settings.BASE_DIR) / relative, os.X_OK),
                    f"{relative} has lost its executable bit "
                    f"(git tracks it as 100755)")

    # --- C3.6, the backup cron ------------------------------------------------

    @property
    def backup_script(self):
        """⚠️ Comments stripped, for the reason `_without_comments` was written
        — and this is the second time that reason has come up.

        The script's own prose explains the rules these tests enforce: "do not
        write `pg_dump | aws s3 cp -`", "this is not a Django management
        command". Scanning the raw text makes a guard that fails on the
        sentence describing the thing it forbids, which is both wrong and
        maddening: the fix would be to delete the explanation.
        """
        return _without_comments(
            (Path(settings.BASE_DIR) / "scripts" / "backup" / "backup.sh"
             ).read_text(encoding="utf-8"))

    def test_the_blueprint_points_at_a_dockerfile_that_is_really_there(self):
        """⚠️ This failure does not happen in this repository.

        A blueprint naming a missing dockerfilePath fails no test and no build
        — it fails the **sync**, which happens once, in a browser, possibly
        months from now, with an error that names a path rather than a reason.
        render.yaml sat in exactly that state between C3.5 and C3.6, and the
        only thing recording it was a warning in its own header.
        """
        for key in ("dockerfilePath", "dockerContext"):
            declared = re.search(rf"^\s*{key}:\s*(\S+)", self.blueprint, flags=re.M)
            self.assertIsNotNone(declared, f"no {key} in render.yaml")
            path = Path(settings.BASE_DIR) / declared.group(1).lstrip("./")
            with self.subTest(key=key):
                self.assertTrue(
                    path.exists(),
                    f"render.yaml's {key} points at {declared.group(1)}, which "
                    f"does not exist — the blueprint will not sync")

    def test_the_backup_image_pins_the_major_version_ci_runs(self):
        """⚠️ The mismatch that is discovered on the day it matters.

        pg_dump refuses outright when its client is older than the server, so
        the image this cron runs is the piece most likely to drift away from
        the database. The number lives in three places — this image, ci.yml,
        and the production database — and this is the one nobody can see from
        a dashboard.
        """
        dockerfile = (Path(settings.BASE_DIR) / "scripts" / "backup" / "Dockerfile"
                      ).read_text(encoding="utf-8")
        in_image = re.search(r"^FROM postgres:(\d+)", dockerfile, flags=re.M)
        self.assertIsNotNone(in_image, "the backup image is not built FROM postgres")
        in_ci = re.search(r"image:\s*postgres:(\d+)", self.ci_workflow)
        self.assertEqual(
            in_image.group(1), in_ci.group(1),
            "the pg_dump taking the backups and the Postgres the tests run "
            "against are different major versions")

    def test_the_backup_cron_is_given_every_variable_the_script_demands(self):
        """The Django services' check, in the shape a shell script needs.

        ⚠️ Of every service here this one's failure is the least visible: a job
           that dies at 2am with nobody watching, and whose absence looks
           exactly like a quiet night. Adding a variable to the script and
           forgetting the blueprint is a one-line commit away.
        """
        demanded = set(re.findall(
            r'^:\s*"\$\{([A-Z0-9_]+):\?', self.backup_script, flags=re.M))
        self.assertNotEqual(
            demanded, set(), "backup.sh no longer checks its inputs up front")
        declared = set(re.findall(
            r"^\s*- key:\s*(\S+)", self.services["rolf-backup"], flags=re.M))
        self.assertEqual(
            demanded - declared, set(),
            f"backup.sh refuses to start without these and the blueprint does "
            f"not supply them: {sorted(demanded - declared)}")

    def test_the_backup_needs_nothing_from_django(self):
        """The landing rule from phase-c.md, made executable.

        Backups have to run **on the day the application cannot start** — which
        is the day the settings, the dependencies or the migrations are what is
        broken. The moment this script reaches for any of that it inherits all
        of those failure modes, and it inherits them quietly: it keeps working
        right up until the day it is needed.
        """
        for forbidden in ("manage.py", "django"):
            with self.subTest(token=forbidden):
                self.assertNotIn(
                    forbidden, self.backup_script.lower(),
                    "the backup script has picked up a dependency on Django")

    def test_the_dump_is_a_file_on_disk_before_any_of_it_is_uploaded(self):
        """⚠️ `pg_dump | aws s3 cp -` is the tempting one-liner, and it is wrong.

        Streaming saves a temporary file and costs the only chance to look at
        the archive. Worse: a pg_dump that dies halfway leaves a **truncated
        object already in the bucket** — the cron goes red, but that object is
        now the newest one there, and a restore would reach for exactly it.
        """
        self.assertNotIn(
            "| aws", self.backup_script,
            "the dump is being piped straight into the uploader; it has to land "
            "on disk and be checked first")
        self.assertIn(
            "pg_restore --list", self.backup_script,
            "nothing proves the archive is readable before it is uploaded")

    def test_the_backup_script_can_delete_nothing(self):
        """Expiry belongs to the bucket's lifecycle rule, not to this file.

        ⚠️ A code path that deletes backups and a code path that writes them,
           living in one file, is one wrong condition away from the deletion
           running against what the other half just uploaded. The key this cron
           holds does not need delete permission either.
        """
        for forbidden in ("s3 rm", "delete-object", "rb s3://"):
            with self.subTest(command=forbidden):
                self.assertNotIn(
                    forbidden, self.backup_script,
                    "the backup script has grown a way to delete objects — "
                    "expiring old dumps is the bucket lifecycle rule's job")


class ProductionHardeningGuardTests(TestCase):
    """Lint-as-test: C3.4's settings, and the two that bite when they disagree.

    ⚠️ Read from the source of config/settings/prod.py rather than by importing
       it. Importing means every R2 and mail variable has to exist in the
       environment of whoever runs the tests — and a guard that only runs where
       the production credentials are is a guard that never runs.

    ⚠️ These are not "did somebody delete a line". Each one has a failure that
       arrives dressed as something else: an infinite redirect loop reads as a
       broken deploy, a missing CSRF origin reads as a broken form, and HSTS
       set bravely on day one reads as nothing at all until the day it cannot
       be undone.
    """

    @property
    def source(self):
        return (Path(settings.BASE_DIR) / "config" / "settings" / "prod.py"
                ).read_text(encoding="utf-8")

    def test_ssl_redirect_never_ships_without_the_proxy_header(self):
        """⚠️ The pair, not either one. Render terminates TLS at its proxy, so
        Django sees http and redirects to https, which arrives as http again —
        a redirect loop on every page, with nothing in the message about the
        setting that caused it.
        """
        redirect = re.search(r"^SECURE_SSL_REDIRECT\s*=\s*(\S+)", self.source, re.M)
        self.assertIsNotNone(redirect, "SECURE_SSL_REDIRECT is gone from prod.py")
        if redirect.group(1) == "True":
            self.assertRegex(
                self.source,
                r'SECURE_PROXY_SSL_HEADER\s*=\s*\(\s*"HTTP_X_FORWARDED_PROTO"\s*,\s*"https"\s*\)',
                "SECURE_SSL_REDIRECT is on with no SECURE_PROXY_SSL_HEADER: "
                "every request will redirect to itself for ever",
            )

    def test_the_cookies_and_the_frame_option_are_set(self):
        for line in ("SESSION_COOKIE_SECURE = True", "CSRF_COOKIE_SECURE = True",
                     'X_FRAME_OPTIONS = "DENY"'):
            with self.subTest(setting=line):
                self.assertIn(line, self.source)

    def test_csrf_origins_are_derived_from_allowed_hosts(self):
        """⚠️ C5 hangs a custom domain on this app, and these two have to change
        together. A second hand-written list is a second thing to forget, and
        forgetting it is invisible: the site opens, every POST is rejected, and
        it reads as "the form is broken".
        """
        self.assertRegex(
            self.source,
            r"CSRF_TRUSTED_ORIGINS\s*=\s*\[[^]]*for host in ALLOWED_HOSTS",
            "CSRF_TRUSTED_ORIGINS is no longer derived from ALLOWED_HOSTS",
        )

    def test_the_hsts_silence_expires_with_the_short_value(self):
        """⚠️ The one that must not become permanent. Silencing W005/W021 is how
        a short HSTS passes `check --deploy`; tying the silence to the value
        means C5 raising the value brings both warnings back on its own, asking
        for exactly the settings C5 exists to turn on. A hardcoded list would
        stay silent for ever, and nothing would ever say so.
        """
        self.assertRegex(
            self.source,
            r"SILENCED_SYSTEM_CHECKS\s*=\s*\(\s*\n?\s*\[[^]]*\]\s*if\s+_HSTS_IS_STILL_PROVISIONAL",
            "the HSTS silence is no longer conditional on the value being short",
        )
        self.assertIn("_HSTS_IS_STILL_PROVISIONAL = SECURE_HSTS_SECONDS < 31536000",
                      self.source)

    def test_error_mail_is_not_wired_to_the_admins(self):
        """⚠️ Not an omission. Error mail and the volunteers' password resets
        come out of one daily allowance, and the day something breaks is the day
        it breaks repeatedly — spending the allowance on notifying us about a
        fault, while the people locked out of their accounts get nothing.
        """
        # Quoted: the prose above the LOGGING block explains the choice and
        # names it, so a bare substring search would convict its own reason.
        self.assertNotRegex(self.source, r'["\']mail_admins["\']')

    def test_sentry_is_told_not_to_collect_personal_data(self):
        # This database holds minors' names, dates of birth and addresses.
        self.assertIn("send_default_pii=False", self.source)
        self.assertNotIn("send_default_pii=True", self.source)


class CheckDeploymentCommandTests(TestCase):
    """The report somebody pastes into a chat window to ask "did I do it right?"

    ⚠️ The first test is the one that matters, and it is a security test rather
       than a formatting one. This command exists because the alternative — a
       screenshot of the dashboard — carries the SMTP password and the object
       store's keys in the picture, so the act of asking for help is what leaks
       them. A report that prints a secret is worse than no report: it is the
       same leak with a friendlier interface.
    """

    def report(self, **overrides):
        out = io.StringIO()
        with override_settings(**overrides):
            call_command("check_deployment", stdout=out)
        return out.getvalue()

    SECRETS = {
        "EMAIL_HOST_PASSWORD": "xkeysib-the-actual-smtp-password",
        "EMAIL_HOST_USER": "9f2c1a@smtp-brevo.example",
    }

    def test_no_secret_reaches_the_output(self):
        text = self.report(EMAIL_HOST="smtp.example.invalid", **self.SECRETS)
        for name, value in self.SECRETS.items():
            with self.subTest(name=name):
                self.assertNotIn(value, text)
                # ⚠️ Not even a prefix. Enough of a key to recognise which key
                #    it is, is enough to be worth rotating after pasting it.
                self.assertNotIn(value[:8], text)

    def test_a_secret_is_reported_by_its_shape_so_a_typo_is_still_visible(self):
        # "set, 32 characters" answers "did the whole thing get pasted" without
        # answering "what is it" — the only question the reader actually has.
        text = self.report(EMAIL_HOST="smtp.example.invalid", **self.SECRETS)
        self.assertIn(f"set, {len(self.SECRETS['EMAIL_HOST_PASSWORD'])} characters", text)

    def test_an_empty_credential_is_called_out(self):
        text = self.report(EMAIL_HOST="smtp.example.invalid", EMAIL_HOST_PASSWORD="")
        self.assertIn("EMAIL_HOST_PASSWORD", text)
        self.assertIn("(empty)", text)

    def test_a_sender_at_the_providers_domain_is_flagged(self):
        # ⚠️ It works, which is why nothing else will ever mention it. The cost
        #    lands on the day the provider changes: a new From: address for
        #    every recipient, and the sending reputation left behind.
        text = self.report(EMAIL_HOST="smtp.example.invalid",
                           DEFAULT_FROM_EMAIL="rolf@brevosend.example")
        self.assertIn("provider's own domain", text)

    def test_a_sender_at_the_foundations_own_domain_is_not_flagged(self):
        text = self.report(EMAIL_HOST="smtp.example.invalid",
                           DEFAULT_FROM_EMAIL="noreply@riveroflife.example")
        self.assertNotIn("provider's own domain", text)

    def test_a_port_that_disagrees_with_the_encryption_is_flagged(self):
        # ⚠️ The failure this one is for does not raise: 465 expects TLS from
        #    the first byte, 587 starts in the clear and upgrades. Ask for the
        #    wrong one and the connection hangs until it times out.
        text = self.report(EMAIL_HOST="smtp.example.invalid",
                           EMAIL_PORT=465, EMAIL_USE_SSL=False, EMAIL_USE_TLS=True)
        self.assertIn("465 with STARTTLS", text)

    def test_a_development_machine_is_not_reported_as_broken(self):
        # ⚠️ Every "must be True in production" line is false on a laptop, and
        #    correctly so. A report that is always red is a report nobody reads
        #    — the same failure C3.4's silenced checks were written to avoid.
        text = self.report()
        self.assertNotIn("thing(s) to fix", text)
        self.assertIn("development machine", text)

    def test_an_empty_dictionary_table_is_called_out(self):
        # ⚠️ The failure this catches is not an error: a coordinator opens
        #    "publish an event", the event-type dropdown is empty, and the page
        #    simply cannot be completed. Nothing is logged anywhere.
        text = self.report()
        self.assertIn("EventType", text)
        self.assertIn("empty, so the form that needs it", text)

    def test_the_role_check_still_asks_for_one_of_the_foundations_own(self):
        """⚠️ This threshold had nothing watching it until 2026-08-26.

        The number is "what the migrations ship, plus one", and the point is to
        catch a foundation that has entered none of its own participation
        roles. Set equal to what ships, it passes on an empty installation
        while still looking like a check — and it silently became that on the
        day a second catch-all row started shipping (0018). So the assertion is
        about the relationship, not about the literal 3.
        """
        from events.models import ParticipationRole

        # ⚠️ Read this table's own line, not the whole report. "empty, so the
        #    form that needs it" is the wording **every** dictionary shares, and
        #    the others are genuinely empty here — asserting on the report as a
        #    whole passes for the wrong reason in one direction and fails for
        #    the wrong reason in the other. Found by writing it the loose way.
        def role_line(text):
            return next(line for line in text.splitlines()
                        if "ParticipationRole" in line)

        shipped = ParticipationRole.objects.count()
        # This database came from the migrations alone, so everything in it is
        # what ships. A threshold equal to that would pass here — and a check
        # that passes on an empty installation is a check that checks nothing.
        self.assertIn("warn", role_line(self.report()))

        ParticipationRole.objects.create(code="lifting", name="Lifting")
        after = role_line(self.report())
        self.assertIn(f"{shipped + 1} rows", after)
        self.assertIn("ok", after)

    def test_a_deployment_whose_only_account_is_the_superuser_is_called_out(self):
        # ⭐ The easiest step in C3.5 to skip, and it leaves no trace: everything
        #    works, and "nobody runs the foundation from a superuser" is broken
        #    the same day.
        get_user_model().objects.create_superuser(
            email="rescue@example.invalid", password="x")
        text = self.report()
        self.assertIn("foundation staff (not superuser)", text)
        self.assertIn("the only way in is the superuser", text)

    def test_a_foundation_account_that_exists_is_not_called_out(self):
        from django.contrib.auth.models import Group

        user = get_user_model().objects.create_user(
            email="office@example.invalid", password="x", is_staff=True)
        # get_or_create: post_migrate already wires this group up (that is
        # where its permission list lives), so creating it here is a duplicate.
        group, _ = Group.objects.get_or_create(name="foundation_admin")
        user.groups.add(group)
        self.assertNotIn("the only way in is the superuser", self.report())

    def test_a_deployment_carrying_demo_logins_is_told_which_ones(self):
        # ⚠️ The one line here that is a credential leak rather than a
        #    misconfiguration: seed_demo's accounts share a password printed in
        #    this repository, and boss@ is in foundation_admin. Naming them is
        #    the point — the reader has to go and delete exactly these.
        get_user_model().objects.create_user(
            email="boss" + DEMO_ADDRESS_SUFFIX, password="x")
        text = self.report()
        self.assertIn("boss" + DEMO_ADDRESS_SUFFIX, text)
        self.assertIn("printed in this repository", text)

    def test_on_a_deployment_a_leftover_demo_login_counts_as_a_fault(self):
        # Being mentioned is not enough: this has to reach the list at the
        # bottom, which is the part somebody actually reads back.
        get_user_model().objects.create_user(
            email="boss" + DEMO_ADDRESS_SUFFIX, password="x")
        with mock.patch.dict(
                os.environ, {"DJANGO_SETTINGS_MODULE": "config.settings.prod"}):
            text = self.report()
        self.assertIn(f"logins at {DEMO_ADDRESS_SUFFIX}: demo accounts", text)

    def test_a_development_machine_is_not_convicted_of_its_own_demo_data(self):
        # ⚠️ Demo accounts on a laptop are the point of seed_demo, not a fault.
        #    Without the same downgrade every other line gets, this check would
        #    make the local report permanently red — and a report that is always
        #    red is the failure mode C3.4 was written to avoid.
        get_user_model().objects.create_user(
            email="boss" + DEMO_ADDRESS_SUFFIX, password="x")
        self.assertNotIn("thing(s) to fix", self.report())

    def test_a_database_with_no_demo_logins_says_so(self):
        # Said out loud rather than left blank: "the line is missing" and "the
        # line is clean" have to look different, or the reset cannot be checked.
        self.assertIn(f"logins at {DEMO_ADDRESS_SUFFIX}", self.report())
        self.assertIn("none", self.report())

    def test_the_live_send_reports_a_refusal_instead_of_raising(self):
        # The command's whole job is to answer; a traceback answers nothing and
        # a management command that crashes reads as "the tool is broken".
        out = io.StringIO()
        with override_settings(
                EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
                EMAIL_HOST="127.0.0.1", EMAIL_PORT=1):
            call_command("check_deployment", send_to="x@example.invalid", stdout=out)
        text = out.getvalue()
        self.assertIn("Live send", text)
        self.assertIn("thing(s) to fix", text)

    def test_a_live_send_that_works_says_accepted_is_not_delivered(self):
        # ⭐ The sentence that stops a green line from being read as "done".
        # A provider accepting a message says nothing about a spam folder, and
        # C3.3's acceptance criterion is explicitly about the folder.
        out = io.StringIO()
        with override_settings(
                EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
            call_command("check_deployment", send_to="x@example.invalid", stdout=out)
        self.assertIn("accepted is not delivered", out.getvalue())


class HealthCheckGuardTests(TestCase):
    """Lint-as-test: the health check path, in the three files that must agree.

    ⚠️ This class exists because of a failed deploy on 2026-08-17, and the
       reason it was worth a guard is the error message: **"Timed out after
       waiting for internal health check to return a successful response
       code"**. That sentence describes an application which is dead or slow.
       The application was neither — it was answering 301, because the platform
       checks the instance directly over plain HTTP (no X-Forwarded-Proto) and
       SECURE_SSL_REDIRECT had just been turned on. Nothing in the failure named
       a setting, a path, or a file.

    Three files have to agree, and two of them fail silently on their own:
    the URLconf (a 404 reads as unhealthy), prod.py's SECURE_REDIRECT_EXEMPT
    (a 301 reads as unhealthy) and render.yaml's healthCheckPath.
    """

    @property
    def blueprint(self):
        return (Path(settings.BASE_DIR) / "render.yaml").read_text(encoding="utf-8")

    @property
    def prod_settings(self):
        return (Path(settings.BASE_DIR) / "config" / "settings" / "prod.py"
                ).read_text(encoding="utf-8")

    def test_the_blueprint_checks_the_path_the_code_serves(self):
        declared = re.search(r"^\s*healthCheckPath:\s*(\S+)", self.blueprint, re.M)
        self.assertIsNotNone(declared, "render.yaml declares no healthCheckPath")
        self.assertEqual(
            declared.group(1).strip("/"), HEALTH_PATH.strip("/"),
            "render.yaml points the health check somewhere other than the view "
            "that answers it — the platform will restart a healthy instance")

    def test_the_health_path_answers_without_touching_the_database(self):
        # ⚠️ The database must not be able to take the site down through this
        #    route. Render restarts an instance whose check stops answering, so
        #    a hiccup would kill every instance rather than showing a slow page.
        with self.assertNumQueries(0):
            response = self.client.get(f"/{HEALTH_PATH}")
        self.assertEqual(response.status_code, 200)

    def test_the_health_path_is_exempt_from_the_ssl_redirect(self):
        """⭐ The one that would have caught the failed deploy.

        Exercised through SecurityMiddleware directly rather than the test
        client: the client's handler builds its middleware once and caches it,
        so an override of SECURE_SSL_REDIRECT would be read too late and this
        test would pass without testing anything.
        """
        exempt = re.search(r"^SECURE_REDIRECT_EXEMPT\s*=\s*(.+)$",
                           self.prod_settings, re.M)
        self.assertIsNotNone(
            exempt, "prod.py exempts nothing from the SSL redirect, so the "
                    "health check will be answered with a 301")
        with override_settings(
                SECURE_SSL_REDIRECT=True, ALLOWED_HOSTS=["testserver"],
                SECURE_REDIRECT_EXEMPT=[rf"^{HEALTH_PATH}?$"]):
            middleware = SecurityMiddleware(lambda request: HttpResponse("ok"))
            plain_http = RequestFactory()
            # The platform's request: no X-Forwarded-Proto anywhere on it.
            self.assertEqual(
                middleware(plain_http.get(f"/{HEALTH_PATH}")).status_code, 200)
            # ⚠️ The other half. Exempting the health path also "fixes" this by
            #    exempting everything, and then nothing on the site is forced on
            #    to HTTPS while every test still passes.
            self.assertEqual(middleware(plain_http.get("/")).status_code, 301)
            self.assertEqual(
                middleware(plain_http.get("/events/")).status_code, 301)
