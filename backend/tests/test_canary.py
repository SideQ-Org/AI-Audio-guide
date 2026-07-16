"""Phase 6 — canary auto-apply + auto-rollback (Block 4). Offline."""

from __future__ import annotations

import asyncio
import uuid

import pytest

from app.config import settings
from app.services.agent.prompts import (
    _load,
    clear_session_prompt_override,
    set_session_prompt_override,
)
from app.services.quality.canary import _in_canary, canary_prompt_for
from app.services.quality.registry import PromptRegistry


def _reg(tmp_path) -> PromptRegistry:
    return PromptRegistry(tmp_path / "reg")


# --- registry canary pointer ---------------------------------------------- #
def test_registry_canary_pointer(tmp_path):
    r = _reg(tmp_path)
    r.ensure_baseline("narrator", "free", "baseline text")
    v1 = r.save_version("narrator", "free", "canary candidate text")
    r.set_canary("narrator", "free", v1)
    assert r.canary_version("narrator", "free") == v1
    assert r.canary_text("narrator", "free") == "canary candidate text"
    r.clear_canary("narrator", "free")
    assert r.canary_version("narrator", "free") is None


def test_kill_switch_also_drops_canary(tmp_path):
    r = _reg(tmp_path)
    base = r.ensure_baseline("narrator", "free", "baseline")
    v1 = r.save_version("narrator", "free", "cand")
    r.set_active("narrator", "free", v1)
    r.set_canary("narrator", "free", v1)
    assert r.kill_switch("narrator", "free") == base
    assert r.active_version("narrator", "free") == base
    assert r.canary_version("narrator", "free") is None  # experiment killed too


# --- per-session prompt override ------------------------------------------ #
def test_session_prompt_override_scopes_load():
    base = _load("narrator")[:20]
    set_session_prompt_override({"narrator": "КАНАРЕЕЧНЫЙ ПРОМПТ"})
    try:
        assert _load("narrator") == "КАНАРЕЕЧНЫЙ ПРОМПТ"
    finally:
        clear_session_prompt_override()
    assert _load("narrator")[:20] == base  # reverts cleanly


# --- membership ----------------------------------------------------------- #
def test_in_canary_bounds_and_determinism():
    assert _in_canary("any-sid", 0.0) is False    # 0% -> nobody
    assert _in_canary("any-sid", 1.0) is True      # 100% -> everybody
    # deterministic: same sid, same answer
    assert _in_canary("sid-xyz", 0.5) == _in_canary("sid-xyz", 0.5)
    # a 50% split actually splits a batch of sids (not all one way)
    got = [_in_canary(f"sid-{i}", 0.5) for i in range(50)]
    assert 0 < sum(got) < 50


# --- canary_prompt_for (dormant vs active) -------------------------------- #
def test_canary_prompt_for_dormant_by_default(tmp_path):
    r = _reg(tmp_path)
    r.ensure_baseline("narrator", "free", "baseline")
    v1 = r.save_version("narrator", "free", "canary")
    r.set_canary("narrator", "free", v1)
    # canary disabled -> None regardless of a staged version
    assert not settings.canary_enabled
    assert canary_prompt_for(r, "sid1", "free") is None


def test_canary_prompt_for_active(tmp_path):
    r = _reg(tmp_path)
    r.ensure_baseline("narrator", "free", "baseline")
    v1 = r.save_version("narrator", "free", "CANARY TEXT")
    r.set_canary("narrator", "free", v1)
    settings.canary_enabled = True
    settings.canary_fraction = 1.0  # everyone in the canary
    try:
        assert canary_prompt_for(r, "sid1", "free") == {"narrator": "CANARY TEXT"}
        # no staged version -> None
        r.clear_canary("narrator", "free")
        assert canary_prompt_for(r, "sid1", "free") is None
    finally:
        settings.canary_enabled = False
        settings.canary_fraction = 0.0


# --- monitor: SQLite integration ------------------------------------------ #
def test_monitor_rolls_back_a_regressing_canary(tmp_path):
    pytest.importorskip("aiosqlite")
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import StaticPool

    from app.services.accounts import db
    from app.services.accounts.models import Base, User, Walk, WalkQuality
    from app.services.quality.canary import _in_canary, monitor_and_rollback

    async def run():
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool,
                                      connect_args={"check_same_thread": False})

        @event.listens_for(engine.sync_engine, "connect")
        def _fk(dbapi, _):  # pragma: no cover
            dbapi.cursor().execute("PRAGMA foreign_keys=ON")

        async with engine.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        db._set_engine_for_tests(engine)
        settings.database_url = "sqlite+aiosqlite://"
        settings.canary_enabled = True
        settings.canary_fraction = 0.5
        settings.canary_min_walks = 3
        settings.canary_margin = 0.05
        r = _reg(tmp_path)
        base = r.ensure_baseline("narrator", "free", "baseline")
        cand = r.save_version("narrator", "free", "bad canary")
        r.set_active("narrator", "free", base)
        r.set_canary("narrator", "free", cand)
        try:
            uid = uuid.uuid4()
            async with db.get_sessionmaker()() as s:
                s.add(User(id=uid, email="t@t.t"))  # FK parent for walks.user_id
                await s.flush()
                # craft sids: canary arm scores LOW, control arm scores HIGH
                made = {"canary": 0, "control": 0}
                i = 0
                while made["canary"] < 4 or made["control"] < 4:
                    sid = f"sid-{i}"
                    i += 1
                    arm = "canary" if _in_canary(sid, 0.5) else "control"
                    if made[arm] >= 4:
                        continue
                    made[arm] += 1
                    wid = uuid.uuid4()
                    s.add(Walk(id=wid, user_id=uid, sid=sid, language="ru"))
                    s.add(WalkQuality(walk_id=wid, user_id=uid, tier="free", n_blurbs=3,
                                      score=10.0 if arm == "canary" else 80.0))
                await s.commit()
            action = await monitor_and_rollback(r, target="narrator", tier="free")
            kb = r.known_bad("narrator", "free")
            return action, r.canary_version("narrator", "free"), kb, cand
        finally:
            settings.canary_enabled = False
            settings.canary_fraction = 0.0
            settings.database_url = ""
            await db.dispose_engine()

    action, canary_after, known_bad, cand = asyncio.run(run())
    assert action == "rolled_back"            # canary regressed -> auto-rollback
    assert canary_after is None               # canary cleared
    assert cand in known_bad                  # won't be re-staged/re-proposed
