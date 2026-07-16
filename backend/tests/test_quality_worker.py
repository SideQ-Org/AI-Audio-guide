"""Quality worker (Block 4 Phase 4): pure scoring + a SQLite sweep integration test."""

from __future__ import annotations

import asyncio
import uuid

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("aiosqlite")

from sqlalchemy import event, func, select  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.config import settings  # noqa: E402
from app.services.accounts import db, history  # noqa: E402
from app.services.accounts.models import Base, WalkQuality  # noqa: E402
from app.services.agent.interest_judge import AXES, JudgeVerdict  # noqa: E402
from app.services.quality.worker import Blurb, score_blurbs, sweep_once  # noqa: E402
from app.shared.schemas import (  # noqa: E402
    Address,
    GeoPoint,
    Place,
    SessionState,
    Significance,
)


# --- pure scoring ---------------------------------------------------------- #
def test_score_blurbs_discriminates_and_flags_gates():
    good = [
        Blurb("Тело Ленина забальзамировали в 1924 году и позже перезахоронили тут.",
              "ru", facts="Ленин, 1924, перезахоронение."),
        Blurb("Гауптвахту XIX века отдали под трибунал.", "ru", facts="Гауптвахта, XIX век."),
    ]
    dull = [
        Blurb("Это парк, тут гуляют люди. Время застыло, всё дышит историей.", "ru"),
        Blurb("Это парк, здесь гуляют и отдыхают люди.", "ru"),
    ]
    rg = asyncio.run(score_blurbs(good))
    rd = asyncio.run(score_blurbs(dull))
    assert rg.score > rd.score
    assert rg.n_blurbs == 2
    assert not rg.used_judge
    assert rd.cliche_rate > 0            # the cliché blurb is caught
    assert "taxonomy" in rd.diagnostics


def test_score_blurbs_empty_is_zero():
    r = asyncio.run(score_blurbs([Blurb("", "ru"), Blurb("   ", "ru")]))
    assert r.n_blurbs == 0
    assert r.score == 0.0


def test_score_blurbs_records_tier_and_passes_it_to_judge():
    seen_tiers = []

    class _RecJudge:
        async def score(self, text, *, facts=None, language="ru", tier=None):
            seen_tiers.append(tier)
            return JudgeVerdict("", {ax: 2 for ax in AXES}, True, False, 2)

    r = asyncio.run(score_blurbs(
        [Blurb("Музей открыли в 1901 году.", "ru", facts="1901", tier="paid")],
        judge=_RecJudge(),
    ))
    assert r.tier == "paid"          # recorded onto walk_quality
    assert seen_tiers == ["paid"]    # the judge was told the tier


def test_score_blurbs_flags_object_repetition():
    # the SAME object narrated 3x with different wording — lexical novelty wouldn't catch it,
    # object-level repeat does. Isolate the penalty: IDENTICAL texts, only place ids differ,
    # so any score gap is purely the object-repeat penalty.
    texts = [
        "Руины конюшни хранят следы старой усадьбы.",
        "От усадебной конюшни остались только живописные развалины.",
        "Эти каменные руины — всё, что уцелело от конюшни усадьбы.",
    ]
    repeated = [Blurb(t, "ru", place="ruins") for t in texts]
    distinct = [Blurb(t, "ru", place=p) for t, p in zip(texts, ["a", "b", "c"], strict=True)]
    r_rep = asyncio.run(score_blurbs(repeated))
    r_dist = asyncio.run(score_blurbs(distinct))
    assert r_rep.diagnostics["object_repeat_rate"] > 0
    assert r_rep.diagnostics["taxonomy"].get("repeat_object") == 2  # 2nd & 3rd tellings
    assert r_dist.diagnostics.get("object_repeat_rate", 0) == 0
    assert r_rep.score < r_dist.score  # only difference is the object-repeat penalty


def test_score_blurbs_with_judge_uses_verdict():
    class _Judge:
        async def score(self, text, *, facts=None, language="ru", tier=None):
            grounded = bool(facts)  # no facts -> ungrounded, trips the gate
            return JudgeVerdict(
                rationale="", axes={ax: 4 for ax in AXES},
                grounded=grounded, cliche=False, overall=4,
            )

    grounded = asyncio.run(score_blurbs([Blurb("факт", "ru", facts="есть факт")], judge=_Judge()))
    ungrounded = asyncio.run(score_blurbs([Blurb("выдумка", "ru", facts=None)], judge=_Judge()))
    assert grounded.used_judge
    assert grounded.grounded_rate == 1.0
    assert ungrounded.grounded_rate == 0.0
    assert grounded.score > ungrounded.score


# --- DB sweep integration -------------------------------------------------- #
def _make_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _rec):  # pragma: no cover
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return engine


async def _drain():
    if history._tasks:
        await asyncio.gather(*list(history._tasks))


def test_sweep_scores_finished_walk_and_is_idempotent():
    async def run():
        engine = _make_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        db._set_engine_for_tests(engine)
        settings.database_url = "sqlite+aiosqlite://"
        settings.capture_narration_samples = True
        try:
            st = SessionState(
                session_id="q" * 20, user_id=str(uuid.uuid4()), language="ru",
                address=Address(city="Москва"),
            )
            place = Place(id="p1", name="Мавзолей", category="monument",
                          location=GeoPoint(lat=55.75, lon=37.62))
            history.record_object(
                st, place, Significance.HIGH,
                "Тело Ленина забальзамировали в 1924 году.",
                facts="Ленин, 1924.",
            )
            await _drain()
            first = await sweep_once(use_judge=False)
            async with db.get_sessionmaker()() as s:
                n_rows = await s.scalar(select(func.count()).select_from(WalkQuality))
                row = (await s.scalars(select(WalkQuality))).one()
                data = (first, n_rows, row.n_blurbs, row.used_judge, row.score)
            second = await sweep_once(use_judge=False)  # nothing new -> idempotent
            return data, second
        finally:
            settings.capture_narration_samples = False
            await db.dispose_engine()
            settings.database_url = ""

    (first, n_rows, n_blurbs, used_judge, score), second = asyncio.run(run())
    assert first == 1            # one finished walk scored
    assert n_rows == 1
    assert n_blurbs == 1
    assert used_judge is False
    assert 0.0 <= score <= 100.0
    assert second == 0           # already scored -> not re-processed
