"""Build an Orchestrator from settings — picks Geo source, enricher and the
agent backend (heuristic / openai / anthropic). Keeps the WS handler thin.
"""

from __future__ import annotations

from pathlib import Path

from app.config import settings
from app.services.agent.companion import Companion, HeuristicCompanion, LLMCompanion
from app.services.agent.narrator import LLMNarrator, Narrator, TemplateNarrator
from app.services.agent.orchestrator import Orchestrator
from app.services.agent.pipeline import TextPipeline
from app.services.agent.planner import HeuristicPlanner, LLMPlanner, Planner
from app.services.agent.scorer import HeuristicScorer, LLMScorer, Scorer
from app.services.agent.summarizer import LLMSummarizer, NullSummarizer, Summarizer
from app.services.enrichment.enricher import (
    CompositeEnricher,
    Enricher,
    MockEnricher,
    WebSearchEnricher,
    WikiEnricher,
)
from app.services.geo.discovery import Discovery
from app.services.geo.geocoder import Geocoder, OverpassGeocoder
from app.services.geo.providers import OverpassProvider, StaticPlaceProvider
from app.services.state.store import StateStore, default_store

# Demo data (self-contained walk) — used when geo_source/enrichment=fixture/mock.
_FIX = Path(__file__).resolve().parents[3] / "tests" / "fixtures"


def _roles() -> tuple[Scorer, Narrator, Companion]:
    backend = settings.agent_backend
    if backend in ("openai", "anthropic"):
        if backend == "openai":
            from app.services.llm.client import OpenAICompatLLM

            llm = OpenAICompatLLM()
        else:
            from app.services.llm.client import AnthropicLLM

            llm = AnthropicLLM()
        return LLMScorer(llm), LLMNarrator(llm), LLMCompanion(llm)
    return HeuristicScorer(), TemplateNarrator(), HeuristicCompanion()


def _summarizer() -> Summarizer:
    """The end-of-walk recap LLM (its own stateless client), or a no-op offline."""
    if settings.agent_backend == "openai":
        from app.services.llm.client import OpenAICompatLLM

        return LLMSummarizer(OpenAICompatLLM())
    if settings.agent_backend == "anthropic":
        from app.services.llm.client import AnthropicLLM

        return LLMSummarizer(AnthropicLLM())
    return NullSummarizer()


def _discovery() -> Discovery:
    if settings.geo_source == "overpass":
        return Discovery(OverpassProvider())
    return Discovery(StaticPlaceProvider.from_json(_FIX / "places_red_square.json"))


def _enricher() -> Enricher:
    if settings.enrichment_source == "websearch":
        from app.services.llm.client import OpenAICompatLLM

        web = WebSearchEnricher(
            OpenAICompatLLM(),
            max_results=settings.web_search_max_results,
            max_tokens=settings.web_search_max_tokens,
            cache_path=settings.enrich_cache_path,
        )
        # Wikipedia/Wikidata first (free, high quality); paid web search only for
        # places without a wiki article and notable enough.
        return CompositeEnricher(
            WikiEnricher(), web, web_min_weight=settings.enrich_min_weight
        )
    return MockEnricher.from_json(_FIX / "facts_red_square.json")


def _geocoder() -> Geocoder | None:
    # Reverse geocoding only makes sense with the live geo source; the fixture
    # demo is self-contained/offline, so it stays addressless (no network).
    if settings.geocoder_source == "overpass" and settings.geo_source == "overpass":
        return OverpassGeocoder()
    return None


def _area_llm():
    """An LLM with web_facts() for area enrichment — only when we have a real web
    provider (OpenRouter). None otherwise (area monologue uses general knowledge)."""
    if settings.area_enrich and settings.agent_backend == "openai":
        from app.services.llm.client import OpenAICompatLLM

        return OpenAICompatLLM()
    return None


def _name_localizer():
    """Translates place TITLES to the session language (cheap, cached). LLM-backed on
    the real backends; the no-LLM default (deterministic romanization) is used for the
    offline/heuristic path."""
    from app.services.agent.name_localizer import NameLocalizer

    backend = settings.agent_backend
    if backend == "openai":
        from app.services.llm.client import OpenAICompatLLM

        return NameLocalizer(OpenAICompatLLM())
    if backend == "anthropic":
        from app.services.llm.client import AnthropicLLM

        return NameLocalizer(AnthropicLLM())
    return NameLocalizer()


def _planner() -> Planner:
    """Forms the per-area story arc. LLM-backed in production; deterministic
    (names the area + generic outline) for the offline/heuristic path."""
    backend = settings.agent_backend
    if backend == "openai":
        from app.services.llm.client import OpenAICompatLLM

        return LLMPlanner(OpenAICompatLLM())
    if backend == "anthropic":
        from app.services.llm.client import AnthropicLLM

        return LLMPlanner(AnthropicLLM())
    return HeuristicPlanner()


def build_orchestrator(store: StateStore | None = None) -> Orchestrator:
    scorer, narrator, companion = _roles()
    web = settings.enrichment_source == "websearch"
    pipeline = TextPipeline(
        scorer,
        narrator,
        _enricher(),
        language=settings.default_language,
        # bound enrichment only for the real (slow/paid) provider; the mock path
        # enriches every candidate inline (instant).
        enrich_top_k=settings.enrich_top_k if web else None,
        enrich_timeout_s=settings.enrich_timeout_s if web else None,
        area_llm=_area_llm(),
        planner=_planner(),
        name_localizer=_name_localizer(),
    )
    return Orchestrator(
        _discovery(), pipeline, companion, store or default_store(),
        geocoder=_geocoder(), summarizer=_summarizer(),
    )
