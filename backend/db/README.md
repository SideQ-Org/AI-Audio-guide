# Durable layer — dev runbook (accounts & walk history)

Phase 2 of `ACCOUNTS_DESIGN.md`: the durable Postgres layer (`users` / `identities` /
`walks` / `walk_events`), **separate** from the ephemeral session store. Guest mode =
this layer untouched (`database_url` empty → `accounts_enabled()` is False, nothing runs).

Nothing here is wired into the live tour yet — that's phase 3 (sid↔user_id) and phase 4
(history writes). This is schema + repository + migrations only.

## Install

```bash
.venv\Scripts\python -m pip install -e ".[accounts]"   # sqlalchemy, alembic, asyncpg, aiosqlite, pyjwt
```

## Local dev DB — Supabase CLI (recommended, §9a/§11.5)

Brings up the same Postgres + Auth stack as prod, locally in docker:

```bash
supabase init          # once, in repo root — creates supabase/ config
supabase start         # boots local Postgres (:54322) + Auth + Studio
```

Point the backend at it and apply the schema + RLS:

```bash
# backend/.env  (or the shell env)
DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/postgres

.venv\Scripts\python -m alembic upgrade head          # create tables (design §4)
psql "postgresql://postgres:postgres@127.0.0.1:54322/postgres" -f db/rls.sql   # Row-Level Security
```

`supabase stop` to tear down. Cloud project is only needed at phase 7 (real Google/Apple
consoles); everything up to REST /walks is buildable and testable locally without it.

## Migrations

```bash
.venv\Scripts\python -m alembic upgrade head     # apply
.venv\Scripts\python -m alembic downgrade base   # roll back
.venv\Scripts\python -m alembic revision -m "msg" --autogenerate   # new migration (after model edits)
```

`alembic/env.py` reads the URL from `settings.database_url`, so migrations always target
whatever the app targets (local stack, cloud, or a throwaway SQLite file for a smoke check:
`DATABASE_URL=sqlite+aiosqlite:///./tmp.db`).

## Tests

`tests/test_accounts_repo.py` runs the repository CRUD + cross-user isolation against
in-memory SQLite (no Postgres needed). It `importorskip`s SQLAlchemy, so the base offline
gate (`.[dev,stt]`, without the `accounts` extra) stays green by skipping it; with the extra
installed it runs as part of `pytest -q`.

## RLS note

`db/rls.sql` compares `walks.user_id` to `auth.uid()`. That works because a user's PK is
seeded from the Supabase JWT `sub` (`repository.get_or_create_user(user_id=...)`), so our
`users.id == auth.users.id`. History INSERTs are done by the backend under the service-role
key (bypasses RLS); end users only ever read their own rows.
