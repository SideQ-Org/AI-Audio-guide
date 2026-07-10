"""Speech-to-text for the voice barge-in path.

  * MockSTT          — canned transcript (wiring / tests, no heavy deps)
  * FasterWhisperSTT — local Whisper on GPU/CPU (optional; lazy-imported)

Sits at the transport layer: the WS handler decodes an audio clip, calls
``transcribe``, and feeds the text to ``orchestrator.on_utterance``.
"""

from __future__ import annotations

import asyncio
import io
from typing import Protocol


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


def build_stt() -> STTClient:
    from app.config import settings

    if settings.stt_backend == "faster_whisper":
        return FasterWhisperSTT(
            model_size=settings.whisper_model_size,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute_type,
        )
    return MockSTT(settings.stt_mock_text)
