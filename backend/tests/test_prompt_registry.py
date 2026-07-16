"""PromptRegistry — memory + versioning + rollback (Block 4 hardening). Offline."""

from __future__ import annotations

from app.services.quality.registry import Experiment, PromptRegistry


def _reg(tmp_path) -> PromptRegistry:
    return PromptRegistry(tmp_path / "reg")


def test_versions_are_immutable_and_content_addressed(tmp_path):
    r = _reg(tmp_path)
    a = r.save_version("narrator", "free", "текст версии A")
    a2 = r.save_version("narrator", "free", "текст версии A")
    b = r.save_version("narrator", "free", "текст версии B")
    assert a == a2 and a != b                         # content-hash id, idempotent
    assert r.version_text("narrator", "free", a) == "текст версии A"
    assert r.version_text("narrator", "free", "nope") is None


def test_ensure_baseline_and_active_pointer(tmp_path):
    r = _reg(tmp_path)
    base = r.ensure_baseline("narrator", "free", "БАЗОВЫЙ промпт")
    assert r.ensure_baseline("narrator", "free", "другой") == base  # idempotent — keeps first
    assert r.active_version("narrator", "free") == base
    assert r.active_text("narrator", "free") == "БАЗОВЫЙ промпт"


def test_set_active_and_rollback(tmp_path):
    r = _reg(tmp_path)
    base = r.ensure_baseline("narrator", "free", "v0 baseline")
    v1 = r.save_version("narrator", "free", "v1 better")
    v2 = r.save_version("narrator", "free", "v2 even better")
    r.set_active("narrator", "free", v1)
    r.set_active("narrator", "free", v2)
    assert r.active_version("narrator", "free") == v2
    # rollback unwinds the history: v2 -> v1 -> baseline
    assert r.rollback("narrator", "free") == v1
    assert r.active_version("narrator", "free") == v1
    assert r.rollback("narrator", "free") == base
    assert r.rollback("narrator", "free") is None      # nothing left to undo


def test_kill_switch_forces_baseline(tmp_path):
    r = _reg(tmp_path)
    base = r.ensure_baseline("narrator", "paid", "baseline")
    v1 = r.save_version("narrator", "paid", "candidate")
    r.set_active("narrator", "paid", v1)
    assert r.kill_switch("narrator", "paid") == base
    assert r.active_version("narrator", "paid") == base


def test_memory_records_and_reads_back(tmp_path):
    r = _reg(tmp_path)
    r.record_experiment(Experiment(id="v1", target="narrator", tier="free",
                                   verdict="accepted", dev=0.6, gold=0.55))
    r.record_experiment(Experiment(id="v2", target="narrator", tier="free",
                                   verdict="rejected", stop_reason="coverage_regressed"))
    past = r.past_attempts("narrator", "free")
    assert [p["id"] for p in past] == ["v1", "v2"]      # newest last
    assert past[1]["stop_reason"] == "coverage_regressed"


def test_known_bad_feeds_oscillation_guard(tmp_path):
    r = _reg(tmp_path)
    r.record_experiment(Experiment(id="bad1", target="narrator", tier="free", verdict="rejected"))
    r.record_experiment(
        Experiment(id="bad2", target="narrator", tier="free", verdict="rolled_back")
    )
    r.record_experiment(Experiment(id="good", target="narrator", tier="free", verdict="accepted"))
    assert set(r.known_bad("narrator", "free")) == {"bad1", "bad2"}
    # a later 'accepted' on a previously-bad id clears it
    r.record_experiment(Experiment(id="bad1", target="narrator", tier="free", verdict="accepted"))
    assert "bad1" not in r.known_bad("narrator", "free")


def test_past_attempts_skips_corrupt_lines(tmp_path):
    r = _reg(tmp_path)
    r.record_experiment(Experiment(id="v1", target="narrator", tier="free", verdict="accepted"))
    # simulate a partial/corrupt write
    p = r._ledger_path("narrator", "free")
    with p.open("a", encoding="utf-8") as fh:
        fh.write('{"id": "broken", incomplete\n')
    r.record_experiment(Experiment(id="v2", target="narrator", tier="free", verdict="rejected"))
    ids = [x["id"] for x in r.past_attempts("narrator", "free")]
    assert ids == ["v1", "v2"]        # the corrupt line is skipped, not fatal
