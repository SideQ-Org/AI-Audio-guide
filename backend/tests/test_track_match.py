"""Ф3/Ф4: Orchestrator.matched_track map-matches the walked path (straight-line passthrough
offline), gated by track_match_enabled / min_points. Fully offline."""

from __future__ import annotations

import asyncio

from app.services.agent.factory import build_orchestrator
from app.services.state.store import InMemoryStateStore


def _orch(monkeypatch, **over):
    from app.config import settings

    monkeypatch.setattr(settings, "agent_backend", "heuristic")
    monkeypatch.setattr(settings, "geo_source", "fixture")
    monkeypatch.setattr(settings, "enrichment_source", "mock")
    monkeypatch.setattr(settings, "routing_source", "straight")
    monkeypatch.setattr(settings, "track_match_enabled", True)
    monkeypatch.setattr(settings, "track_match_min_points", 3)
    for k, v in over.items():
        monkeypatch.setattr(settings, k, v)
    return build_orchestrator(store=InMemoryStateStore())


_PATH = [
    [55.7539, 37.6208], [55.7535, 37.6212], [55.7531, 37.6216, 1.0], [55.7528, 37.6220],
]


async def _seed(orch, sid, path):
    st = await orch.store.load(sid)
    st.path = [list(p) for p in path]
    await orch.store.save(st)


def test_matched_track_passthrough_preserves_paused(monkeypatch):
    orch = _orch(monkeypatch)

    async def run():
        await _seed(orch, "t1", _PATH)
        return await orch.matched_track("t1")

    m = asyncio.run(run())
    # Straight-line match is a passthrough: same points, paused flag kept.
    assert m is not None
    assert [p[:2] for p in m] == [p[:2] for p in _PATH]
    assert any(len(p) > 2 and p[2] == 1.0 for p in m)


def test_matched_track_disabled_returns_none(monkeypatch):
    orch = _orch(monkeypatch, track_match_enabled=False)

    async def run():
        await _seed(orch, "t2", _PATH)
        return await orch.matched_track("t2")

    assert asyncio.run(run()) is None


def test_matched_track_too_short_returns_none(monkeypatch):
    orch = _orch(monkeypatch, track_match_min_points=10)

    async def run():
        await _seed(orch, "t3", _PATH)
        return await orch.matched_track("t3")

    assert asyncio.run(run()) is None
