"""Project-wide guards. These live in core because they police every app.

Most of what follows is lint dressed up as tests — the pattern this project
keeps reaching for when a rule has to hold everywhere and no linter enforces it
(migration guard, D16 time, D18 layering, D14 mappings, org-tree traversal).
"""

import colorsys
import datetime
import re
from pathlib import Path
from unittest import mock

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse

from core.constraints import CONSTRAINT_FIELD
from core.models import HomePage
from core.palette import ramp_from, relative_luminance
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
            contact_type=Contact.ContactType.INDIVIDUAL, legal_last_name="Alice")
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

        user = get_user_model().objects.create_user(username="nobody", password="x")
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
        user = get_user_model().objects.create_user(username="v", password="x")
        self.client.force_login(user)
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "core/home.html")

    def test_the_menu_offers_signing_in_to_a_stranger(self):
        page = self.client.get(reverse("home")).content.decode()
        self.assertIn(reverse("accounts:login"), page)
        self.assertIn(reverse("accounts:register"), page)

    def test_the_menu_offers_the_volunteer_pages_once_signed_in(self):
        user = get_user_model().objects.create_user(username="v", password="x")
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
        user = get_user_model().objects.create_user(username="v", password="x")
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
