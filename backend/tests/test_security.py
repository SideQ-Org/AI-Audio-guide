"""Этап 0 — protect the open /ws endpoint and cap spend."""

import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import app.main as main_module
from app.config import settings
from app.main import _too_big
from app.services.agent.factory import build_orchestrator
from app.services.llm.client import METER, BudgetExceeded, OpenAICompatLLM
from app.services.llm.router import Role
from app.services.stt.stt import build_stt


def _app() -> TestClient:
    settings.agent_backend = "heuristic"
    settings.geo_source = "fixture"
    settings.stt_backend = "mock"
    main_module._orchestrator = build_orchestrator()
    main_module._stt = build_stt()
    return TestClient(main_module.app)


# --- hard spend cap --------------------------------------------------------- #
def test_hard_cap_blocks_llm_call():
    settings.usd_hard_cap = 1.0
    saved = METER.provider_cost
    METER.provider_cost = 5.0  # pretend the process has already spent $5
    try:
        llm = OpenAICompatLLM(base_url="http://unused", api_key="k", default_model="m")
        with pytest.raises(BudgetExceeded):
            asyncio.run(llm.complete_text(Role.NARRATOR, "s", "u"))
    finally:
        METER.provider_cost = saved
        settings.usd_hard_cap = 0.0


def test_under_cap_does_not_block():
    settings.usd_hard_cap = 1000.0
    saved = METER.provider_cost
    METER.provider_cost = 0.0
    try:
        assert METER.over_hard_cap() is False
    finally:
        METER.provider_cost = saved
        settings.usd_hard_cap = 0.0


# --- /ws token gate --------------------------------------------------------- #
def test_ws_rejects_without_token_and_accepts_with_it():
    settings.ws_token = "secret"
    try:
        client = _app()
        # no token -> connection refused before accept
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws") as ws:
                ws.receive_json()
        # correct token -> works
        with client.websocket_connect("/ws?token=secret") as ws:
            ws.send_json({"type": "position", "lat": 55.7525, "lon": 37.6231})
            assert ws.receive_json()["type"] in ("state", "narration")
    finally:
        settings.ws_token = ""


# --- receive-loop robustness (a bad frame must NOT drop the socket) --------- #
def test_ws_survives_malformed_json_and_invalid_message():
    client = _app()
    with client.websocket_connect("/ws") as ws:
        # non-JSON text -> error frame, socket stays open
        ws.send_text("this is not json{")
        assert ws.receive_json() == {"type": "error", "message": "invalid json"}
        # a JSON value that isn't an object -> error frame
        ws.send_text("[1, 2, 3]")
        assert ws.receive_json() == {"type": "error", "message": "invalid message"}
        # a malformed typed message (lat not a number) -> error, not a disconnect
        ws.send_json({"type": "position", "lat": "abc", "lon": 1})
        assert ws.receive_json()["type"] == "error"
        # the socket is STILL usable: a valid position now drives the tour
        ws.send_json({"type": "position", "lat": 55.7525, "lon": 37.6231})
        assert ws.receive_json()["type"] in ("state", "narration")


def test_ws_caps_oversized_frame_before_parsing():
    settings.max_ws_frame_chars = 10
    try:
        client = _app()
        with client.websocket_connect("/ws") as ws:
            ws.send_text('{"type":"ping"}')  # 15 chars > 10 -> rejected pre-parse
            assert ws.receive_json() == {"type": "error", "message": "frame too large"}
    finally:
        settings.max_ws_frame_chars = 8_000_000 + 65_536


# --- coordinate bounds (M5) ------------------------------------------------- #
def test_ws_rejects_out_of_range_coordinates():
    client = _app()
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "position", "lat": 999.0, "lon": 0.0})  # lat > 90
        assert ws.receive_json()["type"] == "error"
        ws.send_json({"type": "position", "lat": 0.0, "lon": -999.0})  # lon < -180
        assert ws.receive_json()["type"] == "error"
        # a valid fix still drives the tour — the bad frames didn't drop the socket
        ws.send_json({"type": "position", "lat": 55.7525, "lon": 37.6231})
        assert ws.receive_json()["type"] in ("state", "narration")


# --- inbound message rate limit (M8) ---------------------------------------- #
def test_message_rate_limit_token_bucket():
    settings.ws_msgs_per_sec = 0.001  # ~no refill over the microseconds this test runs
    settings.ws_msg_burst = 3
    try:
        rt = main_module._SessionRuntime(object(), object(), "s" * 20)
        allowed = [rt.allow_message() for _ in range(5)]
        assert allowed[:3] == [True, True, True]  # burst budget
        assert allowed[3] is False and allowed[4] is False  # then throttled
    finally:
        settings.ws_msgs_per_sec = 20.0
        settings.ws_msg_burst = 40


def test_rate_limit_disabled_when_rate_zero():
    settings.ws_msgs_per_sec = 0.0
    try:
        rt = main_module._SessionRuntime(object(), object(), "s" * 20)
        assert all(rt.allow_message() for _ in range(100))  # limiter off
    finally:
        settings.ws_msgs_per_sec = 20.0


# --- X-Forwarded-For trust (H5) --------------------------------------------- #
class _FakeWS:
    def __init__(self, xff: str, peer: str) -> None:
        self.headers = {"x-forwarded-for": xff} if xff else {}
        self.client = type("C", (), {"host": peer})()


def test_client_ip_trusts_xff_only_behind_proxy():
    ws = _FakeWS("1.2.3.4, 9.9.9.9", "10.0.0.1")
    try:
        settings.trust_proxy = False
        assert main_module._client_ip(ws) == "10.0.0.1"  # spoofable XFF ignored -> peer
        settings.trust_proxy = True
        # rightmost entry is the one the trusted proxy appended (the real peer)
        assert main_module._client_ip(ws) == "9.9.9.9"
    finally:
        settings.trust_proxy = False


# --- input-size limits ------------------------------------------------------ #
def test_too_big_helper():
    settings.max_utterance_chars = 10
    settings.max_audio_b64_chars = 20
    try:
        assert _too_big({"text": "x" * 50}, "utterance") is True
        assert _too_big({"text": "ok"}, "utterance") is False
        assert _too_big({"data_b64": "y" * 50}, "audio") is True
        assert _too_big({"data_b64": "y" * 5}, "audio") is False
        assert _too_big({"lat": 1, "lon": 2}, "position") is False
    finally:
        settings.max_utterance_chars = 2000
        settings.max_audio_b64_chars = 8_000_000
