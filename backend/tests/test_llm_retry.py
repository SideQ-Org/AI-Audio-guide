"""Anti-429 behaviour of OpenAICompatLLM: Retry-After honouring + bounded retries.

Offline (httpx.MockTransport) — no network. Guards the rate-limit handling that keeps a walk
narrating under OpenRouter throttling instead of erroring out.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.config import settings
from app.services.llm.client import (
    LLM_BACKGROUND,
    OpenAICompatLLM,
    _retry_after_seconds,
    as_background,
)
from app.services.llm.router import Role


class _Resp:
    def __init__(self, headers: dict) -> None:
        self.headers = headers


def test_retry_after_seconds_prefers_header_and_caps() -> None:
    cap = settings.llm_retry_after_cap_s
    assert _retry_after_seconds(_Resp({"retry-after": "3"}), 99.0) == 3.0  # header wins
    assert _retry_after_seconds(_Resp({"retry-after": "99999"}), 1.0) == cap  # capped
    assert _retry_after_seconds(_Resp({}), 2.0) == 2.0  # no header -> fallback
    # bad header -> fallback
    assert _retry_after_seconds(_Resp({"retry-after": "soon"}), 1.5) == 1.5


def _client(handler) -> OpenAICompatLLM:
    c = OpenAICompatLLM(default_model="test-model")
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return c


def test_429_then_200_retries_and_succeeds() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            # Retry-After "0" -> no real sleep, so the test is instant.
            return httpx.Response(429, headers={"retry-after": "0"}, json={"error": "rate"})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}}], "usage": {}}
        )

    out = asyncio.run(_client(handler)._chat(Role.NARRATOR, "sys", "user", 64))
    assert out == "ok"
    assert calls["n"] == 2  # exactly one retry after the 429


def test_persistent_429_raises_after_max_retries(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_max_retries", 3)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, headers={"retry-after": "0"}, json={"error": "rate"})

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(_client(handler)._chat(Role.NARRATOR, "sys", "user", 64))
    assert calls["n"] == 3  # tried exactly llm_max_retries times, then gave up


def test_4xx_not_retried() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"error": "bad request"})

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(_client(handler)._chat(Role.NARRATOR, "sys", "user", 64))
    assert calls["n"] == 1  # a 400 won't get better on retry — single attempt


def test_as_background_marks_only_its_own_context() -> None:
    seen = {}

    async def probe() -> None:
        seen["bg"] = LLM_BACKGROUND.get()

    asyncio.run(as_background(probe()))
    assert seen["bg"] is True  # the wrapped coroutine sees background=True
    assert LLM_BACKGROUND.get() is False  # …and it never leaks to the caller's context


def test_fallback_models_added_when_configured(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_fallback_models", ["fb/alt-provider"])
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}}], "usage": {}}
        )

    asyncio.run(_client(handler)._chat(Role.NARRATOR, "sys", "user", 32))
    # OpenRouter server-side fallback: primary first, then the configured equivalents
    assert captured["body"]["models"] == ["test-model", "fb/alt-provider"]


def test_fallback_list_dedups_the_primary(monkeypatch) -> None:
    # A shared free/paid fallback list may contain the current primary — it must not appear twice.
    monkeypatch.setattr(settings, "openai_fallback_models", ["test-model", "fb/other"])
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}}], "usage": {}}
        )

    asyncio.run(_client(handler)._chat(Role.NARRATOR, "sys", "user", 32))
    assert captured["body"]["models"] == ["test-model", "fb/other"]  # primary once, then the rest


def test_no_models_key_without_fallback(monkeypatch) -> None:
    monkeypatch.setattr(settings, "openai_fallback_models", [])
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}}], "usage": {}}
        )

    asyncio.run(_client(handler)._chat(Role.NARRATOR, "sys", "user", 32))
    assert "models" not in captured["body"]  # single-model path, unchanged
