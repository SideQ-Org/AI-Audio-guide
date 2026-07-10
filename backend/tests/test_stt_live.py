"""Local STT smoke (faster-whisper). Opt-in: needs the model downloaded.

Skipped unless faster-whisper is installed AND STT_LIVE=1 is set (the test
loads/downloads a Whisper model, so it stays out of the default suite).
"""

from __future__ import annotations

import asyncio
import io
import os
import struct
import wave

import pytest

pytest.importorskip("faster_whisper")
if not os.getenv("STT_LIVE"):
    pytest.skip("set STT_LIVE=1 to run the local STT model test", allow_module_level=True)

from app.services.stt.stt import FasterWhisperSTT  # noqa: E402


def _silence_wav(seconds: int = 1, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<" + "h" * rate * seconds, *([0] * rate * seconds)))
    return buf.getvalue()


def test_faster_whisper_loads_and_transcribes():
    stt = FasterWhisperSTT(model_size="tiny", device="cpu", compute_type="int8")
    text = asyncio.run(stt.transcribe(_silence_wav(), language="ru"))
    assert isinstance(text, str)
