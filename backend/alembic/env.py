"""Alembic environment — async, wired to the app's settings + ORM metadata.

Reads the DB URL from ``settings.database_url`` (not alembic.ini) so a single knob
drives both the app and its migrations. Runs against Postgres (Supabase local/cloud)
in the normal case; SQLite is used only by the offline tests, which build the schema
straight from ``Base.metadata`` and don't invoke Alembic.
"""

from __future__ import annotations

import asyncio

from alembic import context
from app.config import settings
from app.services.accounts.db import get_engine
from app.services.accounts.models import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    url = settings.database_url
    if not url:
        raise RuntimeError(
            "database_url is not set — set it (Supabase DB URL / local stack) before "
            "running migrations"
        )
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(
        connection=connection, target_metadata=target_metadata, compare_type=True
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    # Reuse the app's engine builder so migrations get the SAME connection handling
    # as the running server — in particular the Supabase transaction-pooler fix
    # (NullPool + unique prepared-statement names) that stops asyncpg hanging.
    _url()  # fail fast with a clear message if database_url is unset
    engine = get_engine()
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
