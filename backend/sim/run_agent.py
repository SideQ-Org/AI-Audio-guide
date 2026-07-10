"""Run the text pipeline over the virtual walk and print the narration.

    python -m sim.run_agent                 # offline: heuristic scorer + template narrator
    python -m sim.run_agent --llm anthropic # real LLM via the LLMClient interface (needs a key)

Stage-2 deliverable: living narration over the sim, with dedup, switching,
adaptive radius and [SILENCE]. (Production model stack is multi-provider and
wired later — see ARCHITECTURE §7.)
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.config import settings
from app.services.agent.narrator import LLMNarrator, TemplateNarrator
from app.services.agent.pipeline import TextPipeline
from app.services.agent.scorer import HeuristicScorer, LLMScorer
from app.services.enrichment.enricher import MockEnricher
from app.services.geo.discovery import Discovery
from app.services.geo.providers import StaticPlaceProvider
from sim.routes import RED_SQUARE
from sim.walk import walk

_FIX = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def build_pipeline(use_llm: str | None) -> TextPipeline:
    enricher = MockEnricher.from_json(_FIX / "facts_red_square.json")
    if use_llm == "anthropic":
        from app.services.llm.client import AnthropicLLM

        llm = AnthropicLLM()
        return TextPipeline(LLMScorer(llm), LLMNarrator(llm), enricher)
    if use_llm == "openai":
        from app.services.llm.client import OpenAICompatLLM

        llm = OpenAICompatLLM()
        return TextPipeline(LLMScorer(llm), LLMNarrator(llm), enricher)
    return TextPipeline(HeuristicScorer(), TemplateNarrator(), enricher)


async def main(use_llm: str | None) -> None:
    provider = StaticPlaceProvider.from_json(_FIX / "places_red_square.json")
    discovery = Discovery(provider)
    pipeline = build_pipeline(use_llm)

    seen: list[str] = []
    history: list[str] = []
    last_place_id: str | None = None

    for step in walk(RED_SQUARE, speed_mps=1.3, step_s=8.0):
        result = await discovery.discover_adaptive(
            step.position, step.heading, seen, settings.default_radius_m
        )
        out = await pipeline.step(
            result.candidates,
            seen=seen,
            history=history,
            heading=step.heading,
            pace=step.pace,
            switching=bool(last_place_id and out_is_new(result, last_place_id)),
        )
        tag = []
        if result.expanded:
            tag.append(f"radius->{result.radius_m:.0f}m")
        if result.exhausted:
            tag.append("EXHAUSTED")
        print(f"t={step.t:5.0f}s  {' '.join(tag)}")
        if out.text:
            print(f"    Гид: {out.text}")
            history.append(out.text)
            if out.place:
                seen.append(out.place.id)
                last_place_id = out.place.id
        else:
            print("    …(молчит)")


def out_is_new(result, last_place_id: str) -> bool:
    return bool(result.candidates) and result.candidates[0].place.id != last_place_id


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--llm", choices=["anthropic", "openai"], default=None)
    asyncio.run(main(ap.parse_args().llm))
