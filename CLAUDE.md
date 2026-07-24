# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An autonomous, real-time audio guide for everyday walks.

- **Backend:** FastAPI + asyncio + one WebSocket `/ws`
- **Client:** Flutter app (Android/iOS/web/desktop)
- **Core loop:** GPS/heading → discover nearby places → enrich with facts → rank → narrate aloud
- **Interaction model:** the walk is continuous; the user can barge in by voice/text, get an answer, and the tour resumes
- **Second mode:** guided walks (`"Проведи меня"`) plan a route of stops and lead the walker stop-to-stop

## Read this first

For non-trivial work, read in this order:

1. `README.md` — shortest product + architecture overview
2. `backend/README.md` or `mobile/README.md` — depending on where you are changing code
3. `ARCHITECTURE.md` — deeper system design (Russian)
4. `design/DESIGN_SPEC.md` — before non-trivial mobile UI work
5. `CONTINUE.md` — if present in this checkout; it is the current operational handoff

Subsystem docs worth reading only when relevant:

- `ACCOUNTS_DESIGN.md` — accounts / durable history / billing
- `design/COMMUNITY.md` — friends / feed / challenges / co-walk
- `MEMORY_GRAPH_DESIGN.md` — long-term narrative memory design
- `BLOCK4_SELF_IMPROVEMENT.md` — quality worker / judge / optimizer / prompt registry

When docs disagree, **code wins**.

Current code-level truths that override older prose:

- `backend/app/config.py` is the authoritative source for env flags/defaults
- local session state is **in-memory by default**; the production deploy enables Redis-backed session storage
- speech is **client-side by default** unless neural TTS is explicitly enabled
- current `deploy/Caddyfile` proxies API/WS routes and returns a simple non-API response (`AI Audio Guide`) for other paths; it does **not** currently serve a Flutter web build

## Common commands

### Backend

Run everything from `backend/`.

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev,stt]"
cp .env.example .env

.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest tests/test_orchestrator.py -q
.venv/bin/python -m pytest tests/test_orchestrator.py::test_name -q
```

Notes:

- Python 3.11+
- install `.[accounts]` only when you need the durable accounts/billing layer
- the offline pytest suite is the main regression gate; `*_live.py` needs network/keys
- `backend/app/config.py` is the fastest place to verify whether a feature is on by default, optional, or prod-only

Useful backend simulation / evaluation commands:

```bash
.venv/bin/python -m sim.run_orchestrator
.venv/bin/python -m sim.run_geo
.venv/bin/python -m sim.run_agent
.venv/bin/python -m sim.e2e_regions
```

### Runtime verification

After changes to **agent / geo / guided mode / history / `/ws` behavior**, use the project `/verify` skill.

It drives the real WebSocket surface and catches issues the unit suite misses.

### Mobile

Run everything from `mobile/`.

```bash
flutter analyze
flutter test
flutter test test/widget_test.dart
flutter run -d chrome
flutter build apk --release --dart-define-from-file=dart_defines.json
```

Notes:

- use `flutter run -d chrome` for the fastest dev loop
- **always** use `--dart-define-from-file=dart_defines.json` for real Android builds; a bare APK bakes in localhost / guest-only config
- Android emulator usually needs:

```bash
adb -s emulator-5554 reverse tcp:8000 tcp:8000
```

- iOS builds require macOS + Xcode

### Deploy

Run from `deploy/`:

```bash
docker compose up -d --build
```

Production topology lives in `deploy/docker-compose.yml`.
`backend/docker-compose.yml` is a local/LAN backend stack, not the main prod entrypoint.

## Big-picture architecture

### 1. The backend is one stateful orchestrator

The architectural center is `backend/app/services/agent/orchestrator.py`.

It owns the walk/session state and the main FSM for both:

- **free walk** narration
- **guided walk** navigation

Other components are intentionally more stateless. They do not coordinate directly with each other; they coordinate through `SessionState` owned by the orchestrator.

### 2. The main runtime contract is the WebSocket

`backend/app/shared/schemas.py` is the single source of truth for:

- domain models
- role inputs/outputs
- session state
- WebSocket messages

Read this file first when reasoning about compatibility.

The backend surface is centered on `/ws`, not REST.

REST exists mostly for optional durable/account features (`/me`, `/walks`, `/community/*`, billing).

### 3. `main.py` is the runtime shell, not the domain brain

`backend/app/main.py` handles:

- FastAPI setup
- `/ws` connection lifecycle
- heartbeat / reconnect-related behavior
- auth binding
- pacing / sending / producer task orchestration
- routing REST routers

The actual domain decisions belong in the orchestrator and pipeline, not in the WebSocket handler.

### 4. The content pipeline hides latency aggressively

`backend/app/services/agent/pipeline.py` is the per-tick content pipeline.

It handles the expensive path:

- selecting/warming facts
- prewarming narration
- area-beat prefetch
- seam stitching for prefetched text
- reusing cached work when the walker reaches an object
- materializing a startup contract that can be adopted by the next live startup

This repo relies heavily on **prefetch + cache + background warmup** to feel real-time.
Current startup-latency mechanics to preserve:

- `prewarm` is a **short-lived, non-live WS pass** — not the live walk socket
- guided startup prewarms a fast first phrase and now prefers an **area-led** startup contract
- free-walk startup can hand off a prewarmed startup contract from a one-shot prewarm sid into the
  later live sid via a coarse geo/language cache key
- none of this should mutate `live_position`, `greeted`, pacing state, or other live-session
  semantics before the real walk starts

### 5. LLM roles are split by responsibility, not into independent agents

Representative roles under `backend/app/services/agent/`:

- `scorer.py` — ranks candidates and decides what is worth talking about
- `narrator.py` — writes spoken object/area narration
- `planner.py` — builds area-level story arcs
- `companion.py` — answers user barge-in questions and can return control patches
- `tour_scripter.py` — builds the route-wide narrative arc for guided mode
- `summarizer.py` — produces the end-of-walk recap

Important: these are **not** autonomous agents talking to each other. The orchestrator calls them as stateless roles.

### 6. Narrative memory is a real subsystem, not just a prompt trick

`backend/app/shared/memory.py` stores whole-walk memory such as:

- prior narrations
- narrated objects
- told facts

This supports:

- anti-repeat across the full walk
- fact-level dedup
- callbacks to earlier places
- revisit behavior when looping back past something already covered

### 7. Geo, enrichment, and routing are separate service layers

Key modules:

- `services/geo/` — Overpass discovery, inventory caching, ranking, routing, track matching
- `services/enrichment/enricher.py` — Wikipedia/Wikidata first, paid web fallback second
- `services/llm/` — provider-agnostic client + per-role routing
- `services/stt/` and `services/tts/` — speech input/output backends

These are service layers used by the orchestrator; they should stay swappable.

### 8. The Flutter client is not a thin shell

`mobile/lib/main.dart` owns a lot of real runtime behavior:

- WebSocket lifecycle and reconnects
- speech queueing and pacing acknowledgements
- GPS/heading capture
- local track smoothing / impossible-jump filtering
- guided-mode UI lifecycle
- end-of-walk handling
- foreground/background behavior

Do not assume the backend alone defines runtime semantics.

### 9. Social realtime bypasses the backend

The community/presence layer is split:

- backend REST under `/community/*`
- direct client → Supabase Realtime presence channels for live presence / co-walk

Do not route those features back through the backend by accident.

### 10. Quality/self-improvement is a sidecar, not part of the hot path

`backend/app/services/quality/` evaluates completed walks, stores quality metrics, and can stage prompt changes via a registry/canary flow.

That subsystem is important, but it must stay **off the live `/ws` path**:

- the quality worker may be absent
- prompt optimization must not block live narration
- quality/judge failures should not break the base walking experience

## Key files to enter quickly

- `backend/app/shared/schemas.py` — contracts
- `backend/app/main.py` — `/ws` runtime shell
- `backend/app/services/agent/orchestrator.py` — stateful core
- `backend/app/services/agent/pipeline.py` — content pipeline / prefetch path
- `backend/app/shared/memory.py` — anti-repeat and fact memory
- `mobile/lib/main.dart` — client runtime behavior
- `deploy/docker-compose.yml` — production service topology
- `deploy/Caddyfile` — actual exposed HTTP/WS behavior

## Invariants to preserve

These are product constraints, not cleanup suggestions.

### Real-time behavior

- minimize latency from position update to spoken narration
- preserve prewarm/prefetch behavior unless replacing it with something equivalent
- do not turn sentence-level delivery back into paragraph-level delivery

### Session continuity

- sessions resume by stable `sid`, not by socket identity
- backend should not treat disconnect as walk end
- reconnects must preserve seen/history/memory so the guide does not restart the tour

### Facts-only narration

- facts come from enrichment / verified context
- if there is not enough grounded information, the guide should stay quiet rather than invent details
- anti-fabrication is enforced in code, not only in prompts

### No repeats

- repeat protection is whole-walk, not just recent-history
- preserve object-level, fact-level, and dedup logic when changing ranking or narration flow

### Gaze-aware narration

- objects in front of the walker matter more
- left/right language should only be used when heading confidence supports it

### Pause vs mute

- pause is a **real server-side halt** of tour work/spend
- mute is not the same thing

### Guided mode ordering

- route planning must happen before the first reactive narration tick when starting guided mode
- guided mode is an orchestrator mode, not a separate app

### Client/server pacing contract

- the client acknowledges `played` at speech start so the server can stream ahead
- if you change pacing semantics, treat it as a protocol change, not a refactor

## Optional subsystems

These are intentionally optional and should degrade cleanly when unconfigured:

- accounts / durable walk history / billing
- community / challenges / social feed
- Redis session storage
- neural TTS
- quality worker / judge / prompt registry / canary flow

Base walking behavior should still work without them.

## UI guidance

For non-trivial mobile UI work, read `design/DESIGN_SPEC.md` first.

Do not invent a parallel visual language when the design system already defines the screen/component behavior.
