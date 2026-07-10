"""TTS interface + a null implementation.

Stage 4 streams narration as text over the WebSocket; audio synthesis is a
provider concern wired later (Cartesia / local Piper) behind ``TTSClient``,
exactly like the LLM provider. ``NullTTS`` yields nothing (text-only demo).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol


class TTSClient(Protocol):
    async def stream(self, text: str, *, voice: str, language: str) -> AsyncIterator[bytes]: ...


class NullTTS:
    """No audio — used while no TTS provider is configured."""

    async def stream(self, text: str, *, voice: str, language: str) -> AsyncIterator[bytes]:
        return
        yield b""  # pragma: no cover  (makes this an async generator)
