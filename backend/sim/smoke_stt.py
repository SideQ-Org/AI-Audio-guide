"""Smoke test for the configured STT backend (faster-whisper).

Transcribes a WAV file and prints the text. First run downloads the Whisper model.

Run:  python -m sim.smoke_stt <path-to-wav>
"""

from __future__ import annotations

import asyncio
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

from app.config import settings
from app.services.stt.stt import build_stt


async def main(path: str) -> None:
    print(f"STT_BACKEND={settings.stt_backend}  model={settings.whisper_model_size}")
    with open(path, "rb") as f:
        audio = f.read()
    print(f"audio: {len(audio)} bytes")

    stt = build_stt()  # first build loads/downloads the model
    t0 = time.perf_counter()
    text = await stt.transcribe(audio, language=settings.default_language)
    dt = time.perf_counter() - t0
    print(f"\n[transcript] {text!r}")
    print(f"[took] {dt:.2f}s")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: python -m sim.smoke_stt <path-to-wav>")
    asyncio.run(main(sys.argv[1]))
