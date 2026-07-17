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
  The **active-tour home** is a distinct redesigned state (`ui/screens.dart` `HomeModules`,
  `ui/components.dart` `StatusIsland`/`TourControls`/`MicButton`): swiping "Поехали" plays a
  staggered activation choreography (map blur lifts + inactive blocks slide away → a Dynamic-Island
  status pill drops in and a matte-glass control panel with a highlighted mic slides up), always-on
  map controls (zoom/compass/recenter), a per-walk journal, and a Stop → end-of-walk summary sheet
  (duration · distance · places · a **track mini-map** · and an async LLM **structured recap** — the
  client keeps the socket open ~18 s after `end` so the recap can land, showing a spinner until it
  does). The **GPS track** is drawn live on the map as a growing glow polyline (grey dashed on paused
  stretches) via the shared `ui/track_map.dart` (`TrackMap`/`trackPolylines`) — the same renderer
  feeds the summary, the history-list thumbnails and the walk detail; the list `/walks` payload
  carries a downsampled `path` for the previews. Map markers use **crisp halos/borders, not blurred
  shadows** — blurred marker shadows smear/ghost when the map pans under Impeller (iOS sim) — and are
  **typed pins** (per-category icon/colour, `ui.Cat`); tapping one opens the object **card**. All
  modal sheets share one **content-sized** container, `ui/components.dart` `CardSheet` (the cream
  gradient of the cards, flat fill — no mesh/blur, so no lag and **no empty space below short
  content**; `scrollable:false` for list sheets so their `Flexible`/`ListView` still flex under the
  height cap; `RoundedSheet` is a deprecated alias). The object card shows the narrator's structured
  `card` facts + `image` photo (`_cardShell` → `CardSheet`). In-well text fields (search bars, etc.)
  use `ui.bareInput()` — it nulls **every** `InputDecoration` border slot + `filled:false`, so the
  theme's focused outline can't draw a box inside the pill ("поле в поле").
- `deploy/` — Caddy + docker-compose for the prod host: Caddy terminates TLS, **serves the
  Flutter web build** at `/`, and reverse-proxies `/ws /health /ready /stats` to the backend.
  The web build is a **generated artifact**, not checked in: build it in `mobile/` and copy it
  into `deploy/web/` (bind-mounted `./web:/srv/web:ro`) before `docker compose up` — see
  "Production web build" below. Access logging is on (`docker logs ai-guide-caddy`).
- **Design docs** (read these before non-trivial changes): `ARCHITECTURE.md` (full design, in
  Russian), `CONTINUE.md` (handoff: current state, run commands, gotchas — the most up-to-date
  status), `MODEL_COMPARISON.md` (model choice/cost), `E2E_REGIONS.md` (regional eval results),
  `BUSINESS_LOGICS.pdf` (original Russian spec, source of `SYSTEM_PROMPT_RU`), plus `docs/`
  (`ARCHITECTURE_FLOW.md` — the per-tick loop as Mermaid block-diagrams; `MODEL_LATENCY_RESEARCH.md`
  — the barge-in latency / voice-model research, incl. why GPT Realtime voice is a non-starter under
  the regional geoblock). `LIVE_CAMERA_RESEARCH.md` — the live-camera ("покажи гиду
  достопримечательность") research: why native live APIs (Gemini Live / GPT Realtime) are closed to
  us and the recommended one-shot snapshot → vision-LLM path over the existing OpenRouter transport;
  **research only, nothing implemented**. For the
  accounts/tiers work: `ACCOUNTS_DESIGN.md` (durable-layer design), `SUPABASE_SETUP.md` (the
  checklist to turn accounts ON — dormant without keys), `PROD_INFRA.md` (what's prototype-grade
  and the knob to harden each), plus `PRIVACY_POLICY.md` / `TERMS.md` / `MVP_PITCH.md`. For the
  mobile visual overhaul: `design/DESIGN_SPEC.md` (the single source of truth for the
  premium redesign — design tokens, palette, per-screen specs in `design/screens/`, refs in
  `design/refs/`; **substantially built into Flutter now** — login/register, home (incl. the
  active-tour state), community, profile and settings all ship the redesign via `ui/design.dart`
  tokens + `ui/components.dart`; read the spec before touching mobile UI to stay on-palette). For the
  social layer: `design/COMMUNITY.md` (friends/feed/challenges/group-streaks + Realtime presence and
  co-walk design) and `design/PROFILE_ACHIEVEMENTS.md` (the level curve + achievements shown on the
  profile — `ui/level.dart`/`ui/achievements.dart` mirror the same formulas the backend derives). For the
  narrative-memory work: `MEMORY_GRAPH_DESIGN.md` (a per-walk memory graph of objects/themes/facts to
  kill repetition/fabrication and enable callbacks + long-term memory). **Partly built now** — the
  `NarrativeDirector` (`agent/director.py`) + `WalkMemory` (`shared/memory.py`) ship callbacks,
  fact-level dedup/anti-fabrication, look-ahead foreshadow and revisit (see "Narrative director &
  memory graph" below); the full typed-graph + arc planning stays design.

> **Note:** `CONTINUE.md`, `SUPABASE_SETUP.md`, and `PROD_INFRA.md` are **gitignored** operational
> handoff docs (they hold prod IP / SSH workflow / test-account creds) — they live only in the
> personal repo, so a fresh checkout of the shared repo won't have them even though this file cites
> them heavily (e.g. `CONTINUE.md §0h`). Don't treat their absence as a mistake or go looking for them.

> The prose in `ARCHITECTURE.md` predates some decisions. Where it disagrees with the code,
> the code wins: the **default LLM backend is Claude/Anthropic** (see `backend/app/config.py`),
> state defaults to **in-memory** (Redis optional), and **TTS runs on the client by default**
> (server TTS is a no-op `NullTTS` unless neural TTS is turned on — see "Neural TTS" below).
> `CONTINUE.md` reflects the real deployed config (OpenRouter/Gemini in
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
  The Narrator is handed **`AVOID_OPENERS`** (`languages.recent_openers` — its own recent opening
  phrases) so it doesn't start object after object the same way; its output passes code backstops
  in `split_hook` (attributions/solicits) plus **`strip_factless_history`** — with empty FACTS,
  any invented history/date/creation sentence is dropped, keeping only the plain naming line (the
  anti-fabrication net over the "detsad appeared in those years…" slip). An optional **listener
  form-of-address** (он/она/нейтрально; `USER_ADDRESS` ContextVar → `_address_instr`, where neutral
  *actively* avoids gendered 2nd-person) is appended to its prompt — distinct from the guide's own
  voice-gender (`assistant_gender`/`_SELF_REFERENCE`). In the SAME call it emits a trailing
  **`CARD:`** block of dry, re-readable facts (`split_card`, `narrator_emit_card`) for the object
  card — stripped before TTS, never spoken.
- **Planner** (`planner.py`, `planner.txt`) — on entering a new area, forms a story arc (theme +
  outlined topics) so narration across many objects reads as one coherent spine rather than
  disconnected blurbs. `HeuristicPlanner` (offline) or `LLMPlanner` (structured JSON).
- **Companion** (`companion.py`) — handles voice/text barge-in; can use tools; returns a reply
  plus an optional `control_patch` (e.g. "skip shops", "be brief") that steers the tour. Its
  inline reply is the **whole** answer — the question is deliberately NOT re-queued as an area-beat
  "focus" topic (that produced a second, redundant/stale beat re-telling the same fact on a field
  walk; `on_utterance` no longer touches `pending_focus`).

Prompts are assembled in layers: `SYSTEM_PROMPT(role, lang) = CORE(lang) + ROLE_BLOCK(role) +
RUNTIME_CONTEXT`. `core.txt` holds the invariants shared by every role; `RUNTIME_CONTEXT` is the
volatile per-tick context (built last, for prompt caching).

The per-tick agent work (discovery → facts → Scorer → Narrator, plus area-monologue interleaving
and prefetch) is assembled in `pipeline.py` (`TextPipeline`), separate from the orchestrator's
FSM/persistence. `TextPipeline` also **pre-generates** the narration for the object you're walking
toward (`warm_narration` → `_narr_cache`, keyed `(place_id, lang)`) so its blurb is spoken the
instant you arrive rather than after a 5–20 s LLM wait; `step()` pops the cache when present. The
**area monologue is pre-generated the same way**: while the current outline beat is being spoken,
the orchestrator warms the NEXT one in the background (`prefetch_area` → `commit_area`, gated by
`AREA_PREFETCH`) so the inter-beat LLM latency (10–17 s cold at session start — the "медленно
переключался между блоками" gap) is hidden behind delivery instead of opening a silent gap.
`prefetch_area` is strictly **read-only** (no `store.save`): it runs concurrently with delivery /
weave / barge-in without touching session state, and the producer commits the result single-
threaded, re-checking freshness (still the next outline topic) so it can't repeat or drift.
`name_localizer.py` (`NameLocalizer`, cached LLM) renders place titles in the session language
while keeping proper names transliterated — it feeds both narration and map labels.

`narration_schedule.py` (`NarrationScheduler`, pure state/logic, no I/O) is what makes narration
**sentence-level**: the producer delivers one sentence at a time so a place entering the narrate
bubble is **woven in at a sentence boundary** (never a mid-word cut). The interrupted line's
remaining sentences are parked on a stack and **resume** afterward with a spoken connective
(`resume_connective`), unless the walker has moved too far for it to still make sense. If the
object being told outranks the newcomer (`current_outranks`), the current line finishes in full and
the newcomer is covered briefly afterward in the past tense — `narrate_object(..., passed=True)`,
which the Narrator renders via its `FLAGS.passed` "объект уже позади" block ("кстати, мы прошли…").
`languages.py` also holds the instant, no-LLM **session greeting** (`greeting`, time-of-day +
random tail) spoken the moment a walk starts to fill the cold-load gap before the area intro.

**Narrative director & memory graph** (`director.py` + `shared/memory.py`). `director.py` is the
**content-planner** layer — deterministic (no LLM in the tick, O(objects)), it decides *structure*
while the Narrator stays the *realizer*. Reading `SessionState.memory` (`WalkMemory`) and the live
candidate window it owns four decisions, threaded into `pipeline.step`/`warm_narration` and the area
path as hints the Narrator may use:
- **Callbacks** (`find_callback`) — reference an earlier-narrated object of the same category
  ("как та церковь, что мы прошли раньше"), excluding the last couple and dull/commercial types.
- **Look-ahead** (`find_lookahead`) — tease a notable object coming up ahead in the gaze cone
  ("впереди — старая усадьба"), so the tour leans forward.
- **Fact dedup / anti-fabrication** (`atomize_facts` + `WalkMemory.new_facts`/`told_facts`) —
  atomize enrichment into sentence-level facts and feed a beat ONLY not-yet-told ones (kills reworded
  "опять про берёзы"); wired into the area path (`_emit_area_beat`). Elaborate additionally requires
  facts (no facts ⇒ silence, never invented history).
- **Revisit** (`find_revisit`) — when the walker loops back to an object told earlier — near it now
  AND ≥`revisit_min_route_m` of route walked since (the `SessionState.route_len_m` odometer, so it
  never fires right after the main narration) — add "снова у X" + one fresh detail via elaborate.

`WalkMemory` is the graph substrate, persisted in `SessionState` (survives resume): `narrations`
(whole-walk anti-repeat corpus, Jaccard/containment), `objects` (`ObjectMemo` nodes —
id/name/category/wikidata/theme/significance/lat/lon/said_route_m; recall + callbacks + revisit),
`told_facts` (fact-level dedup). The FULL graph (typed Object/Area/Theme/Fact nodes + edges,
look-ahead **arc** planning) is designed in `MEMORY_GRAPH_DESIGN.md`; the director + WalkMemory above
are the **built** slice (the doc's "design only" note is stale — Phases 1-5 shipped).

`summarizer.py` (`LLMSummarizer`) writes the **end-of-walk structured recap** from the whole-walk
narration corpus — one post-walk LLM call fired on `end` (kept walks), delivered async as a `summary`
WS message and rendered in the client's Stop sheet (spinner → text).

**Interestingness metric & self-improvement (Block 4** — full feature doc + ops runbook in
`BLOCK4_SELF_IMPROVEMENT.md`; failure-mode model + deploy runbook in `BLOCK4_FIXER_HARDENING.md`;
original design in `Блок4_Интересность_метрики_и_луп_самоулучшения.md`). Narration quality is scored
as a **number**, reference-free, in a **separate sidecar container** so it can't destabilize the live
tour. **Deployed to prod and the autonomous loop is CLOSED + LIVE** (capture on, worker scoring with
a validated judge, **canary armed at 10% with the first machine-found candidate staged**). The
**full loop (Phases 0–6) is built and has run end-to-end**: the optimizer found a narrator rewrite
that beat baseline through the dev-CI + hard-gates + held-out gold gate (**gold 0.215 → 0.252, +17%**),
auto-staged it as the CANARY (`9338dcb234ce`), and `canary_enabled=1`/`canary_fraction=0.1` now routes
~10% of live sessions to it via a stable sid-hash (`canary_prompt_for` → `set_prompt_override`); the
quality worker compares canary vs control `walk_quality` to **auto-promote** on a clear live win or
**auto-roll-back** on regression (`canary_min_walks`/`canary_margin`/`canary_window`), no human in the
loop. The canary machinery is otherwise **inert** without those flags + a staged version. Invariant: a
candidate reaches the canary only after the offline held-out gold gate — the canary is the *live
confirmation*, not the first line of defence. Pieces:
- **Instrumentation (Phase 0, additive, flag-gated — `capture_narration_samples` /
  `capture_interest_signals`, both default OFF).** `accounts/history.record_object` also writes a
  durable **`narration_samples`** row (the FACTS given to the narrator + a compact context JSON +
  the narration) in the same txn as the event — the groundedness gate needs the FACTS, which
  `walk_events` doesn't store. `record_interest_signal` logs real signals (`interest_signals`:
  follow-up barge-in ≫ completion ≫ skip/mute, Twitter-spirit weights). FACTS are threaded out via
  `pipeline.StepResult.facts` → `orchestrator._record_history`. Tables + migrations `0008`/`0009`
  + RLS in `db/rls.sql`; **auth-user + `DATABASE_URL` only** (guests capture nothing).
- **Code-metrics panel (Phase 1, `agent/interest_metrics.py`).** Pure-stdlib, reference-free,
  language-agnostic (8 languages): distinct-n, self-repetition, MTLD, NIDF, number density,
  speakability, novelty (reuses `is_near_duplicate`), cliché (reuses `narrator._CLICHE_FILLER_MARKERS`).
  Two audio-specific calibrations found on real prod walks: **number density counts dates spoken as
  WORDS** ("в тридцатых годах", not "1930" — a digit-only regex reads ~0 concreteness on concrete
  prose; per-language lexicon), and **`object_repeat_rate`** flags re-narrating the same object
  ("опять про руины"), which lexical novelty misses when the wording differs (soft walk-score penalty
  + a `repeat_object` taxonomy count, applied in the worker).
  `sim/interest_corpus.py` builds the corpus from `e2e_results.json`/the DB with a deterministic
  stratified train/dev/test/**sacred-holdout** split; `sim/interest_eval.py` prints the per-region
  "number" (`python -m sim.interest_eval`).
- **LLM judge (Phase 2, `agent/interest_judge.py` + `prompts/judge.txt`, role `Role.JUDGE`).**
  G-Eval rubric (8 axes → hard-gates), pointwise + pairwise-with-order-swap. The JUDGE role is
  pinned to a **different model family than the generator** (`config.model_judge` /
  `openai_model_judge`, excluded from the paid-tier override) to fight self-preference bias.
  `sim/human_calib.py` computes %agreement + **Cohen's κ** against human labels (κ≥~0.6 before the
  judge is trusted). logprob-weighting is a documented TODO (needs a client change; the frontier
  gold judge is geoblocked anyway → lean on the rubric + human calibration).
- **Composite (Phase 3, `agent/interest_score.py`).** `score = interestingness · Π(hard_gates)` —
  gates (groundedness via the persisted FACTS, cliché, non-repeat) can't be bought back by
  interest. Inverted-U for non-monotonic axes; `fit_weights` (pure-Python ridge least-squares) fits
  the blend on human labels.
- **Quality worker (Phase 4, `app/services/quality/`, `deploy/` service `quality-worker`).** A
  **separate container** off the same image + `.env`, internal-only, that sweeps finished walks
  (`repository.list_unscored_walks`, idempotent via the unique `walk_quality.walk_id`), scores each
  blurb (code panel + optional judge), and writes one **`walk_quality`** row (aggregates + failure
  taxonomy). Reads DB, writes its own table — never the backend event loop / prompts. Run:
  `python -m app.services.quality [--once] [--judge]`. A read-only dashboard over `walk_quality` is
  the one deferred piece.
- **Self-improvement loop (Phase 5, `sim/prompt_optimize.py` + `prompts/optimizer.txt`, role
  `Role.OPTIMIZER`).** The "fixer": rewrites a system prompt (e.g. `narrator.txt`) against the
  evaluator until it plateaus, **producing a validated candidate + evidence bundle — it never writes
  the live prompt** (that's Phase 6). Hybrid OPRO(propose) + TextGrad(critique from the failure
  taxonomy) + DSPy dev/holdout discipline. Safety, per the research: a cheap **search judge** ranks
  candidates on dev while a **gold judge** gates promotion on the **held-out** set only (the optimizer
  never sees held-out); **hard-gates never degrade** (no buying interest with fabrication/cliché);
  **stop by the gold judge**, with a reward-hacking detector (search rises but gold doesn't → reject).
  Candidates are swapped in for evaluation via an in-process **prompt override**
  (`prompts.set_prompt_override`, empty in the live backend — also the Phase 6 hot-swap seed). The
  LLM pieces go through `LLMClient`, so the loop is unit-tested with fakes; a live run needs reachable
  generator + judge models. `write_candidate` persists `candidate.txt` + `evidence.json` for review.
  Two objective safeguards found on real prod walks: the loop **rejects a "fatalistic silence" fix**
  (a `coverage_not_degraded` gate — a candidate may not raise the silence rate; the answer to
  "no facts" is research, not going quiet), and **fix #3** makes research real via config knobs
  (`fact_warm_tier_min`/`fact_warm_sig_min`, used by `pipeline._fact_warm_gate`) that widen when the
  pipeline fetches facts for a facts-less object — the optimizer can propose an enrichment
  `config_patch` (`apply_config_patch`), not just prompt text.
- **Durability: memory + versioning + rollback (`app/services/quality/registry.py`,
  `BLOCK4_FIXER_HARDENING.md`).** `PromptRegistry` is a file-based, per-(target,tier) store rooted
  at **`prompt_registry/<target>/<tier>/`** (runtime state, untracked — a fresh clone won't have it):
  immutable version texts (`versions/<hash>.txt`), an append-only **experiment ledger**
  (`ledger.jsonl` — the memory of what was tried and whether it was accepted/rejected/rolled-back),
  and an **active pointer** (`active.json`) with rollback history.
  The optimizer seeds its trajectory from `past_attempts` (persistent across runs) and refuses to
  re-try `known_bad` versions (**oscillation guard**); on promotion it `save_version` + `set_active`.
  `check_and_rollback` reverts the active pointer when a promoted version regresses live (the Phase-6
  safety net), and `kill_switch` forces the pinned baseline. `BLOCK4_FIXER_HARDENING.md` models every
  failure mode (reward-hacking, judge drift, regression/oscillation, corpus, deployment, memory
  corruption) and the mechanic that contains each.

Services (`backend/app/services/`):
- `geo/` — OSM **Overpass** discovery: radius search, type/distance/gaze-cone ranking, adaptive
  radius, dedup. Linear features (rivers/canals) snap to the nearest geometry point. **Candidate
  dedup** (`geo/ranking.py` `Dedup` dataclass, threaded through discovery) keeps the same object
  out of LLM context twice — by `wikidata` QID, by same-name-within-`dedup_name_radius_m`, and by
  name across the many same-name OSM segments of a **linear** feature (`LINEAR_CATEGORIES`, so a
  river/promenade is narrated once, not per segment). `geo/inventory.py`
  is a per-session object cache: it fetches a wide disc **once** and reuses it for ranking across
  ticks, re-fetching only when the user walks far from the anchor — this is what keeps Overpass
  query volume down (`inventory_*` config, on by default).
- `enrichment/enricher.py` — `CompositeEnricher`: **Wikipedia/Wikidata first (free)** for places
  tagged `wikipedia=`/`wikidata=`, paid OpenRouter web-search fallback only for the rest. Kept
  **off the hot-path**: top-K candidates, prefetch-ahead, ~9 s timeout, memory+disk cache. It also
  captures the **object photo** for the card (`image_for(place_id)`), sourced free/exact in this
  order: the Wikipedia lead image, then the **Wikidata `P18`** image (for `wikidata`-tagged objects
  with no article — pulled from the entity JSON already fetched, no extra request), then an OSM tag
  (`wikimedia_commons=File:…` → a Commons thumbnail, or a direct `image=https://…`) with no network.
- `llm/` — provider-agnostic `LLMClient` + a per-role router. Default Anthropic; OpenAI-compatible
  base URL for OpenRouter or local LM Studio. A `METER` tracks tokens/cost per session.
- `stt/` — voice barge-in transcription: `MockSTT` (tests), `FasterWhisperSTT` (local CPU/GPU,
  slow — ~8-10 s), or **`OpenRouterSTT`** (cloud Whisper-family via the OpenAI-compatible
  `/audio/transcriptions`, ~3 s — the **prod default**, `STT_BACKEND=openrouter`). Reuses the LLM
  creds; prod uses `mistralai/voxtral-mini-transcribe` (perfect Russian, no geoblock — `openai/*`
  transcription 403s from the prod region, like TTS). `tts/` — `NullTTS` (text-only, default; the
  **client** speaks via `flutter_tts`) **or** `OpenAITTS` (optional **neural** voice — see below).
- `state/store.py` — session store, in-memory by default (LRU + TTL caps), Redis optional.
- `accounts/` — **optional** durable layer (SQLAlchemy async → Postgres/Supabase in prod, SQLite
  in tests) for user accounts + walk history. `auth.py` verifies a Supabase JWT (JWKS or legacy
  HS256) → `user_id`; `repository.py`/`models.py`/`db.py` are the CRUD/ORM/engine; `api.py` is the
  REST surface (`/me`, `/walks`). Ownership is enforced both in-app and by Postgres RLS
  (`backend/db/rls.sql`). Entirely dormant without keys — see "Accounts & tiers" below.
  - **Community/social** (`community.py` + `community_api.py`, mounted at `/community/*`; design
    in `design/COMMUNITY.md`) is a second durable module kept separate from `repository.py` the
    same way `history.py` is: it owns **friendships, an activity feed, challenges, and group
    streaks**, plus the values *derived* from the durable `walks` rows (level, streak, presence,
    challenge progress — `level_for_walks()` mirrors mobile `ui/level.dart`). Same guard as the
    rest of the durable layer: 503 when accounts are off. New ORM tables live in `models.py`
    (`Friendship`, `ActivityEvent`, `Challenge`, `ChallengeParticipant`, `GroupStreak`,
    `GroupStreakMember`) with RLS policies in `rls.sql`. **Live presence + co-walk** ride Supabase
    Realtime directly from the client (`mobile/lib/accounts/realtime_service.dart`), not the
    backend: a global `presence:community` channel for friends' live "walking now" status (coords
    rounded ~110 m for privacy) and per-room `presence:cowalk:<CODE>` channels for two friends
    walking together. Caddy proxies `/community/*` to the backend (`deploy/`).
- `billing/` — **optional** subscription receipt verification. Client buys via the store, POSTs
  the purchase token to `/billing/...`; `verify.py` checks it against Google Play (Apple stubbed)
  and flips the account to the paid tier. 503 when unconfigured.

`shared/schemas.py` is the single source of truth for domain models **and** the WebSocket contract.
`config.py` is the env/`.env`-driven `Settings` — the dial-board for the whole backend.
`agent/factory.py` builds the `Orchestrator` from settings (picks Geo source, enricher, and the
heuristic/openai/anthropic backend), keeping the WS handler thin.

**Live-walk debugging** (`agent/walklog.py`): one structured logger (`aiguide.agent`, INFO,
`propagate=False`) reconstructs a whole walk end-to-end — the actual TEXT the agents produced
(narration/area beats/replies), coords walked, every external call + count, and **why the guide
stayed silent**. Pull it with `docker logs ai-guide | grep aiguide.agent`; each line is stamped
`sid=<id>` (a `CURRENT_SID` ContextVar) so concurrent walks stay separable. Set `walk_log_dir` for
a rotating file sink that survives the docker-logs ring buffer. Complements `llm.client.METER`
(token/cost) and `metrics.GUIDE` (counters) — reach for this first when a live walk misbehaves.

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

### Guided mode ("Проведи меня" — proactive routed walks)

Besides the default reactive "free walk", the guide can **lead**: plan a route of interesting
stops, propose it, and navigate the walker stop-to-stop. `SessionState.guide_mode` is
`free | guided`; the free path is untouched when guided is off.

- **Planning** (`geo/route_planner.py` `RoutePlanner`): picks + orders stops by significance
  (floor `route_min_significance`, `route_min_stops`/`route_max_stops` caps). Three request
  shapes via `start_guided`: time budget (`budget_min`), distance budget (`budget_km`), or a
  destination (explicit point, or `pick_landmark` — the guide picks one); destination mode
  samples POIs from overlapping discs along the origin→destination **corridor**
  (`route_corridor_pad_m`). POI fetch and routing both have safety nets (`_safe_fetch`/
  `_safe_route`) — a slow/blocked Overpass or a down OSRM degrades, never crashes planning
  (`main.py` additionally catches `plan_route` failures so the socket survives).
- **Pedestrian routing** (`geo/routing.py`): `routing_source=straight` (no network, MVP-safe,
  `walk_speed_mps` for durations) or `osrm` — a self-hosted foot-profile OSRM on the internal
  docker network (geo-block-proof); any OSRM error falls back to straight-line.
- **Leading** (`orchestrator._guided_tick`, replaces the reactive tick once the route is
  accepted): arrival detect within `nav_arrival_radius_m` → narrate the stop via the **same
  pipeline** the reactive path uses; between stops `nav_between_mode` = `teaser` (tease the next
  stop inside `nav_teaser_radius_m`) | `silent` | `area`. **Soft off-route reroute**: drifting
  > `nav_offroute_m` off the remaining route line arms a debounce
  (`nav_offroute_debounce_s`), then the pending tail is re-planned (min gap
  `nav_reroute_min_interval_s`; after `nav_reroute_max` reroutes, lead quietly by straight
  line). `NavState` lives in `SessionState`, so a guided walk **survives reconnect/resume**.
  Tests: `tests/test_guided_reroute.py`.
- **WS frames** (`shared/schemas.py`): in — `start_guided`, `route_accept`, `route_reject`,
  `skip_stop`; out — `route` (the proposal: ordered stops + polyline), `stop_reached`, `reroute`,
  `route_done` (client shows the summary on Stop).
- **Mobile** (`main.dart`): a "Проведи меня" chooser sheet, the planned route drawn as a dashed
  line + numbered stop pins, an accept/reject sheet, and a next-stop chip with a
  locally-computed direction arrow.

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
  `address_form` (`WSSetAddressForm` — the optional listener он/она/нейтрально; persisted on
  `SessionState.user_address`, survives resume), `pause`/`resume` (real server-side halt — see below),
  `end` (Stop button: halt the tour;
  with `discard:true` the backend deletes this session's walk — a walk shorter than the client's
  10-minute record threshold is dropped, see "Session record rule" below), `ping` (keepalive, ignored),
  `start_guided`/`route_accept`/`route_reject`/`skip_stop` (guided mode — see above).
- **Out:** `state`, `narration` (text + place + coords, **+ optional `audio_b64`/`audio_mime`** —
  see Neural TTS — **+ optional `card`/`image`/`category`** for the tappable object card: the dry
  `CARD:` facts, a photo URL, and the type), `places` (all discovered objects, for map pins), `reply` (also carries optional
  audio), `transcript`, `summary` (structured end-of-walk recap, pushed async after `end` on a kept
  walk — see the director/summarizer above), `language`, `error`, `ping` (server keepalive every
  20 s), `quota` (`{scope:"daily"}` — a free account is out of daily tours; upgrade prompt),
  `route`/`stop_reached`/`reroute`/`route_done` (guided mode — see above).
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

**Session record rule (Stop → summary, 10-minute gate).** The **Stop** button ends the session
outright (client `_endSession`): it sends `end`, halts the walk, and shows a formatted **end-of-walk
summary** (duration · distance · places narrated). The client owns the record decision — it stamps
`_sessionStart` on activation and accumulates walked distance. A session **≥10 min counts and is
kept**; a **shorter one is discarded** — the client sends `end` with `discard:true` and the backend
(`_discard_walk` in `main.py`) deletes the walk row persisted for that session, so short walks never
enter history/streaks/challenges. (History rows are still created eagerly on the first narrated
object — see `history.py`; the `end`/`discard` signal is what prunes a too-short one on Stop.)

Other endpoints: `GET /health` (liveness), `GET /ready` (503 if recent LLM calls all failed),
`GET /stats` (admin, gated by `STATS_TOKEN`), `GET /` (browser test client, `backend/web/index.html`),
plus the accounts/billing/community REST routers (`/me`, `/walks`, `/billing/...`, `/community/*`)
mounted in `main.py`.

### Neural TTS (optional, tier-gated — off by default)

By default the guide is spoken by the **client's on-device `flutter_tts`** (robotic, and it
mispronounces proper names/numbers). A **neural voice** can be turned on server-side: with
`TTS_BACKEND=openai` + a key, a **PAID** session has each spoken sentence synthesized by
`OpenAITTS` (`services/tts/tts.py`, OpenAI `gpt-4o-mini-tts` → mp3) and attached **base64 to the
same `narration`/`reply` frame** (`audio_b64`/`audio_mime`). The client (`main.dart`) plays that
audio via **`audioplayers`** when present and falls back to `flutter_tts` when absent — so free
tier, guests, TTS-off, and synth failures all still speak, unchanged. Design invariants:
- **Gating** is by `SESSION_TIER` + `tts_tier_min` (like model routing); synth happens in
  `Connection.send_out()` **before** the send lock (an HTTP call must not block heartbeat/state),
  and returns `None` on any error → text-only. It's keyed to the existing per-sentence `played`
  pacing, so weaving/barge-in/pause are untouched (`_hush`/`pause` stop the `AudioPlayer` too).
- **Cache** `(sha1(text), voice, fmt)` (memory + optional `TTS_CACHE_PATH` disk) reuses a phrase
  across sessions; TTS spend is metered into the same `METER`/`USD_HARD_CAP` as LLM cost.
- **Creds reuse the LLM's** — OpenRouter proxies an OpenAI-compatible `/audio/speech`, so
  `TTS_API_KEY`/`TTS_BASE_URL` default (empty) to `OPENAI_API_KEY`/`OPENAI_BASE_URL` (the same
  OpenRouter setup). **Region caveat (our prod):** OpenRouter geoblocks OpenAI/Google/Anthropic
  from the prod region (§5), so `openai/gpt-4o-mini-tts` is **unreachable** there — the default is
  **`x-ai/grok-voice-tts-1.0`** (voice `Ara`), which returns mp3 and works from the blocked region.
  Gemini TTS also works but only emits `pcm` (would need client-side WAV-wrapping). List what's
  available: `GET {base}/models?output_modalities=speech`.
- **iOS gotcha:** the `audioplayers` session uses the **same `.playback` category** as
  `flutter_tts` (`AudioContextIOS` in `_initTts`) so paid-tier audio keeps playing screen-locked
  and routes to Bluetooth. Free tier separately gets an **upgraded on-device voice**
  (`_selectBestVoice`: iOS enhanced/premium, Android network voices) via `getVoices`/`setVoice`.
- Knobs: `TTS_BACKEND`, `TTS_MODEL`, `TTS_VOICE`(+`_BY_LANG`), `TTS_FORMAT`, `TTS_API_KEY`,
  `TTS_BASE_URL`, `TTS_TIMEOUT_S`, `TTS_TIER_MIN`, `TTS_CACHE_PATH`, `TTS_PRICE_PER_MCHAR`.

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
`NARRATE_RADIUS_M` — the "right here" passing bubble, 55 m (was 45 m — too tight; real side-passes
hovered at 50–53 m and never fired) — with `NARRATE_RADIUS_LOW_M` (32 m) applied instead for a
LOW-significance, fact-less object (a plain kindergarten/shop) so it only fires when you're truly
beside it, not 48 m to the side (`Orchestrator._narrate_reach_m`) — and `REACH_RADIUS_M` — the
tighter cap on the gaze-gated
reach fallback so it fires for what you're *about* to reach, not 150–200 m away —
`SCORER_MAX_CANDIDATES`),
area monologue (`AREA_ENRICH`, `AREA_MAX_BEATS`, `AREA_PREFETCH` — background pre-gen of the next
outline beat, above; `AREA_CASCADE_REQUIRES_FACTS` — level-aware anti-fabrication: with no verified
facts the fact-less area cascade still keeps talking about the well-known **city** but never
descends to the street/district detail the model would invent; `AREA_CITYLESS_MAX` (2) — a hard cap
on that fact-less city fallback, because once the real city facts are spent the model **fabricates**
fresh non-repeating specifics every tick and `is_repeat` can't catch invention (the 1-я Советская
loop of 8 invented monologues) — reset by a real object / new area; note `AREA_MAX_BEATS` was found
to be **dead config**, never enforced), the inventory cache (`INVENTORY_ENABLED`,
`INVENTORY_RADIUS_M`, `INVENTORY_REFETCH_FRAC`, `INVENTORY_TTL_S`), enrichment (`ENRICH_TOP_K`,
`ENRICH_LOOKAHEAD_K`, `ENRICH_TIMEOUT_S`, `ENRICH_CACHE_PATH`), STT (`STT_BACKEND` —
`faster_whisper` local vs `openrouter` cloud; `WHISPER_*` for local, `STT_MODEL`/`STT_TIMEOUT_S`
for cloud), neural TTS (`TTS_BACKEND`, `TTS_VOICE`, `TTS_TIER_MIN`, `TTS_PRESYNTH`, … — see "Neural
TTS" above), revisit (`REVISIT_ENABLED`, `REVISIT_RADIUS_M`, `REVISIT_MIN_ROUTE_M`), guided mode
(`ROUTING_SOURCE`/`OSRM_URL`, the `ROUTE_*` planning knobs and `NAV_*` leading knobs — see "Guided
mode" above), the Phase 6 canary (`CANARY_ENABLED`, `CANARY_FRACTION`, … — dormant by default), and
the state store (`REDIS_URL`, `SESSION_TTL_S`, `MAX_SESSIONS`). `config.py` itself is the
authoritative list.

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
