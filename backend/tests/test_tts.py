"""Offline unit tests for the neural TTS provider (no network).

We patch ``httpx.AsyncClient`` inside the tts module so ``OpenAITTS.synth`` exercises the
real code path (request shape, cache, error degradation) without a key or a socket.
"""

from __future__ import annotations

import asyncio

import httpx

from app.services.tts import tts as tts_mod
from app.shared.schemas import WSNarration


class _FakeResp:
    def __init__(self, status_code: int = 200, content: bytes = b"AUDIO", text: str = "") -> None:
        self.status_code = status_code
        self.content = content
        self.text = text


def _fake_client_cls(
    calls: list, *, status: int = 200, content: bytes = b"AUDIO", boom: bool = False
):
    """Build a stand-in for httpx.AsyncClient that records each POST into ``calls``."""

    class _Client:
        def __init__(self, *a, **k) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a) -> bool:
            return False

        async def post(self, url, **kw):
            calls.append({"url": url, **kw})
            if boom:
                raise httpx.TimeoutException("boom")
            return _FakeResp(status, content)

    return _Client


def _make(cache_path: str = "") -> tts_mod.OpenAITTS:
    return tts_mod.OpenAITTS(
        api_key="k",
        base_url="https://api.openai.com/v1",
        model="gpt-4o-mini-tts",
        fmt="mp3",
        timeout_s=8.0,
        price_per_mchar=12.0,
        cache_path=cache_path,
    )


def test_synth_returns_bytes_and_hits_openai(monkeypatch):
    calls: list = []
    monkeypatch.setattr(tts_mod.httpx, "AsyncClient", _fake_client_cls(calls))
    tts = _make()

    async def scenario():
        return await tts.synth("Привет, мир.", voice="sage", language="ru")

    audio = asyncio.run(scenario())
    assert audio == b"AUDIO"
    assert len(calls) == 1
    # Request shape: correct endpoint + body the OpenAI /audio/speech API expects.
    assert calls[0]["url"].endswith("/audio/speech")
    body = calls[0]["json"]
    assert body["model"] == "gpt-4o-mini-tts"
    assert body["voice"] == "sage"
    assert body["input"] == "Привет, мир."
    assert body["response_format"] == "mp3"


def test_synth_caches_repeat(monkeypatch):
    calls: list = []
    monkeypatch.setattr(tts_mod.httpx, "AsyncClient", _fake_client_cls(calls))
    tts = _make()

    async def scenario():
        a = await tts.synth("Одна фраза.", voice="sage", language="ru")
        b = await tts.synth("Одна фраза.", voice="sage", language="ru")
        return a, b

    a, b = asyncio.run(scenario())
    assert a == b == b"AUDIO"
    assert len(calls) == 1  # second call served from cache, no second HTTP request


def test_synth_inflight_dedup(monkeypatch):
    # Two concurrent synths of the SAME text share ONE network call (the pre-warm task and the
    # live send racing). Without dedup this would be 2 HTTP calls (double spend, double latency).
    calls: list = []
    monkeypatch.setattr(tts_mod.httpx, "AsyncClient", _fake_client_cls(calls))
    tts = _make()

    async def scenario():
        return await asyncio.gather(
            tts.synth("Общая фраза.", voice="Ara", language="ru"),
            tts.synth("Общая фраза.", voice="Ara", language="ru"),
        )

    a, b = asyncio.run(scenario())
    assert a == b == b"AUDIO"
    assert len(calls) == 1  # shared one in-flight request


def test_synth_http_error_returns_none(monkeypatch):
    calls: list = []
    monkeypatch.setattr(tts_mod.httpx, "AsyncClient", _fake_client_cls(calls, status=500))
    tts = _make()

    async def scenario():
        return await tts.synth("Ошибка сервера.", voice="sage", language="ru")

    assert asyncio.run(scenario()) is None  # degrade to on-device voice, don't raise


def test_synth_timeout_returns_none(monkeypatch):
    calls: list = []
    monkeypatch.setattr(tts_mod.httpx, "AsyncClient", _fake_client_cls(calls, boom=True))
    tts = _make()

    async def scenario():
        return await tts.synth("Таймаут.", voice="sage", language="ru")

    assert asyncio.run(scenario()) is None


def test_empty_text_is_noop(monkeypatch):
    calls: list = []
    monkeypatch.setattr(tts_mod.httpx, "AsyncClient", _fake_client_cls(calls))
    tts = _make()

    async def scenario():
        return await tts.synth("   ", voice="sage", language="ru")

    assert asyncio.run(scenario()) is None
    assert calls == []  # never hits the network for blank input


def test_disk_cache_roundtrips(monkeypatch, tmp_path):
    calls: list = []
    monkeypatch.setattr(tts_mod.httpx, "AsyncClient", _fake_client_cls(calls))

    async def scenario(t):
        return await t.synth("На диск.", voice="sage", language="ru")

    first = _make(cache_path=str(tmp_path))
    assert asyncio.run(scenario(first)) == b"AUDIO"
    assert len(calls) == 1
    # A fresh client with the same cache dir loads the blob — no second HTTP call.
    second = _make(cache_path=str(tmp_path))
    assert asyncio.run(scenario(second)) == b"AUDIO"
    assert len(calls) == 1


def test_build_tts_off_by_default():
    # Default settings (tts_backend="null") => NullTTS, which never produces audio.
    assert isinstance(tts_mod.build_tts(), tts_mod.NullTTS)

    async def scenario():
        return await tts_mod.NullTTS().synth("x", voice="sage", language="ru")

    assert asyncio.run(scenario()) is None


def test_ws_narration_carries_optional_audio():
    # Contract round-trip: the audio fields serialize and default to None when absent.
    plain = WSNarration(text="Дом Пашкова.")
    assert plain.audio_b64 is None and plain.audio_mime is None
    withaudio = WSNarration.model_validate(
        {
            "type": "narration",
            "text": "Дом Пашкова.",
            "audio_b64": "QUJD",
            "audio_mime": "audio/mpeg",
        }
    )
    assert withaudio.audio_b64 == "QUJD"
    assert withaudio.audio_mime == "audio/mpeg"
