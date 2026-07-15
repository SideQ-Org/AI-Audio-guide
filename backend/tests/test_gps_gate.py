"""GPS outlier / spoofing gate: a teleport (jammer drift) is anchored to the last trusted point
so the tour never narrates a phantom position, while normal jitter passes and a genuine
relocation recovers. Recovery is TIME-based — the window grows as time since the trusted point."""

import app.main as main_module
from app.config import settings
from app.shared.geo_math import bearing_deg, haversine_m
from app.shared.schemas import GazeConfidence, GeoPoint, Heading


class _FakeWS:
    async def send_json(self, obj):  # pragma: no cover - unused here
        return None


def _rt():
    return main_module._SessionRuntime(_FakeWS(), None, "sid-gps")


def _clock(monkeypatch, start=1000.0):
    box = {"t": start}
    monkeypatch.setattr(main_module.time, "monotonic", lambda: box["t"])
    return box


def test_first_fix_always_accepted():
    assert _rt().accept_fix(GeoPoint(lat=55.0, lon=37.0)) is True


def test_small_jitter_always_accepted():
    rt = _rt()
    assert rt.accept_fix(GeoPoint(lat=55.0, lon=37.0)) is True
    # ~11 m step is below the jump floor -> accepted regardless of implied speed.
    assert rt.accept_fix(GeoPoint(lat=55.0001, lon=37.0)) is True


def test_sustained_spoof_held_far_longer_than_a_few_ticks(monkeypatch):
    # The core fix: a multi-minute jammer drift sends DOZENS of far fixes; they must all be
    # held (the old 3-tick count followed the phantom after ~3 fixes).
    rt = _rt()
    clock = _clock(monkeypatch)
    assert rt.accept_fix(GeoPoint(lat=55.0, lon=37.0)) is True
    far = GeoPoint(lat=55.05, lon=37.0)  # ~5.5 km north — drift to the city centre
    for _ in range(30):
        clock["t"] += 2.0  # 2 s per fix -> 60 s total, still under gps_max_hold_s
        assert rt.accept_fix(far) is False  # anchored to the trusted point


def test_consistent_relocation_recovers_as_window_grows(monkeypatch):
    # A real ~500 m relocation: implausible at first, but once enough time passes the implied
    # speed (dist/dt) falls under the cap and it's accepted on its own — before the hard hold cap.
    rt = _rt()
    clock = _clock(monkeypatch)
    assert rt.accept_fix(GeoPoint(lat=55.0, lon=37.0)) is True
    p = GeoPoint(lat=55.0045, lon=37.0)  # ~500 m
    clock["t"] += 2.0
    assert rt.accept_fix(p) is False  # 250 m/s -> rejected
    clock["t"] += 60.0  # window grew: ~500 m over ~62 s = ~8 m/s < 15
    assert rt.accept_fix(p) is True


def test_far_teleport_recovers_after_hold_cap(monkeypatch):
    # A genuine teleport / GPS re-lock too far to ever look plausible must still win once we've
    # held for gps_max_hold_s (we can't freeze on a stale point forever).
    rt = _rt()
    clock = _clock(monkeypatch)
    assert rt.accept_fix(GeoPoint(lat=55.0, lon=37.0)) is True
    far = GeoPoint(lat=55.05, lon=37.0)  # ~5.5 km
    clock["t"] += 1.0
    assert rt.accept_fix(far) is False
    clock["t"] += settings.gps_max_hold_s + 1.0  # waited out the hold cap
    assert rt.accept_fix(far) is True


def _hi(bearing):
    return Heading(direction_deg=bearing, gaze_confidence=GazeConfidence.HIGH)


def test_dead_reckon_advances_along_confident_heading(monkeypatch):
    # During a held spoof, a trustworthy (compass) heading advances the anchor along the walk.
    rt = _rt()
    clock = _clock(monkeypatch)
    trusted = GeoPoint(lat=55.0, lon=37.0)
    assert rt.accept_fix(trusted) is True
    clock["t"] += 30.0  # 30 s of jam at 1.2 m/s -> ~36 m north
    dr = rt.dead_reckoned(_hi(0.0))  # heading due north
    assert dr is not None
    moved = haversine_m(trusted, dr)
    assert 25 < moved < 45  # ~36 m
    assert abs(bearing_deg(trusted, dr) - 0.0) < 5 or abs(bearing_deg(trusted, dr) - 360.0) < 5


def test_dead_reckon_capped(monkeypatch):
    rt = _rt()
    clock = _clock(monkeypatch)
    trusted = GeoPoint(lat=55.0, lon=37.0)
    assert rt.accept_fix(trusted) is True
    clock["t"] += 100000.0  # absurd jam -> would be km, but capped
    dr = rt.dead_reckoned(_hi(90.0))
    assert dr is not None
    assert haversine_m(trusted, dr) <= settings.gps_dr_max_m + 1


def test_dead_reckon_none_when_heading_untrusted(monkeypatch):
    rt = _rt()
    clock = _clock(monkeypatch)
    assert rt.accept_fix(GeoPoint(lat=55.0, lon=37.0)) is True
    clock["t"] += 30.0
    low = Heading(direction_deg=0.0, gaze_confidence=GazeConfidence.LOW)
    assert rt.dead_reckoned(low) is None  # untrusted heading -> hold, don't drift


def test_dead_reckon_none_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "gps_dead_reckon", False)
    rt = _rt()
    clock = _clock(monkeypatch)
    assert rt.accept_fix(GeoPoint(lat=55.0, lon=37.0)) is True
    clock["t"] += 30.0
    assert rt.dead_reckoned(_hi(0.0)) is None


def test_gate_disabled_when_speed_nonpositive(monkeypatch):
    monkeypatch.setattr(settings, "gps_max_speed_mps", 0.0)
    rt = _rt()
    assert rt.accept_fix(GeoPoint(lat=55.0, lon=37.0)) is True
    # With the gate off, even a teleport is accepted.
    assert rt.accept_fix(GeoPoint(lat=56.0, lon=37.0)) is True
