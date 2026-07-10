"""NameLocalizer: translate the common-noun parts of a Cyrillic title into the
session language, keep proper names, fall back to romanization without an LLM."""

import asyncio

from app.services.agent.name_localizer import NameLocalizer


class FakeLLM:
    def __init__(self, fn):
        self._fn = fn
        self.calls = 0

    async def complete_text(self, role, system, user, *, max_tokens=1024):
        self.calls += 1
        return self._fn(user)


def test_translates_cyrillic_common_nouns_and_caches():
    llm = FakeLLM(lambda u: "City Park")
    loc = NameLocalizer(llm)
    r = asyncio.run(loc.localize({"name": "Городской парк"}, "Городской парк", "en"))
    assert r == "City Park"
    asyncio.run(loc.localize({"name": "Городской парк"}, "Городской парк", "en"))
    assert llm.calls == 1  # second call served from cache


def test_prefers_exonym_and_keeps_russian_without_calling_llm():
    llm = FakeLLM(lambda u: "SHOULD NOT BE CALLED")
    loc = NameLocalizer(llm)
    tags = {"name": "Красная площадь", "name:en": "Red Square"}
    assert asyncio.run(loc.localize(tags, "Красная площадь", "en")) == "Red Square"
    # A Russian session keeps the authentic Cyrillic name.
    assert asyncio.run(loc.localize({"name": "Красная площадь"}, "Красная площадь", "ru")) \
        == "Красная площадь"
    # A name already in Latin script is kept as-is (no translation).
    assert asyncio.run(loc.localize({"name": "Tverskaya"}, "Tverskaya", "en")) == "Tverskaya"
    assert llm.calls == 0


def test_no_llm_falls_back_to_romanization():
    loc = NameLocalizer()  # no LLM (offline / heuristic)
    assert asyncio.run(loc.localize({"name": "Звонница"}, "Звонница", "en")) == "Zvonnitsa"


def test_batch_is_sync_exonym_then_romanize_no_llm_call():
    # localize_batch must be fast & synchronous (runs on the pin-frame path): it never
    # calls the LLM, only exonym / cache / romanization.
    llm = FakeLLM(lambda u: "SHOULD NOT BE CALLED")
    loc = NameLocalizer(llm)
    items = [
        ({"name": "Красная площадь", "name:en": "Red Square"}, "Красная площадь"),  # exonym
        ({"name": "Звонница"}, "Звонница"),                                          # romanize
        ({"name": "Tverskaya"}, "Tverskaya"),                                        # Latin, kept
    ]
    assert loc.localize_batch(items, "en") == ["Red Square", "Zvonnitsa", "Tverskaya"]
    assert llm.calls == 0


def test_warm_batch_fills_cache_for_the_next_frame():
    llm = FakeLLM(lambda u: "1. City Park\n2. Belfry")
    loc = NameLocalizer(llm)
    items = [({"name": "Городской парк"}, "Городской парк"), ({"name": "Звонница"}, "Звонница")]

    # Before warming, the (synchronous) frame romanizes the uncached names.
    assert loc.localize_batch(items, "en") == ["Gorodskoy park", "Zvonnitsa"]

    async def warm():
        loc.warm_batch(items, "en")  # schedules a background task
        await asyncio.gather(*loc._warm_tasks)  # let it finish (one LLM call)

    asyncio.run(warm())
    assert llm.calls == 1
    # The next frame now serves the cached translations.
    assert loc.localize_batch(items, "en") == ["City Park", "Belfry"]
