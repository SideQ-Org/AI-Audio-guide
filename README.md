# AI Audio Guide

An autonomous, real-time **audio guide for everyday walks**. Open the app, start walking, and it
narrates the places around you out loud — the history, the stories, what you're actually looking at.
No tapping, no reading, no planning a route. Walking *is* the interface.

At any point you can **interrupt by voice** ("what's that building on the left?") — it answers in the
same voice and remembers the conversation, then picks the tour back up where it left off.

> Status: working MVP. Python/FastAPI backend + Flutter client, talking over a single WebSocket.
> Runs end-to-end on cloud LLMs (OpenRouter/Anthropic/Gemini) or a local model (LM Studio), and
> ships as an Android APK, an iOS build, and a browser web app.

---

## How it works

The guide is one continuous loop driven by where you walk:

```
 GPS + heading ─▶ find nearby places ─▶ rank by distance & gaze ─▶ enrich with facts
   (phone)          (OSM Overpass)        ("what am I passing?")     (Wikipedia → web)
                                                                          │
        spoken aloud ◀────── narrate a short, spoken blurb ◀── pick what's worth saying
   (on-device / neural voice)   (LLM, streamed)                (director: significance + story arc)
```

- **Real-time.** Minimal latency from a position update to the start of narration.
- **Says only what's true.** Facts come from enrichment (Wikipedia/web); if there's nothing solid
  to say, it stays quiet rather than making things up.
- **Tracks what you can see.** Objects in your gaze direction score higher; it won't announce
  something 200 m behind you.
- **Never repeats itself.** A per-walk memory means it won't re-tell a place or a fact — even one it
  mentioned an hour ago.
- **Never dead air.** When nothing's right beside you, it carries the story of the area, or reaches
  for a landmark you can see ahead — and expands its search so you're not left in silence.

A single **stateful orchestrator** ("the brain") owns the loop and all session state; around it are
stateless LLM roles (scorer, narrator, planner, companion), a deterministic **narrative director**
over a per-walk **memory graph**, and services (geo, enrichment, STT, TTS).
See [`ARCHITECTURE.md`](ARCHITECTURE.md) for the full design.

### Agent & memory structure

```
                         ┌─────────────────────  ORCHESTRATOR (the brain)  ─────────────────────┐
   GPS/heading  ─tick─▶  │  FSM + all session state (seen-list, arc, WalkMemory) · per-tick loop │
                         └───────────────┬───────────────────────────────────────┬──────────────┘
                                         ▼                                        ▼
   Services (stateless)          Pipeline (TextPipeline)                Narrative director (deterministic)
   ├ geo/    OSM Overpass  ─────▶ discovery → facts → pick ─────┐       reads WalkMemory + candidates:
   ├ enrich/ Wikipedia→web        (significance heuristic)      │       • callbacks   (as that church earlier…)
   ├ llm/    per-role router      warm_narration / prefetch     │       • look-ahead  (…an estate up ahead)
   ├ stt/    cloud Whisper        (hide LLM latency)            │       • fact dedup  (only not-yet-told facts)
   └ tts/    neural voice ◀───────────────┐                     │       • revisit     (looped back → new detail)
                                          │                     ▼
   LLM roles (stateless):          NarrationScheduler ◀── Narrator (realizer) ◀── director hints
   Scorer · Narrator · Planner ·   sentence-level delivery,     summarizer → end-of-walk recap
   Companion (barge-in)            weave/park/resume
                                          │
                              one sentence at a time ──▶ WebSocket ──▶ client TTS / neural audio
```

- **Orchestrator** (`agent/orchestrator.py`) — the FSM + session state; the only component that
  calls geo, the pipeline, the director and the store. Roles never talk to each other.
- **LLM roles** (`scorer/narrator/planner/companion.py`) — stateless prompt+model; "Landmark" is the
  top significance tier routed to a premium model, not a separate role.
- **Narrative director** (`agent/director.py`) — deterministic content-planner (no LLM in the tick):
  callbacks, look-ahead foreshadow, fact-level dedup + anti-fabrication, and revisit, all read off the
  memory graph and passed to the Narrator as hints.
- **Memory graph** (`shared/memory.py`, `WalkMemory` in `SessionState`) — the whole-walk substrate:
  `narrations` (anti-repeat corpus), `objects` (narrated-object nodes for recall/callbacks/revisit),
  `told_facts` (fact-level dedup). Survives reconnects. Full typed graph designed in
  [`MEMORY_GRAPH_DESIGN.md`](MEMORY_GRAPH_DESIGN.md); the director + WalkMemory are the built slice.
- **NarrationScheduler** (`agent/narration_schedule.py`) — sentence-level delivery so a new object is
  woven in at a boundary; latency is hidden by pre-generating and pre-synthesizing the next line.

---

## Repository layout

| Path | What's inside |
|------|----------------|
| [`backend/`](backend/) | FastAPI + asyncio + WebSocket server — the orchestrator and all agent logic. |
| [`mobile/`](mobile/) | Flutter client (Android / iOS / web): full-screen map, on-device TTS/STT, 8 languages, walking with the screen locked. |
| [`deploy/`](deploy/) | Caddy + docker-compose for a production host (TLS termination, serves the web build, proxies the backend). |

Component-level setup lives in [`backend/README.md`](backend/README.md) and
[`mobile/README.md`](mobile/README.md).

---

## Quick start

**Backend** (from `backend/`, Python 3.11+):

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev,stt]"
cp .env.example .env                 # then set your keys (see .env.example)
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Out of the box the defaults wire an **offline/heuristic** stack, so tests and the simulator run
without any API keys. For a real walk, flip the wiring in `.env` (LLM backend, `GEO_SOURCE=overpass`,
`ENRICHMENT_SOURCE=websearch`) — the knobs are documented in `backend/.env.example` and `app/config.py`.

**Mobile** (from `mobile/`, Flutter 3.22+, backend running on `:8000`):

```bash
flutter run -d chrome        # quickest loop — a simulated walk works in the browser without GPS
flutter build apk            # Android
flutter build ipa            # iOS (macOS + Xcode only)
```

Point the client at your backend with `--dart-define=WS_URL=ws://<host>:8000/ws`.

---

## Features

- 🎧 **Zero-interaction tour** — open and walk; it talks when there's something to say.
- 🗣️ **Voice barge-in** — ask anything mid-walk; it answers (~3 s cloud STT) and resumes, keeping context.
- 🧠 **A story, not a stream** — the director weaves callbacks ("as that church earlier…"),
  foreshadows what's ahead, and greets you back when you loop past a place, on one coherent arc.
- 🔊 **Neural voice (paid tier)** — a lifelike server-synthesized voice, pre-synthesized so it plays
  gaplessly; the free tier uses the on-device voice.
- 🗺️ **Live GPS track** — the route is drawn on the map as you walk, shown in the end-of-walk
  **structured recap**, and kept as a thumbnail in your walk history.
- 🌍 **8 languages** — narration and place names localized, proper names transliterated.
- 🔒 **Background walking** — keeps narrating with the screen off or an earbud in; a shade-card
  **Pause** button really halts the tour (no generation, no spend).
- 🧭 **Gaze-aware** — knows "in front of you" from a steady walking course or a held-up compass.
- 👤 **Accounts & tiers (optional)** — sign-in, saved walk history, and a paid tier are a dormant,
  opt-in layer; with no keys the app is guest-only and behaves exactly like the base MVP.
  See [`ACCOUNTS_DESIGN.md`](ACCOUNTS_DESIGN.md).

---

## Configuration

Everything is driven by env vars in `backend/.env` (gitignored). The authoritative list is
`backend/app/config.py`; a good starting point is `backend/.env.example`. Highlights:

- `AGENT_BACKEND` — `heuristic` (offline) · `openai` (OpenAI-compatible / OpenRouter / LM Studio) · `anthropic`
- `GEO_SOURCE` — `fixture` · `overpass` (required for a real walk)
- `ENRICHMENT_SOURCE` — `mock` · `websearch` (Wikipedia + paid fallback)
- Safety/spend rails: `WS_TOKEN`, `USD_HARD_CAP`, `MAX_CONNECTIONS_PER_IP`, …

---

## Testing & simulation

```bash
# from backend/
.venv/bin/python -m pytest -q                        # offline test gate (no keys/network)
.venv/bin/python -m ruff check .                      # lint
.venv/bin/python -m sim.run_orchestrator              # run the full agent over a fixture walk
.venv/bin/python -m sim.e2e_regions                   # walk real OSM routes across many regions
```

The `sim/` harness is the main quality tool — it exercises the agent without sensors, TTS, or the
Flutter app. See `backend/README.md` for the full list.

---

## Docs

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — full system design (Russian).
- [`ACCOUNTS_DESIGN.md`](ACCOUNTS_DESIGN.md) — the optional accounts / walk-history layer.
- [`MODEL_COMPARISON.md`](MODEL_COMPARISON.md) — model choice & cost.
- [`E2E_REGIONS.md`](E2E_REGIONS.md) — regional evaluation results.
- [`MVP_PITCH.md`](MVP_PITCH.md) · [`PRIVACY_POLICY.md`](PRIVACY_POLICY.md) · [`TERMS.md`](TERMS.md)

---

## Tech stack

**Backend:** Python, FastAPI, asyncio, WebSocket · OSM Overpass (discovery) · Wikipedia/Wikidata +
web search (facts) · provider-agnostic LLM client (Anthropic / OpenAI-compatible / OpenRouter) ·
deterministic narrative director over a per-walk memory graph · STT (cloud Whisper via OpenRouter, or
local faster-whisper) · optional neural TTS (OpenAI-compatible `/audio/speech`) · SQLAlchemy +
Postgres (optional durable layer).
**Mobile:** Flutter/Dart · OpenStreetMap tiles + live GPS-track polyline · `flutter_tts` (on-device
voice) + `audioplayers` (neural voice playback) · foreground-service background location.
**Deploy:** Caddy (automatic HTTPS) + Docker Compose.
