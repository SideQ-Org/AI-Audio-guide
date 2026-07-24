"""Enrichment speed/quality upgrades: instant OSM-tag facts, Wikidata claim facts,
TTL'd negative web cache, and in-flight dedup of concurrent lookups."""

from __future__ import annotations

import asyncio

from app.services.enrichment.enricher import (
    CompositeEnricher,
    WebSearchEnricher,
    WikiEnricher,
    _osm_instant_facts,
)
from app.shared.schemas import GeoPoint, Place


def _place(tags=None, cat="monument") -> Place:
    return Place(
        id="node/1", name="Памятник", category=cat,
        location=GeoPoint(lat=55.75, lon=37.62), tags=tags or {},
    )


# --- instant facts from OSM tags ------------------------------------------------ #


def test_instant_facts_from_tags():
    facts = _osm_instant_facts({
        "inscription": "Человеку труда",
        "start_date": "1977",
        "architect": "В. И. Иванов",
        "height": "25",
        "opening_hours": "Mo-Su 10:00-18:00",
    })
    assert facts is not None
    assert "«Человеку труда»" in facts
    assert "1977" in facts and "В. И. Иванов" in facts and "25 m" in facts
    assert "10:00-18:00" in facts


def test_instant_facts_empty_tags():
    assert _osm_instant_facts({}) is None
    assert _osm_instant_facts(None) is None
    assert _osm_instant_facts({"name": "x", "amenity": "school"}) is None


class _NoneEnricher:
    async def facts_for(self, place, context=None, language="ru"):
        return None

    def image_for(self, place_id):
        return None


def test_composite_serves_tag_facts_when_wiki_and_web_empty():
    comp = CompositeEnricher(_NoneEnricher(), _NoneEnricher())
    place = _place({"inscription": "Слава героям", "start_date": "1965"})
    facts = asyncio.run(comp.facts_for(place))
    assert facts and "Слава героям" in facts and "1965" in facts


def test_composite_prepends_tag_facts_to_wiki():
    class _Wiki(_NoneEnricher):
        async def facts_for(self, place, context=None, language="ru"):
            return "Статья: памятник открыт в присутствии первых строителей."

    comp = CompositeEnricher(_Wiki(), _NoneEnricher())
    place = _place({"architect": "И. П. Мартос"})
    facts = asyncio.run(comp.facts_for(place))
    assert facts is not None
    assert facts.index("И. П. Мартос") < facts.index("Статья")


# --- in-flight dedup ------------------------------------------------------------- #


def test_concurrent_lookups_share_one_search():
    calls = {"n": 0}

    class _Slow(_NoneEnricher):
        async def facts_for(self, place, context=None, language="ru"):
            calls["n"] += 1
            await asyncio.sleep(0.05)
            return "факт"

    comp = CompositeEnricher(_Slow(), _NoneEnricher())
    place = _place()

    async def run():
        a, b = await asyncio.gather(comp.facts_for(place), comp.facts_for(place))
        return a, b

    a, b = asyncio.run(run())
    assert a == b == "факт"
    assert calls["n"] == 1, "two concurrent ticks must share ONE lookup"


# --- negative web cache TTL ------------------------------------------------------- #


class _FakeLLM:
    def __init__(self):
        self.calls = 0

    async def web_facts(self, system, user, *, max_results=3, max_tokens=400):
        self.calls += 1
        return "NONE"


def test_negative_web_cache_expires(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "enrich_retry_broaden", False)
    llm = _FakeLLM()
    web = WebSearchEnricher(llm)
    place = _place()
    assert asyncio.run(web.facts_for(place)) is None
    assert llm.calls == 1
    # Fresh negative: served from cache, no re-search.
    assert asyncio.run(web.facts_for(place)) is None
    assert llm.calls == 1
    # Expired negative (TTL=0): re-searched.
    monkeypatch.setattr(settings, "enrich_negative_ttl_s", 0.0)
    assert asyncio.run(web.facts_for(place)) is None
    assert llm.calls == 2


def test_legacy_permanent_negative_heals(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "enrich_retry_broaden", False)
    llm = _FakeLLM()
    web = WebSearchEnricher(llm)
    web._cache["ru:node/1"] = None  # legacy permanent negative from an old cache file
    assert asyncio.run(web.facts_for(_place())) is None
    assert llm.calls == 1, "a legacy None negative must be retried once (then re-TTL'd)"


# --- wikidata claim facts ---------------------------------------------------------- #


def test_claim_facts_from_entity_json():
    entity = {
        "descriptions": {"ru": {"value": "памятник в Долгопрудном"}},
        "claims": {
            "P571": [{"mainsnak": {"datavalue": {"value": {"time": "+1977-05-00T00:00:00Z"}}}}],
            "P2048": [{"mainsnak": {"datavalue": {"value": {"amount": "+25.5"}}}}],
        },
    }
    wiki = WikiEnricher()

    class _NoHTTP:  # labels are only fetched for entity-valued claims — none here
        pass

    facts = asyncio.run(wiki._claim_facts(_NoHTTP(), entity, ("ru", "en")))
    assert facts is not None
    assert "памятник в Долгопрудном" in facts
    assert "1977" in facts and "25.5 m" in facts


# --- fact interest ranking ---------------------------------------------------------- #


def test_rank_facts_puts_concrete_first():
    from app.services.agent.interest_metrics import rank_facts

    facts = [
        "Рядом растут берёзы.",
        "Здание построено в 1937 году архитектором Иваном Жолтовским.",
        "Это известное место для прогулок.",
    ]
    ranked = rank_facts(facts, "ru")
    assert ranked[0].startswith("Здание построено")
    assert rank_facts(facts, "ru", top_k=1) == [ranked[0]]


def test_area_input_carries_visible():
    from app.services.agent.prompts import build_area_user
    from app.shared.schemas import Address, AreaInput

    inp = AreaInput(address=Address(city="Москва"), visible=["Музей истории"])
    user = build_area_user(inp)
    assert "VISIBLE" in user and "Музей истории" in user
