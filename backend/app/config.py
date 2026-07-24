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
    model_answer_fast: str = ""  # Anthropic-path fast-tier model (usually the OpenAI path is used)
    model_landmark: str = "claude-opus-4-8"
    model_enricher: str = "claude-haiku-4-5"
    # Interestingness judge (Block 4). MUST be a different family than the generator
    # (self-preference bias). Off the hot path — a strong model is fine. Empty ⇒ falls
    # back to the narrator model (acceptable for offline eval, not for a real gold gate).
    model_judge: str = ""
    # Prompt-rewrite proposer for the self-improvement loop (Block 4 Phase 5). The strongest
    # frontier model available — it rewrites system prompts from the failure taxonomy. Empty
    # ⇒ falls back to the landmark (premium) model.
    model_optimizer: str = ""

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
    # Interestingness judge (Block 4), OpenAI-compat path. A reachable NON-generator family
    # (e.g. a Qwen/GLM/Mistral via OpenRouter from the geoblocked region). Empty ⇒ base model.
    openai_model_judge: str = ""
    # Prompt-rewrite proposer (Block 4 loop), OpenAI-compat path. The strongest reachable model.
    openai_model_optimizer: str = ""
    # Two-tier answer: a FAST model gives ONE instant sentence, then the Companion (strong tier)
    # continues/deepens without repeating it (see docs/MODEL_LATENCY_RESEARCH.md). Empty model or
    # answer_two_tier=False => single-tier (Companion only, the old behaviour). Reachable pick from
    # the geoblocked region: a Groq-routed open model via OpenRouter (TTFT <1s), pinned with
    # openai_provider_answer_fast (comma-separated OpenRouter provider order, e.g. "Groq,Cerebras").
    openai_model_answer_fast: str = ""
    openai_provider_answer_fast: str = ""  # OpenRouter provider order for the fast tier (optional)
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
    # The interestingness JUDGE (Block 4) must be deterministic — a gold-standard evaluator can't
    # give different verdicts on the same blurb. Near-0 so borderline cases score consistently.
    openai_judge_temperature: float = 0.0
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
    # Mirror RACE for a fetch: the first N mirrors are queried concurrently (staggered
    # by overpass_race_stagger_s so a fast healthy primary usually answers alone) and
    # the first success wins. Measured: the primary's server-side execution is ~5 s on a
    # dense disc and public mirrors fail unpredictably — sequential failover stacked
    # timeout+retry into the 10-26 s cold fetches seen in prod walk logs.
    overpass_race: int = 2
    overpass_race_stagger_s: float = 1.5
    # L2 DISK cache of raw Overpass responses (dir path; "" = off). Survives restarts —
    # prod restarts wiped every warm disc, so the next walk paid the cold fetch again —
    # and is shared across sessions walking the same area. OSM data changes on the scale
    # of days; the query center is snapped to a ~110 m grid so repeat walks re-hit it.
    overpass_disk_cache: str = ""
    overpass_disk_ttl_s: float = 86400.0
    # Reverse geocoding (city/district/street for the "general -> specific" monologue).
    #   overpass -> derive admin areas + street from the Overpass endpoint above
    #   none     -> no geocoding (guide won't name the area)
    geocoder_source: str = "overpass"  # overpass | none
    # Re-resolve the address after moving this far. Lower = the street name catches up
    # sooner after you turn onto a new one (the "долго находит улицу" lag); the request
    # is still off the hot-path and cheap relative to the LLM calls. 35 m (was 90): the
    # street was lagging up to 90 m behind, so the guide named the street you'd left.
    geocoder_min_move_m: float = 35.0
    # Grid cache for reverse geocoding (~11 m cells — fine, so parallel streets never
    # share a cached address and the street is resolved for where you actually are; a
    # coarse cell named the wrong street after a turn). A block's address is stable, so
    # a standing/slow walker resolves instantly from memory instead of a round-trip.
    geocoder_cache_ttl_s: float = 21600.0
    # Freshness vs latency (fixes the "рассказывал про Парковую, которую я прошёл" freeze):
    # a cache MISS while barely past the move-gate carries the last address one tick +
    # warms the cell in the background; but once the walker is more than this far from the
    # last COMMITTED fix, the old street is stale — resolve NOW (bounded-blocking), because
    # the pure background-carry never caught up while walking (each fresh 11 m cell missed,
    # so the street froze on the first fix until the walker stopped). Self-hosted Overpass
    # answers a point query in ~1 s, so a short blocking resolve keeps the street correct.
    geocoder_carry_max_m: float = 40.0
    geocoder_block_timeout_s: float = 3.0

    # Area-level monologue (the spine that fills gaps between objects)
    area_enrich: bool = True  # fetch verified facts about the district/city (web search)
    # DEEPEN: when the current area facts are all told, fetch the NEXT rotated search
    # angle (history → people → streets → today → events → names → industry → wars →
    # temples → nature → daily life → curiosities → science → culture) and append the
    # fresh facts, so a walker lingering in one area keeps hearing REAL new facts instead
    # of going silent — the guide "постоянно ищет факты про место, пока рассказывает".
    # Number of extra rounds beyond the first batch (0 disables deepening); capped at the
    # number of _AREA_ANGLES (14). Each round is a background web search, gated by the
    # per-angle in-flight dedup + disk cache, so this doesn't hammer the provider.
    area_deepen_max: int = 13
    # Trigger a deepen fetch once the not-yet-told area facts fall to this few. 3 (was 1):
    # start fetching the next angle while ~3 facts (≈30-45 s of talking) still remain, so
    # the fresh batch lands BEFORE the current runs dry — hides the ~15 s web-search gap
    # that caused the 2-4 tick silences between rounds.
    area_deepen_low_facts: int = 3
    # Warm this many angle-rounds AHEAD in one go when deepening, so the pipeline stays a
    # step in front of delivery (two searches in flight → the round after next is ready too).
    area_deepen_prefetch_ahead: int = 2
    area_max_beats: int = 4  # area beats per area before easing off (objects reset this)
    # Offline reserve target: build a queue by estimated spoken duration, not just a handful of
    # fallback items. Guided mode can prepare more coherent long-form material, so it gets a
    # bigger default budget than free walk. Hard caps bound queue growth and payload size.
    reserve_target_s: float = 360.0
    reserve_target_guided_s: float = 480.0
    reserve_hard_cap_s: float = 600.0
    reserve_max_items: int = 24
    reserve_audio_head_items: int = 6
    # Anti-fabrication: the area cascade ("tell an atypical fact про <city/district/street>")
    # makes the model INVENT specifics when it has no verified facts (a field walk fabricated
    # a "метеоритный кратер" in a fact-less suburb — a facts-only violation). When True, the
    # ungrounded cascade is skipped in fact-less areas; the planned arc + reach + real objects
    # carry the tour instead. Grounded areas cascade as before.
    area_cascade_requires_facts: bool = True
    # Hard cap on the FACT-LESS city fallback (area_cascade_requires_facts=True + no verified
    # facts + a known city). That path leans on "widely-known city knowledge", but once the real
    # facts are spent the model FABRICATES fresh specifics every tick ("первая почтовая станция",
    # "испытывали полигон") — and because each invention is textually different, is_repeat can't
    # stop the loop (a walk down 1-я Советская got 8 invented monologues in a row). After this
    # many grounded city lines in one dry stretch the fallback goes quiet; a real object or a new
    # area re-arms it. Keep small: a fact-less town deserves a line or two, not a lecture.
    area_cityless_max: int = 2
    # Dry-area gate: after this many CONSECUTIVE empty/suppressed area beats stop calling
    # the narrator for this area at all (each call burns 9-18 s of LLM latency to produce
    # [SILENCE] — the 17.07 walk logged 4 in a row). A real object, a new street, or a
    # new area re-opens the tap.
    area_dry_max: int = 3
    # Restore the OLD blocking inline area-facts fetch inside the tick (up to
    # enrich_timeout_s of silence). Default off: the fetch is warmed in the background
    # and the beat is skipped for one tick instead.
    area_enrich_inline: bool = False
    # Home-screen prewarm (WSPrewarm): the client pings its position before the tour so
    # the Overpass disc / geocode / area plan+facts are warm when «Поехали» is tapped.
    # The inventory warm self-gates (ensure() refetches only >400 m from the anchor +
    # a 90 s Overpass HTTP cache); the GEO warm (reverse-geocode + planner LLM + web
    # facts) is gated here because the geocoder has no cache of its own.
    prewarm_enabled: bool = True
    prewarm_min_move_m: float = 150.0
    prewarm_min_interval_s: float = 120.0
    # Shared persistent fact buffer (object + area facts). Keeps a warm factual substrate across
    # startup / route planning / transient network drops; rendered narration still stays in memory.
    fact_buffer_path: str = ""
    # Soft anti-"вторая библиотека подряд": an ordinary (no facts, <HIGH) object whose
    # category was narrated less than the cooldown ago is rank-demoted by the penalty
    # factor (a lone candidate still wins — coverage is never lost, only reordered).
    narrate_category_cooldown_s: float = 480.0
    narrate_category_penalty: float = 2.5
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
    # Two-tier barge-in answer: fast model speaks one instant sentence, then the Companion
    # continues without repeating it. Needs a fast model configured (openai_model_answer_fast).
    answer_two_tier: bool = True
    # After a voice question or an un-pause, speak a short "back to the tour" bridge before
    # continuing (languages.tour_bridge) — returning to the SAME topic if it's still relevant
    # (we're still near where we paused) or leading into fresh nearby material if we've walked
    # past it. Off => resume silently (the old behaviour). Radii decide "still relevant": a
    # narrated OBJECT goes stale quickly (you pass it); an AREA/district line stays relevant
    # over a longer stretch.
    resume_bridge: bool = True
    resume_bridge_obj_radius_m: float = 70.0
    resume_bridge_area_radius_m: float = 180.0
    # Late-binding seam stitch: a pre-generated blurb (object _narr_cache / prefetched area beat)
    # was rendered minutes before delivery against stale context, so its opening can't connect to
    # what was just spoken — the "disconnected cards" seam. When ON, a fast cheap LLM call rewrites
    # ONLY its first sentence at delivery time to continue the last spoken line; any failure or
    # timeout falls back to the untouched text. Live (non-cached) renders already see a fresh
    # CONTINUE_FROM and are never stitched. The timeout is deliberately tight: the one sensitive
    # spot is the weave insert, where the stitch delays a "speak instantly" moment. 3.0 covers
    # deepseek-chat's measured 1.6-2.9 s on the ANSWER_FAST route (probed live on prod, and the
    # QUALITY winner there — Groq-pinned llama/kimi were faster but fabricated or garbled RU).
    seam_stitch: bool = True
    seam_stitch_timeout_s: float = 3.0
    # Speak a short neutral "let me think" filler the instant a question arrives, so the STT
    # (~3 s) + answer LLM (~2 s) gap after the user asks isn't dead silence. The real answer
    # follows as the next reply. Off => answer with no filler (the old behaviour).
    thinking_filler: bool = True
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
    # More facts per COURSE (both object and area enrichment use these): raised so each
    # search returns a richer batch — the guide has more real material per angle before it
    # rotates to the next course (people → events → architecture → …). OpenRouter bills per
    # web result, but the paid tier wants depth ("пусть ищет все, больше фактов").
    web_search_max_results: int = 5  # web results per place
    web_search_max_tokens: int = 800
    enrich_top_k: int = 3  # how many top-ranked candidates to enrich per tick (current narration)
    # Look-ahead fact warming: facts for objects you're walking TOWARD (in the course
    # cone, within the live window) are fetched in the background so they're cached
    # before you arrive — narration on approach is then instant, not a cold web search.
    enrich_lookahead_k: int = 6
    # Web-search timeout. MUST exceed the real round-trip or EVERY area comes back factless and
    # the guide goes silent (cityless cap). Measured on prod: deepseek + the OpenRouter web plugin
    # distils ~3000 tokens of injected search results, which takes ~14-16 s for an area query — so
    # the old 9.0 s (and even 15 s) timed out on essentially every call (Долгопрудный: 32 empty /
    # 1 facts). 25 s clears it with margin under load; the area fetch is warmed in the background
    # behind the intro so the extra seconds don't delay the first beat.
    enrich_timeout_s: float = 25.0
    # When ELABORATING (going deeper on one object across follow-ups) and the cached facts are
    # thinner than this, fetch a bit MORE (angle-focused web search) so the deeper angles have
    # fresh material instead of running dry after a detail or two ("будет больше фактов искать").
    # Once per object; 0 disables the deepen fetch. Latency lands during a lull, so it's OK.
    elaborate_deepen_below_chars: int = 260
    # Wiki facts are always free; this only gates the PAID web-search fallback for
    # places WITHOUT a wiki article: search them iff type_weight >= this. 0 = full
    # quality (search every non-wiki place); raise it to trade some facts for cost.
    enrich_min_weight: float = 0.0
    enrich_cache_path: str = ""  # "" => memory only; a path persists facts across runs
    # On a first web-search miss, retry ONCE with a broadened natural-language query
    # (type words + name + city) before committing the permanent negative cache — the
    # exact form misses small local monuments the loose form finds.
    enrich_retry_broaden: bool = True
    # A NEGATIVE web-search result (no facts found) is cached with this TTL instead of
    # forever: a new/renamed object with no web presence today may gain an article — a
    # permanent negative froze it factless for the lifetime of the cache file. 7 days.
    enrich_negative_ttl_s: float = 604800.0
    # Steering prefix for the OpenRouter web plugin's search query — bias the engine
    # toward FRESH sources so «что здесь сейчас» reflects the current state.
    web_search_prompt: str = (
        "Prefer recent, authoritative sources; the place's CURRENT state and use matter."
    )

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
    # How far the walker may move and still RESUME a line we paused to weave an object in.
    # Tighter than weave_radius_m (300): a resumed line whose remaining sentences would land
    # only after the walker has moved a couple hundred metres reads as orphaned ("keeps talking
    # but I forgot what about"). Past this, drop the parked line instead of resuming it stale.
    resume_weave_radius_m: float = 120.0
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
    # A TIGHTER passing bubble for a LOW-significance, fact-less object (a plain
    # kindergarten / shop / office): 55 m is "right here" for a park or monument, but for a
    # nondescript building it reads as "way over there" — the guide said "справа как раз
    # проходишь детский сад «Ивушка»" at 48 m, which felt too far (and, with no facts, it then
    # invented history). Such objects only fire the bubble when you're genuinely beside them.
    # Notable (MEDIUM+) or fact-bearing objects keep the full narrate_radius_m.
    narrate_radius_low_m: float = 32.0
    # The reach fallback (gaze-gated, FORWARD-only — the reach set filters `in_gaze_cone`, so this
    # extends the trigger distance for objects AHEAD without touching side/behind, which stay on
    # the narrate_radius_m bubble). Bumped 100 -> 130: notable things straight ahead (a monument
    # ~110 m up the street) were falling just outside 100 m and going unnarrated on sparse walks.
    # Still much tighter than weave_radius_m so it doesn't announce a place 150-200 m off.
    reach_radius_m: float = 130.0
    # A WIDER reach for genuinely notable objects (HIGH/LANDMARK significance or a
    # museum-grade category weight): a museum 150 m ahead is worth reaching for even
    # though a shop at that range isn't. Field-found: at ВДНХ the Tretyakov pavilion
    # hovered at 115-146 m and was never narrated while entrance arches got told thrice.
    reach_radius_notable_m: float = 190.0
    # Major-road narration (МКАД, шоссе, named interchanges): you can't walk them, so the
    # normal bubble/reach never fires — this WIDER, in-cone trigger narrates a big named
    # road ONCE when you come near it. SECONDARY: it only runs when there's no real object
    # in the bubble AND after the notable-object reach, so it can't outrank a museum you're
    # passing; deduped by name (LINEAR) so "ты у МКАД" is said once, never repeated. 0 off.
    narrate_major_roads: bool = True
    road_reach_radius_m: float = 280.0
    # An elaborate follow-up ("more about the last object") only makes sense while the
    # walker is still NEAR that object. Past this distance the moment has passed — the
    # guide must not keep talking about the courthouse the user already left behind
    # ("я уже ушёл от суда, а он мне опять про суд" — field feedback).
    elaborate_max_distance_m: float = 90.0
    # ON again (19.07, paired with the DEEPEN mechanism above): a factless area beat is
    # skipped, because without verified facts the model FABRICATES specific street
    # history — "Хенель-Клаусс-штрассе, где ОТКРЫЛСЯ ПЕРВЫЙ в Дрездене магазин
    # самообслуживания", "ИМЕННО ЗДЕСЬ в 1991-м..." (real prod fabrications that read as
    # "путает улицы, врёт"). The reason this gate caused dead silence BEFORE was that
    # nothing refilled the facts once spent; now `area_deepen_max` keeps fetching fresh
    # REAL facts (history → people → streets → today), so the monologue talks on verified
    # material and only goes quiet when a place genuinely has nothing left — which is the
    # correct outcome (silence beats invented "first-in-the-city" claims). The capped
    # fact-less CITY fallback (area_cityless_max, well-known city knowledge only) and an
    # explicit user focus stay allowed.
    area_beat_requires_new_facts: bool = True
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
    # PREDICTIVE background refresh: once the walker is past this fraction of the disc
    # radius from the anchor, a re-centred disc is fetched in the BACKGROUND and swapped
    # in when ready — the old walked-past-edge refetch ran inside the tick and blocked
    # narration for the full cold fetch (10-26 s measured in prod walk logs). The stale
    # disc keeps serving meanwhile (stale-while-revalidate); only a session with NO disc
    # at all (or teleported outside it entirely) still fetches in the foreground.
    inventory_predict_frac: float = 0.35
    inventory_refresh_min_gap_s: float = 20.0  # min seconds between background refreshes
    inventory_pass_margin_m: float = 40.0  # recede this far past closest-approach => "passed"
    inventory_ttl_s: float = 3600.0  # evict idle session inventories
    inventory_max_sessions: int = 2000  # LRU cap on cached inventories

    # Drop private service/commerce with no sightseeing value (clinics, dentists, vet clinics,
    # pharmacies, kindergartens/child-development centres, social facilities) BEFORE the object is
    # created — so it never appears as a map pin, in narration or in a route. Cut by tag, not by
    # weight (see categories.is_junk); hospitals + schools are kept. Set 0 to disable.
    filter_junk_objects: bool = True

    # Pedestrian routing (proactive "guided" mode). "straight" needs no network and is
    # MVP-safe; "osrm" talks to a self-hosted foot-profile OSRM on the internal docker
    # network (geo-block-proof, see services/geo/routing.py). On any OSRM error the route
    # planner falls back to straight-line, so the walk still gets a route.
    routing_source: str = "straight"  # straight | osrm
    osrm_url: str = "http://osrm-foot:5000"
    routing_timeout_s: float = 4.0
    walk_speed_mps: float = 1.3  # ~4.7 km/h — straight-line duration + budget->metres conversion
    routing_table_max_points: int = 100  # cap the OSRM /table request size (pre-filter top-N)

    # Track map-matching: snap the walked GPS breadcrumb to the road/footpath network (OSRM
    # /match) so the drawn track is smooth and follows streets, not raw jitter. Needs
    # routing_source=osrm; without it (or on any error) the raw (already spoof-cleaned) path is
    # kept. Live track is re-matched + pushed every track_match_interval_s.
    track_match_enabled: bool = True
    track_match_interval_s: float = 25.0  # live re-match/emit cadence
    track_match_min_points: int = 8  # skip matching a tiny track
    track_match_chunk: int = 90  # OSRM /match caps ~100 coords/request — split longer runs
    track_match_radius_m: float = 12.0  # GPS accuracy hint passed to OSRM (radiuses)
    # Honesty guard against a snapped DETOUR when the real path isn't in OSM (an unmapped
    # alley/shortcut/cut-through): if OSRM's match confidence is below this, OR the snapped
    # length exceeds the raw length by more than the factor (+floor), keep the real (smoothed)
    # segment instead of a plausible-but-wrong route around the block.
    track_match_min_confidence: float = 0.3
    track_match_detour_factor: float = 1.8  # snapped/raw length ratio that flags a detour
    track_match_detour_floor_m: float = 30.0  # ignore the factor below this absolute slack

    # Route planning (guided mode): how the guide picks + orders interesting stops.
    route_min_significance: str = "MEDIUM"  # SKIP|LOW|MEDIUM|HIGH|LANDMARK — floor for the route
    route_min_stops: int = 2  # fewer interesting places than this => "little of note nearby"
    route_max_stops: int = 8  # hard cap on stops per route
    route_max_fetch_m: float = 4000.0  # cap the candidate-fetch radius for a long walk
    route_corridor_pad_m: float = 600.0  # widen the origin->destination corridor by this

    # Guided navigation: leading the walker along the accepted route.
    nav_arrival_radius_m: float = 35.0  # within this of a pending stop => "reached", narrate it
    nav_teaser_radius_m: float = 150.0  # tease the next stop once inside this
    nav_between_mode: str = "teaser"  # teaser | silent | area — what to do between stops
    # Pass-by narration on the leg BETWEEN stops: the guide still tells the story of an
    # interesting object it is leading the walker past (same narrate bubble + pipeline as
    # the free walk), deduped against the route's own stops. Without it a guided leg is
    # silence + turn cues, and the walker passes museums unremarked (field feedback).
    nav_passby_enabled: bool = True
    nav_passby_min_gap_s: float = 40.0  # min seconds between pass-by narrations on a leg
    # Overshoot retire: a stop the walker came near (min approach <= near_m) but is now
    # clearly receding from (current - min approach >= recede_m) is retired as passed —
    # narrated in the past tense — instead of stalling the tour on it forever (GPS jump,
    # a tight arrival radius, or the walker simply not stopping).
    nav_overshoot_near_m: float = 110.0
    nav_overshoot_recede_m: float = 60.0
    # Turn-by-turn navigator cues (guided mode, OSRM steps): deterministic spoken
    # directions («через сто метров поверни направо на Парковую») between stops. Only
    # active when the route came from OSRM (straight-line routes carry no maneuvers).
    nav_cues_enabled: bool = True
    nav_cue_fire_m: float = 35.0  # speak the turn command inside this radius
    nav_cue_preannounce_m: float = 110.0  # "через N метров …" heads-up inside this
    nav_cue_min_gap_s: float = 8.0  # min seconds between spoken cues (anti-spam)
    nav_offroute_m: float = 50.0  # distance off the remaining route line that counts as "off-route"
    nav_offroute_debounce_s: float = 20.0  # hold off-route this long before rerouting
    nav_reroute_min_interval_s: float = 30.0  # min gap between reroutes (anti-spam)
    nav_reroute_max: int = 8  # after this many reroutes, lead by straight line quietly

    # Guided-mode whole-route narration arc (TourScripter). Because the whole route is known
    # at accept, the guide plans ONE coherent tour (intro + per-stop role + transitions +
    # finale) instead of narrating each stop reactively. False => the per-stop reactive path.
    guided_script_enabled: bool = True
    guided_script_max_stops: int = 8  # cap the scripter prompt; extra stops fall back to per-stop

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
    # GPS outlier / spoofing gate (main.py `accept_fix`): drop a fix that implies an impossible
    # speed vs. the last TRUSTED one — a phone in dense/suburban cover (central Moscow jammers)
    # spikes/drifts hundreds of metres to the city centre, which otherwise narrates objects near
    # a phantom position ("10 min about Sheremetyevo"). A jump under gps_jump_floor_m is always
    # kept (normal jitter). Recovery is TIME-based, not tick-based: while a far fix is held the
    # allowed window grows on its own (the implied speed = dist/dt falls as dt since the trusted
    # point grows), so a CONSISTENT relocation is accepted once it's plausible; gps_max_hold_s is
    # the hard backstop after which any persistent far fix is accepted (a real teleport / GPS
    # re-lock must eventually win). A count-based cap (the old gps_max_rejects) followed a
    # sustained spoof after just ~3 fixes — the phone sends dozens during a multi-minute jam.
    # Set gps_max_speed_mps<=0 to disable. 15 m/s ≈ 54 km/h — above walking, below a teleport.
    gps_max_speed_mps: float = 15.0
    gps_jump_floor_m: float = 40.0
    gps_max_hold_s: float = 120.0  # hold an implausible fix at most this long, then recover
    gps_max_rejects: int = 3  # legacy/unused (kept so old .env files load); see gps_max_hold_s
    # Inertial dead-reckoning while a spoof is held: instead of FREEZING the tour at the last
    # trusted point during a multi-minute jam, advance the anchor along the heading at a walking
    # pace so it roughly tracks where the walker actually is. ONLY when the heading is trustworthy
    # (gaze_confidence=high — a compass / steady course, which GPS spoofing does NOT corrupt), and
    # capped at gps_dr_max_m so a bad heading can't run away. Off => hold at the trusted point.
    gps_dead_reckon: bool = True
    gps_dr_speed_mps: float = 1.2  # assumed walking pace while dead-reckoning
    gps_dr_max_m: float = 150.0    # cap on total dead-reckoned displacement from the trusted point
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
    # Server-side backstop behind the client's 10-minute record rule: when a session
    # rotates to a NEW walk (gap above), the PREVIOUS walk is deleted if it spanned less
    # than this many seconds — catching walks that never got an explicit `end`/discard
    # (killed app, dead socket at Stop). Deliberately BELOW the client's 600 s: a walk
    # row starts at the first narrated object (later than the tour itself), so a tight
    # threshold here would prune walks the client explicitly chose to keep. 0 = off.
    walk_min_record_s: float = 480.0

    # --- Self-improvement corpus capture (Block 4 §D2, Phase 0) ------------------
    # Persist a NarrationSample (FACTS + full NarratorInput → narration) per narrated
    # object, and real interest signals (follow-up/skip/…). Both durable-layer only
    # (auth user + DATABASE_URL), best-effort, off the hot path. OFF by default so the
    # base MVP behaviour is unchanged until the quality worker is wired up.
    capture_narration_samples: bool = False
    capture_interest_signals: bool = False
    # Quality-worker sidecar (Block 4 Phase 4). Runs in a SEPARATE container; these are read
    # only there. use_judge adds the LLM judge (needs a reachable non-generator model).
    quality_worker_interval_s: float = 60.0
    quality_worker_use_judge: bool = False
    quality_worker_limit: int = 50  # walks scored per sweep
    # Decision log: the quality worker + optimizer write a followable, human-readable trace of
    # what they DECIDE after each walk (score, taxonomy, worst blurbs) and what the optimizer
    # tunes (propose/accept/reject/rollback). Empty ⇒ stdout only (docker logs); set a dir for a
    # rotating file sink that survives the docker-logs ring buffer.
    quality_log_dir: str = ""
    # Self-improvement durability (Block 4 hardening): where prompt versions + the experiment
    # ledger (memory) + active pointer live. File-based, git-friendly.
    prompt_registry_dir: str = "prompt_registry"
    # Phase 6 canary auto-apply (DORMANT by default — no live prompt change until BOTH
    # canary_enabled AND a canary version is staged in the registry AND fraction>0). A fraction
    # of sessions (by stable sid-hash) use the staged canary narrator prompt; the worker monitors
    # canary vs control walk_quality and auto-rolls-back on regression / promotes on a clear win.
    canary_enabled: bool = False
    canary_fraction: float = 0.0        # 0.0–1.0 of sessions routed to the canary prompt
    canary_min_walks: int = 8           # min scored walks per arm before a promote/rollback call
    canary_margin: float = 0.05         # canary must beat/lose control by this (0–1) to act
    canary_window: int = 60             # recent walks considered by the monitor
    # Aggressive-research knobs (fix #3): widen when the pipeline fetches facts for a facts-less
    # object (_start_fact_warm), so the fix for "no facts" is research, not fabrication/silence.
    # Defaults preserve today's behaviour (paid + MEDIUM+); broaden to research more.
    fact_warm_tier_min: str = "paid"    # paid | free  (free ⇒ research on the free tier too)
    fact_warm_sig_min: str = "LOW"      # min significance to research (LOW|MEDIUM|HIGH|LANDMARK)
    # LOW (was MEDIUM): «пусть ищет все» — the background research now also covers
    # ordinary-but-real objects; the paid-tier gate inside the enricher still bounds
    # the spend, and the interest RANKING (rank_facts) keeps only the best on top.

    # --- Free/paid tier limits (feature: account tiers) --------------------------
    # Free accounts are cost-capped so ads roughly offset the (DeepSeek + wiki-only)
    # spend; paid accounts (Gemini + full web facts) are uncapped. Enforced
    # server-side; the client mirrors them for UX (upgrade prompts). 0 => unlimited.
    free_tier_daily_tours: int = 2  # new walks a free user may start per rolling 24h
    free_tier_walk_limit: int = 10  # saved walks retained for a free user (ring buffer)
    # Beta / early-access: mint every NEW durable user as a lifetime "paid" account (no
    # ads, no caps, premium model). A convenience knob for the closed-testing phase — set
    # OFF the day real store subscriptions go live so new signups follow the normal free
    # path. Only affects users created *after* this is on; existing rows are untouched.
    # Applied in get_or_create_user (subscription_platform="grant", expires_at=None).
    grant_premium_to_new_users: bool = False

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
