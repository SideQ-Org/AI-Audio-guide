"""Speech-to-text for the voice barge-in path.

  * MockSTT          — canned transcript (wiring / tests, no heavy deps)
  * FasterWhisperSTT — local Whisper on GPU/CPU (optional; lazy-imported)
  * OpenRouterSTT    — cloud Whisper via an OpenAI-compatible /audio/transcriptions endpoint;
                       ~1-2 s vs ~8-10 s for local CPU Whisper — the voice-latency fix.

Sits at the transport layer: the WS handler decodes an audio clip, calls
``transcribe``, and feeds the text to ``orchestrator.on_utterance``.
"""

from __future__ import annotations

import asyncio
import io
from typing import Protocol

from app.services.agent.walklog import get_logger

# Route STT failures through the walk logger (aiguide.agent) so an empty transcript's REAL cause
# (e.g. `stt http 402`) is visible in `docker logs | grep aiguide.agent`, tagged with the sid —
# a plain `logging.getLogger("aiguide.stt")` is silenced by uvicorn and vanished (the "два
# 'не расслышал'" debugging that had no server-side trace).
_log = get_logger()


class STTClient(Protocol):
    async def transcribe(self, audio: bytes, *, language: str = "ru") -> str: ...


class MockSTT:
    def __init__(self, text: str = "привет") -> None:
        self._text = text

    async def transcribe(self, audio: bytes, *, language: str = "ru") -> str:
        return self._text


class FasterWhisperSTT:
    """Local Whisper via faster-whisper (ctranslate2). Decodes the audio
    container with bundled ffmpeg/av, so webm/opus and wav both work."""

    def __init__(
        self, model_size: str = "small", device: str = "auto", compute_type: str = "auto"
    ) -> None:
        from faster_whisper import WhisperModel

        self._model = WhisperModel(model_size, device=device, compute_type=compute_type)

    async def transcribe(self, audio: bytes, *, language: str = "ru") -> str:
        return await asyncio.to_thread(self._transcribe_sync, audio, language)

    def _transcribe_sync(self, audio: bytes, language: str) -> str:
        segments, _ = self._model.transcribe(io.BytesIO(audio), language=language)
        return " ".join(s.text for s in segments).strip()


class OpenRouterSTT:
    """Cloud Whisper via an OpenAI-compatible ``/audio/transcriptions`` endpoint (OpenRouter by
    default). Multipart upload of the WAV clip → transcript. On ANY error returns "" so the
    barge-in degrades to a 'didn't catch that' nudge instead of breaking the tour."""

    def __init__(self, *, api_key: str, base_url: str, model: str, timeout_s: float) -> None:
        self._key = api_key
        self._base = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_s

    async def transcribe(self, audio: bytes, *, language: str = "ru") -> str:
        import httpx

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                r = await client.post(
                    f"{self._base}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self._key}"},
                    files={"file": ("clip.wav", audio, "audio/wav")},
                    data={"model": self._model, "language": language},
                )
            if r.status_code != 200:
                _log.warning("stt http %s: %s", r.status_code, r.text[:200])
                return ""
            return (r.json().get("text") or "").strip()
        except Exception as e:  # noqa: BLE001 — transient network/timeout -> degrade to no-text
            _log.warning("stt transcribe failed: %s", e)
            return ""


def build_stt() -> STTClient:
    from app.config import settings

    if settings.stt_backend == "openrouter":
        return OpenRouterSTT(
            api_key=settings.stt_api_key or settings.openai_api_key,
            base_url=settings.stt_base_url or settings.openai_base_url,
            model=settings.stt_model,
            timeout_s=settings.stt_timeout_s,
        )
    if settings.stt_backend == "faster_whisper":
        return FasterWhisperSTT(
            model_size=settings.whisper_model_size,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute_type,
        )
    return MockSTT(settings.stt_mock_text)
