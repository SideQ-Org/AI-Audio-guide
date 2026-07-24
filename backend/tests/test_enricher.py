import asyncio

from app.services.enrichment.enricher import (
    EnrichmentCache,
    WebSearchEnricher,
    prefetch,
)
from app.shared.schemas import Candidate, GazeConfidence, GeoPoint, Place


class FakeWebLLM:
    """Stand-in for OpenAICompatLLM.web_facts. ``reply`` may be a string or a
    callable(query)->string; raises ``boom`` times before succeeding."""

    def __init__(self, reply="* факт", boom=0):
        self._reply = reply
        self._boom = boom
        self.calls = 0

    async def web_facts(self, system, user, *, max_results=3, max_tokens=400):
        self.calls += 1
        if self._boom > 0:
            self._boom -= 1
            raise RuntimeError("network")
        return self._reply(user) if callable(self._reply) else self._reply


def _place(pid, name="X", lat=55.75, lon=37.62):
    return Place(id=pid, name=name, category="historic", location=GeoPoint(lat=lat, lon=lon))


def _cand(pid, name="X"):
    return Candidate(place=_place(pid, name), distance_m=10.0, type_weight=1.0,
                     in_gaze_cone=True, gaze_confidence=GazeConfidence.LOW)


def test_returns_and_caches_facts():
    llm = FakeWebLLM(reply="* построен в 1555 году")
    enr = WebSearchEnricher(llm)
    f1 = asyncio.run(enr.facts_for(_place("p1")))
    f2 = asyncio.run(enr.facts_for(_place("p1")))
    assert f1 == "* построен в 1555 году"
    assert f2 == f1
    assert llm.calls == 1  # second call served from cache


def test_cache_is_language_keyed():
    cache = EnrichmentCache()
    cache.put("p", "русские факты", "ru")
    cache.put("p", "english facts", "en")
    assert cache.get("p", "ru") == "русские факты"
    assert cache.get("p", "en") == "english facts"
    assert cache.has("p", "ru") and cache.has("p", "en")
    assert not cache.has("p", "fr")  # not cached in French yet
    assert "p" in cache  # __contains__ answers "cached in any language?"
    assert "q" not in cache


def test_web_enricher_searches_once_per_language():
    # A different language is a fresh search (so facts come back in that language);
    # the same language hits the cache. The system prompt carries a language directive.
    llm = FakeWebLLM(reply="* a fact")
    enr = WebSearchEnricher(llm)
    asyncio.run(enr.facts_for(_place("p"), language="en"))
    asyncio.run(enr.facts_for(_place("p"), language="en"))  # cache hit
    assert llm.calls == 1
    asyncio.run(enr.facts_for(_place("p"), language="ru"))  # different lang -> re-search
    assert llm.calls >= 2


def test_no_facts_marker_is_none_and_cached():
    llm = FakeWebLLM(reply="НЕТ")
    enr = WebSearchEnricher(llm)
    assert asyncio.run(enr.facts_for(_place("p2"))) is None
    assert asyncio.run(enr.facts_for(_place("p2"))) is None
    # A first miss now costs exactly TWO searches (exact + one broadened retry — the
    # "Человеку Труда -> empty forever" fix); the double miss is then cached negative.
    assert llm.calls == 2


def test_first_miss_retries_broadened_then_caches_positive():
    class TwoStageLLM(FakeWebLLM):
        async def web_facts(self, system, query, **kw):
            self.calls += 1
            self.last_query = query
            # exact form misses, the broadened retry (type words first) finds it
            return "НЕТ" if self.calls == 1 else "* открыт к юбилею завода"

    llm = TwoStageLLM(reply="")
    enr = WebSearchEnricher(llm)
    place = _place("p-mon")
    place.category = "memorial"
    facts = asyncio.run(enr.facts_for(place, context="Долгопрудный, Россия"))
    assert facts == "* открыт к юбилею завода"
    assert llm.calls == 2
    assert "памятник" in llm.last_query  # localized type word, not the raw OSM tag
    assert asyncio.run(enr.facts_for(place, context="Долгопрудный, Россия")) == facts
    assert llm.calls == 2  # positive result cached


def test_error_returns_none_and_is_not_cached():
    llm = FakeWebLLM(reply="* факт", boom=1)
    enr = WebSearchEnricher(llm)
    assert asyncio.run(enr.facts_for(_place("p3"))) is None  # first call raises
    assert asyncio.run(enr.facts_for(_place("p3"))) == "* факт"  # retried, succeeds
    assert llm.calls == 2


def test_query_pins_coordinates():
    q = WebSearchEnricher._query(_place("p4", "Мечеть", lat=43.3177, lon=45.6939), "Грозный")
    assert "Мечеть" in q and "Грозный" in q and "43.3177" in q and "45.6939" in q


def test_prefetch_respects_top_k_and_fills_cache():
    llm = FakeWebLLM(reply=lambda u: f"факт: {u[:6]}")
    enr = WebSearchEnricher(llm)
    cache = EnrichmentCache()
    cands = [_cand(f"p{i}", f"P{i}") for i in range(5)]
    asyncio.run(prefetch(cands, enr, cache, top_k=2))
    assert llm.calls == 2  # only the top 2 were searched
    assert sum(c.place.id in cache for c in cands) == 2


def test_disk_cache_persists(tmp_path):
    path = tmp_path / "facts.json"
    llm = FakeWebLLM(reply="* факт")
    asyncio.run(WebSearchEnricher(llm, cache_path=str(path)).facts_for(_place("p9")))
    assert path.exists()
    # a fresh enricher reads the file and does not call the LLM again
    llm2 = FakeWebLLM(reply="* другое")
    enr2 = WebSearchEnricher(llm2, cache_path=str(path))
    assert asyncio.run(enr2.facts_for(_place("p9"))) == "* факт"
    assert llm2.calls == 0


# -- composite: wiki-first, paid web only for non-wiki notable places ---------
class FakeEnricher:
    def __init__(self, reply=None):
        self._reply = reply
        self.calls = 0

    async def facts_for(self, place, context=None, language="ru"):
        self.calls += 1
        return self._reply


def test_composite_prefers_wiki_and_skips_web():
    from app.services.enrichment.enricher import CompositeEnricher
    wiki = FakeEnricher("wiki facts")
    web = FakeEnricher("web facts")
    comp = CompositeEnricher(wiki, web, web_min_weight=0.0)
    p = _place("p1", "Памятник")
    assert asyncio.run(comp.facts_for(p)) == "wiki facts"
    assert web.calls == 0  # wiki hit -> no paid web search


def _run_tier(coro, tier: str):
    """Run ``coro`` with the tier ContextVar set (the enricher reads it to gate the
    paid web-search fallback). Reset after so tiers don't leak between tests."""
    from app.services.llm.client import SESSION_TIER
    tok = SESSION_TIER.set(tier)
    try:
        return asyncio.run(coro)
    finally:
        SESSION_TIER.reset(tok)


def test_composite_falls_back_to_web_when_paid_and_no_wiki():
    from app.services.enrichment.enricher import CompositeEnricher
    wiki = FakeEnricher(None)  # no wiki article
    web = FakeEnricher("web facts")
    # category "historic" -> weight 0.75 >= 0.5 threshold -> web is used (PAID only)
    comp = CompositeEnricher(wiki, web, web_min_weight=0.5)
    assert _run_tier(comp.facts_for(_place("p2")), "paid") == "web facts"
    assert web.calls == 1


def test_composite_free_tier_is_wiki_only():
    from app.services.enrichment.enricher import CompositeEnricher
    wiki = FakeEnricher(None)  # no wiki article
    web = FakeEnricher("web facts")
    # Same qualifying weight, but FREE tier never touches the paid web search.
    comp = CompositeEnricher(wiki, web, web_min_weight=0.5)
    assert _run_tier(comp.facts_for(_place("p2")), "free") is None
    assert web.calls == 0  # cost lever: free = wiki-only


def test_composite_skips_web_for_mundane_below_threshold():
    from app.services.enrichment.enricher import CompositeEnricher
    from app.shared.schemas import GeoPoint, Place
    wiki = FakeEnricher(None)
    web = FakeEnricher("web facts")
    comp = CompositeEnricher(wiki, web, web_min_weight=0.5)
    shop = Place(id="s", name="Shop", category="shop", location=GeoPoint(lat=1, lon=2))
    # Paid tier so the WEIGHT threshold (not the tier gate) is what blocks web here.
    assert _run_tier(comp.facts_for(shop), "paid") is None  # shop weight 0.25 < 0.5
    assert web.calls == 0


def test_wiki_enricher_no_tag_returns_none():
    from app.services.enrichment.enricher import WikiEnricher
    # no wikipedia/wikidata tag -> None without any network call
    assert asyncio.run(WikiEnricher().facts_for(_place("nt"))) is None


def _tagged(pid, tags):
    return Place(id=pid, name="X", category="historic",
                location=GeoPoint(lat=55.75, lon=37.62), tags=tags)


def test_p18_image_from_wikidata_entity():
    """#1 Wikidata P18 -> a Commons thumbnail URL, extracted from the entity JSON we already
    fetch for sitelinks (no extra request) — the win for wikidata objects with no article."""
    from app.services.enrichment.enricher import WikiEnricher
    entity = {"claims": {"P18": [{"mainsnak": {"datavalue": {"value": "Red Square 01.jpg"}}}]}}
    url = WikiEnricher._p18_image(entity)
    assert url == (
        "https://commons.wikimedia.org/wiki/Special:FilePath/Red_Square_01.jpg?width=640"
    )
    # No P18 claim -> None (falls through to no image, never crashes).
    assert WikiEnricher._p18_image({"claims": {}}) is None
    assert WikiEnricher._p18_image({}) is None


def test_osm_tag_image_commons_and_url():
    """#2 photo straight off the OSM tags: wikimedia_commons=File:… and a direct https image=."""
    from app.services.enrichment.enricher import WikiEnricher
    assert WikiEnricher._osm_tag_image({"wikimedia_commons": "File:Foo bar.jpg"}) == (
        "https://commons.wikimedia.org/wiki/Special:FilePath/Foo_bar.jpg?width=640"
    )
    assert WikiEnricher._osm_tag_image({"image": "https://pics.example/x.jpg"}) == (
        "https://pics.example/x.jpg"
    )
    # http (mixed-content) and Category: are rejected; no tag -> None.
    assert WikiEnricher._osm_tag_image({"image": "http://insecure/x.jpg"}) is None
    assert WikiEnricher._osm_tag_image({"wikimedia_commons": "Category:Churches"}) is None
    assert WikiEnricher._osm_tag_image({}) is None


def test_facts_for_captures_osm_image_without_wiki_or_network():
    """A non-wiki object with an image= tag gets a card photo even though it has no facts —
    captured before the wiki gate, so no network call happens (returns None facts, image set)."""
    from app.services.enrichment.enricher import WikiEnricher
    enr = WikiEnricher()
    place = _tagged("osm1", {"image": "https://pics.example/cafe.jpg"})
    assert asyncio.run(enr.facts_for(place)) is None  # no wiki tags -> no facts, no network
    assert enr.image_for("osm1") == "https://pics.example/cafe.jpg"  # ...but the photo is there
