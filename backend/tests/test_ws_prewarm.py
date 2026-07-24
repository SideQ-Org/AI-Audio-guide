"""Home-screen prewarm (WSPrewarm): warms the session's Overpass disc + geo caches
WITHOUT narrating — no greeting, no walk row, no seen/history; the tour then starts
instantly on the first real position. Offline (fixture geo, heuristic backend)."""

import asyncio
import time

import app.main as main_module
from app.config import settings
from app.main import _SessionRuntime
from app.shared.schemas import GeoPoint

from .test_ws import _heuristic_app, _recv


def _wait_disc(orch, sid, timeout_s: float = 4.0):
    """Poll the per-session inventory until the fire-and-forget warm lands."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        inv = orch.discovery.inventory.peek(sid)
        if inv is not None:
            return inv
        time.sleep(0.05)
    return None


def test_ws_prewarm_warms_without_narrating():
    client = _heuristic_app()
    settings.session_greeting = True  # the greeting must survive the prewarm untouched
    sid = "prewarmtest00001"
    try:
        with client.websocket_connect(f"/ws?sid={sid}") as ws:
            ws.send_json({"type": "prewarm", "lat": 55.7525, "lon": 37.6231})
            orch = main_module._orchestrator
            inv = _wait_disc(orch, sid)
            assert inv is not None and inv.places  # disc warmed for THIS sid

            st = asyncio.run(orch.store.load(sid))
            assert st.greeted is False  # nothing greeted/narrated during prewarm
            assert st.seen_place_ids == [] and st.narration_history == []
            if orch.pipeline.fact_buffer is not None and st.area_key:
                assert orch.pipeline.fact_buffer.get_area(st.area_key, st.language) is not None

            # The real tour start: first position -> the normal greeting flow opens.
            ws.send_json(
                {"type": "position", "lat": 55.7525, "lon": 37.6231,
                 "gaze_confidence": "low"}
            )
            first = _recv(ws)
            assert first["type"] == "state"
            second = _recv(ws)
            assert second["type"] == "narration"  # the canned greeting
    finally:
        settings.session_greeting = False


def test_ws_prewarm_does_not_greet_or_set_live_position():
    client = _heuristic_app()
    sid = "prewarmtest00000"
    with client.websocket_connect(f"/ws?sid={sid}") as ws:
        ws.send_json({"type": "prewarm", "lat": 55.7525, "lon": 37.6231})
        orch = main_module._orchestrator
        _wait_disc(orch, sid)
        st = asyncio.run(orch.store.load(sid))
        assert st.position is None
        assert st.greeted is False


def test_ws_prewarm_reanchors_after_moving():
    """Repeated prewarms re-fetch the disc once the user walks beyond the re-anchor
    threshold (inventory_radius_m * refetch_frac = 400 m) — home-screen freshness."""
    client = _heuristic_app()
    sid = "prewarmtest00002"
    with client.websocket_connect(f"/ws?sid={sid}") as ws:
        orch = main_module._orchestrator
        ws.send_json({"type": "prewarm", "lat": 55.7525, "lon": 37.6231})
        a = _wait_disc(orch, sid)
        assert a is not None
        # ~900 m north — well beyond the 400 m re-anchor threshold.
        ws.send_json({"type": "prewarm", "lat": 55.7606, "lon": 37.6231})
        deadline = time.monotonic() + 4.0
        b = None
        while time.monotonic() < deadline:
            b = orch.discovery.inventory.peek(sid)
            if b is not None and abs(b.anchor.lat - 55.7606) < 1e-6:
                break
            time.sleep(0.05)
        assert b is not None and abs(b.anchor.lat - 55.7606) < 1e-6


def test_ws_prewarm_disabled_is_noop():
    client = _heuristic_app()
    sid = "prewarmtest00003"
    settings.prewarm_enabled = False
    try:
        with client.websocket_connect(f"/ws?sid={sid}") as ws:
            ws.send_json({"type": "prewarm", "lat": 55.7525, "lon": 37.6231})
            ws.send_json({"type": "ping"})  # flush the dispatch loop
            time.sleep(0.2)
            assert main_module._orchestrator.discovery.inventory.peek(sid) is None
    finally:
        settings.prewarm_enabled = True


def test_prewarm_geo_gate_by_distance_and_time():
    rt = _SessionRuntime.__new__(_SessionRuntime)  # gate logic only — no ws needed
    rt._last_prewarm = None
    p = GeoPoint(lat=55.7525, lon=37.6231)
    assert rt.allow_geo_prewarm(p) is True  # first is always allowed
    assert rt.allow_geo_prewarm(p) is False  # same spot immediately -> gated
    near = GeoPoint(lat=55.7529, lon=37.6231)  # ~45 m
    assert rt.allow_geo_prewarm(near) is False  # < 150 m and < 120 s
    far = GeoPoint(lat=55.7545, lon=37.6231)  # ~220 m
    assert rt.allow_geo_prewarm(far) is True  # moved past the distance gate
