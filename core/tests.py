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
    WARN_ON_NOTE = re.compile(r"⚠️[ \t]*(?=\*\*(?:20\d\d-\d\d-\d\d|原文|原方案|原来|本条|本节|整节))")
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
                    problems.append(f"{path}:{number}  ⚠️ on a revision note — it is history, not a trap")
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
