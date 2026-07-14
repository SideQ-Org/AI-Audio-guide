"""FastAPI entrypoint.

  * GET  /health  — liveness
  * GET  /        — browser demo client (web/index.html)
  * WS   /ws      — drives the orchestrator: position/utterance in, narration/
                    reply/state out (audio is added with a TTS provider)
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hmac
import json
import logging
import re
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import ValidationError

from app.config import settings
from app.services.accounts.api import router as accounts_router
from app.services.accounts.auth import verify_token
from app.services.accounts.community_api import router as community_router
from app.services.agent.companion import heuristic_patch
from app.services.agent.factory import build_orchestrator
from app.services.agent.languages import normalize, stt_unclear, tour_bridge
from app.services.agent.narration_schedule import NarrationScheduler
from app.services.agent.orchestrator import (
    Orchestrator,
    OrchestratorOutput,
    State,
    merge_patch,
)
from app.services.agent.walklog import get_logger
from app.services.billing.api import router as billing_router
from app.services.llm.client import (
    METER,
    SESSION_ID,
    SESSION_TIER,
    USER_ADDRESS,
    as_background,
)
from app.services.metrics import GUIDE
from app.services.stt.stt import STTClient, build_stt
from app.shared.geo_math import haversine_m
from app.shared.schemas import (
    GeoPoint,
    Heading,
    Pace,
    WSAudioInput,
    WSAuth,
    WSControl,
    WSPositionUpdate,
    WSSetAddressForm,
    WSSetLanguage,
    WSSetTheme,
    WSUserUtterance,
)

app = FastAPI(title="AI Audio Guide", version="0.1.0")
app.include_router(accounts_router)  # /me, /walks (read history under auth); §7
app.include_router(billing_router)  # /billing/google/verify (grant paid tier); tiers
app.include_router(community_router)  # /community/* (friends, feed, challenges); COMMUNITY.md
_log = logging.getLogger("aiguide.ws")
_walk = get_logger()  # shared walk logger (aiguide.agent): pause/resume/listen events

# lightweight observability state
_active_sessions: set[str] = set()
_counters = {"step_errors": 0, "question_errors": 0}
_READY_FAIL_THRESHOLD = 3  # consecutive LLM failures => /ready goes unhealthy
_PING_INTERVAL_S = 20  # WS keepalive cadence: keeps mobile NAT/proxy mappings alive
# Accepted client session-id shape. Min 16 chars so a too-short id can't be brute-forced
# to resume someone else's tour (the client mints 32-char cryptographic ids).
_SID_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")

_WEB_INDEX = Path(__file__).resolve().parent.parent / "web" / "index.html"
_WEB_DASHBOARD = Path(__file__).resolve().parent.parent / "web" / "dashboard.html"

# Spoken-duration estimate for pacing sentence delivery (see _wait_played). Latin/Cyrillic
# scripts run ~14 chars/s; logographic scripts (Chinese/Japanese) pack far more content per
# character, so a flat 14 badly under-estimates them and releases the next sentence early —
# re-creating the queue pile-up weaving is meant to avoid. Keyed by normalized language.
_CHARS_PER_SEC: dict[str, float] = {"zh": 5.0, "ja": 5.0, "ko": 6.0}
_DEFAULT_CHARS_PER_SEC = 14.0


def _speech_seconds(text: str, language: str | None) -> float:
    """Estimated spoken duration of `text` in the session language, clamped to [1.5, 18.0]s."""
    rate = _CHARS_PER_SEC.get((language or "").split("-")[0].lower(), _DEFAULT_CHARS_PER_SEC)
    return min(max(len(text) / rate, 1.5), 18.0)
_orchestrator: Orchestrator | None = None
_stt: STTClient | None = None
_stt_lock = asyncio.Lock()


def get_orchestrator() -> Orchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = build_orchestrator()
    return _orchestrator


async def _load_entitlement(user_id: str) -> tuple[str, int]:
    """Resolve (effective_tier, tours_today) for a signed-in user from the durable
    store — the WS side of the /me entitlements. Degrades to ("free", 0) for guests,
    a base install without the accounts extra, or any DB hiccup, so auth never fails
    on entitlement lookup (feature: account tiers)."""
    if not user_id:
        return "free", 0
    try:
        from datetime import UTC, datetime, timedelta

        from app.services.accounts import repository as repo
        from app.services.accounts.db import accounts_enabled, session_scope

        if not accounts_enabled():
            return "free", 0
        since = datetime.now(UTC) - timedelta(hours=24)
        async with session_scope() as session:
            user = await repo.get_user(session, user_id=user_id)
            tier = repo.effective_tier(user)
            tours_today = await repo.count_walks_since(
                session, user_id=user_id, since=since
            )
        return tier, tours_today
    except Exception as e:  # noqa: BLE001 — entitlement is best-effort; default to free
        _log.warning("entitlement load failed for %s: %r", user_id, e)
        return "free", 0


async def _discard_walk(orch, session_id: str, user_id: str | None) -> None:
    """Delete the walk persisted for this session (a too-short walk the client asked to
    discard) and clear it off the SessionState so it can't be reused. Best-effort: a
    guest, a base install, or any DB hiccup just no-ops — the tour is never affected."""
    if not user_id:
        return
    try:
        from app.services.accounts import repository as repo
        from app.services.accounts.db import accounts_enabled, session_scope

        if not accounts_enabled():
            return
        state = await orch.store.load(session_id)
        walk_id = getattr(state, "walk_id", None)
        if not walk_id:
            return
        async with session_scope() as session:
            await repo.delete_walk(session, walk_id=walk_id, user_id=user_id)
        # Clear so a resumed/continued session starts a fresh walk rather than re-using
        # (and re-persisting) the just-deleted one.
        state.walk_id = None
        state.walk_last_event_at = None
        await orch.store.save(state)
    except Exception as e:  # noqa: BLE001 — discard is best-effort; never break the socket
        _log.warning("discard walk failed for session %s: %r", session_id, e)


async def get_stt() -> STTClient:
    """Build the STT client off the event loop. ``faster-whisper`` model load is a
    synchronous, multi-second (first-run: multi-minute download) operation — running
    it inline would freeze EVERY connection. We build it in a thread under a lock so
    it loads once and never blocks the loop, whether triggered by warm-up or the
    first voice question."""
    global _stt
    if _stt is None:
        async with _stt_lock:
            if _stt is None:
                _stt = await asyncio.to_thread(build_stt)
    return _stt


@app.on_event("startup")
async def _warm_stt() -> None:
    """Preload the STT model off the request path so the first voice question
    doesn't pay the (one-time) model-load cost. Non-fatal."""

    async def _load() -> None:
        try:
            await get_stt()
        except Exception:  # noqa: BLE001 — warming is best-effort
            pass

    asyncio.create_task(_load())


@app.on_event("startup")
async def _warm_db_pool() -> None:
    """Keep the durable-layer connection pool warm. The Supabase pooler sits ~190 ms RTT away
    (eu-central-1), so opening a fresh connection costs ~3.8 s — a cold pool would make the first
    /me + /community/* load after any idle gap pay that. Pre-open the pool at boot and re-touch it
    every 60 s (under pool_recycle) so real requests always find a warm, fresh connection; the
    keepalive also doubles as liveness (we run without pool_pre_ping). Best-effort; never fatal."""
    from sqlalchemy import text

    try:
        from app.services.accounts.db import accounts_enabled, session_scope
    except Exception:  # noqa: BLE001 — durable extra not installed
        return
    if not accounts_enabled():
        return

    async def _ping() -> None:
        async with session_scope() as s:
            await s.execute(text("select 1"))

    async def _keepalive() -> None:
        # Five concurrent pings so every core pooled connection (pool_size=5) stays warm + fresh.
        while True:
            try:
                await asyncio.gather(*[_ping() for _ in range(5)], return_exceptions=True)
            except Exception:  # noqa: BLE001 — pool warming must never crash the app
                pass
            await asyncio.sleep(60)

    asyncio.create_task(_keepalive())


@app.on_event("startup")
async def _warn_unbounded_spend() -> None:
    """The /ws endpoint is public. If a real (paid) LLM backend is wired but no hard
    spend ceiling is set, an abuser could drive unbounded cost — warn loudly at boot so
    a prod deploy can't silently run without USD_HARD_CAP (also set a cap on the
    provider dashboard; the code cap is only a backstop)."""
    if settings.agent_backend != "heuristic" and settings.usd_hard_cap <= 0:
        _log.warning(
            "SECURITY: public /ws on a paid backend (%s) with USD_HARD_CAP=0 — no spend "
            "ceiling. Set USD_HARD_CAP in .env and a monthly cap on the provider dashboard.",
            settings.agent_backend,
        )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "ai-audio-guide", "version": app.version}


@app.get("/ready")
async def ready() -> JSONResponse:
    """Readiness: unhealthy if the last few LLM calls all failed (quota/region/outage),
    so a wedged backend can be detected/restarted instead of looking 'healthy'."""
    ok = METER.consecutive_failures < _READY_FAIL_THRESHOLD
    body = {
        "ready": ok,
        "active_sessions": len(_active_sessions),
        "consecutive_llm_failures": METER.consecutive_failures,
    }
    return JSONResponse(body, status_code=200 if ok else 503)


@app.get("/stats")
async def stats(token: str = "") -> dict:
    """Admin-only ops view: active sessions, cumulative + per-session cost, errors.
    Disabled unless STATS_TOKEN is set and matches."""
    if not settings.stats_token or not hmac.compare_digest(token, settings.stats_token):
        raise HTTPException(status_code=404)
    return {
        "active_sessions": len(_active_sessions),
        "ws_step_errors": _counters["step_errors"],
        "ws_question_errors": _counters["question_errors"],
        **METER.snapshot(),
    }


def _dash_authorized(token: str) -> bool:
    """Dashboard gate. Dev-convenience: OPEN when no STATS_TOKEN is set (local runs),
    required when it is (so a prod deploy stays gated). More permissive than /stats
    on purpose — the dashboard is a local ops view."""
    return not settings.stats_token or hmac.compare_digest(token, settings.stats_token)


@app.get("/dashboard/data")
async def dashboard_data(token: str = "") -> JSONResponse:
    """All dashboard numbers in one payload: server/live, config, cost (METER) and
    product KPIs (GUIDE). Polled by the dashboard page."""
    if not _dash_authorized(token):
        raise HTTPException(status_code=404)
    meter = METER.snapshot()
    hard_cap = settings.usd_hard_cap
    cost = meter["cost_usd"]
    body = {
        "server": {
            "active_sessions": len(_active_sessions),
            "ws_step_errors": _counters["step_errors"],
            "ws_question_errors": _counters["question_errors"],
            "ready": METER.consecutive_failures < _READY_FAIL_THRESHOLD,
            "consecutive_llm_failures": METER.consecutive_failures,
            "version": app.version,
        },
        "config": {
            "agent_backend": settings.agent_backend,
            "geo_source": settings.geo_source,
            "enrichment_source": settings.enrichment_source,
            "stt_backend": settings.stt_backend,
            "default_language": settings.default_language,
            "model": settings.openai_model or "(per-role)",
            "usd_hard_cap": hard_cap,
            "usd_session_budget": settings.usd_session_budget,
        },
        "cost": {
            **meter,
            "cap_remaining_usd": round(hard_cap - cost, 4) if hard_cap > 0 else None,
            "cap_used_frac": min(round(cost / hard_cap, 3), 1.0) if hard_cap > 0 else None,
        },
        "guide": GUIDE.snapshot(),
    }
    return JSONResponse(body)


@app.get("/dashboard")
async def dashboard(token: str = "") -> HTMLResponse:
    """Self-contained ops dashboard page. Bakes the token into the page so its
    background polling of /dashboard/data authenticates automatically."""
    if not _dash_authorized(token):
        raise HTTPException(status_code=404)
    if _WEB_DASHBOARD.exists():
        html = _WEB_DASHBOARD.read_text(encoding="utf-8").replace("__DASH_TOKEN__", token)
        return HTMLResponse(html)
    return HTMLResponse("<h1>dashboard.html missing</h1>", status_code=500)


@app.get("/")
async def index() -> HTMLResponse:
    if _WEB_INDEX.exists():
        # bake the /ws access token into the served page so the browser client
        # authenticates automatically (empty in dev => open).
        html = _WEB_INDEX.read_text(encoding="utf-8").replace("__WS_TOKEN__", settings.ws_token)
        return HTMLResponse(html)
    return HTMLResponse("<h1>AI Audio Guide backend</h1><p>See /health</p>")


async def _synth_audio(text: str, tier: str, language: str) -> tuple[str | None, str | None]:
    """Neural TTS for a spoken line -> (base64, mime), or (None, None) to fall back to the
    client's on-device voice. Off unless TTS is configured AND the tier qualifies; any error
    inside ``synth`` already degrades to None, so this never breaks the tour. Usually a cache
    hit — the sentence was pre-synthesized on the approach (see _prewarm_audio)."""
    from app.services.tts.tts import get_tts, should_synth, voice_for

    if not should_synth(tier):
        return None, None
    tts = get_tts()
    audio = await tts.synth(text, voice=voice_for(language), language=language)
    if not audio:
        return None, None
    return base64.b64encode(audio).decode("ascii"), getattr(tts, "mime", "audio/mpeg")


async def _send(
    ws: WebSocket,
    out: OrchestratorOutput,
    audio_b64: str | None = None,
    audio_mime: str | None = None,
) -> None:
    await ws.send_json({"type": "state", "state": out.state})
    # Defense in depth: never ship the [SILENCE] sentinel to the client TTS. The
    # narration pipeline normalizes it away (split_hook), but guard the wire too.
    if out.kind == "narration" and out.text and out.text.strip() != "[SILENCE]":
        await ws.send_json(
            {
                "type": "narration",
                "text": out.text,
                "place_id": out.place_id,
                "place_name": out.place_name,
                "lat": out.lat,
                "lon": out.lon,
                "final": True,
                # Structured re-readable facts + a photo URL + category for the object card (not
                # spoken). Repeated on each sentence of the object; the client keeps the first.
                "card": out.card,
                "image": out.image,
                "category": out.category,
                # Neural audio for this sentence (PAID + TTS on); absent => client speaks
                # the text with its on-device voice.
                "audio_b64": audio_b64,
                "audio_mime": audio_mime,
            }
        )
    elif out.kind == "reply" and out.text:
        await ws.send_json(
            {"type": "reply", "text": out.text, "audio_b64": audio_b64, "audio_mime": audio_mime}
        )


class _SessionRuntime:
    """Per-connection runtime. A background *producer* generates the narration,
    decoupled from the GPS messages: ``position`` just refreshes the live context,
    while the producer emits ONE paragraph at a time, paced by the client's
    ``played`` signal (with a length-based fallback so older clients still flow).

    A question (``utterance``/``audio``) has top priority: it cancels the in-flight
    generation (its half-built, unsaved state is discarded) and is answered immediately
    by the Companion; then the producer resumes the tour where it left off. The inline
    reply is the whole answer — it is NOT re-woven into a later area beat.
    """

    def __init__(self, ws: WebSocket, orch: Orchestrator, session_id: str) -> None:
        self.ws = ws
        self.orch = orch
        self.session_id = session_id
        self.user_id: str | None = None  # set on a valid `auth` message; None = guest
        self.tier: str = "free"  # account tier; upgraded on a valid paid `auth` (feature: tiers)
        self.user_address: str = ""  # grammatical form to address the walker ("" = neutral)
        self.tours_today: int = 0  # walks started in the last 24h (loaded on auth; quota gate)
        self.quota_notified = False  # the daily-quota nudge was already sent this session
        self.live_position: GeoPoint | None = None
        self.live_heading = Heading()
        self.live_pace = Pace.SLOW
        # GPS outlier gate: a phone in dense/suburban cover spikes 100-200 m between
        # fixes and snaps back. Trusting those makes the guide narrate objects near a
        # phantom position ("talks about a kindergarten you never approached") and churn
        # topics. Track the last ACCEPTED fix + its arrival time so a jump implying an
        # impossible speed is dropped (with a jitter floor, and recovery after a run of
        # rejects so a genuine relocation / GPS re-lock isn't stuck forever).
        self._fix_pos: GeoPoint | None = None
        self._fix_t: float = 0.0
        self._gps_rejects = 0
        self.played = asyncio.Event()
        self.wake = asyncio.Event()  # context changed (new position / area / theme)
        # Narration is delivered ONE SENTENCE at a time via this scheduler, so a place that
        # enters the bubble is woven in at a sentence boundary (never a mid-word cut) and
        # the interrupted line resumes afterwards. `pending_insert` = a fresh bubble object
        # (id, significance) flagged by peek_bubble, consumed at the next boundary.
        self.sched = NarrationScheduler(settings.default_language)
        self._presynth_tasks: set[asyncio.Task] = set()  # bg TTS pre-warm task refs
        self.language = settings.default_language
        self.pending_insert: tuple[str, object] | None = None
        self._insert_id: str | None = None  # debounce: don't re-flag the same object
        # A newcomer we deferred because the object we're narrating outranks it — covered
        # briefly ("кстати, мы прошли …") once the current object is fully told.
        self.deferred_object: str | None = None
        # After a barge-in answer or an un-pause, speak one short "back to the tour" bridge
        # (resume the same topic if still relevant, else lead into fresh material). `_bridge_i`
        # rotates the phrase so it doesn't repeat.
        self._break_bridge = False
        self._bridge_i = 0
        self.resume = asyncio.Event()  # a barge-in finished; producer may continue
        self.barging = False
        self.listening = False  # mic open: hold the producer so it can't talk over the user
        self.closing = False  # the socket is gone — the producer must EXIT, not preempt
        self.paused = False  # user tapped Pause: hold the producer AND stop all generation
        self.paused_gate = asyncio.Event()  # set = running; cleared = paused (producer parks here)
        self.paused_gate.set()
        self.step_task: asyncio.Task | None = None
        # Background pre-generation of the NEXT area beat, warmed behind the current beat's
        # delivery so the inter-beat LLM latency isn't a silent gap. Read-only in the
        # orchestrator (no state mutation); committed single-threaded in _step.
        self._area_prefetch: asyncio.Task | None = None
        self.send_lock = asyncio.Lock()
        # Inbound-message token bucket (anti-flood). Starts full so a normal reconnect
        # burst (auth + language + theme + resume position) is never throttled.
        self._bucket_tokens = float(settings.ws_msg_burst)
        self._bucket_ts = time.monotonic()

    def allow_message(self) -> bool:
        """Token-bucket rate limit for inbound frames. Refills at ws_msgs_per_sec up to a
        burst of ws_msg_burst; returns False when a client floods faster than that. Normal
        walking cadence (position ~1/s, played acks, heartbeat) stays well under the cap."""
        rate = settings.ws_msgs_per_sec
        if rate <= 0:
            return True  # limiter disabled
        now = time.monotonic()
        self._bucket_tokens = min(
            float(settings.ws_msg_burst),
            self._bucket_tokens + (now - self._bucket_ts) * rate,
        )
        self._bucket_ts = now
        if self._bucket_tokens < 1.0:
            return False
        self._bucket_tokens -= 1.0
        return True

    def accept_fix(self, fix: GeoPoint) -> bool:
        """GPS outlier gate. Reject a fix that implies an impossible speed since the last
        accepted one — a teleport-and-snap-back spike that would move the tour to a
        phantom position. A small jump (< jump floor) is always accepted so normal
        jitter isn't gated; after a run of rejects we accept anyway so a genuine
        relocation or GPS re-lock recovers instead of freezing on a stale point. Gate
        off => `gps_max_speed_mps <= 0`."""
        max_speed = settings.gps_max_speed_mps
        now = time.monotonic()
        if max_speed <= 0 or self._fix_pos is None:
            self._fix_pos, self._fix_t, self._gps_rejects = fix, now, 0
            return True
        dist = haversine_m(fix, self._fix_pos)
        dt = max(now - self._fix_t, 0.5)  # floor dt so a fast burst can't divide by ~0
        implausible = (
            dist > settings.gps_jump_floor_m and dist / dt > max_speed
        )
        if implausible and self._gps_rejects < settings.gps_max_rejects:
            self._gps_rejects += 1
            _walk.info(
                "gps reject #%d: %.0fm in %.1fs (%.0f m/s > %.0f) -> drop fix",
                self._gps_rejects, dist, dt, dist / dt, max_speed,
            )
            return False
        self._fix_pos, self._fix_t, self._gps_rejects = fix, now, 0
        return True

    async def send_out(self, out: OrchestratorOutput) -> None:
        # Synthesize neural audio BEFORE taking the send lock — an ~sub-second HTTP call must
        # not block heartbeat pings / state frames on the same socket. No-op (returns None)
        # for free tier or when TTS is off, so the default path is unchanged. Usually a cache
        # hit: the sentence was pre-synthesized by _present/warm_narration on the approach.
        audio_b64 = audio_mime = None
        if out.kind in ("narration", "reply") and out.text and out.text.strip() != "[SILENCE]":
            audio_b64, audio_mime = await _synth_audio(out.text, self.tier, self.language)
        async with self.send_lock:
            await _send(self.ws, out, audio_b64, audio_mime)

    def _present(self, out: OrchestratorOutput) -> None:
        """Make `out` the current line AND pre-synthesize all its sentences in the background,
        so per-sentence `send_out` hits the TTS cache instead of a fresh network round-trip.
        This is what removes the inter-sentence gap under neural TTS: while sentence 1 is being
        spoken, sentences 2..N are already being synthesized. No-op unless TTS+tier qualify."""
        self.sched.set_current(out)
        cur = self.sched.current
        if cur is not None:
            self._prewarm_audio(cur.sentences)

    def _prewarm_audio(self, sentences: list[str]) -> None:
        from app.services.tts.tts import get_tts, should_synth, voice_for

        if not settings.tts_presynth or not should_synth(self.tier):
            return
        tts, voice = get_tts(), voice_for(self.language)
        for s in sentences:
            if not s or not s.strip():
                continue
            task = asyncio.ensure_future(tts.synth(s, voice=voice, language=self.language))
            self._presynth_tasks.add(task)
            task.add_done_callback(self._presynth_tasks.discard)

    async def send_json(self, obj: dict) -> None:
        async with self.send_lock:
            await self.ws.send_json(obj)

    async def send_walk_summary(self) -> None:
        """Generate the end-of-walk structured recap and push it to the client (Stop sheet)."""
        SESSION_ID.set(self.session_id)  # attribute the summary's LLM cost to this session
        SESSION_TIER.set(self.tier)  # tier -> model routing for the recap
        USER_ADDRESS.set(self.user_address)
        try:
            text = await self.orch.summarize(self.session_id)
        except Exception as e:  # noqa: BLE001 — a failed recap must never break the end flow
            _log.warning("summary failed (%s): %s", self.session_id, e)
            return
        if text:
            with contextlib.suppress(Exception):
                await self.send_json({"type": "summary", "text": text})

    async def run_heartbeat(self) -> None:
        """App-level keepalive. A periodic ping keeps mobile-carrier NAT / proxy
        mappings alive during narration lulls, so an idle socket isn't silently
        reaped (the cause of the reconnect storms seen on a real walk). The client
        ignores it; if the peer is already gone the send fails and the connection
        tears down promptly."""
        while True:
            await asyncio.sleep(_PING_INTERVAL_S)
            try:
                await self.send_json({"type": "ping"})
            except Exception:  # noqa: BLE001 — peer gone; let the receive loop end the session
                return

    async def run_producer(self) -> None:
        while True:
            self.step_task = asyncio.ensure_future(self._step())
            try:
                await self.step_task
            except asyncio.CancelledError:
                # Distinguish a barge-in preempt (handle_question cancelled the inner
                # step_task) from a real shutdown (the /ws finally cancelled the whole
                # producer). Without this check, a disconnect that lands while barging
                # was swallowed as a preempt -> the producer kept hot-looping forever,
                # sending into the closed socket (the "zombie producer" leak).
                if self.closing:
                    raise  # socket gone -> exit the producer for good
                if self.paused:  # user paused the tour -> park until resumed (not a barge-in)
                    await self.paused_gate.wait()
                    continue
                if self.barging:  # preempted by a question, not a shutdown
                    self.barging = False
                    await self.resume.wait()
                    continue
                raise  # genuine shutdown -> let the producer exit
            except Exception as e:  # noqa: BLE001 — a bad step must NOT kill the producer
                _counters["step_errors"] += 1
                _log.warning("producer step failed (%s): %s", self.session_id, e)
                await asyncio.sleep(2)  # throttle so a persistent failure can't hot-loop
            finally:
                self.step_task = None

    def _quota_blocked(self) -> bool:
        """Free tier over the daily tour quota (feature: account tiers): stay silent so
        no LLM spend happens, and nudge the client to upgrade. Paid / limit 0 => never
        blocked. Computed from the count loaded at auth time (one continuous session
        counts as one tour — the common open-app-and-walk case)."""
        limit = settings.free_tier_daily_tours
        return self.tier != "paid" and limit > 0 and self.tours_today >= limit

    async def _step(self) -> None:
        if self.listening:  # mic open — stay silent until the question is handled
            self.wake.clear()
            await self.resume.wait()
            return
        if self.paused:  # user paused — no narration AND no generation (unlike mute, which ticks)
            self.wake.clear()
            await self.paused_gate.wait()
            return
        if self._quota_blocked():  # free user out of daily tours — silent + upgrade nudge
            if not self.quota_notified:
                self.quota_notified = True
                with contextlib.suppress(Exception):
                    await self.send_json({"type": "quota", "scope": "daily"})
            self.wake.clear()
            await self.wake.wait()
            return
        if self.live_position is None:  # no GPS yet — idle until the first fix
            self.wake.clear()
            await self.wake.wait()
            return
        SESSION_ID.set(self.session_id)  # attribute LLM cost to this session
        SESSION_TIER.set(self.tier)  # tier -> model + enrichment for this turn
        USER_ADDRESS.set(self.user_address)

        # 0) Just back from a question or an un-pause? Lead back into the tour with ONE short
        #    spoken bridge. Return to the same topic if it's still relevant (we're within the
        #    resume radius of where we paused — a narrated object goes stale fast, an area/
        #    district line stays relevant longer), else drop it and move on to fresh material.
        if self._break_bridge:
            self._break_bridge = False
            top = self.sched.top_paused()
            radius = settings.resume_bridge_area_radius_m
            if top is not None and top.is_object:
                radius = settings.resume_bridge_obj_radius_m
            relevant = top is not None and self.sched.resumable(self.live_position, radius)
            if relevant:
                self.sched.resume(self.live_position, radius, add_connective=False)
            else:
                self.sched.drop_paused()
            mode = "continue" if relevant else "onward"
            bridge = OrchestratorOutput(
                state=State.NARRATING.value,
                kind="narration",
                text=tour_bridge(self.language, self._bridge_i, mode),
            )
            self._bridge_i += 1
            await self.send_out(bridge)
            await self._wait_played(bridge.text)
            return

        # 1) A fresh bubble object to weave in at this sentence boundary?
        ins = self.pending_insert
        if ins is not None:
            self.pending_insert = None
            obj_id, obj_sig = ins
            if self.sched.current_outranks(obj_sig):
                # The object we're telling outranks the newcomer -> don't interrupt; finish
                # it and cover the newcomer briefly afterwards ("кстати, мы прошли …").
                self.deferred_object = obj_id
                _walk.info("weave: %s deferred (lower priority), finishing current", obj_id)
            else:
                # Park the current line's remaining sentences and narrate the object NOW
                # (inline, using the pre-generated blurb). MUST fetch it here — otherwise
                # step 2 would just resume the line we paused and the object would never play.
                self.sched.pause_current(self.live_position)
                obj = await self.orch.narrate_object(self.session_id, obj_id)
                if obj.kind == "narration" and obj.text:
                    self._present(obj)
                    _walk.info("weave: inserted object %s", obj_id)

        # 2) Speak the next sentence of the current line (never cut — it always finishes),
        #    else resume a paused line, else cover a deferred newcomer.
        frame = self.sched.next_frame()
        if frame is None and self.sched.resume(self.live_position, settings.weave_radius_m):
            _walk.info("resume: continuing a paused line")
            frame = self.sched.next_frame()
        if frame is None and self.deferred_object is not None:
            dobj, self.deferred_object = self.deferred_object, None
            out = await self.orch.narrate_object(self.session_id, dobj, passed=True)
            if out.kind == "narration" and out.text:
                self._present(out)
                frame = self.sched.next_frame()
        if frame is not None:
            await self.send_out(frame)
            await self._wait_played(frame.text)  # pace per sentence
            return

        # 3) Nothing scheduled -> next narration. Prefer a ready pre-generated area beat
        #    (its LLM latency was hidden behind the previous beat's delivery); else ask the
        #    orchestrator live. Either way, if what we land is an area beat, start warming
        #    the NEXT one behind this beat's delivery.
        out = await self._take_prefetched_area()
        if out is not None:
            # Served a warmed beat WITHOUT a live on_position tick — so discovery didn't
            # run. Keep the Overpass disc warm (non-blocking, refetches only if the anchor
            # moved) so bubble weaving (peek_bubble) and the map don't starve across a
            # monologue streak. Bubble/reach objects still take priority: peek_bubble runs
            # every position frame, and once the outline is dry prefetch yields to on_position.
            self.orch._warm_inventory(self.session_id, self.live_position)
        else:
            out = await self.orch.on_position(
                self.session_id, self.live_position, self.live_heading, self.live_pace
            )
        await self._maybe_send_places()
        if out.kind == "narration" and out.text:
            self._present(out)
            if out.place_id is None:  # an area beat (not an object) -> warm the next one
                self._start_area_prefetch()
            frame = self.sched.next_frame()
            if frame is not None:
                await self.send_out(frame)
                await self._wait_played(frame.text)
                return
        await self.send_out(out)  # state / silence
        self.wake.clear()  # nothing to say -> idle until the context changes
        await self.wake.wait()

    def _start_area_prefetch(self) -> None:
        """Kick off (once) a background pre-generation of the next area beat. The task is
        read-only in the orchestrator, so it's safe to run concurrently with delivery, a
        weave, or a barge-in — nothing it does is persisted until _take commits it."""
        if not settings.area_prefetch:
            return
        if self._area_prefetch is not None and not self._area_prefetch.done():
            return  # one already in flight
        self._area_prefetch = asyncio.ensure_future(as_background(self._prefetch_area()))

    async def _prefetch_area(self) -> tuple[str, str, str | None] | None:
        SESSION_ID.set(self.session_id)  # attribute LLM cost to this session
        SESSION_TIER.set(self.tier)  # tier -> model routing for the warmed beat
        USER_ADDRESS.set(self.user_address)
        return await self.orch.prefetch_area(self.session_id, self.live_pace)

    async def _take_prefetched_area(self) -> OrchestratorOutput | None:
        """If a warmed area beat is ready (or nearly), commit it as the next narration.
        Awaits an in-flight prefetch rather than discarding it — it started during the
        last beat, so finishing it still beats a cold live call. Returns None (fall back
        to a live on_position) when there's no prefetch or it went stale / empty."""
        task = self._area_prefetch
        if task is None:
            return None
        try:
            pre = await task  # a barge-in cancels this step_task -> CancelledError re-raised
        except asyncio.CancelledError:
            raise  # leave the ref so the resumed step (or cleanup) can reuse/cancel it
        except Exception:
            self._area_prefetch = None
            return None
        self._area_prefetch = None
        if pre is None:
            return None
        topic, text, hook = pre
        try:
            out = await self.orch.commit_area(
                self.session_id, topic, text, hook, self.live_pace
            )
        except Exception:
            return None
        if out is not None:
            _walk.info("prefetch: served warmed area beat topic=%r", topic)
        return out

    def cancel_prefetch(self) -> None:
        if self._area_prefetch is not None and not self._area_prefetch.done():
            self._area_prefetch.cancel()
        self._area_prefetch = None

    async def _maybe_send_places(self) -> None:
        """Push the full set of nearby objects for the map when the search disc has
        (re)fetched — so the client can pin everything it found, not just narrated
        places. Best-effort: a failure here must never disturb narration."""
        try:
            inv = getattr(self.orch.discovery, "inventory", None)
            places = inv.take_places_update(self.session_id) if inv is not None else None
        except Exception:
            return
        if not places:
            return
        # Localize the pin labels to the session language, same as the narration title
        # (one cached batch call; romanizes anything it can't translate in time).
        try:
            language = (await self.orch.store.load(self.session_id)).language
        except Exception:
            language = "ru"
        loc = self.orch.pipeline.name_localizer
        pairs = [(p.tags, p.name) for p in places]
        names = loc.localize_batch(pairs, language)  # fast: cache + romanize, no LLM
        loc.warm_batch(pairs, language)  # background: translate uncached -> next frame
        items = [
            {
                "id": p.id,
                "name": name,
                "category": p.category,
                "lat": p.location.lat,
                "lon": p.location.lon,
            }
            for p, name in zip(places, names, strict=True)
        ]
        with contextlib.suppress(Exception):
            await self.send_json({"type": "places", "items": items})

    async def _wait_played(self, text: str) -> None:
        self.played.clear()
        # The client acks `played` at TTS COMPLETION, so release the NEXT sentence the moment
        # this one finishes — exact pacing, no queue pile-up, and a place weaves in right
        # after the current sentence. A generous cap (if the ack is ever dropped) prevents a
        # stall; the per-language duration estimate is only that ceiling now.
        cap = min(_speech_seconds(text, self.language) * 1.6, 25.0)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self.played.wait(), timeout=cap)

    async def pause_for_listen(self) -> None:
        """Mic opened (user about to ask): stop the producer's current step and hold,
        so the guide can't keep talking over the question — and its own TTS doesn't
        bleed into the recording. Released when the question is answered (or cancelled)."""
        self.listening = True
        self.barging = True
        self.resume.clear()
        _walk.info("listen on session=%s", self.session_id)
        if self.step_task is not None and not self.step_task.done():
            self.step_task.cancel()

    def resume_after_listen(self) -> None:
        """Mic closed with nothing to ask (empty clip): let the tour continue."""
        if self.listening:
            self.listening = False
            _walk.info("listen off session=%s", self.session_id)
            self.resume.set()

    async def pause_tour(self) -> None:
        """User tapped Pause: cut the current phrase and halt ALL generation (discovery/
        enrichment/narration) until resumed. Unlike `mute` (which still runs the per-tick
        agent work and just says nothing), the producer parks on `paused_gate`, so no LLM/
        Overpass spend happens while paused. The session stays alive (seen-list/history/area
        intro intact) so resume continues the SAME tour, not a fresh one."""
        if self.paused:
            return
        self.paused = True
        self.paused_gate.clear()
        _walk.info("pause session=%s", self.session_id)
        if self.step_task is not None and not self.step_task.done():
            self.step_task.cancel()  # drop the in-flight step (nothing is persisted mid-step)
        # Park the current line so an un-pause returns to it via a bridge (if still relevant).
        if settings.resume_bridge:
            self.sched.pause_current(self.live_position)
        with contextlib.suppress(Exception):
            await self.send_json({"type": "state", "state": "paused"})

    def resume_tour(self) -> None:
        """User tapped Play: continue the same tour from the current position."""
        if not self.paused:
            return
        self.paused = False
        self.paused_gate.set()
        if settings.resume_bridge:
            self._break_bridge = True  # lead back into the tour with a spoken bridge
        _walk.info("resume session=%s", self.session_id)
        self.wake.set()  # re-evaluate context now that we're live again

    async def _answer_streaming(self, text: str) -> bool:
        """Stream the Companion reply, speaking each sentence as it lands — barge-in latency:
        first audio in ~2 s instead of ~8 s for the whole answer. Returns True if it handled
        the answer; False (having emitted nothing) so handle_question falls back to the
        single-shot JSON path (also the route for a non-streaming backend / disabled flag).
        A mid-stream failure after ≥1 sentence still finalizes with what was already said."""
        companion = self.orch.companion
        if not settings.companion_stream or not hasattr(companion, "respond_stream"):
            return False
        st, cinp = await self.orch.prepare_utterance(self.session_id, text)
        sentences: list[str] = []
        try:
            async for sent in companion.respond_stream(cinp):
                sentences.append(sent)
                await self.send_out(
                    OrchestratorOutput(state=State.ANSWERING.value, kind="reply", text=sent)
                )
        except Exception as e:  # noqa: BLE001 — degrade to the fallback / partial answer
            if not sentences:
                _log.warning("companion stream failed pre-token (%s): %r", self.session_id, e)
                return False  # nothing spoken yet -> safe to fall back to the JSON path
            _log.warning("companion stream cut mid-reply (%s): %r", self.session_id, e)
        reply = " ".join(sentences).strip()
        if not reply:
            return False
        await self.orch.finalize_utterance(st, text, reply, heuristic_patch(text))
        return True

    async def handle_question(self, msg: dict, kind: str) -> None:
        """Top-priority barge-in: cancel the producer's current step, answer now."""
        self.listening = True  # in case the mic-open signal was lost — hold either way
        self.barging = True
        self.resume.clear()
        SESSION_ID.set(self.session_id)  # attribute the answer's LLM cost to this session
        SESSION_TIER.set(self.tier)  # tier -> model + enrichment for this answer
        USER_ADDRESS.set(self.user_address)
        if self.step_task is not None and not self.step_task.done():
            self.step_task.cancel()
        # Park whatever we were narrating so the post-answer bridge can return to it (if it's
        # still relevant) instead of jump-cutting back into the middle of a topic.
        if settings.resume_bridge:
            self.sched.pause_current(self.live_position)
        try:
            if kind == "audio":
                a = WSAudioInput.model_validate(msg)
                st = await self.orch.store.load(self.session_id)
                stt = await get_stt()
                text = await stt.transcribe(
                    base64.b64decode(a.data_b64), language=st.language
                )
                await self.send_json({"type": "transcript", "text": text})
                if not text.strip():  # nothing intelligible — say so instead of a vague reply
                    await self.send_json(
                        {"type": "error", "message": stt_unclear(st.language)}
                    )
                    return
            else:
                text = WSUserUtterance.model_validate(msg).text
            if not await self._answer_streaming(text):
                out = await self.orch.on_utterance(self.session_id, text)
                await self.send_out(out)
        except Exception as e:  # noqa: BLE001 — a failed question must not drop the session
            _counters["question_errors"] += 1
            _log.warning("question handling failed (%s): %r", self.session_id, e)
            with contextlib.suppress(Exception):
                st = await self.orch.store.load(self.session_id)
                await self.send_json({"type": "error", "message": stt_unclear(st.language)})
        finally:
            self.listening = False
            if settings.resume_bridge:
                self._break_bridge = True  # producer leads back in with a bridge
            self.resume.set()  # let the producer continue regardless


# process-wide count of concurrent WS connections per client IP (simple abuse guard)
_ip_conns: dict[str, int] = {}


def _client_ip(websocket: WebSocket) -> str:
    # X-Forwarded-For is client-controlled and trivially spoofed, so only trust it when
    # we know we sit behind a trusted proxy (settings.trust_proxy — set in prod where
    # Caddy terminates TLS). Behind exactly one proxy the RIGHTMOST entry is the one the
    # proxy appended (the real TCP peer); earlier entries could be forged by the client.
    # Without trust_proxy (dev / direct exposure) always use the socket peer.
    if settings.trust_proxy:
        xff = websocket.headers.get("x-forwarded-for", "")
        if xff:
            return xff.split(",")[-1].strip()
    return websocket.client.host if websocket.client else "?"


def _session_id_for(websocket: WebSocket) -> str:
    """Resume the SAME session across reconnects when the client supplies a stable
    ``?sid=``; otherwise mint a fresh one. Validated to a safe shape so the id can't
    be used to probe or collide with arbitrary store keys."""
    sid = websocket.query_params.get("sid", "").strip()
    return sid if _SID_RE.match(sid) else uuid.uuid4().hex


def _too_big(msg: dict, kind: str) -> bool:
    if kind == "utterance":
        return len(str(msg.get("text", ""))) > settings.max_utterance_chars
    if kind == "audio":
        return len(str(msg.get("data_b64", ""))) > settings.max_audio_b64_chars
    if kind == "auth":
        return len(str(msg.get("token", ""))) > 8192  # a JWT is ~1-2 KB
    return False


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    # --- gate the open endpoint BEFORE accepting (token + per-IP limit) -------
    if settings.ws_token and not hmac.compare_digest(
        websocket.query_params.get("token", ""), settings.ws_token
    ):
        await websocket.close(code=1008)  # policy violation
        return
    ip = _client_ip(websocket)
    if settings.max_connections_per_ip and _ip_conns.get(ip, 0) >= settings.max_connections_per_ip:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    # Count the connection only AFTER a successful accept and INSIDE the try, so the
    # finally always decrements. Previously a handshake/setup failure leaked a slot;
    # a flaky phone reconnecting could pile up leaks until it tripped
    # max_connections_per_ip and locked itself (and NAT-mates) out until restart.
    _ip_conns[ip] = _ip_conns.get(ip, 0) + 1
    rt: _SessionRuntime | None = None
    producer: asyncio.Task | None = None
    heartbeat: asyncio.Task | None = None
    try:
        orch = get_orchestrator()
        # A stable client ``?sid=`` resumes the SAME session on reconnect (seen-list,
        # history, area intro) instead of restarting the tour — the fix for the
        # "repeats + lost continuity after every WiFi/cell drop" symptom.
        rt = _SessionRuntime(websocket, orch, _session_id_for(websocket))
        _active_sessions.add(rt.session_id)
        producer = asyncio.ensure_future(rt.run_producer())
        heartbeat = asyncio.ensure_future(rt.run_heartbeat())
        while True:
            # Read text (not receive_json) so we can cap the frame BEFORE parsing — a
            # giant frame can't blow up memory. A malformed/oversized/invalid frame is
            # answered with an `error` and the loop continues; only a real disconnect
            # (WebSocketDisconnect) ends it. Previously an invalid `position` raised an
            # unhandled ValidationError that tore the socket down.
            raw = await websocket.receive_text()
            if len(raw) > settings.max_ws_frame_chars:
                await rt.send_json({"type": "error", "message": "frame too large"})
                continue
            if not rt.allow_message():
                # Flood control: drop silently (no error frame — that would only amplify
                # a flood). A well-behaved client never trips this.
                continue
            try:
                msg = json.loads(raw)
            except (ValueError, TypeError):
                await rt.send_json({"type": "error", "message": "invalid json"})
                continue
            if not isinstance(msg, dict):
                await rt.send_json({"type": "error", "message": "invalid message"})
                continue
            kind = msg.get("type")
            if kind in ("ping", "pong"):
                continue  # keepalive — nothing to do
            if _too_big(msg, kind):
                await rt.send_json({"type": "error", "message": "message too large"})
                continue
            try:
                await _dispatch(rt, orch, msg, kind)
            except ValidationError:
                await rt.send_json({"type": "error", "message": "invalid message"})
    except WebSocketDisconnect:
        pass
    finally:
        n = _ip_conns.get(ip, 0) - 1
        if n > 0:
            _ip_conns[ip] = n
        else:
            _ip_conns.pop(ip, None)
        if rt is not None:
            rt.closing = True  # tell the producer this cancel is a shutdown, not a barge-in
        if producer is not None:
            producer.cancel()
        if heartbeat is not None:
            heartbeat.cancel()
        if rt is not None and rt.step_task is not None:
            rt.step_task.cancel()
        if rt is not None:
            rt.cancel_prefetch()  # drop any in-flight background beat gen (read-only, unsaved)
        if producer is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await producer
        if rt is not None:
            _active_sessions.discard(rt.session_id)
        # The session is intentionally NOT deleted on disconnect: it is kept so a
        # reconnect (WiFi/cell drop) resumes the same tour. The store TTL-evicts idle
        # sessions (session_ttl_s) and LRU-caps the total, so this cannot leak.


async def _dispatch(rt: _SessionRuntime, orch: Orchestrator, msg: dict, kind: str | None) -> None:
    """Handle one inbound frame. Raises pydantic.ValidationError on a malformed typed
    message (position/language/theme/control), which the caller turns into an `error`
    frame without dropping the connection."""
    if kind == "position":
        p = WSPositionUpdate.model_validate(msg)
        fix = GeoPoint(lat=p.lat, lon=p.lon)
        # Gate GPS outliers only for the ACTIVE tour — that's where a phantom position
        # would drive discovery/narration. While paused nothing generates, so keep the
        # raw fix for the breadcrumb track + the resume position.
        if not rt.paused and not rt.accept_fix(fix):
            return  # GPS outlier — drop it (don't move the tour to a phantom position)
        rt.live_position = fix
        rt.live_heading = Heading(
            direction_deg=p.direction_deg, gaze_confidence=p.gaze_confidence
        )
        rt.live_pace = p.pace
        rt.wake.set()
        # Object weaving: if a fresh place is now in the narrate bubble, flag it so the
        # producer slots it in at the NEXT sentence boundary (never mid-word). Cheap
        # (cached inventory, no network); skipped while the producer is already held.
        if not (rt.paused or rt.listening or rt.barging):
            with contextlib.suppress(Exception):
                hit = await rt.orch.peek_bubble(rt.session_id, fix, rt.live_heading)
                if hit is not None and hit[0] != rt._insert_id:
                    rt._insert_id = hit[0]
                    rt.pending_insert = hit
                    _walk.info("bubble object %s flagged to weave", hit[0])
                    rt.wake.set()  # nudge the producer if it's idle
                elif hit is None:
                    rt._insert_id = None
        if rt.paused:
            # Tour is paused (no generation), but keep breadcrumbing the GPS track so the
            # walked-while-paused stretch is recorded and flagged for the history route,
            # and keep the walk from rotating into a second one on a long pause.
            await orch.breadcrumb_paused(rt.session_id, rt.live_position)
    elif kind == "played":
        rt.played.set()
    elif kind == "listen":
        # mic opened/closed on the client: pause the tour while the user speaks
        if bool(msg.get("on")):
            await rt.pause_for_listen()
        else:
            rt.resume_after_listen()
    elif kind == "pause":
        # user tapped Pause (in-app button or notification): halt narration + generation
        await rt.pause_tour()
    elif kind == "resume":
        rt.resume_tour()
    elif kind == "end":
        # Client tapped Stop. Halt the tour; if it asks to discard (walk was shorter
        # than the client's record threshold, e.g. <10 min), drop the persisted walk
        # so short sessions aren't recorded (design: "менее 10 минут не записываем").
        await rt.pause_tour()
        if msg.get("discard"):
            await _discard_walk(orch, rt.session_id, rt.user_id)
        else:
            # A kept walk: generate the structured recap in the background and push it to the
            # Stop sheet when ready (the sheet shows a spinner until it arrives).
            asyncio.ensure_future(rt.send_walk_summary())
    elif kind in ("utterance", "audio"):
        await rt.handle_question(msg, kind)
    elif kind == "auth":
        # Identify the user (design §6). Validate off the loop; degrade to guest on
        # failure (never refuse the socket). Binds user_id into the resumable session
        # so the tour keeps it across reconnects; a later invalid token downgrades it.
        a = WSAuth.model_validate(msg)
        user_id = await asyncio.to_thread(verify_token, a.token)
        # Resolve the account tier + today's tour count (feature: account tiers) so this
        # session gets the right model/enrichment and the daily-quota gate.
        tier, tours_today = await _load_entitlement(user_id or "")
        state = await orch.store.load(rt.session_id)
        state.user_id = user_id
        state.tier = tier
        await orch.store.save(state)
        rt.user_id = user_id
        rt.tier = tier
        rt.tours_today = tours_today
        rt.user_address = state.user_address  # restore the walker's form-of-address on resume
        rt.quota_notified = False  # re-evaluate the gate for this (re)auth
        await rt.send_json({
            "type": "auth",
            "authenticated": user_id is not None,
            "tier": tier,
            "tours_today": tours_today,
            "daily_tour_limit": None if tier == "paid" else settings.free_tier_daily_tours,
        })
    elif kind == "language":
        lang = WSSetLanguage.model_validate(msg)
        state = await orch.store.load(rt.session_id)
        state.language = normalize(lang.language)
        # The cached area facts were fetched in the OLD language; drop them so the area
        # monologue refetches (and re-narrates) in the new one. Place facts are
        # cache-keyed by language, so they refresh on their own.
        state.area_facts = None
        await orch.store.save(state)
        rt.language = state.language
        rt.sched.language = state.language  # resume connectives in the chosen language
        await rt.send_json({"type": "language", "language": state.language})
    elif kind == "theme":
        t = WSSetTheme.model_validate(msg)
        await orch.set_theme(rt.session_id, t.theme)
        rt.wake.set()
    elif kind == "control":
        c = WSControl.model_validate(msg)
        state = await orch.store.load(rt.session_id)
        state.control_patch = merge_patch(state.control_patch, c.patch)
        await orch.store.save(state)
        await rt.send_json({"type": "state", "state": state.state})
    elif kind == "address_form":
        # The user's optional grammatical form of address ("masculine"|"feminine"|"" neutral).
        af = WSSetAddressForm.model_validate(msg)
        form = af.form if af.form in ("masculine", "feminine") else ""
        state = await orch.store.load(rt.session_id)
        state.user_address = form
        await orch.store.save(state)
        rt.user_address = form
    else:
        await rt.send_json({"type": "error", "message": f"unknown type: {kind}"})
