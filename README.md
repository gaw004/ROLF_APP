# ROLF_APP

A volunteer- and resource-management web application for a non-profit foundation:
who the people are, what posts they hold, what they turn up to, and what money
comes in.

A single Django application with the Django admin as its interface — no separate
frontend, no paid SaaS, no microservices. Everything lives in one ordinary
Postgres database that a `pg_dump` can carry away whole.

**Start here:** [`docs/planning/goal.md`](docs/planning/goal.md) is the single
source of truth — what we are building, every significant decision and why it was
made, and where the work currently stands.
[`docs/planning/01-roadmap.md`](docs/planning/01-roadmap.md) holds the
implementation steps for the phase in progress.

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

The admin is at http://127.0.0.1:8000/admin/ — that is the whole interface for
now, by design.

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

## Layout

```
config/settings/     base.py + dev.py + prod.py; secrets come from the environment
core/                what every app shares (TimeStampedModel)
accounts/            the custom User model, optionally linked to a Contact
contact/             people and organizations in one table, plus their relationships
docs/planning/       goal.md (authoritative) and the current phase's roadmap
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
