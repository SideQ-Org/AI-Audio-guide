"""Этап 2 — readiness + admin stats; + walk logging (Этап 1: полное логирование прогулки)."""

import asyncio
import logging
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main_module
from app.config import settings
from app.services.agent.companion import HeuristicCompanion
from app.services.agent.factory import build_orchestrator
from app.services.agent.narrator import TemplateNarrator
from app.services.agent.orchestrator import Orchestrator
from app.services.agent.pipeline import TextPipeline
from app.services.agent.scorer import HeuristicScorer
from app.services.enrichment.enricher import MockEnricher
from app.services.geo.discovery import Discovery
from app.services.geo.geocoder import MockGeocoder
from app.services.geo.providers import StaticPlaceProvider
from app.services.llm.client import METER
from app.services.state.store import InMemoryStateStore
from app.services.stt.stt import build_stt
from app.shared.schemas import Address
from sim.routes import RED_SQUARE
from sim.walk import walk

_FIX = Path(__file__).parent / "fixtures"


class _CaptureHandler(logging.Handler):
    """Collects formatted messages from the walk logger (which has propagate=False,
    so pytest's caplog can't see it — we attach directly)."""

    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _capture_walk_log() -> _CaptureHandler:
    h = _CaptureHandler()
    logging.getLogger("aiguide.agent").addHandler(h)
    return h


def _client() -> TestClient:
    settings.agent_backend = "heuristic"
    settings.geo_source = "fixture"
    settings.stt_backend = "mock"
    main_module._orchestrator = build_orchestrator()
    main_module._stt = build_stt()
    return TestClient(main_module.app)


def test_ready_reflects_consecutive_llm_failures():
    c = _client()
    saved = METER.consecutive_failures
    try:
        METER.consecutive_failures = 0
        assert c.get("/ready").status_code == 200
        METER.consecutive_failures = 5
        assert c.get("/ready").status_code == 503
    finally:
        METER.consecutive_failures = saved


def test_stats_gated_by_token():
    c = _client()
    settings.stats_token = ""
    try:
        assert c.get("/stats").status_code == 404  # disabled when no token set
        settings.stats_token = "adm"
        assert c.get("/stats").status_code == 404  # missing/wrong token
        r = c.get("/stats?token=adm")
        assert r.status_code == 200
        body = r.json()
        assert "cost_usd" in body and "active_sessions" in body and "errors" in body
    finally:
        settings.stats_token = ""


def test_walk_log_captures_narration_text_geocode_and_discovery():
    """A driven walk emits a readable pipeline trace on aiguide.agent, and crucially the
    TEXT the agents produce (not just markers) — the whole point of Этап 1."""

    async def run(handler: _CaptureHandler):
        provider = StaticPlaceProvider.from_json(_FIX / "places_red_square.json")
        enricher = MockEnricher.from_json(_FIX / "facts_red_square.json")
        pipeline = TextPipeline(HeuristicScorer(), TemplateNarrator(), enricher)
        orch = Orchestrator(
            Discovery(provider),
            pipeline,
            HeuristicCompanion(),
            InMemoryStateStore(),
            geocoder=MockGeocoder(
                Address(country="Россия", city="Москва", district="Тверской",
                        street="Красная площадь")
            ),
        )
        for step in walk(RED_SQUARE, speed_mps=1.3, step_s=8.0):
            out = await orch.on_position("s1", step.position, step.heading, step.pace)
            if out.kind == "narration" and out.text:
                return  # got at least one spoken line — enough to assert the trace
        return

    h = _capture_walk_log()
    try:
        asyncio.run(run(h))
    finally:
        logging.getLogger("aiguide.agent").removeHandler(h)

    msgs = h.messages
    assert any(m.startswith("discover r=") for m in msgs), "discovery not logged"
    assert any(m.startswith("geocode ") for m in msgs), "geocode not logged"
    assert any(m.startswith("state ") for m in msgs), "FSM transition not logged"
    # The narration TEXT must appear in the log, not just a marker: a step/narrate line
    # carries the spoken text after the "| " separator.
    spoken = [m for m in msgs if ("narrate step place=" in m or "step place=" in m) and " | " in m]
    assert spoken, "no narration-with-text line logged"
    assert any(m.rsplit(" | ", 1)[-1].strip() for m in spoken), "narration text is empty in log"


def test_walk_log_records_pause_and_resume():
    """pause/resume are logged with a timestamp (the walk-log noted they weren't before,
    so a silent stretch couldn't be told apart from 'no new places')."""

    class _FakeWS:
        async def send_json(self, obj: dict) -> None:
            return None

    async def run(handler: _CaptureHandler):
        orch = Orchestrator(
            Discovery(StaticPlaceProvider([])),
            TextPipeline(HeuristicScorer(), TemplateNarrator(), MockEnricher({})),
            HeuristicCompanion(),
            InMemoryStateStore(),
        )
        rt = main_module._SessionRuntime(_FakeWS(), orch, "sid-pause")
        await rt.pause_tour()
        rt.resume_tour()

    h = _capture_walk_log()
    try:
        asyncio.run(run(h))
    finally:
        logging.getLogger("aiguide.agent").removeHandler(h)

    assert any(m.startswith("pause session=") for m in h.messages), "pause not logged"
    assert any(m.startswith("resume session=") for m in h.messages), "resume not logged"
