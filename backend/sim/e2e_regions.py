"""End-to-end regional walks: live Overpass (real OSM places) + the LLM agent.

Models real user routes across diverse RF regions (tourist hotspots AND
residential/industrial outskirts) plus a couple abroad, each in a plausible UI
language. For every route it walks a few positions through the full orchestrator
(discover -> score -> narrate, with adaptive radius + dedup) and records what the
guide actually says — proving facts-only behaviour, silence on empty outskirts,
and per-session multilingual narration.

    # all scenarios (needs AGENT_BACKEND=openai in .env):
    python -m sim.e2e_regions
    # a subset, against an Overpass mirror (public overpass-api.de is often blocked):
    E2E_ONLY=grozny-heart,rome-colosseo \
      OVERPASS_URL=https://maps.mail.ru/osm/tools/overpass/api/interpreter \
      python -m sim.e2e_regions

Env knobs: OVERPASS_URL (default settings.overpass_url), E2E_ONLY (comma keys),
E2E_OUT (markdown path, default ./e2e_results.md; .json written alongside).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from math import atan2, cos, pi, radians, sin
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.config import settings
from app.services.agent.companion import LLMCompanion
from app.services.agent.languages import normalize
from app.services.agent.narrator import LLMNarrator
from app.services.agent.orchestrator import Orchestrator
from app.services.agent.pipeline import TextPipeline
from app.services.agent.scorer import LLMScorer
from app.services.enrichment.enricher import MockEnricher, WebSearchEnricher
from app.services.geo.discovery import Discovery
from app.services.geo.providers import OverpassProvider
from app.services.llm.client import METER, OpenAICompatLLM
from app.services.state.store import InMemoryStateStore
from app.shared.schemas import GeoPoint, Heading, Pace

_FIX = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
OVERPASS_URL = os.environ.get("OVERPASS_URL", settings.overpass_url)
OUT_MD = Path(os.environ.get("E2E_OUT", "e2e_results.md"))
OUT_JSON = OUT_MD.with_suffix(".json")
ONLY = {k for k in os.environ.get("E2E_ONLY", "").split(",") if k}


def bearing(a, b) -> float:
    la1, la2, dn = radians(a[0]), radians(b[0]), radians(b[1] - a[1])
    y = sin(dn) * cos(la2)
    x = cos(la1) * sin(la2) - sin(la1) * cos(la2) * cos(dn)
    return (atan2(y, x) * 180 / pi + 360) % 360


# (key, title, kind, language, [waypoints lat,lon]) — short realistic walks.
SCENARIOS = [
    ("msk-red-square", "Москва — Красная площадь", "турист", "ru",
     [(55.7539, 37.6208), (55.7525, 37.6231), (55.7517, 37.6189)]),
    ("msk-kapotnya", "Москва — Капотня (промзона/окраина)", "окраина", "ru",
     [(55.6388, 37.7958), (55.6372, 37.7990), (55.6356, 37.8020)]),
    ("spb-nevsky", "Санкт-Петербург — Невский проспект", "турист", "ru",
     [(59.9343, 30.3351), (59.9311, 30.3470), (59.9286, 30.3585)]),
    ("spb-kupchino", "Санкт-Петербург — Купчино (спальный)", "окраина", "ru",
     [(59.8300, 30.3760), (59.8286, 30.3802), (59.8270, 30.3845)]),
    ("kazan-kremlin", "Казань — Кремль / Кул-Шариф", "турист", "ru",
     [(55.7989, 49.1064), (55.7975, 49.1082), (55.7963, 49.1098)]),
    ("ekb-uralmash", "Екатеринбург — Уралмаш (рабочая окраина)", "окраина", "ru",
     [(56.8835, 60.5870), (56.8850, 60.5902), (56.8865, 60.5934)]),
    ("sochi-naberezhnaya", "Сочи — Морвокзал / набережная", "турист", "ru",
     [(43.5782, 39.7200), (43.5766, 39.7221), (43.5750, 39.7242)]),
    ("kaliningrad-kant", "Калининград — Кафедральный собор (Кант)", "турист", "ru",
     [(54.7065, 20.5118), (54.7055, 20.5135), (54.7045, 20.5152)]),
    # «Сердце Чечни» (мечеть им. А. Кадырова) is at 43.3177, 45.6939.
    ("grozny-heart", "Грозный — «Сердце Чечни» / пр. Путина", "турист", "ru",
     [(43.3177, 45.6939), (43.3168, 45.6952), (43.3159, 45.6965)]),
    ("nnov-sormovo", "Нижний Новгород — Сормово (пром. окраина)", "окраина", "ru",
     [(56.3760, 43.8680), (56.3745, 43.8702), (56.3730, 43.8724)]),
    ("paris-eiffel", "Париж — Эйфелева башня / Марсово поле", "турист", "fr",
     [(48.8584, 2.2945), (48.8570, 2.2962), (48.8556, 2.2980)]),
    ("rome-colosseo", "Рим — Колизей / Римский форум", "турист", "it",
     [(41.8902, 12.4922), (41.8915, 12.4901), (41.8925, 12.4880)]),
]


async def run_scenario(orch: Orchestrator, sc) -> dict:
    key, title, kind, lang, route = sc
    st = await orch.store.load(key)
    st.language = normalize(lang)
    await orch.store.save(st)

    c0 = METER.cost_usd
    steps, narrations, error = [], [], None
    for i, pt in enumerate(route):
        nxt = route[i + 1] if i + 1 < len(route) else route[i]
        hd = Heading(direction_deg=bearing(pt, nxt))
        try:
            out = await orch.on_position(key, GeoPoint(lat=pt[0], lon=pt[1]), hd, Pace.SLOW)
        except Exception as e:  # network/Overpass hiccup
            error = f"{type(e).__name__}: {e}"
            break
        steps.append({"pos": pt, "state": out.state, "kind": out.kind,
                      "place": out.place_name, "sig": out.significance})
        if out.kind == "narration" and out.text:
            narrations.append({"place": out.place_name, "sig": out.significance,
                               "text": out.text})

    st2 = await orch.store.load(key)
    return {"key": key, "title": title, "kind": kind, "lang": lang,
            "places_seen": len(st2.seen_place_ids), "final_radius_m": st2.current_radius_m,
            "narrations": narrations, "steps": steps,
            "cost_usd": round(METER.cost_usd - c0, 5), "error": error}


async def main() -> None:
    llm = OpenAICompatLLM()
    web = settings.enrichment_source == "websearch"
    if web:
        enricher = WebSearchEnricher(llm, max_results=settings.web_search_max_results,
                                     max_tokens=settings.web_search_max_tokens,
                                     cache_path=settings.enrich_cache_path)
    else:
        enricher = MockEnricher.from_json(_FIX / "facts_red_square.json")
    pipeline = TextPipeline(LLMScorer(llm), LLMNarrator(llm), enricher,
                            enrich_top_k=settings.enrich_top_k if web else None,
                            enrich_timeout_s=settings.enrich_timeout_s if web else None)
    orch = Orchestrator(Discovery(OverpassProvider(url=OVERPASS_URL)), pipeline,
                        LLMCompanion(llm), InMemoryStateStore())
    print(f"Overpass: {OVERPASS_URL} · enrichment: {settings.enrichment_source}", flush=True)

    results = []
    for sc in SCENARIOS:
        if ONLY and sc[0] not in ONLY:
            continue
        print(f"\n===== {sc[1]}  [{sc[3]}] =====", flush=True)
        res = await run_scenario(orch, sc)
        results.append(res)
        if res["error"]:
            print(f"  ⚠ ERROR: {res['error']}", flush=True)
        elif not res["narrations"]:
            print(f"  · молчание (мест: {res['places_seen']}, радиус "
                  f"{res['final_radius_m']:.0f}м)  ${res['cost_usd']}", flush=True)
        for n in res["narrations"]:
            print(f"  🗣 [{n['sig']}] {n['place']}: {n['text'][:160]}", flush=True)
        await asyncio.sleep(1.5)  # be gentle to public Overpass

    OUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    total = round(sum(r["cost_usd"] for r in results), 4)
    n_narr = sum(len(r["narrations"]) for r in results)
    lines = ["# E2E прогон по регионам — результаты\n",
             f"Сценариев: {len(results)} · озвучек: {n_narr} · итог: ${total}\n",
             "| # | Маршрут | Тип | Язык | Мест | Радиус | Озвучек | $ |",
             "|---|---------|-----|------|------|--------|---------|---|"]
    for i, r in enumerate(results, 1):
        lines.append(f"| {i} | {r['title']} | {r['kind']} | {r['lang']} | {r['places_seen']} | "
                     f"{r['final_radius_m']:.0f}м | {len(r['narrations'])} | {r['cost_usd']} |")
    lines.append("\n## Озвучки по сценариям\n")
    for r in results:
        lines.append(f"### {r['title']}  [{r['lang']}]  — {r['kind']}")
        if r["error"]:
            lines.append(f"- ⚠ {r['error']}\n")
            continue
        if not r["narrations"]:
            lines.append(f"- _молчание_ (мест: {r['places_seen']}, "
                         f"радиус {r['final_radius_m']:.0f}м)\n")
            continue
        for n in r["narrations"]:
            lines.append(f"- **[{n['sig']}] {n['place']}** — {n['text']}")
        lines.append("")
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nИТОГ: {len(results)} сценариев, {n_narr} озвучек, ${total}", flush=True)
    print(f"Отчёт: {OUT_MD}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
