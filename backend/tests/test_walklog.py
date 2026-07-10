"""Walk-log backbone: per-tick call counters, session-id stamping, and the
persistent file sink used for full-walk debugging."""

import logging

from app.config import settings
from app.services.agent import walklog


def test_tick_counters_roundtrip():
    walklog.tick_reset()
    walklog.tick_bump("overpass")
    walklog.tick_bump("wiki", 2)
    walklog.tick_bump("wiki")
    assert walklog.tick_snapshot() == {"overpass": 1, "wiki": 3}


def test_tick_bump_is_noop_outside_a_tick():
    # Fresh context (no tick_reset): bumps must not raise and snapshot is empty.
    walklog._TICK_COUNTERS.set(None)
    walklog.tick_bump("overpass")
    assert walklog.tick_snapshot() == {}


def test_file_sink_writes_trace_with_session_id(tmp_path, monkeypatch):
    """When walk_log_dir is set, the trace is ALSO written to <dir>/walk.log, and every
    line carries the current session id so concurrent walks stay separable."""
    log = logging.getLogger("aiguide.agent")
    saved_handlers, saved_filters = log.handlers[:], log.filters[:]
    saved_flag = getattr(log, "_walk_configured", False)
    for h in saved_handlers:
        log.removeHandler(h)
    for f in saved_filters:
        log.removeFilter(f)
    log._walk_configured = False  # force get_logger to reconfigure with our dir
    monkeypatch.setattr(settings, "walk_log_dir", str(tmp_path))
    monkeypatch.setattr(settings, "walk_log_verbose", True)
    try:
        wl = walklog.get_logger()
        walklog.CURRENT_SID.set("sid-42")
        wl.info("pos #7 lat=1.0 lon=2.0")
        for h in wl.handlers:
            h.flush()
        content = (tmp_path / "walk.log").read_text(encoding="utf-8")
        assert "pos #7 lat=1.0 lon=2.0" in content
        assert "sid=sid-42" in content
    finally:  # restore the shared logger for the rest of the suite
        for h in log.handlers[:]:
            log.removeHandler(h)
        for f in log.filters[:]:
            log.removeFilter(f)
        for h in saved_handlers:
            log.addHandler(h)
        for f in saved_filters:
            log.addFilter(f)
        log._walk_configured = saved_flag
