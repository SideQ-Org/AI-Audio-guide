"""Async engine + session factory for the durable layer.

Lazy and optional: the engine is built on first use from ``settings.database_url``.
When that is empty (the MVP/guest default) ``accounts_enabled()`` is False and nothing
here is touched — the live tour never depends on a database being present.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def accounts_enabled() -> bool:
    """True when a durable store is configured. Guards every history write so guest
    mode (no database_url) is a pure no-op."""
    return bool(settings.database_url)


def _is_transaction_pooler(url: str) -> bool:
    """True for a Supabase Supavisor (a.k.a. transaction pooler, port 6543) URL.

    The pooler multiplexes many clients over a small set of server backends, so
    asyncpg's cached, numerically-named prepared statements collide across backends
    and a call **hangs** (a raw single-connection asyncpg run never reuses a prepared
    statement across backends — which is exactly why "raw driver works, SQLAlchemy
    hangs"). Detect by host/port so a direct/dedicated or local connection keeps
    normal pooling.
    """
    return "pooler.supabase.com" in url or ":6543" in url


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        url = settings.database_url
        if not url:
            raise RuntimeError("database_url is not set — accounts layer is disabled")
        if url.startswith("sqlite"):
            _engine = create_async_engine(
                url, echo=settings.db_echo, pool_pre_ping=True, future=True
            )
            # SQLite ignores FK constraints unless asked per-connection; enable so
            # ON DELETE CASCADE works like Postgres (dev smoke / a file-backed run).
            from sqlalchemy import event

            @event.listens_for(_engine.sync_engine, "connect")
            def _fk_on(dbapi_conn, _rec):  # pragma: no cover - trivial pragma hook
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()
        elif _is_transaction_pooler(url):
            # Supabase transaction pooler: the documented SQLAlchemy-asyncpg fix
            # (dialect docs, "Prepared Statement Name with PGBouncer") — NullPool so
            # Supavisor owns pooling and we never hold/reuse a server connection, plus
            # per-prepare unique statement names and a disabled asyncpg statement cache
            # so nothing is reused across pooled backends.
            from sqlalchemy.pool import NullPool

            _engine = create_async_engine(
                url,
                echo=settings.db_echo,
                poolclass=NullPool,
                connect_args={
                    "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__",
                    "statement_cache_size": 0,
                },
                future=True,
            )
        else:
            # Direct/dedicated Postgres (or the local supabase stack on :54322):
            # ordinary pooling with prepared-statement caching is fine and faster.
            _engine = create_async_engine(
                url, echo=settings.db_echo, pool_pre_ping=True, future=True
            )

    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(), expire_on_commit=False, class_=AsyncSession
        )
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Transactional scope: commit on success, rollback on error, always close."""
    maker = get_sessionmaker()
    async with maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """Close the pool (app shutdown / test teardown). Safe if never initialized."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


def _set_engine_for_tests(engine: AsyncEngine | None) -> None:
    """Inject a prebuilt engine (e.g. in-memory SQLite) so tests don't touch a real
    Postgres. Resets the cached sessionmaker to bind to the new engine."""
    global _engine, _sessionmaker
    _engine = engine
    _sessionmaker = None
