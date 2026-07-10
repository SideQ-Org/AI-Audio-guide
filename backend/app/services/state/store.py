"""Session state store: in-memory (default) or Redis.

The orchestrator owns all state mutation: load -> mutate -> save. ``load``
returns a deep copy so a half-applied mutation never leaks before ``save``.
"""

from __future__ import annotations

import time
from typing import Protocol

from app.config import settings
from app.shared.schemas import SessionState

_KEY = "session:{}"


def _new_session(session_id: str) -> SessionState:
    """Fresh session seeded with the configured default language."""
    from app.config import settings

    return SessionState(session_id=session_id, language=settings.default_language)


class StateStore(Protocol):
    async def load(self, session_id: str) -> SessionState: ...
    async def save(self, state: SessionState) -> None: ...
    async def delete(self, session_id: str) -> None: ...


class InMemoryStateStore:
    """Bounded in-memory store: idle sessions expire (TTL) and the total is LRU-capped,
    so the process can't leak memory one ever-growing dict entry per connection."""

    def __init__(self) -> None:
        # session_id -> (last_access_monotonic, state)
        self._data: dict[str, tuple[float, SessionState]] = {}

    def _evict_expired(self) -> None:
        ttl = settings.session_ttl_s
        if ttl <= 0:
            return
        cutoff = time.monotonic() - ttl
        for sid in [s for s, (ts, _) in self._data.items() if ts < cutoff]:
            self._data.pop(sid, None)

    def _cap(self) -> None:
        cap = settings.max_sessions
        while cap and len(self._data) > cap:
            oldest = min(self._data, key=lambda s: self._data[s][0])  # LRU
            self._data.pop(oldest, None)

    async def load(self, session_id: str) -> SessionState:
        self._evict_expired()
        entry = self._data.get(session_id)
        if entry is None:
            return _new_session(session_id)
        self._data[session_id] = (time.monotonic(), entry[1])  # refresh access time
        return entry[1].model_copy(deep=True)

    async def save(self, state: SessionState) -> None:
        self._data[state.session_id] = (time.monotonic(), state.model_copy(deep=True))
        self._cap()

    async def delete(self, session_id: str) -> None:
        self._data.pop(session_id, None)


class RedisStateStore:
    """Requires a running Redis and the ``redis`` package. Used when
    ``settings.redis_url`` is set."""

    def __init__(self, url: str) -> None:
        import redis.asyncio as redis

        self._r = redis.from_url(url, encoding="utf-8", decode_responses=True)

    async def load(self, session_id: str) -> SessionState:
        raw = await self._r.get(_KEY.format(session_id))
        if raw is None:
            return _new_session(session_id)
        return SessionState.model_validate_json(raw)

    async def save(self, state: SessionState) -> None:
        ttl = int(settings.session_ttl_s) or None  # auto-expire idle sessions
        await self._r.set(_KEY.format(state.session_id), state.model_dump_json(), ex=ttl)

    async def delete(self, session_id: str) -> None:
        await self._r.delete(_KEY.format(session_id))


def default_store() -> StateStore:
    from app.config import settings

    if settings.redis_url:
        return RedisStateStore(settings.redis_url)
    return InMemoryStateStore()
