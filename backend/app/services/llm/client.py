"""LLM client wrapper around the Anthropic SDK + a fake for tests.

Roles map to models via ``router.model_for``. Two entry points:
  * complete_text — plain text (Narrator / Companion)
  * complete_json — structured JSON validated by the API (Scorer)

``FakeLLM`` returns canned responses so the pipeline and tests run with no
API key; real narration quality is exercised with a key via ``AnthropicLLM``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from contextvars import ContextVar
from typing import Any, Protocol

import httpx

from app.config import settings

# Set per WS turn so the meter can attribute LLM cost to a session (propagates
# through awaits within the task that sets it).
SESSION_ID: ContextVar[str] = ContextVar("aiguide_session", default="")
# The account tier of the current turn's session (feature: account tiers). "free" =>
# openai_model (DeepSeek) + wiki-only facts; "paid" => openai_model_paid (Gemini) +
# full web facts. Set per turn next to SESSION_ID; read by _model_for / _reasoning_for
# and the enricher. Defaults "free" so guests + any non-WS caller (sims/tests) behave.
SESSION_TIER: ContextVar[str] = ContextVar("aiguide_tier", default="free")
# How the guide addresses the LISTENER grammatically this turn: "masculine" | "feminine" | ""
# (neutral). Set per turn next to SESSION_TIER; read by the Narrator when it builds its prompt.
USER_ADDRESS: ContextVar[str] = ContextVar("aiguide_user_address", default="")

from .router import Role, model_for  # noqa: E402 — after the ContextVar defs above

# Global cap on simultaneous chat-completion POSTs (all roles, all client instances) so a
# bursty walk self-smooths under the provider's rate ceiling instead of tripping 429s. Lazy so
# it binds to the running loop; shared module-wide (the router builds a client per role).
_llm_sem: asyncio.Semaphore | None = None
_llm_bg_sem: asyncio.Semaphore | None = None

# True inside a background LLM call (narration pre-gen, area/enrichment prefetch). Such calls
# ALSO hold a slot in the smaller background semaphore, so under throttling they're bounded and
# the LIVE narrator/scorer/companion path keeps global slots (never starves behind pre-warming).
LLM_BACKGROUND: ContextVar[bool] = ContextVar("aiguide_llm_bg", default=False)


def _get_llm_sem() -> asyncio.Semaphore:
    global _llm_sem
    if _llm_sem is None:
        _llm_sem = asyncio.Semaphore(max(1, settings.llm_max_concurrency))
    return _llm_sem


def _get_bg_sem() -> asyncio.Semaphore:
    global _llm_bg_sem
    if _llm_bg_sem is None:
        _llm_bg_sem = asyncio.Semaphore(max(1, settings.llm_bg_concurrency))
    return _llm_bg_sem


def as_background(coro):
    """Wrap a coroutine so every LLM call it makes is marked background (lower-priority
    sub-budget). Set inside the coroutine so it lands in the spawned task's own context copy —
    use at fire-and-forget launch sites: ensure_future(as_background(warm_narration(...)))."""

    async def _runner():
        LLM_BACKGROUND.set(True)
        return await coro

    return _runner()


def _retry_after_seconds(resp: Any, fallback: float) -> float:
    """How long to wait before retrying a 429/503, honouring the server's hint. Prefer the
    standard `Retry-After` header (seconds), then OpenRouter's `X-RateLimit-Reset` (epoch ms),
    else the caller's computed backoff. Capped so a bad header can't stall a walk."""
    hdr = resp.headers if resp is not None else {}
    ra = hdr.get("retry-after")
    if ra:
        try:
            return min(float(ra), settings.llm_retry_after_cap_s)
        except ValueError:
            pass
    reset = hdr.get("x-ratelimit-reset")
    if reset:
        try:
            # epoch milliseconds -> seconds from now (best-effort; clamp to a sane window)
            secs = float(reset) / 1000.0 - _now_s()
            if 0 < secs < settings.llm_retry_after_cap_s:
                return secs
        except ValueError:
            pass
    return min(fallback, settings.llm_retry_after_cap_s)


def _now_s() -> float:
    import time

    return time.time()


def _prices_for(model: str) -> tuple[float, float]:
    """(in, out) USD/Mtok for a model. The paid model gets its own prices (Gemini is
    ~5×/10× DeepSeek); everything else uses the base (free) prices. Only feeds the
    fallback estimator — OpenRouter's per-call reported cost is authoritative."""
    if settings.openai_model_paid and model == settings.openai_model_paid:
        return (
            settings.openai_price_in_per_mtok_paid or settings.openai_price_in_per_mtok,
            settings.openai_price_out_per_mtok_paid or settings.openai_price_out_per_mtok,
        )
    return settings.openai_price_in_per_mtok, settings.openai_price_out_per_mtok

_log = logging.getLogger("aiguide.tokens")
if not _log.handlers:  # ensure token usage prints regardless of uvicorn's config
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    _log.addHandler(_h)
    _log.setLevel(logging.INFO)
    _log.propagate = False

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class BudgetExceeded(RuntimeError):
    """Raised instead of making an LLM call once the hard spend cap is hit, so the
    guide degrades to silence rather than burning money on an open endpoint."""


def _parse_json(text: str) -> dict[str, Any]:
    return json.loads(_FENCE.sub("", text).strip())


class TokenMeter:
    """Process-cumulative token/cost accounting for the OpenAI-compatible client.

    Logs per-call usage and a running total. Cost is estimated from the configured
    per-Mtok prices (0 => skip). The optional budget is only a soft warning — the
    real monthly cap must be set on the provider (OpenRouter) dashboard.
    """

    def __init__(self) -> None:
        self.calls = 0
        self.tok_in = 0
        self.tok_out = 0
        self.tok_cached = 0  # prompt tokens served from the provider cache
        self.provider_cost = 0.0  # USD reported by OpenRouter (accounts for cache)
        self.est_cost = 0.0  # per-model token estimate for calls the provider didn't cost
        self._warned = False
        self.errors = 0  # total LLM call failures (after retry)
        self.consecutive_failures = 0  # reset on any success — drives /ready
        self.by_session: dict[str, dict] = {}  # session_id -> {calls, cost, tok_in, tok_out}

    def note_failure(self) -> None:
        self.errors += 1
        self.consecutive_failures += 1

    def record_tts(self, chars: int, price_per_mchar: float) -> None:
        """Account for neural-TTS spend (char-based) in the same running total as LLM
        cost, so ``over_hard_cap`` gates audio synthesis too. Attributed to the session."""
        cost = chars / 1e6 * price_per_mchar
        self.est_cost += cost
        sid = SESSION_ID.get()
        if sid and sid in self.by_session:
            self.by_session[sid]["cost"] += cost

    def _attribute(self, ti: int, to: int, usage: dict[str, Any]) -> None:
        sid = SESSION_ID.get()
        if not sid:
            return
        if sid not in self.by_session and len(self.by_session) >= 2000:
            self.by_session.pop(next(iter(self.by_session)), None)  # FIFO cap
        s = self.by_session.setdefault(
            sid, {"calls": 0, "cost": 0.0, "tok_in": 0, "tok_out": 0}
        )
        s["calls"] += 1
        s["tok_in"] += ti
        s["tok_out"] += to
        if usage.get("cost") is not None:
            s["cost"] += float(usage["cost"])

    def snapshot(self) -> dict[str, Any]:
        top = sorted(self.by_session.items(), key=lambda kv: kv[1]["cost"], reverse=True)[:20]
        return {
            "calls": self.calls,
            "cost_usd": round(self.cost_usd, 4),
            "tok_in": self.tok_in,
            "tok_out": self.tok_out,
            "tok_cached": self.tok_cached,
            "errors": self.errors,
            "consecutive_failures": self.consecutive_failures,
            "tracked_sessions": len(self.by_session),
            "top_sessions": [
                {"session": k, "calls": v["calls"], "cost_usd": round(v["cost"], 4)}
                for k, v in top
            ],
        }

    @property
    def cost_usd(self) -> float:
        # Provider-reported cost (already reflects cache discounts) plus a per-model
        # estimate for any calls the provider didn't cost (prompt-cache off / dev).
        return self.provider_cost + self.est_cost

    def over_hard_cap(self) -> bool:
        """True once cumulative process spend reaches the configured hard cap."""
        cap = settings.usd_hard_cap
        return cap > 0 and self.cost_usd >= cap

    def record(self, role: Role, model: str, usage: dict[str, Any] | None) -> None:
        usage = usage or {}
        ti = int(usage.get("prompt_tokens", 0) or 0)
        to = int(usage.get("completion_tokens", 0) or 0)
        cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0)
        self.calls += 1
        self.tok_in += ti
        self.tok_out += to
        self.tok_cached += cached
        if usage.get("cost") is not None:
            self.provider_cost += float(usage["cost"])
        else:  # provider omitted cost — estimate from per-model prices
            pin, pout = _prices_for(model)
            self.est_cost += ti / 1e6 * pin + to / 1e6 * pout
        self.consecutive_failures = 0  # a successful call clears the failure streak
        self._attribute(ti, to, usage)
        budget = settings.usd_session_budget
        cost = self.cost_usd
        hit = f" cache={cached}" if cached else ""
        tail = f" | ~${cost:.4f}" + (f"/${budget:.0f}" if budget else "")
        _log.info(
            "%s %s: +%d/+%d tok%s | total in=%d out=%d cached=%d (%d calls)%s",
            role, model, ti, to, hit, self.tok_in, self.tok_out, self.tok_cached,
            self.calls, tail,
        )
        if budget and cost >= budget and not self._warned:
            self._warned = True
            _log.warning(
                "Session spend ~$%.2f reached the $%.0f soft budget. "
                "Set a hard monthly cap on the OpenRouter dashboard.",
                cost, budget,
            )


METER = TokenMeter()


class LLMClient(Protocol):
    async def complete_text(
        self, role: Role, system: str, user: str, *, max_tokens: int = 1024
    ) -> str: ...

    async def complete_json(
        self,
        role: Role,
        system: str,
        user: str,
        schema: dict[str, Any],
        *,
        max_tokens: int = 1024,
    ) -> dict[str, Any]: ...


class AnthropicLLM:
    """Real client. Requires ANTHROPIC_API_KEY (in env or settings)."""

    def __init__(self, api_key: str | None = None) -> None:
        import anthropic

        key = api_key or settings.anthropic_api_key or None
        self._client = anthropic.AsyncAnthropic(api_key=key)

    @staticmethod
    def _text(resp: Any) -> str:
        return "".join(b.text for b in resp.content if b.type == "text").strip()

    async def complete_text(
        self, role: Role, system: str, user: str, *, max_tokens: int = 1024
    ) -> str:
        # Match the OpenAI-compat path's per-role temperature (Anthropic has no
        # frequency/presence penalties, so temperature is the one shared knob).
        temperature = (
            settings.openai_narrator_temperature
            if role in (Role.NARRATOR, Role.LANDMARK)
            else settings.openai_text_temperature
        )
        resp = await self._client.messages.create(
            model=model_for(role),
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return self._text(resp)

    async def complete_json(
        self,
        role: Role,
        system: str,
        user: str,
        schema: dict[str, Any],
        *,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        resp = await self._client.messages.create(
            model=model_for(role),
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        return json.loads(self._text(resp))


class FakeLLM:
    """Deterministic stand-in. ``text_response`` / ``json_response`` may be a
    constant or a callable(role, system, user) -> value."""

    def __init__(self, text_response: Any = "[SILENCE]", json_response: Any = None) -> None:
        self._text = text_response
        self._json = json_response or {"scored": [], "next": None, "expand_radius": False}

    async def complete_text(
        self, role: Role, system: str, user: str, *, max_tokens: int = 1024
    ) -> str:
        value = self._text(role, system, user) if callable(self._text) else self._text
        return str(value)

    async def complete_json(
        self,
        role: Role,
        system: str,
        user: str,
        schema: dict[str, Any],
        *,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        return self._json(role, system, user) if callable(self._json) else self._json


class OpenAICompatLLM:
    """Any OpenAI-compatible /chat/completions endpoint — LM Studio, OpenRouter,
    vLLM, etc. One impl covers local (free) and cloud providers."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        default_model: str | None = None,
    ) -> None:
        self._url = (base_url or settings.openai_base_url).rstrip("/") + "/chat/completions"
        self._default = default_model or settings.openai_model
        self._client = httpx.AsyncClient(
            # Bound a single attempt so a slow/stalled provider can't hold a narration
            # tick for ~45s (×2 retries ×2 json re-ask ≈ 3 min). 22s covers a normal
            # DeepSeek call with headroom; _post_with_retry adds at most one retry.
            timeout=httpx.Timeout(22.0, connect=8.0),
            headers={
                "Authorization": f"Bearer {api_key or settings.openai_api_key}",
                "X-Title": "AI Audio Guide",
            },
        )

    def _model_for(self, role: Role) -> str:
        # Paid sessions use the premium model on every role (feature: account tiers) — EXCEPT the
        # barge-in Companion, which stays on the fast base/override model so a voice answer comes
        # back quickly (~3 s vs ~9 s). Its context (last narration, address, theme, conversation)
        # is all in the prompt, so a lighter model still answers in-context. When openai_model_paid
        # is unset, tiers are off and everyone gets the base path.
        if (
            SESSION_TIER.get() == "paid"
            and settings.openai_model_paid
            # JUDGE/OPTIMIZER are excluded on purpose: the judge must stay a DIFFERENT model
            # family from the generator (self-preference bias) and the optimizer picks its own
            # strong model — neither should be swapped for the paid generator.
            and role not in (Role.COMPANION, Role.ANSWER_FAST, Role.JUDGE, Role.OPTIMIZER)
        ):
            return settings.openai_model_paid
        override = {
            Role.SCORER: settings.openai_model_scorer,
            Role.NARRATOR: settings.openai_model_narrator,
            Role.LANDMARK: settings.openai_model_landmark,
            Role.COMPANION: settings.openai_model_companion,
            Role.ENRICHER: settings.openai_model_enricher,
            # Fast tier-1 answer: its own (fast, Groq-routed) model; falls back to the base model.
            Role.ANSWER_FAST: settings.openai_model_answer_fast,
            # Interestingness judge (Block 4): its own model, a different family than the
            # generator. Empty ⇒ falls back to the base model (fine for offline eval).
            Role.JUDGE: settings.openai_model_judge,
            # Prompt-rewrite proposer (Block 4 loop): the strongest reachable model.
            Role.OPTIMIZER: settings.openai_model_optimizer,
        }.get(role, "")
        model = override or self._default
        if not model:
            raise RuntimeError("No OpenAI-compatible model configured (set OPENAI_MODEL)")
        return model

    @staticmethod
    def _provider_for(role: Role) -> dict[str, Any] | None:
        """OpenRouter provider routing for a role (None => let OpenRouter choose). Only the fast
        tier pins a provider (e.g. Groq/Cerebras) so its <1s TTFT is guaranteed and it stays
        reachable past the OpenAI/Google/Anthropic geoblock. Ignored by plain-OpenAI backends."""
        if role is Role.ANSWER_FAST and settings.openai_provider_answer_fast:
            raw = settings.openai_provider_answer_fast
            order = [p.strip() for p in raw.split(",") if p.strip()]
            if order:
                return {"order": order}
        return None

    # Roles where reasoning (on a reasoning-capable model) can be safely capped: the
    # narration roles (Narrator and Landmark) just write prose for an already-chosen
    # place — the skip/silence judgment lives in the Scorer + the deterministic
    # short-circuits — so they need little thinking. On a reasoning model, leaving
    # Landmark uncapped once let its planning scaffold ("3-6 sentences? Yes…") leak into
    # the spoken text, so it is capped too. The Enricher just extracts facts — capped so
    # its planning does not pollute them. Scorer (significance) and Companion keep theirs.
    # No-op on a non-reasoning model (DeepSeek free, Mistral-large paid): the param is
    # only sent when openai_reasoning_* is configured (default off), so a non-reasoning
    # model never receives it.
    _REASONING_CAP_ROLES = frozenset({Role.NARRATOR, Role.LANDMARK, Role.ENRICHER})

    def _reasoning_for(self, role: Role) -> dict[str, Any] | None:
        # When tiers are on and the free model is not a reasoning model (DeepSeek),
        # never send it the reasoning param. When tiers are off (openai_model_paid unset),
        # keep the legacy config-driven behaviour so a single reasoning-model dev/eval
        # config still gets its cap.
        if settings.openai_model_paid and SESSION_TIER.get() != "paid":
            return None
        cap = settings.openai_reasoning_max_tokens
        if cap > 0 and role in self._REASONING_CAP_ROLES:
            return {"max_tokens": cap}
        if settings.openai_reasoning_effort:
            return {"effort": settings.openai_reasoning_effort}
        return None

    def _system_msg(self, system: str) -> dict[str, Any]:
        # Mark the static CORE+ROLE prefix for provider prompt caching (OpenRouter
        # cache_control). Plain string otherwise (LM Studio doesn't grok parts).
        if settings.openai_prompt_cache:
            return {
                "role": "system",
                "content": [
                    {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
                ],
            }
        return {"role": "system", "content": system}

    async def _chat(self, role: Role, system: str, user: str, max_tokens: int, **extra) -> str:
        if METER.over_hard_cap():
            _log.warning(
                "hard spend cap $%.2f reached — blocking %s call",
                settings.usd_hard_cap, role,
            )
            raise BudgetExceeded(f"spend cap ${settings.usd_hard_cap:.2f} reached")
        model = self._model_for(role)
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                self._system_msg(system),
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            **extra,
        }
        reasoning = self._reasoning_for(role)
        if reasoning:
            payload["reasoning"] = reasoning
        provider = self._provider_for(role)
        if provider:
            payload["provider"] = provider
        if settings.openai_fallback_models:
            # OpenRouter tries these in order server-side when the primary is throttled/down —
            # one call, no client round-trip. Keep same-tier so quality holds. The primary leads;
            # drop it from the tail so a shared free/paid fallback list can't list it twice.
            payload["models"] = [
                model, *[m for m in settings.openai_fallback_models if m != model]
            ]
        if settings.openai_prompt_cache:
            # ask OpenRouter to return cost + cached-token accounting in usage
            payload["usage"] = {"include": True}
        try:
            data = await self._post_with_retry(payload, role)
        except Exception:
            METER.note_failure()  # feeds /ready + error counters
            raise
        METER.record(role, model, data.get("usage"))
        # Some models/providers return a message with content=None (empty/filtered response, or a
        # reasoning model that emitted only reasoning). Coerce to "" so a null never crashes the
        # caller with AttributeError — an empty completion is handled downstream as silence/empty.
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        return (msg.get("content") or "").strip()

    async def _post_with_retry(self, payload: dict, role: Role) -> dict:
        """POST with retries on a transient failure (timeout / 5xx / 429). A 429 (rate limit —
        common as STT+TTS+LLM share one OpenRouter key) clears with backoff, so it IS retried;
        other 4xx (region block / bad request) is not — that just wastes a call.

        The POST holds a slot in the global concurrency semaphore (self-smooths bursts under the
        provider's rate ceiling); the slot is RELEASED during inter-attempt backoff so a sleeping
        retry never starves a fresh call. A 429/503 honours the server's Retry-After header, so a
        retry lands right after the limit resets rather than hammering blindly; the fallback
        backoff carries jitter to de-sync a herd of calls that all got throttled together."""
        attempts = max(1, settings.llm_max_retries)
        sem = _get_llm_sem()
        # A background call ALSO holds a slot in the smaller bg semaphore for its WHOLE life
        # (incl. retries/backoff), so under throttling background work queues on the bg budget
        # while the live path keeps the global slots.
        async with AsyncExitStack() as stack:
            if settings.llm_bg_concurrency > 0 and LLM_BACKGROUND.get():
                await stack.enter_async_context(_get_bg_sem())
            return await self._retry_loop(payload, role, sem, attempts)

    async def _retry_loop(
        self, payload: dict, role: Role, sem: asyncio.Semaphore, attempts: int
    ) -> dict:
        for attempt in range(attempts):
            try:
                async with sem:  # cap concurrent in-flight POSTs; freed during backoff below
                    resp = await self._client.post(self._url, json=payload)
                    resp.raise_for_status()
                    return resp.json()
            except (httpx.TimeoutException, httpx.TransportError) as e:
                if attempt == attempts - 1:
                    raise
                _log.warning("LLM %s transient error, retrying: %s", role, e)
                await asyncio.sleep(0.6 * (attempt + 1) + random.uniform(0, 0.4))
            except httpx.HTTPStatusError as e:
                code = e.response.status_code
                retryable = code >= 500 or code == 429
                if attempt == attempts - 1 or not retryable:
                    raise  # 4xx (except 429) won't get better on retry
                _log.warning("LLM %s %s, retrying", role, code)
                base = settings.llm_retry_backoff_s if code == 429 else 0.6
                wait = _retry_after_seconds(
                    e.response, base * (attempt + 1) + random.uniform(0, 0.5)
                )
                await asyncio.sleep(wait)
        raise RuntimeError("unreachable")

    @staticmethod
    def _sampling_for(role: Role) -> dict[str, Any]:
        """Per-role sampling. Narration roles (Narrator/Landmark, incl. the area
        monologue which runs as NARRATOR) get a hotter temperature + frequency/presence
        penalties to break templated openings and repeated connectors (A1). A 0-valued
        penalty is omitted so single-model/LM-Studio configs stay unaffected."""
        if role in (Role.NARRATOR, Role.LANDMARK):
            params: dict[str, Any] = {"temperature": settings.openai_narrator_temperature}
            if settings.openai_narrator_frequency_penalty:
                params["frequency_penalty"] = settings.openai_narrator_frequency_penalty
            if settings.openai_narrator_presence_penalty:
                params["presence_penalty"] = settings.openai_narrator_presence_penalty
            return params
        # The JUDGE is an evaluator, not a writer: it must be as DETERMINISTIC as possible so the
        # same blurb scores the same every time (a gold standard can't flip-flop on borderline
        # cases). Low temp; the text default (hotter, for variety) would make it inconsistent.
        if role is Role.JUDGE:
            return {"temperature": settings.openai_judge_temperature}
        return {"temperature": settings.openai_text_temperature}

    async def complete_text(
        self, role: Role, system: str, user: str, *, max_tokens: int = 1024
    ) -> str:
        return await self._chat(role, system, user, max_tokens, **self._sampling_for(role))

    async def complete_json(
        self,
        role: Role,
        system: str,
        user: str,
        schema: dict[str, Any],
        *,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        response_format = {
            "type": "json_schema",
            "json_schema": {"name": "output", "strict": True, "schema": schema},
        }
        text = await self._chat(
            role, system, user, max_tokens, temperature=0, response_format=response_format
        )
        try:
            return _parse_json(text)
        except json.JSONDecodeError:
            # safety net: re-ask in plain text mode with an explicit instruction
            guard = f"{user}\n\nВерни строго валидный JSON по схеме, без markdown."
            text = await self._chat(role, system, guard, max_tokens, temperature=0)
            return _parse_json(text)

    async def stream_text(
        self, role: Role, system: str, user: str, *, max_tokens: int = 512
    ) -> AsyncIterator[str]:
        """Stream a text completion, yielding content deltas as they arrive — so the barge-in
        Companion can speak its FIRST sentence within ~2 s instead of after the whole answer.
        Usage is metered from the final chunk (stream_options.include_usage). Any error
        propagates so the caller can fall back to the non-streaming path."""
        if METER.over_hard_cap():
            raise BudgetExceeded(f"spend cap ${settings.usd_hard_cap:.2f} reached")
        model = self._model_for(role)
        payload: dict[str, Any] = {
            "model": model,
            "messages": [self._system_msg(system), {"role": "user", "content": user}],
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
            **self._sampling_for(role),
        }
        reasoning = self._reasoning_for(role)
        if reasoning:
            payload["reasoning"] = reasoning
        provider = self._provider_for(role)
        if provider:
            payload["provider"] = provider
        usage: dict[str, Any] | None = None
        try:
            async with self._client.stream("POST", self._url, json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("usage"):
                        usage = chunk["usage"]
                    choices = chunk.get("choices") or []
                    if choices:
                        piece = (choices[0].get("delta") or {}).get("content")
                        if piece:
                            yield piece
        except Exception:
            METER.note_failure()  # feeds /ready + error counters
            raise
        METER.record(role, model, usage)

    async def web_facts(
        self, system: str, user: str, *, max_results: int = 3, max_tokens: int = 400
    ) -> str:
        """Web-grounded fact extraction via the OpenRouter "web" plugin.

        The plugin injects live search results; the (Enricher-role) model distils
        verifiable facts from them. Returns the model's text — the caller decides
        what an empty/"no facts" answer means.
        """
        return await self._chat(
            Role.ENRICHER,
            system,
            user,
            max_tokens,
            temperature=0.3,
            plugins=[{"id": "web", "max_results": max_results}],
        )

    async def aclose(self) -> None:
        await self._client.aclose()
