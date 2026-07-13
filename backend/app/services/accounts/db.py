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
    """True ONLY for a Supabase Supavisor **transaction**-mode URL (port 6543).

    Transaction mode multiplexes clients over a shared set of server backends and
    rotates the backend per transaction, so a reused pooled connection sees prepared
    statements "disappear" (prepared on backend A, executed on backend B →
    ``InvalidSQLStatementNameError``). That path must therefore run WITHOUT a persistent
    pool (NullPool) — see get_engine.

    Match ONLY ``:6543``. The Supavisor **session** pooler shares the same host but on
    ``:5432`` and pins one backend per client connection for its whole life, so it CAN
    reuse a pooled connection with cached prepared statements — the fast default path.
    That's why prod uses the session pooler; don't fold it back into this branch.
    """
    return ":6543" in url


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
            # Supabase transaction pooler (:6543). CANNOT keep a persistent pool: the backend
            # rotates per transaction, so a reused connection's prepared statements vanish
            # (InvalidSQLStatementNameError). NullPool (one fresh connection per checkout, bound to
            # one backend for its single-transaction life) + disabled statement cache + unique
            # names is the only correct config here — but it pays a full TLS+auth handshake to the
            # (remote) pooler on EVERY request (~3.8 s to eu-central-1). Prefer the session pooler
            # (:5432, the else branch) in prod for reuse; this branch stays correct-but-slow.
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
            # Session pooler (:5432), direct/dedicated Postgres, or the local supabase stack
            # (:54322). One backend is pinned per connection for its whole life, so we REUSE a
            # persistent pool with normal (cached) prepared statements — fast. No pool_pre_ping:
            # the session pooler is ~190 ms RTT away (eu-central-1), so a per-checkout liveness
            # round-trip would add ~190 ms to every request; staleness is guarded by AGE
            # (pool_recycle) plus the startup keepalive (main.py `_warm_db_pool`) that keeps the
            # pool warm and fresh. Connection reuse is what avoids the ~3.8 s cold handshake.
            _engine = create_async_engine(
                url,
                echo=settings.db_echo,
                pool_size=5,
                max_overflow=10,
                pool_recycle=1500,
                future=True,
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
