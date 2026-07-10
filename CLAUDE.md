# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An autonomous, real-time **audio guide for everyday walks**. The app tracks the user's GPS
position and heading and narrates the surrounding places aloud, with no interaction
required — open the app and walk. You can also **interrupt by voice** (barge-in) to ask a
question; the guide answers in the same voice and memory, then resumes the tour.

A working MVP exists: a **Python/FastAPI backend** (the agent brain) and a **Flutter client**
(map UI + on-device speech), talking over a single WebSocket. It runs end-to-end on cloud
LLMs (OpenRouter) or a local model (LM Studio), and the Flutter app builds to an Android APK.

## Repository layout

- `backend/` — FastAPI + asyncio + WebSocket server; the orchestrator and all agent logic.
- `mobile/` — Flutter client (Android/iOS/web/Windows): full-screen OSM map, on-device TTS/STT,
  8 languages, background-while-locked walking (foreground service + Pause-button shade card).
- `deploy/` — Caddy + docker-compose for the prod host: Caddy terminates TLS, **serves the
  Flutter web build** at `/`, and reverse-proxies `/ws /health /ready /stats` to the backend.
  The web build is a **generated artifact**, not checked in: build it in `mobile/` and copy it
  into `deploy/web/` (bind-mounted `./web:/srv/web:ro`) before `docker compose up` — see
  "Production web build" below. Access logging is on (`docker logs ai-guide-caddy`).
- **Design docs** (read these before non-trivial changes): `ARCHITECTURE.md` (full design, in
  Russian), `CONTINUE.md` (handoff: current state, run commands, gotchas — the most up-to-date
  status), `MODEL_COMPARISON.md` (model choice/cost), `E2E_REGIONS.md` (regional eval results),
  `BUSINESS_LOGICS.pdf` (original Russian spec, source of `SYSTEM_PROMPT_RU`). For the
  accounts/tiers work: `ACCOUNTS_DESIGN.md` (durable-layer design), `SUPABASE_SETUP.md` (the
  checklist to turn accounts ON — dormant without keys), `PROD_INFRA.md` (what's prototype-grade
  and the knob to harden each), plus `PRIVACY_POLICY.md` / `TERMS.md` / `MVP_PITCH.md`. For the
  in-progress mobile visual overhaul: `design/DESIGN_SPEC.md` (the single source of truth for the
  premium redesign — design tokens, palette, per-screen specs in `design/screens/`, refs in
  `design/refs/`; **not yet built into Flutter** — read it before touching mobile UI). For the
  planned narrative-memory work: `MEMORY_GRAPH_DESIGN.md` (draft — a per-walk memory graph of
  objects/themes/facts to kill repetition/fabrication and enable callbacks + long-term memory;
  design only, not built).

> The prose in `ARCHITECTURE.md` predates some decisions. Where it disagrees with the code,
> the code wins: the **default LLM backend is Claude/Anthropic** (see `backend/app/config.py`),
> state defaults to **in-memory** (Redis optional), and **TTS runs on the client** (server TTS
> is a no-op `NullTTS`). `CONTINUE.md` reflects the real deployed config (OpenRouter/Gemini in
> dev, DeepSeek in prod due to a regional block).

## Backend — commands

All from `backend/` (Windows paths shown; on POSIX use `.venv/bin/`):

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev,stt]"   # stt extra = local faster-whisper
copy .env.example .env                                 # then set keys (see config below)

.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000   # run (0.0.0.0 for devices)
.venv\Scripts\python -m ruff check .                   # lint
.venv\Scripts\python -m pytest -q                      # tests
.venv\Scripts\python -m pytest tests/test_orchestrator.py -q             # single file
.venv\Scripts\python -m pytest tests/test_orchestrator.py::test_name     # single test
```

On Windows, set `$env:PYTHONIOENCODING="utf-8"` before running anything that prints narration
(Cyrillic in the console otherwise breaks).

**Test layout:** the offline suite (no network/keys) is the regression gate and must stay green.
Tests named `*_live.py` (`test_llm_live`, `test_stt_live`) need real network/keys and are not
part of the offline gate. `tests/fixtures/` holds canned places/facts so the agent runs deterministically.

**Simulations** (`backend/sim/`, run as modules — they are the main quality harness, exercising
the agent without sensors/TTS/Flutter):
- `python -m sim.run_orchestrator --llm openai` — full agent on fixtures.
- `python -m sim.eval_live --n 5` — quality metrics + token/cost log.
- `python -m sim.e2e_regions` — walks real OSM routes through the full agent across many regions.
  Needs `OVERPASS_URL=https://maps.mail.ru/osm/tools/overpass/api/interpreter` (public
  overpass-api.de is often blocked); subset via `E2E_ONLY=msk-red-square,paris-eiffel`.
- `python -m sim.run_geo` — exercise just the geo/discovery pipeline; `python -m sim.run_agent`
  — exercise a single role (scorer/narrator/…) in isolation.
- `sim.smoke_openrouter` / `sim.smoke_stt <wav>` / `sim.smoke_cache` — targeted smoke checks.

## Mobile — commands

From `mobile/` (Dart SDK ≥3.4 / Flutter 3.22+, needs the backend running on `:8000`):

```bash
flutter analyze
flutter test                  # widget smoke test
flutter run -d chrome         # quickest loop (web; simulated walk works without GPS)
flutter build apk --debug     # Android
flutter build ipa             # iOS — macOS + Xcode ONLY (not buildable on Windows); see mobile/README.md
```

**Production web build** (what `deploy/` serves — the same Flutter app compiled for the browser):

```bash
flutter build web             # from mobile/ → outputs build/web/
# then copy build/web/* into deploy/web/ (the Caddy bind-mount) and restart Caddy.
```

Swapping `deploy/web/` by directory rename breaks Caddy's bind-mount — restart the `caddy`
container after updating it.

**Background-while-locked** (walk with screen off / earbud): a `flutter_foreground_task` LOCATION
service keeps GPS+WS+TTS alive and shows a shade card with a Pause button. The plugin's
`ForegroundService` is declared in `mobile/android/.../AndroidManifest.xml` (type `location`);
iOS needs the `UIBackgroundModes` + `AppDelegate.swift` setup already in the repo. Don't remove
geolocator's plain `AndroidSettings` (no `foregroundNotificationConfig` — the FG service owns the card).

For the Android emulator after installing the APK: `adb -s emulator-5554 reverse tcp:8000 tcp:8000`
(so `ws://localhost:8000/ws` reaches the host) and grant `RECORD_AUDIO`. See `mobile/README.md`
for the full emulator dance. `mobile/android/gradle.properties` sets `kotlin.incremental=false`
**on purpose** — required because the pub cache (`C:`) and project (`D:`) are on different drives;
don't remove it.

## Architecture — the big picture

One **stateful orchestrator** ("the brain") drives a continuous loop and owns all session state
(FSM, seen-list, history, conversational memory). Around it are **stateless LLM roles** and
**services**. Roles never talk to each other — only through the `SessionState` the orchestrator
hands them. ("Splitting models per service" in the docs is about deployment/routing, **not**
multiple independent agents.)

The LLM roles (`backend/app/services/agent/`, prompts in `backend/prompts/*.txt`):
- **Scorer** (`scorer.py`) — ranks nearby candidates, picks the next place, decides
  `expand_radius`. JSON-only, cheap model. Gated by a deterministic heuristic so the LLM is
  only called when the candidate set changes materially.
- **Narrator** (`narrator.py`) — writes the short spoken SUMMARY for the chosen place. Also
  writes area-level monologue (see `area.txt`). **"Landmark" is not a separate role/file** — it
  is the top `Significance` tier (`significance.py`); when a place scores `LANDMARK`,
  `role_for_significance()` routes the *same* Narrator call to the premium `model_landmark`.
- **Planner** (`planner.py`, `planner.txt`) — on entering a new area, forms a story arc (theme +
  outlined topics) so narration across many objects reads as one coherent spine rather than
  disconnected blurbs. `HeuristicPlanner` (offline) or `LLMPlanner` (structured JSON).
- **Companion** (`companion.py`) — handles voice/text barge-in; can use tools; returns a reply
  plus an optional `control_patch` (e.g. "skip shops", "be brief") that steers the tour.

Prompts are assembled in layers: `SYSTEM_PROMPT(role, lang) = CORE(lang) + ROLE_BLOCK(role) +
RUNTIME_CONTEXT`. `core.txt` holds the invariants shared by every role; `RUNTIME_CONTEXT` is the
volatile per-tick context (built last, for prompt caching).

The per-tick agent work (discovery → facts → Scorer → Narrator, plus area-monologue interleaving
and prefetch) is assembled in `pipeline.py` (`TextPipeline`), separate from the orchestrator's
FSM/persistence. `TextPipeline` also **pre-generates** the narration for the object you're walking
toward (`warm_narration` → `_narr_cache`, keyed `(place_id, lang)`) so its blurb is spoken the
instant you arrive rather than after a 5–20 s LLM wait; `step()` pops the cache when present.
`name_localizer.py` (`NameLocalizer`, cached LLM) renders place titles in the session language
while keeping proper names transliterated — it feeds both narration and map labels.

`narration_schedule.py` (`NarrationScheduler`, pure state/logic, no I/O) is what makes narration
**sentence-level**: the producer delivers one sentence at a time so a place entering the narrate
bubble is **woven in at a sentence boundary** (never a mid-word cut). The interrupted line's
remaining sentences are parked on a stack and **resume** afterward with a spoken connective
(`resume_connective`), unless the walker has moved too far for it to still make sense. If the
object being told outranks the newcomer (`current_outranks`), the current line finishes in full and
the newcomer is covered briefly afterward as "by the way, we passed…" (`passed_object_intro`).
`languages.py` also holds the instant, no-LLM **session greeting** (`greeting`, time-of-day +
random tail) spoken the moment a walk starts to fill the cold-load gap before the area intro.

Services (`backend/app/services/`):
- `geo/` — OSM **Overpass** discovery: radius search, type/distance/gaze-cone ranking, adaptive
  radius, dedup. Linear features (rivers/canals) snap to the nearest geometry point. `geo/inventory.py`
  is a per-session object cache: it fetches a wide disc **once** and reuses it for ranking across
  ticks, re-fetching only when the user walks far from the anchor — this is what keeps Overpass
  query volume down (`inventory_*` config, on by default).
- `enrichment/enricher.py` — `CompositeEnricher`: **Wikipedia/Wikidata first (free)** for places
  tagged `wikipedia=`/`wikidata=`, paid OpenRouter web-search fallback only for the rest. Kept
  **off the hot-path**: top-K candidates, prefetch-ahead, ~9 s timeout, memory+disk cache.
- `llm/` — provider-agnostic `LLMClient` + a per-role router. Default Anthropic; OpenAI-compatible
  base URL for OpenRouter or local LM Studio. A `METER` tracks tokens/cost per session.
- `stt/` — `faster-whisper` (real) or `MockSTT`. `tts/` — interface only; `NullTTS` server-side
  (the **client** speaks via `flutter_tts`).
- `state/store.py` — session store, in-memory by default (LRU + TTL caps), Redis optional.
- `accounts/` — **optional** durable layer (SQLAlchemy async → Postgres/Supabase in prod, SQLite
  in tests) for user accounts + walk history. `auth.py` verifies a Supabase JWT (JWKS or legacy
  HS256) → `user_id`; `repository.py`/`models.py`/`db.py` are the CRUD/ORM/engine; `api.py` is the
  REST surface (`/me`, `/walks`). Ownership is enforced both in-app and by Postgres RLS
  (`backend/db/rls.sql`). Entirely dormant without keys — see "Accounts & tiers" below.
- `billing/` — **optional** subscription receipt verification. Client buys via the store, POSTs
  the purchase token to `/billing/...`; `verify.py` checks it against Google Play (Apple stubbed)
  and flips the account to the paid tier. 503 when unconfigured.

`shared/schemas.py` is the single source of truth for domain models **and** the WebSocket contract.
`config.py` is the env/`.env`-driven `Settings` — the dial-board for the whole backend.

### The agent loop (core domain logic)

1. **Find objects** — Overpass places within radius N, weighted by type and boosted for proximity
   and gaze-cone alignment.
2. **Persist + resolve address** — store found objects (also the seen-tracking) and resolve
   country/city/district/street.
3. **Enrich + score significance** — web/wiki facts per place; assign `SKIP → LOW → MEDIUM → HIGH
   → LANDMARK` from proximity, gaze, and historical/cultural value.
4. **Generate SUMMARY + stream** — Narrator writes a short SUMMARY, streamed to the client TTS.
   Discovery never stops: if a more relevant object appears mid-narration, generate a new SUMMARY
   and switch **seamlessly**.
5. **Adaptive radius** — if nothing new appears and heading is unchanged, expand the search radius
   so the user is never left in silence (the orchestrator also "elaborates" on the current place,
   up to `_MAX_ELABORATE` follow-ups, before going quiet).
6. **Context dedup** — only unseen places enter the LLM context.

### WebSocket contract (`/ws`)

The single transport. The backend runs a **background producer** per connection that emits
narration **one sentence at a time** (via `NarrationScheduler`, see above), paced by the client's
`played` signal; `position` messages just refresh live context — and, when a fresh object lands in
the narrate bubble, `peek_bubble` (cheap, cached-inventory, no network) flags it so the producer
weaves it in at the next sentence boundary. A question (`utterance`/`audio`) is top-priority: it
cancels the in-flight step, answers, then the producer resumes. Don't reintroduce paragraph-at-a-
time delivery — the sentence granularity is what makes seamless weaving possible.

- **In:** `position` (lat/lon/heading/pace), `utterance` (typed question), `audio` (base64 WAV →
  STT), `listen` (mic open/close, sent around a voice question), `played` (paced-playback ack),
  `language`, `theme`, `control` (manual `control_patch`), `auth` (Supabase JWT, see Accounts),
  `pause`/`resume` (real server-side halt — see below), `ping` (keepalive, ignored).
- **Out:** `state`, `narration` (text + place + coords), `places` (all discovered objects, for map
  pins), `reply`, `transcript`, `language`, `error`, `ping` (server keepalive every 20 s), `quota`
  (`{scope:"daily"}` — a free account is out of daily tours; the client shows the upgrade prompt).
  The `auth` reply also carries `tier` / `tours_today` / `daily_tour_limit` (feature: account tiers).

**Connectivity resilience (the real-walk fix — see `CONTINUE.md` §0).** A real mobile walk drops
the socket constantly (NAT idle-reaping during narration lulls, cell handovers, coverage gaps),
which a localhost/emulator or stationary-WiFi test never reproduces. Two mechanisms keep the tour
coherent: (1) an **app-level heartbeat** both directions (`run_heartbeat` server-side, a 15 s timer
client-side) keeps the socket alive through lulls; (2) **session resume** — the client sends a
stable `?sid=` and the backend keys `SessionState` by it and **does not delete the session on
disconnect** (TTL/LRU evict instead), so a reconnect continues the same tour (seen-list, history,
area intro) instead of repeating from scratch. Don't reintroduce per-socket session ids or
delete-on-disconnect.

**Pause vs mute.** `pause`/`resume` are a *real* server-side halt: `pause_tour()` cancels the
in-flight producer step and parks it (no discovery/LLM/Overpass spend at all), `resume_tour()`
wakes it — the client also re-asserts pause after a reconnect. This is distinct from `mute`, which
keeps the per-tick agent work running and only silences output. A GPS point walked while paused is
recorded as a flagged breadcrumb (`[lat, lon, 1.0]`; unpaused points are `[lat, lon]`), rendered as
a grey dashed polyline in history; a long pause refreshes `walk_last_event_at` so it doesn't rotate
into a second walk. The in-app pause button and the notification button share one `_paused` flag.

Other endpoints: `GET /health` (liveness), `GET /ready` (503 if recent LLM calls all failed),
`GET /stats` (admin, gated by `STATS_TOKEN`), `GET /` (browser test client, `backend/web/index.html`),
plus the accounts/billing REST routers (`/me`, `/walks`, `/billing/...`) mounted in `main.py`.

### Accounts, tiers & billing (optional — dormant by default)

A whole feature layer that is **off unless keys are set**: with no `DATABASE_URL`/Supabase
config the app is guest-only and behaves exactly as the MVP did. Every module is import-safe for
the base install (durable/store SDK imports are deferred into handlers; endpoints answer 503),
and `verify_token` never raises into the connection path — any auth problem degrades to a guest.
Install the durable deps with the `accounts` extra (`pip install -e ".[accounts]"`).

- **Auth is per-session over the WS**, not per-request: the client sends an `auth` message with a
  Supabase JWT; `main.py` verifies it and resolves `(tier, tours_today)` from the durable layer,
  echoed back on the `auth` reply. A guest is `tier="free"`, `user_id=None`.
- **Tiers drive model + enrichment routing.** `SESSION_TIER` (a `ContextVar` in `llm/client.py`)
  is set per turn; a `paid` session uses `openai_model_paid` on every role (else `openai_model`,
  the free model). When `openai_model_paid` is unset, tiers are off and everyone gets the base
  path — don't assume it's always set.
- **Free-tier quota gating** (`main.py` `_quota_blocked`): a free user past `free_tier_daily_tours`
  new walks per rolling 24 h goes **silent** and gets one `quota` nudge (client shows the upgrade
  prompt). Paid is unlimited. `free_tier_walk_limit` caps retained history (ring buffer).
- **Client side** (`mobile/lib/`): `supabase_flutter` for login, `in_app_purchase` for the paid
  subscription, `google_mobile_ads` for free-tier interstitial ads (`ads/ads_service.dart`,
  `billing/billing_service.dart`). All gated on `AccountsConfig.enabled` (Supabase dart-defines) —
  absent keys ⇒ the app runs guest-only as before.
- **Mandatory-login gate** (when accounts ARE configured): the root `build()` in `main.dart` wraps
  `home` in an `AnimatedBuilder(AuthService)` that shows `LoginScreen(isGate:true)` until signed in —
  there is no guest mode. `accounts/login_screen.dart` (sign in) + `accounts/register_screen.dart`
  (separate create-account page it navigates to). Note: sign-out disposes `HomePage`, which closes
  the WS and fires `_onDisconnected` mid-`dispose()` — guard setState there with a `_disposed` flag
  (`mounted` is still true during dispose). See `CONTINUE.md` §0h.
- **Building on macOS** (the project moved from Windows): quirks are catalogued in `CONTINUE.md` §0h
  — Telegram-quarantine on `gradlew`, `caffeinate` around long downloads, CocoaPods CDN pre-warm,
  `flutter config --no-enable-swift-package-manager`, and the iOS `GADApplicationIdentifier` plist key.
- **iOS release / TestFlight** is configured and shipping: bundle id, `ExportOptions.plist`, and the
  ATS `ITSAppUsesNonExemptEncryption` key are set — the signing gotchas are catalogued in `CONTINUE.md` §0m.

## Invariants to preserve

Product requirements, not suggestions — keep them in any change:
- **Real-time**: minimize latency from position update to start of narration.
- **No repeats**: only unseen places enter LLM context.
- **Facts only**: never fabricate; facts come from enrichment (wiki/web). If unsure, stay silent
  (Narrator returns exactly `[SILENCE]`).
- **Gaze priority, with confidence**: objects in the gaze direction score higher. `gaze_confidence`
  is `high` only when the facing is trustworthy — a held-up compass **or** a steady GPS course while
  walking (the user moves the way they face); standing/wandering/pocketed stays `low`. At `low`,
  never say "left/right" (only forward/backward is knowable); the flag is threaded into Scorer and Narrator.
- **Seamless switching** and **adaptive radius** as above.
- **Narration style**: friendly and conversational; no clichés ("unique place", "important
  landmark"); don't inflate ordinary places. Respectful tone for memorials/temples; no ad-speak
  for shops. This is audio — plain speech, no markup/lists, numbers and dates spoken naturally.

## Configuration (key `.env` knobs)

Set in `backend/.env` (gitignored). Defaults wire an **offline/heuristic** stack so sim and tests
run without keys. For a real walk, flip the wiring:
- `AGENT_BACKEND` — `heuristic` (offline) | `openai` (OpenAI-compatible / OpenRouter / LM Studio) | `anthropic`.
- `GEO_SOURCE` — `fixture` (Red Square only) | `overpass` (**required** for a real walk).
- `ENRICHMENT_SOURCE` — `mock` (tests) | `websearch` (wiki + paid fallback).
- `STT_BACKEND` — `mock` | `faster_whisper`.
- For OpenRouter set `OPENAI_BASE_URL`/`OPENAI_API_KEY`/`OPENAI_MODEL`; for LM Studio point the
  base URL at `http://localhost:1234/v1`. Per-role overrides exist (`OPENAI_MODEL_SCORER`, etc.).
- **Security/spend** (the `/ws` is public): `WS_TOKEN`, `MAX_CONNECTIONS_PER_IP`, `USD_HARD_CAP`
  (blocks LLM calls past a ceiling), `USD_SESSION_BUDGET` (soft per-session warning),
  `MAX_UTTERANCE_CHARS`, `MAX_AUDIO_B64_CHARS`, `MAX_WS_FRAME_CHARS` (frame cap before JSON parse).
  A real monthly cap must also be set on the provider dashboard — the code cap is a backstop.

`config.py` has many more knobs, grouped by subsystem: per-role model routing (`MODEL_SCORER`,
`MODEL_NARRATOR`, `MODEL_COMPANION`, `MODEL_LANDMARK`, `MODEL_ENRICHER`), the instant opener
(`SESSION_GREETING`), radius tuning (`DEFAULT_RADIUS_M`, `MAX_RADIUS_M`, `WEAVE_RADIUS_M`,
`NARRATE_RADIUS_M` — the tight "right here" passing bubble, 45 m — and `REACH_RADIUS_M` — the
tighter cap on the gaze-gated reach fallback so it fires for what you're *about* to reach, not
150–200 m away — `SCORER_MAX_CANDIDATES`),
area monologue (`AREA_ENRICH`, `AREA_MAX_BEATS`), the inventory cache (`INVENTORY_ENABLED`,
`INVENTORY_RADIUS_M`, `INVENTORY_REFETCH_FRAC`, `INVENTORY_TTL_S`), enrichment (`ENRICH_TOP_K`,
`ENRICH_LOOKAHEAD_K`, `ENRICH_TIMEOUT_S`, `ENRICH_CACHE_PATH`), Whisper (`WHISPER_MODEL_SIZE`,
`WHISPER_DEVICE`, `WHISPER_COMPUTE_TYPE`), and the state store (`REDIS_URL`, `SESSION_TTL_S`,
`MAX_SESSIONS`). `config.py` itself is the authoritative list.

**Accounts/tiers/billing** (all empty = feature off, guest-only): `DATABASE_URL` (durable layer;
empty ⇒ `accounts_enabled()` False), `SUPABASE_JWKS_URL` or `SUPABASE_JWT_SECRET` (+ `SUPABASE_JWT_AUD`)
for WS auth, `OPENAI_MODEL_PAID` (paid-tier model; unset ⇒ tiers off, everyone on `OPENAI_MODEL`),
`FREE_TIER_DAILY_TOURS` / `FREE_TIER_WALK_LIMIT` (quota + history caps), and the Play billing set
(`GOOGLE_PLAY_PACKAGE`, `BILLING_PRODUCT_MONTHLY`/`_YEARLY`). See `SUPABASE_SETUP.md` for the full
turn-on checklist and `ACCOUNTS_DESIGN.md` for the durable-layer design.

See `CONTINUE.md` §6 for a full annotated dev `.env`, and §5 for the **regional block** caveat
(OpenRouter geoblocks OpenAI/Anthropic/Google from some regions → prod uses `deepseek/deepseek-chat`).

## Gotchas (already paid for — see `CONTINUE.md` §7 for the full list)

- Enrichment timeout must be **≥9 s** — web search takes ~5–7 s; shorter and the Narrator gets no facts.
- Wikimedia rejects a bare User-Agent (403) — the `WikiEnricher` UA must stay meaningful.
- Gemini 3.x reasoning can't be disabled; cap `OPENAI_REASONING_MAX_TOKENS` for Narrator/Landmark/Enricher.
- `flutter_compass` was removed (broke AGP 8); facing comes from a fused magnetometer/accelerometer
  compass (`compass.dart`, confident only when held up + steady) or a steady walking GPS course —
  either yields `gaze_confidence=high`; otherwise `position.heading` at `low`.
- Public CARTO/OSM tiles are fine for the prototype, not for production load.
