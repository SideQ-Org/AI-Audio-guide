"""GPS outlier gate: a teleport-and-snap-back spike is dropped so the tour never moves
to a phantom position, while normal jitter passes and a genuine relocation recovers."""

import app.main as main_module
from app.config import settings
from app.shared.schemas import GeoPoint


class _FakeWS:
    async def send_json(self, obj):  # pragma: no cover - unused here
        return None


def _rt():
    return main_module._SessionRuntime(_FakeWS(), None, "sid-gps")


def test_first_fix_always_accepted():
    assert _rt().accept_fix(GeoPoint(lat=55.0, lon=37.0)) is True


def test_impossible_jump_rejected_then_recovers():
    rt = _rt()
    assert rt.accept_fix(GeoPoint(lat=55.0, lon=37.0)) is True
    far = GeoPoint(lat=55.0045, lon=37.0)  # ~500 m north, effectively instantaneous
    # Rejected up to gps_max_rejects times (impossible speed since the last fix)...
    rejects = [rt.accept_fix(far) for _ in range(settings.gps_max_rejects)]
    assert rejects == [False] * settings.gps_max_rejects
    # ...then accepted, so a genuine relocation / GPS re-lock isn't stuck forever.
    assert rt.accept_fix(far) is True


def test_small_jitter_always_accepted():
    rt = _rt()
    assert rt.accept_fix(GeoPoint(lat=55.0, lon=37.0)) is True
    # ~11 m step is below the jump floor -> accepted regardless of implied speed.
    assert rt.accept_fix(GeoPoint(lat=55.0001, lon=37.0)) is True


def test_gate_disabled_when_speed_nonpositive(monkeypatch):
    monkeypatch.setattr(settings, "gps_max_speed_mps", 0.0)
    rt = _rt()
    assert rt.accept_fix(GeoPoint(lat=55.0, lon=37.0)) is True
    # With the gate off, even a teleport is accepted.
    assert rt.accept_fix(GeoPoint(lat=56.0, lon=37.0)) is True
