"""TTS interface + implementations.

Stage 4 streams narration as **text** over the WebSocket; the client speaks it with
on-device ``flutter_tts``. ``NullTTS`` keeps that text-only path (no provider configured).

``OpenAITTS`` adds an optional **neural** voice: for PAID sessions the producer synthesizes
each spoken sentence here (server-side, OpenAI-compatible ``/audio/speech``) and attaches the
audio to the narration frame, so the guide sounds human instead of robotic. It is entirely
dormant unless ``TTS_BACKEND=openai`` + a key is set. The endpoint is the same OpenAI-compatible
one the LLM uses, so it reuses the OpenRouter creds by default (OpenRouter proxies
``/audio/speech``) — no separate OpenAI key needed. Default voice is xAI Grok (mp3, works from
geoblocked regions where OpenAI TTS is cut off); list: ``{base}/models?output_modalities=speech``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import OrderedDict
from pathlib import Path
from typing import Protocol

import httpx

from app.config import settings
from app.services.llm.client import METER

_log = logging.getLogger("aiguide.tts")

# Per-process audio cache ceiling (bytes blobs are larger than facts, so a tighter cap).
_CACHE_CAP = 2000


class TTSClient(Protocol):
    async def synth(self, text: str, *, voice: str, language: str) -> bytes | None: ...


class NullTTS:
    """No audio — the client speaks the narration text with its on-device voice."""

    async def synth(self, text: str, *, voice: str, language: str) -> bytes | None:
        return None


class OpenAITTS:
    """Neural TTS via OpenAI's ``/audio/speech``. Returns encoded audio bytes (mp3 by
    default) for a short sentence, or ``None`` on any error/timeout so the caller degrades
    cleanly to the client's on-device voice. Results are cached (memory + optional disk),
    keyed by ``(sha1(text), voice, fmt)`` so a repeated title/phrase is never re-synthesized
    and is reused across sessions."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        fmt: str,
        timeout_s: float,
        price_per_mchar: float,
        cache_path: str = "",
    ) -> None:
        self._key = api_key
        self._base = base_url.rstrip("/")
        self._model = model
        self._fmt = fmt
        self._timeout = timeout_s
        self._price = price_per_mchar
        self._mem: OrderedDict[tuple[str, str, str], bytes] = OrderedDict()
        # In-flight dedup: concurrent synths of the SAME (text, voice, fmt) share one network
        # call. This is what makes pre-synth safe — the background pre-warm task and the live
        # send both await the same task instead of hitting the API twice.
        self._pending: dict[tuple[str, str, str], asyncio.Task[bytes | None]] = {}
        self._cache_path = cache_path
        if cache_path:
            self._load_disk(cache_path)

    @property
    def mime(self) -> str:
        return {
            "mp3": "audio/mpeg",
            "opus": "audio/ogg",
            "aac": "audio/aac",
            "flac": "audio/flac",
            "wav": "audio/wav",
        }.get(self._fmt, "application/octet-stream")

    def _key_for(self, text: str, voice: str) -> tuple[str, str, str]:
        h = hashlib.sha1(text.encode("utf-8")).hexdigest()  # noqa: S324 — cache key, not security
        return (h, voice, self._fmt)

    async def synth(self, text: str, *, voice: str, language: str) -> bytes | None:
        text = (text or "").strip()
        if not text:
            return None
        key = self._key_for(text, voice)
        cached = self._mem.get(key)
        if cached is not None:
            self._mem.move_to_end(key)  # LRU touch
            return cached
        # Share an already-running synth of the same text (pre-warm + live send racing).
        inflight = self._pending.get(key)
        if inflight is not None:
            return await inflight
        # Cost guard: a hard cap breach blocks synthesis (degrade to on-device voice)
        # exactly like it blocks LLM calls.
        if METER.over_hard_cap():
            _log.warning("tts skipped: over USD hard cap")
            return None
        task = asyncio.ensure_future(self._fetch_and_store(key, text, voice))
        self._pending[key] = task
        task.add_done_callback(lambda _t, k=key: self._pending.pop(k, None))
        return await task

    async def _fetch_and_store(
        self, key: tuple[str, str, str], text: str, voice: str
    ) -> bytes | None:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                r = await client.post(
                    f"{self._base}/audio/speech",
                    headers={"Authorization": f"Bearer {self._key}"},
                    json={
                        "model": self._model,
                        "input": text,
                        "voice": voice,
                        "response_format": self._fmt,
                    },
                )
            if r.status_code != 200:
                _log.warning("tts http %s: %s", r.status_code, r.text[:200])
                return None
            audio = r.content
        except Exception as e:  # noqa: BLE001 — transient network/timeout -> degrade to text
            _log.warning("tts synth failed: %s", e)
            return None
        METER.record_tts(len(text), self._price)
        self._store(key, audio)
        return audio

    def _store(self, key: tuple[str, str, str], audio: bytes) -> None:
        if key not in self._mem and len(self._mem) >= _CACHE_CAP:
            self._mem.popitem(last=False)  # evict LRU
        self._mem[key] = audio
        self._mem.move_to_end(key)
        if self._cache_path:
            self._save_disk(key, audio)

    # -- optional disk persistence (a directory of <sha1>.<voice>.<fmt> blobs) -------- #

    def _blob_path(self, key: tuple[str, str, str]) -> Path:
        h, voice, fmt = key
        return Path(self._cache_path) / f"{h}.{voice}.{fmt}"

    def _load_disk(self, path: str) -> None:
        try:
            p = Path(path)
            p.mkdir(parents=True, exist_ok=True)
            for blob in sorted(p.glob("*.*.*"))[-_CACHE_CAP:]:
                parts = blob.name.rsplit(".", 2)
                if len(parts) == 3:
                    self._mem[(parts[0], parts[1], parts[2])] = blob.read_bytes()
        except Exception as e:  # noqa: BLE001 — cache is best-effort
            _log.warning("tts disk cache load failed: %s", e)

    def _save_disk(self, key: tuple[str, str, str], audio: bytes) -> None:
        try:
            self._blob_path(key).write_bytes(audio)
        except Exception as e:  # noqa: BLE001 — cache is best-effort
            _log.warning("tts disk cache save failed: %s", e)


def build_tts() -> TTSClient:
    """Construct the TTS client from settings (once, like the LLM client). ``null`` backend
    or a missing key => ``NullTTS`` (text-only), so the feature is off by default.

    Endpoint + key default to the existing OpenAI-compatible LLM creds (``openai_base_url`` /
    ``openai_api_key`` — your OpenRouter setup, which now proxies ``/audio/speech``); set the
    ``tts_*`` overrides only to point TTS at a different provider than the LLM."""
    api_key = settings.tts_api_key or settings.openai_api_key
    base_url = settings.tts_base_url or settings.openai_base_url
    if settings.tts_backend == "openai" and api_key:
        return OpenAITTS(
            api_key=api_key,
            base_url=base_url,
            model=settings.tts_model,
            fmt=settings.tts_format,
            timeout_s=settings.tts_timeout_s,
            price_per_mchar=settings.tts_price_per_mchar,
            cache_path=settings.tts_cache_path,
        )
    return NullTTS()


def voice_for(language: str) -> str:
    """The configured voice for a session language (per-language override, else default)."""
    return settings.tts_voice_by_lang.get(language, settings.tts_voice)


_TIER_RANK = {"free": 0, "paid": 1}


def should_synth(tier: str) -> bool:
    """True when this session should get neural audio: TTS configured AND the tier qualifies.
    Shared by the producer (per-sentence send) and the pipeline pre-synth so both gate the
    same way — no wasted synth for free/guest/TTS-off sessions."""
    if settings.tts_backend != "openai":
        return False
    return _TIER_RANK.get(tier, 0) >= _TIER_RANK.get(settings.tts_tier_min, 1)


_client: TTSClient | None = None


def get_tts() -> TTSClient:
    """Process-wide TTS client singleton. The producer AND the pipeline pre-synth share it so
    a sentence pre-warmed on the approach is served from ONE cache when it's spoken."""
    global _client
    if _client is None:
        _client = build_tts()
    return _client
