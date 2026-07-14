"""Application configuration loaded from environment / .env."""

from __future__ import annotations

import json
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    @field_validator("openai_fallback_models", mode="before")
    @classmethod
    def _parse_model_list(cls, v: object) -> object:
        """Accept EITHER a comma-separated string (OPENAI_FALLBACK_MODELS=a,b) OR a JSON array
        — a bare `a,b` would otherwise crash startup (pydantic-settings JSON-decodes list envs).
        NoDecode on the field hands us the raw string here so we can split it ourselves."""
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return []
            return json.loads(s) if s.startswith("[") else [
                x.strip() for x in s.split(",") if x.strip()
            ]
        return v

    # Claude API
    anthropic_api_key: str = ""

    # Model routing (per role)
    model_scorer: str = "claude-haiku-4-5"
    model_narrator: str = "claude-sonnet-4-6"
    model_companion: str = "claude-sonnet-4-6"
    model_landmark: str = "claude-opus-4-8"
    model_enricher: str = "claude-haiku-4-5"

    # OpenAI-compatible provider (LM Studio / OpenRouter / etc.)
    #   LM Studio:  OPENAI_BASE_URL=http://localhost:1234/v1  OPENAI_API_KEY=lm-studio
    #   OpenRouter: OPENAI_BASE_URL=https://openrouter.ai/api/v1  OPENAI_API_KEY=sk-or-...
    openai_base_url: str = "http://localhost:1234/v1"
    openai_api_key: str = ""
    openai_model: str = ""  # default model for every role (also the FREE-tier model)
    openai_model_scorer: str = ""  # optional per-role override (else openai_model)
    openai_model_narrator: str = ""
    openai_model_companion: str = ""
    openai_model_landmark: str = ""
    openai_model_enricher: str = ""
    # OpenRouter server-side fallback: extra equivalent models tried (in order) when the primary
    # is unavailable/throttled (429) — sent as the `models` array so routing happens in ONE call,
    # no client round-trip. Keep these SAME-TIER (a different provider of the same/comparable
    # model) so quality doesn't drop. Empty => feature off (single `model`). Comma-separated OR a
    # JSON array (see _parse_model_list), e.g.
    #   OPENAI_FALLBACK_MODELS=deepseek/deepseek-chat,mistralai/mistral-large-2512
    openai_fallback_models: Annotated[list[str], NoDecode] = []
    # PAID-tier model (feature: account tiers). Paid sessions use this on every role
    # instead of openai_model; empty => paid falls back to openai_model (tiers off).
    #   prod: google/gemini-3.5-flash   free stays deepseek/deepseek-chat
    openai_model_paid: str = ""
    # Provider "thinking"/reasoning effort (OpenRouter). Gemini 3.x requires
    # reasoning (cannot be disabled); "low" minimises the expensive output tokens
    # it spends. "" => don't send the param (e.g. LM Studio, which would reject it).
    openai_reasoning_effort: str = ""  # "" | low | medium | high
    # Hard cap on reasoning tokens (OpenRouter). Reasoning is billed as expensive
    # output; even effort=low spends ~380 tok on Gemini 3.x. A small cap suppresses
    # most of it. >0 overrides effort; verify quality (eval) before lowering.
    openai_reasoning_max_tokens: int = 0
    # Prompt caching (OpenRouter): mark the static CORE+ROLE system prefix with
    # cache_control and request cost/cached-token accounting. Off for LM Studio.
    openai_prompt_cache: bool = False
    # Anti-429 (rate-limit) controls. All roles + STT + TTS share one OpenRouter key, so a
    # busy walk (area beats + enrichment + prefetch) bursts requests and gets 429-throttled.
    # `llm_max_concurrency` caps simultaneous chat-completion POSTs so we self-smooth under the
    # provider's rate ceiling instead of hammering it (companion barge-in streaming is exempt —
    # it stays the priority path). Retries honour the server's Retry-After; `llm_max_retries`
    # (429/5xx/timeout) rides out a throttling window; backoff gets jitter to de-sync a herd.
    llm_max_concurrency: int = 4
    # Background LLM work (narration pre-gen, area-beat prefetch, enrichment prefetch) is capped
    # to this many concurrent calls IN ADDITION to the global cap — so under throttling the LIVE
    # narrator/scorer/companion path keeps slots and never starves behind pre-warming. Should be
    # < llm_max_concurrency to leave live headroom. 0 disables the two-tier split.
    llm_bg_concurrency: int = 2
    llm_max_retries: int = 4
    llm_retry_backoff_s: float = 1.5  # base 429 backoff (×attempt, +jitter); capped by Retry-After
    llm_retry_after_cap_s: float = 20.0  # never wait longer than this even if the header says so

    # Narration sampling (variety — A1). Higher temperature + frequency/presence
    # penalties fight templated openings and repeated connectors ("а ещё…", same
    # intros). Penalties are standard OpenAI params (OpenRouter/DeepSeek/Mistral/LM
    # Studio accept them); a 0 value is omitted from the request. Narrator + Landmark
    # use the narrator knobs; other text roles (Companion) use openai_text_temperature.
    openai_text_temperature: float = 0.8       # baseline for text roles
    openai_narrator_temperature: float = 0.9   # narration/area — a touch hotter for variety
    openai_narrator_frequency_penalty: float = 0.3
    openai_narrator_presence_penalty: float = 0.3

    # Token/cost monitoring (USD per million tokens; 0 => unknown, cost not logged).
    # These are the FREE-tier (openai_model) prices. deepseek-chat: ~0.3 in / 0.9 out.
    openai_price_in_per_mtok: float = 0.0
    openai_price_out_per_mtok: float = 0.0
    # PAID-tier (openai_model_paid) prices for the fallback estimator; 0 => reuse the
    # free prices. gemini-3.5-flash on OpenRouter: 1.5 in / 9.0 out. (OpenRouter's
    # provider-reported per-call cost is authoritative regardless — this only feeds the
    # estimate when the provider omits cost.)
    openai_price_in_per_mtok_paid: float = 0.0
    openai_price_out_per_mtok_paid: float = 0.0
    # Soft warning threshold on process-cumulative spend (USD). 0 => no warning.
    # NOTE: a real monthly cap must be set on the OpenRouter dashboard.
    usd_session_budget: float = 0.0

    # Geo
    overpass_url: str = "https://overpass-api.de/api/interpreter"
    # Extra Overpass endpoints tried (in order) after `overpass_url` and before the
    # built-in public fallbacks. Comma-separated. For a production/high-load deploy point
    # `overpass_url` at a paid or SELF-HOSTED Overpass (the public mirrors are a single
    # point of failure + fair-use rate-limited) and optionally list backups here.
    overpass_mirrors: str = ""
    # Reverse geocoding (city/district/street for the "general -> specific" monologue).
    #   overpass -> derive admin areas + street from the Overpass endpoint above
    #   none     -> no geocoding (guide won't name the area)
    geocoder_source: str = "overpass"  # overpass | none
    # Re-resolve the address after moving this far. Lower = the street name catches up
    # sooner after you turn onto a new one (the "долго находит улицу" lag); the request
    # is still off the hot-path and cheap relative to the LLM calls. 35 m (was 90): the
    # street was lagging up to 90 m behind, so the guide named the street you'd left.
    geocoder_min_move_m: float = 35.0

    # Area-level monologue (the spine that fills gaps between objects)
    area_enrich: bool = True  # fetch verified facts about the district/city (web search)
    area_max_beats: int = 4  # area beats per area before easing off (objects reset this)
    # Anti-fabrication: the area cascade ("tell an atypical fact про <city/district/street>")
    # makes the model INVENT specifics when it has no verified facts (a field walk fabricated
    # a "метеоритный кратер" in a fact-less suburb — a facts-only violation). When True, the
    # ungrounded cascade is skipped in fact-less areas; the planned arc + reach + real objects
    # carry the tour instead. Grounded areas cascade as before.
    area_cascade_requires_facts: bool = True
    # Pre-generate the NEXT outline area beat in the background WHILE the current one is
    # being spoken, so its LLM latency (10-17 s cold on a field walk) is hidden behind
    # delivery instead of opening a silent gap between beats ("медленно переключался
    # между блоками" at session start). Read-only prefetch — it never mutates session
    # state; the producer commits the result single-threaded and re-checks freshness, so
    # it cannot corrupt the running narration. Safety valve: set False to disable.
    area_prefetch: bool = True
    # Speak an instant, warm greeting the moment a walk starts (fills the load gap so the
    # tour begins immediately; the area intro follows). Off => the tour opens with the area intro.
    session_greeting: bool = True
    # Grammatical gender the guide uses when speaking ABOUT ITSELF in first person
    # ("я прошла" vs "я прошёл", "рада" vs "рад") — it should match the TTS voice
    # (default voice "Ara" is female). "feminine" | "masculine" | "neutral" (no gendered
    # self-reference — for languages/voices where it shouldn't be forced).
    assistant_gender: str = "feminine"
    # Stream the barge-in Companion reply sentence-by-sentence to TTS so the first sentence
    # is spoken within ~2 s instead of after the whole (~8 s) answer. Needs an OpenAI-compatible
    # backend (stream_text). Off => the single-shot JSON reply path. On this path tour-steering
    # (skip shops / shorter / mute) is derived heuristically from the question, not the LLM.
    companion_stream: bool = True
    # After a voice question or an un-pause, speak a short "back to the tour" bridge before
    # continuing (languages.tour_bridge) — returning to the SAME topic if it's still relevant
    # (we're still near where we paused) or leading into fresh nearby material if we've walked
    # past it. Off => resume silently (the old behaviour). Radii decide "still relevant": a
    # narrated OBJECT goes stale quickly (you pass it); an AREA/district line stays relevant
    # over a longer stretch.
    resume_bridge: bool = True
    resume_bridge_obj_radius_m: float = 70.0
    resume_bridge_area_radius_m: float = 180.0
    # Anti-repeat: two objects with the SAME name within this distance are treated as the same
    # real-world thing mapped twice (a park's node label + polygon), so the second isn't narrated
    # again. Small on purpose — genuinely different same-named places are farther apart. (Rivers/
    # promenades dedup by name WITHOUT distance — see LINEAR_CATEGORIES; same wikidata QID always.)
    dedup_name_radius_m: float = 60.0
    # Activate the cross-paragraph "next_hook" baton: the Narrator emits a short
    # internal HOOK: line that we strip from speech and hand to the next paragraph,
    # so transitions are woven rather than improvised cold. Kept on the creative
    # (temperature) text path — not JSON — so prose quality is unaffected.
    narrator_emit_hook: bool = True
    # Narrator appends a trailing `CARD:` block (2-3 framing-free facts) in the SAME call as the
    # spoken narration — the re-readable structured facts for the object card, stripped before TTS
    # (like the HOOK baton). Zero extra LLM cost. Off => cards fall back to the spoken text.
    narrator_emit_card: bool = True

    # Wiring (which implementations the orchestrator factory builds)
    agent_backend: str = "heuristic"  # heuristic | openai | anthropic
    geo_source: str = "fixture"  # fixture | overpass
    enrichment_source: str = "mock"  # mock | websearch

    # WebSearch enrichment (real facts via the OpenRouter "web" plugin). Kept off
    # the hot-path: only the top-K nearest candidates are enriched per tick, with a
    # timeout, and results are cached (in-memory + optional JSON file).
    web_search_max_results: int = 2  # web results per place (OpenRouter bills per result)
    web_search_max_tokens: int = 400
    enrich_top_k: int = 2  # how many top-ranked candidates to enrich per tick (current narration)
    # Look-ahead fact warming: facts for objects you're walking TOWARD (in the course
    # cone, within the live window) are fetched in the background so they're cached
    # before you arrive — narration on approach is then instant, not a cold web search.
    enrich_lookahead_k: int = 4
    enrich_timeout_s: float = 9.0  # web search needs ~5-7s; give it time so facts arrive
    # Wiki facts are always free; this only gates the PAID web-search fallback for
    # places WITHOUT a wiki article: search them iff type_weight >= this. 0 = full
    # quality (search every non-wiki place); raise it to trade some facts for cost.
    enrich_min_weight: float = 0.0
    enrich_cache_path: str = ""  # "" => memory only; a path persists facts across runs

    # STT (voice barge-in)
    stt_backend: str = "mock"  # mock | faster_whisper (local CPU/GPU) | openrouter (cloud, fast)
    stt_mock_text: str = "А когда его построили?"
    whisper_model_size: str = "small"
    whisper_device: str = "auto"
    whisper_compute_type: str = "auto"
    # Cloud STT (stt_backend=openrouter): OpenAI-compatible /audio/transcriptions. Reuses the LLM
    # creds by default (your OpenRouter key). ~1-2 s vs ~8-10 s for local CPU Whisper.
    # Mistral Voxtral: single non-OpenAI provider (no geoblock/403 risk — openai/whisper 403'd from
    # prod), perfect Russian, ~3 s, cheap. Verify: {base}/models?output_modalities=transcription
    stt_model: str = "mistralai/voxtral-mini-transcribe"
    stt_api_key: str = ""  # "" => reuse openai_api_key
    stt_base_url: str = ""  # "" => reuse openai_base_url
    stt_timeout_s: float = 15.0

    # Neural TTS (server-side). OFF by default (tts_backend="null"): the server ships text-only
    # narration and the client speaks it with on-device flutter_tts (as the MVP did). When ON,
    # PAID sessions get a neural voice synthesized here and attached (base64) to the narration
    # frame; free sessions still use the on-device voice. Uses the SAME OpenAI-compatible endpoint
    # as the LLM — OpenRouter now proxies /audio/speech — so by default it reuses openai_base_url /
    # openai_api_key (your OpenRouter creds); set tts_base_url/tts_api_key only to override.
    tts_backend: str = "null"  # null | openai (OpenAI-compatible, incl. OpenRouter)
    # Default is xAI Grok Voice: it returns mp3 over OpenRouter and works from geoblocked regions
    # where OpenAI/Google TTS are cut off (our prod). Gemini TTS works too but only emits pcm (needs
    # WAV-wrapping client-side); OpenAI gpt-4o-mini-tts is unreachable from the prod region.
    # List available speech models: GET {base}/models?output_modalities=speech .
    tts_model: str = "x-ai/grok-voice-tts-1.0"
    tts_voice: str = "Ara"  # Grok voices: Eve / Ara / Rex / Sal / Leo (OpenAI: alloy/nova/sage/…)
    tts_voice_by_lang: dict[str, str] = {}  # {"ru": "sage", ...}; falls back to tts_voice
    tts_format: str = "mp3"  # mp3 plays reliably on both iOS & Android (opus/ogg is flaky on iOS)
    tts_api_key: str = ""  # "" => reuse openai_api_key (your OpenRouter key)
    tts_base_url: str = ""  # "" => reuse openai_base_url (your OpenRouter base URL)
    tts_timeout_s: float = 8.0  # a short phrase synthesizes in <1s; cap so a hang degrades to text
    tts_tier_min: str = "paid"  # minimum tier that gets neural audio ("free" => everyone)
    tts_presynth: bool = True  # pre-synthesize upcoming sentences in the background (kills the
    #                          # inter-sentence gap + makes object arrival instant); off => synth
    #                          # lazily per sentence at send time
    tts_cache_path: str = ""  # "" => memory only; a path persists synthesized audio across runs
    tts_price_per_mchar: float = 15.0  # USD per 1M input chars (~$15/1M), for the cost meter

    # Revisit: when the walker RETURNS to an object told earlier this walk, add a fresh detail
    # ("вот мы и снова у …") instead of silence. Gated by route distance walked SINCE it was told,
    # so it never fires right after the main narration — only on a genuine loop back.
    revisit_enabled: bool = True
    revisit_radius_m: float = 60.0  # how close counts as "back at the object"
    revisit_min_route_m: float = 250.0  # must have walked this far along the route since telling it

    # Behaviour
    default_language: str = "ru"
    # Start the search at a medium radius so ONE Overpass query covers both dense
    # city centres and spread-out suburbs (where the nearest object is 150-300 m
    # away). Starting tiny (80 m) forced a slow expand-to-500 m chain in suburbs
    # that blew the tick deadline → "talks about the district but never any object".
    default_radius_m: float = 300.0
    max_radius_m: float = 500.0
    # The live "window" the orchestrator considers each tick: objects within this
    # radius are fact-warmed in the background and pinned on the map. It matches the
    # default search radius so suburban objects are found early. Note: being in the
    # window no longer means being narrated — that's gated by the much smaller
    # narrate_radius_m bubble below (the "passing by" trigger).
    weave_radius_m: float = 300.0
    # The "passing by" bubble: an object is narrated ONLY when the user comes this
    # close to it. Outside the bubble the area story spine (city/district/street)
    # carries the tour — the guide doesn't narrate objects scattered across the
    # wider search radius. Small so narration tracks where the user actually is.
    # 55 m: "right here" for a pedestrian (an object you walk alongside across a normal
    # street), without reaching several houses away. 45 m proved too tight on the field
    # walk — real side-passes hovered at 50-53 m (a park, a monument) and never fired the
    # bubble ("не отработал триггер мимо которого я прошёл"). Objects between this and
    # reach_radius_m are still reachable via the gaze-gated reach fallback.
    narrate_radius_m: float = 55.0
    # The reach fallback (gaze-gated) narrates an object AHEAD only within this radius —
    # much tighter than weave_radius_m so the guide doesn't announce a place 150-200 m
    # away ("triggered several houses off"). It fires only for what you're about to reach.
    reach_radius_m: float = 100.0
    # Cap how many (nearest) candidates are considered per tick — bounds the
    # Scorer's input/output size (its JSON grows linearly with candidate count).
    scorer_max_candidates: int = 6

    # Per-session object inventory — decouples Overpass from the hot path. A wide
    # disc of places is fetched once and reused for every tick (ranking against the
    # live position is free); Overpass is re-hit only when the user walks past
    # `inventory_refetch_frac` of the disc radius from the anchor it was fetched at.
    inventory_enabled: bool = True
    inventory_radius_m: float = 800.0  # wide prefetch disc cached per session
    inventory_refetch_frac: float = 0.5  # re-fetch after moving > frac*radius from the anchor
    inventory_pass_margin_m: float = 40.0  # recede this far past closest-approach => "passed"
    inventory_ttl_s: float = 3600.0  # evict idle session inventories
    inventory_max_sessions: int = 2000  # LRU cap on cached inventories

    # State store ("" => in-memory)
    redis_url: str = ""
    session_ttl_s: float = 3600.0  # evict idle in-memory sessions after this (0 => never)
    max_sessions: int = 2000  # hard LRU cap on in-memory sessions (0 => unbounded)

    # --- Security & limits (protect the public /ws and cap spend) ---------------
    # Shared access token for /ws. "" => open (dev/local). In prod set it and the
    # client must connect with ?token=<value> (baked into the built clients).
    ws_token: str = ""
    max_connections_per_ip: int = 8  # concurrent WS connections per client IP (0 => off)
    # Hard spend ceiling (USD) on cumulative process spend; 0 => off. Once reached,
    # LLM calls are blocked (the guide degrades to silence) instead of burning money.
    usd_hard_cap: float = 0.0
    max_utterance_chars: int = 2000  # reject longer text/voice questions
    max_audio_b64_chars: int = 8_000_000  # ~6 MB decoded clip ceiling (anti-DoS)
    # Hard ceiling on a single inbound WS frame (chars), checked BEFORE JSON parsing so
    # a giant frame can't blow up memory pre-validation. Must exceed the largest legit
    # frame (a base64 audio clip + JSON envelope), so default = audio cap + 64 KB slack.
    max_ws_frame_chars: int = 8_000_000 + 65_536
    stats_token: str = ""  # admin token for /stats; "" => endpoint disabled
    # Walk debug logging (aiguide.agent). VERBOSE emits the full per-tick trace —
    # coordinates, discovery/why-empty, every external call + count, selection
    # reasoning, and the reason for each silence — for live debugging (grep sid=<id>).
    # DIR, if set, ALSO writes that trace to <dir>/walk.log (rotating) so a long walk
    # survives the docker-logs ring buffer and can be pulled whole. Both read once at
    # first log setup, so set them via env before the process starts.
    walk_log_verbose: bool = True
    walk_log_dir: str = ""
    # GPS outlier gate (main.py `accept_fix`): drop a fix that implies an impossible
    # speed vs. the last accepted one — a phone in dense/suburban cover spikes 100-200 m
    # and snaps back, which otherwise narrates objects near a phantom position and churns
    # topics. A jump under gps_jump_floor_m is always kept (normal jitter); after
    # gps_max_rejects consecutive drops we accept anyway (recover from a real relocation
    # / GPS re-lock). Set gps_max_speed_mps<=0 to disable. 15 m/s ≈ 54 km/h — well above
    # walking/running, below a teleport.
    gps_max_speed_mps: float = 15.0
    gps_jump_floor_m: float = 40.0
    gps_max_rejects: int = 3
    # Trust X-Forwarded-For for the client IP. OFF by default (dev/direct exposure, where
    # XFF is client-spoofable). Set TRUE in prod where Caddy terminates TLS and appends
    # the real peer — then per-IP limits see real addresses, not the proxy's.
    trust_proxy: bool = False
    # Inbound WS message rate limit (token bucket per connection). Refills at
    # ws_msgs_per_sec up to ws_msg_burst; 0/sec => limiter off. Generous vs a real walk
    # (position ~1/s + played acks + heartbeat), tight vs a flood.
    ws_msgs_per_sec: float = 20.0
    ws_msg_burst: int = 40

    # --- Accounts & walk history (durable Postgres layer; empty => disabled) -----
    # SQLAlchemy *async* URL for the durable store (users/walks/walk_events). This is
    # a SEPARATE layer from the ephemeral session store (state/store.py) — it survives
    # the walk. Empty => accounts/history disabled (guest-only), the current MVP path.
    #   Supabase local dev (supabase CLI):
    #       postgresql+asyncpg://postgres:postgres@127.0.0.1:54322/postgres
    #   Supabase cloud: the project's DB connection string (postgresql+asyncpg://...)
    #   Tests:          sqlite+aiosqlite://  (in-memory)
    database_url: str = ""
    db_echo: bool = False  # log emitted SQL (debug only)
    # A pause longer than this (seconds) on the same session ends the current walk and
    # starts a new one on the next narrated object — so "morning + evening on one sid"
    # become two walks (design §5). 30 min default.
    walk_gap_s: float = 1800.0

    # --- Free/paid tier limits (feature: account tiers) --------------------------
    # Free accounts are cost-capped so ads roughly offset the (DeepSeek + wiki-only)
    # spend; paid accounts (Gemini + full web facts) are uncapped. Enforced
    # server-side; the client mirrors them for UX (upgrade prompts). 0 => unlimited.
    free_tier_daily_tours: int = 2  # new walks a free user may start per rolling 24h
    free_tier_walk_limit: int = 10  # saved walks retained for a free user (ring buffer)

    # Supabase JWT verification for WS auth (design §9a). Empty => auth disabled, every
    # session is a guest (current MVP behaviour). Set at least one verification path:
    #   supabase_jwks_url — asymmetric (RS256/ES256), the recommended path; the backend
    #     fetches + caches the project's public keys and verifies signatures locally.
    #   supabase_jwt_secret — legacy shared HS256 secret; simpler, secret lives here.
    supabase_jwks_url: str = ""       # https://<proj>.supabase.co/auth/v1/keys
    supabase_jwt_secret: str = ""     # legacy HS256 project secret (fallback)
    supabase_jwt_aud: str = "authenticated"  # expected `aud` claim

    # --- Billing: subscription receipt verification (feature: account tiers) -----
    # Google Play: a service-account JSON key (androidpublisher scope) verifies a
    # client's purchase token before granting the paid tier. Empty => /billing answers
    # 503 (dev without a store). The product ids must match the Play Console products
    # AND the client (mobile/lib/billing/billing_service.dart).
    google_play_package: str = ""            # e.g. com.yourco.aiguide
    google_service_account_json: str = ""    # path to the service-account key file
    billing_product_monthly: str = "premium_monthly"
    billing_product_yearly: str = "premium_yearly"

    # Server
    host: str = "127.0.0.1"
    port: int = 8000


settings = Settings()
