# AI Audio Guide — backend

Python (FastAPI + asyncio + WebSocket) backend for the real-time audio guide.
Design: see `../ARCHITECTURE.md`.

## Setup
```bash
cd backend
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev,stt]"   # Windows (stt = local Whisper)
# source .venv/bin/activate && pip install -e ".[dev,stt]"  # POSIX
cp .env.example .env        # then add OPENAI_API_KEY (OpenRouter sk-or-...)
```

## Run
```bash
.venv\Scripts\python -m uvicorn app.main:app --reload
```
- `GET /health` — liveness
- `GET /` — WebSocket test client (`web/index.html`)
- `WS  /ws` — drives the agent: `position` / `utterance` / `audio` / `language` / `ping` / `prewarm` in →
  `narration` / `reply` / `transcript` / `state` / `ping` out. **Guided mode ("Проведи меня"):**
  `start_guided` / `route_accept` / `route_reject` / `skip_stop` in → `route` / `route_accepted` /
  `stop_reached` / `reroute` / `route_done` out; plus a `track` frame carrying the street-snapped
  walked track. `prewarm` is a non-narrative home-screen warmup: the client may open a short-lived
  socket, send `language` + `prewarm`, and close it again; the backend warms inventory / area /
  startup-contract caches but must not set `live_position`, greet, or wake the producer. Connect
  with `?sid=<stable-id>` to **resume** the same session across reconnects (WiFi/cell drops); both
  sides send a `ping` keepalive so an idle socket isn't reaped mid-walk.

## Checks
```bash
.venv\Scripts\python -m ruff check .
.venv\Scripts\python -m pytest -q
```

## Deploy on a LAN server (Docker)

Run the backend on a box the phone can reach over Wi-Fi (`Dockerfile` +
`docker-compose.yml` included; STT model + fact cache live on a named volume):
```bash
# on the server, in the backend/ folder:
cp .env.example .env          # then fill OPENAI_API_KEY (sk-or-...)
docker compose up -d --build
curl http://localhost:8000/health        # {"status":"ok",...}
```
The phone then connects to `ws://<server-ip>:8000/ws`, typically by building/running the client with
`--dart-define=WS_URL=ws://<server-ip>:8000/ws` (the in-app WebSocket URL field has been removed).
Key `.env` values for a **real walk**: `GEO_SOURCE=overpass` (not `fixture`),
`ENRICHMENT_SOURCE=websearch`, `AGENT_BACKEND=openai`. The first voice question
downloads the Whisper model into the volume (one-time).

## End-to-end regional testing

`sim/e2e_regions.py` walks real OSM routes through the full agent (discover → score →
narrate, adaptive radius + dedup) across diverse RF regions and abroad — tourist centres
**and** residential/industrial outskirts — each in a per-session language.
```bash
# public overpass-api.de is often blocked → use a mirror
OVERPASS_URL=https://maps.mail.ru/osm/tools/overpass/api/interpreter \
  .venv\Scripts\python -m sim.e2e_regions
# subset: E2E_ONLY=msk-red-square,paris-eiffel  ·  output: E2E_OUT=path.md
```
Latest run (2026-06-24): **12 маршрутов, 24 озвучки, ~$0.24** — facts in city centres,
modest/no-cliché on outskirts, silence where nothing notable, French/Italian abroad. Full
results, highlights and findings: [`../E2E_REGIONS.md`](../E2E_REGIONS.md).

Facts come from real enrichment (`ENRICHMENT_SOURCE=websearch`): a **`CompositeEnricher`** that
tries **Wikipedia/Wikidata first (free, high quality)** for OSM places tagged
`wikipedia=`/`wikidata=`, and falls back to a **paid web search** (OpenRouter web plugin via
`WebSearchEnricher`, ~$0.032/search) only for places without a wiki article. Kept off the
hot-path (top-K candidates, prefetch-ahead, 9 s timeout, memory+disk cache, coordinate-
disambiguated). `ENRICH_MIN_WEIGHT` gates only the paid fallback (0 = full quality). So a
wiki-tagged place costs ~$0.008 vs ~$0.038 with a search. Set `ENRICHMENT_SOURCE=mock` for
offline/fixture runs.

## Layout
```
app/
  config.py                  # settings (.env)
  shared/schemas.py          # domain + role I/O + WebSocket contract
  services/
    agent/                   # orchestrator, pipeline, scorer/narrator/companion, planner,
                             #   tour_scripter (whole-route guided arc), summarizer, director
    geo/                     # Overpass provider, ranking, categories (+ is_junk filter),
                             #   routing (OSRM foot/straight), route_planner, track_match
    enrichment/enricher.py   # Wiki + WebSearch + Composite + cache
    llm/                     # OpenAI-compatible client, role router
    stt/                     # faster-whisper / mock
prompts/                     # CORE / scorer / narrator / planner / scripter / companion templates
sim/                         # virtual walk, run_orchestrator, eval_live, e2e_regions, smoke_*
tests/                       # pytest
Dockerfile, docker-compose.yml   # LAN deploy (backend · quality-worker · osrm-foot)
web/index.html               # WS test client
```
