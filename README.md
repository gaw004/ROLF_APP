# ROLF_APP

A volunteer- and resource-management web application for a non-profit foundation:
who the people are, what posts they hold, what they turn up to, and what money
comes in.

A single Django application with the Django admin as its interface — no separate
frontend, no paid SaaS, no microservices. Everything lives in one ordinary
Postgres database that a `pg_dump` can carry away whole.

**Start here:** [`docs/planning/goal.md`](docs/planning/goal.md) — the entry point
and the index: what we are building, the current priority, and where everything
else lives. From there:

- [`docs/planning/decisions/`](docs/planning/decisions/README.md) — every
  significant decision and why it was made, one file per decision (D1–D22).
  **Code comments that say "see goal.md D9" mean D9 in there** — the numbers are
  the stable reference.
- [`docs/planning/phase-b.md`](docs/planning/phase-b.md) — the models and
  implementation notes for the phase in progress, and
  [`docs/planning/02-roadmap.md`](docs/planning/02-roadmap.md) — its step-by-step
  plan.
- [`docs/planning/progress.md`](docs/planning/progress.md),
  [`deferred.md`](docs/planning/deferred.md),
  [`revisions.md`](docs/planning/revisions.md) — what is done, what is
  deliberately postponed, and why decisions changed.

## Requirements

- Python 3.14
- PostgreSQL 18 (`brew install postgresql@18`)

## Getting it running

```bash
git clone <this repo> && cd ROLF_APP

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Start Postgres and create the database:

```bash
brew services start postgresql@18
createdb rolf_dev
```

Create your environment file:

```bash
cp .env.example .env
```

Then generate a secret key and paste it into `.env` as `DJANGO_SECRET_KEY`:

```bash
python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

Also set `DATABASE_URL` in `.env` to your local user, e.g.
`postgres://yourname@localhost:5432/rolf_dev`.

`.env` is never committed. The production key is a different key, generated on
the deployment platform and kept only in its environment variables.

Build the schema and an admin account:

```bash
python manage.py migrate          # also seeds ~7,900 ISO 639-3 languages
python manage.py createsuperuser
python manage.py runserver
```

Then build something to click through:

```bash
python manage.py seed_demo        # refuses to run with DEBUG off; --force overrides
```

It prints the accounts it made and their shared password. There are seven, and
the awkward ones are deliberate: a volunteer with no email and no phone, one
with no birth date on file, a minor reachable only through an emergency
contact, and an event role nobody signed up for. Those are the branches the
acceptance walk checks, and they are the ones that quietly do not exist if you
build the data by hand.

Two interfaces now, and the split matters:

- **http://127.0.0.1:8000/admin/** — for staff. Dictionary tables, contacts,
  posts, tenures. Volunteers are refused it outright, not merely not linked to it.
- **http://127.0.0.1:8000/events/** — for everybody. Register, browse published
  events, sign up, see your own hours; a ministry's admins publish events, open
  roles, check people in, notify signups and read the statistics. Plain Django
  templates with no styling at all in this phase — the acceptance test is
  "clickable, usable, permissions correct".

Authority over a ministry is a row in `org.MinistryRole`, never a Django Group:
Django's permissions are global, and "the food pantry's admin" is not. Every
judgement about it is in `org/permissions.py` and nowhere else. See `goal.md` D20.

## Tests

```bash
python manage.py test
```

Day to day, reuse the test database instead of rebuilding it:

```bash
python manage.py test --keepdb
```

Every fresh test database re-runs the language seed migration, which inserts
about 7,900 rows. At 27 tests that is roughly 1.4s versus 0.8s per run — small,
but it is fixed overhead paid on every single run, and it grows with the number
of migrations rather than with the number of tests. A suite that is annoying to
run stops being run. Drop the flag after changing migrations, or the schema in
the reused database will be stale.

One catch: `--keepdb` leaves `test_rolf_dev` behind, so the next plain
`manage.py test` stops to ask whether it may delete it — which fails outright
when nothing is there to answer, such as in CI. Either keep using `--keepdb`,
pass `--noinput`, or `dropdb test_rolf_dev` once.

## Layout

```
config/settings/     base.py + dev.py + prod.py; secrets come from the environment
core/                what every app shares (TimeStampedModel)
accounts/            the custom User model, optionally linked to a Contact
contact/             people and organizations in one table, plus their relationships
org/                 ministries, posts, tenures, and who administers what
events/              events, the roles they open, signups, hours, notifications
core/notifications/  delivery adapters (console / locmem / email / Novu)
docs/planning/       goal.md (entry point), decisions/ (D1-D22), phase-b.md,
                     0N-roadmap.md (per-phase steps), progress/deferred/revisions
```

`DJANGO_SETTINGS_MODULE` defaults to `config.settings.dev`. Production sets it to
`config.settings.prod`.

## Conventions worth knowing before you change anything

- **Business rules belong in database constraints.** `Model.clean()` does not run
  on `save()`, so a Python-only rule is bypassed by `objects.create()` and
  `bulk_create()`. Constraints hold on every write path. See `goal.md` D9.
- **A constraint and its `clean()` are a pair.** The constraint enforces; the
  `clean()` only decides which form field turns red. Both carry a comment
  pointing at the other — change one, change both. See `goal.md` D14.
- **Categories that may change are database tables, not Python enums**, so they
  can be edited in the admin without a code change or a migration. See `goal.md` D5.
- **New models get tests in the same commit.** Not a style preference: the tests
  are the only reason it is safe to refactor later.
- **Some tests are lint.** `core/tests.py` greps the whole project for rules no
  linter knows: nobody computes "today" outside `core/timeutils`, business logic
  never imports the admin, only `org/services.py` walks the reporting chain,
  only `org/permissions.py` judges authority, views hold no arithmetic, delivery
  backends know nothing about people. They scan themselves too, so never spell a
  forbidden pattern out in a comment — that has caught four of them so far.
