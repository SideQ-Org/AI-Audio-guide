"""Этап 1 — bounded in-memory session store (no leak, evicts on disconnect/idle)."""

import asyncio
import time

from app.config import settings
from app.services.state.store import InMemoryStateStore
from app.shared.schemas import SessionState


def test_delete_removes_session():
    async def run() -> bool:
        s = InMemoryStateStore()
        await s.save(SessionState(session_id="a"))
        assert "a" in s._data
        await s.delete("a")
        return "a" in s._data

    assert asyncio.run(run()) is False


def test_lru_cap_evicts_oldest():
    saved = settings.max_sessions
    settings.max_sessions = 2
    try:
        async def run() -> tuple[int, bool]:
            s = InMemoryStateStore()
            await s.save(SessionState(session_id="a"))
            await s.save(SessionState(session_id="b"))
            await s.save(SessionState(session_id="c"))  # over cap -> evict LRU ("a")
            return len(s._data), ("a" in s._data)

        n, has_a = asyncio.run(run())
        assert n == 2
        assert has_a is False
    finally:
        settings.max_sessions = saved


def test_ttl_evicts_idle():
    saved = settings.session_ttl_s
    settings.session_ttl_s = 0.05
    try:
        async def run() -> bool:
            s = InMemoryStateStore()
            await s.save(SessionState(session_id="a"))
            time.sleep(0.1)  # let "a" go idle past the TTL
            await s.load("b")  # any load triggers _evict_expired
            return "a" in s._data

        assert asyncio.run(run()) is False
    finally:
        settings.session_ttl_s = saved
