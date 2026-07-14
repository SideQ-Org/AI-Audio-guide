"""Fact enrichment: a cache + providers, kept OFF the hot-path.

The orchestrator prefetches facts for upcoming places into the cache; the
narrator reads the cache non-blocking (a miss → empty FACTS → generic/silence).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.parse
from pathlib import Path
from typing import Protocol

import httpx

from app.services.agent.languages import normalize, prompt_language
from app.services.agent.walklog import get_logger, tick_bump
from app.shared.schemas import Candidate, Place

_log = logging.getLogger("aiguide.enrich")
_wlog = get_logger()  # unified walk trace (aiguide.agent), so enrich shows in one stream

_CACHE_CAP = 5000  # per-cache entry ceiling so facts dicts can't grow unbounded

# Default session language across the offline stack (mirrors the "ru" defaults on
# SessionState / the pipeline / ScorerInput), so cache get/put stay consistent.
_DEFAULT_LANG = "ru"


def _bounded_set(cache: dict, key, value, cap: int = _CACHE_CAP) -> None:
    """Insert into a dict with a FIFO size cap (drop the oldest entry when full)."""
    if key not in cache and len(cache) >= cap:
        cache.pop(next(iter(cache)), None)
    cache[key] = value


def _lang_directive(language: str) -> str:
    """Instruction appended to an enrichment system prompt so the model writes the
    facts in the SESSION language, not the language of the (often local) sources —
    e.g. an English session about a Moscow district must not get Russian facts that
    then leak into the narration verbatim."""
    return (
        f" Write the facts in {prompt_language(language)}, translating from the "
        "sources if needed; output only that language."
    )


class Enricher(Protocol):
    async def facts_for(
        self, place: Place, context: str | None = None, language: str = _DEFAULT_LANG
    ) -> str | None: ...

    def image_for(self, place_id: str) -> str | None:
        """Object photo URL captured during enrichment (Wikipedia lead image), or None.
        Default: no image source (overridden by WikiEnricher/CompositeEnricher)."""
        return None


class EnrichmentCache:
    """Facts cache keyed by (place_id, language): the SAME place yields different
    facts per session language, so a Russian session's facts must not be served to
    an English one. ``place_id in cache`` still answers "any language cached?"."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], str] = {}

    def get(self, place_id: str, language: str = _DEFAULT_LANG) -> str | None:
        return self._cache.get((place_id, normalize(language)))

    def put(self, place_id: str, facts: str, language: str = _DEFAULT_LANG) -> None:
        _bounded_set(self._cache, (place_id, normalize(language)), facts)

    def has(self, place_id: str, language: str = _DEFAULT_LANG) -> bool:
        return (place_id, normalize(language)) in self._cache

    def __contains__(self, place_id: str) -> bool:
        return any(pid == place_id for pid, _ in self._cache)


class MockEnricher:
    """Facts from a static fixture (place_id -> facts). For offline sim/tests."""

    def __init__(self, facts: dict[str, str]) -> None:
        self._facts = facts

    async def facts_for(
        self, place: Place, context: str | None = None, language: str = _DEFAULT_LANG
    ) -> str | None:
        return self._facts.get(place.id)

    def image_for(self, place_id: str) -> str | None:
        return None

    @classmethod
    def from_json(cls, path: str | Path) -> MockEnricher:
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))


# Language-neutral instruction (English) so web search isn't biased toward any one
# locale's sources. The facts themselves need no fixed language: the Narrator always
# re-expresses them in the session's {language}. Sentinel is a language-independent
# token (see _is_no_data) so "no reliable data" detection works regardless of locale.
_ENRICH_SYSTEM = (
    "You gather verifiable facts about one specific place for an audio guide. The "
    "place is given by name, city/country and coordinates. CRITICAL: use facts about "
    "this exact object at this exact location only. If search results refer to a "
    "same-named place in another city or country, ignore them. Never mix facts about "
    "different places. From the web-search results, give 2-4 short, reliable facts "
    "(history, who/when built it, what makes it notable, curious details). Facts only "
    "— no filler, no opinions, no invention. If there is no reliable information about "
    "this exact place at this location, reply with exactly: NONE."
)


def _is_no_data(text: str) -> bool:
    """True if the model signalled 'no reliable facts'. Accepts the neutral NONE
    sentinel and the legacy Russian НЕТ, ignoring leading bullets/punctuation."""
    head = text.upper().lstrip("*•-. ")
    return head.startswith("NONE") or head.startswith("НЕТ")


class WebSearchEnricher:
    """Real facts via the OpenRouter "web" plugin. Off the hot-path: a per-place
    negative+positive cache (memory, optionally a JSON file) means each place is
    searched at most once; network/empty results degrade to None (no facts)."""

    def __init__(
        self,
        llm,
        *,
        max_results: int = 3,
        max_tokens: int = 400,
        cache_path: str = "",
    ) -> None:
        self._llm = llm
        self._max_results = max_results
        self._max_tokens = max_tokens
        self._path = Path(cache_path) if cache_path else None
        self._cache: dict[str, str | None] = {}
        if self._path and self._path.exists():
            try:
                self._cache = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._cache = {}

    @staticmethod
    def _query(place: Place, context: str | None) -> str:
        # Always pin the location with coordinates so the model can reject a
        # same-named place elsewhere (e.g. an OSM "Eurocity" in Moscow vs Gibraltar).
        where = context or place.tags.get("addr:city") or ""
        coords = f"coordinates {place.location.lat:.4f}, {place.location.lon:.4f}"
        parts = [
            place.name,
            f"({place.category})" if place.category else "",
            where,
            coords,
        ]
        return " ".join(p for p in parts if p).strip()

    def _persist(self) -> None:
        if not self._path:
            return
        try:
            self._path.write_text(
                json.dumps(self._cache, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    async def facts_for(
        self, place: Place, context: str | None = None, language: str = _DEFAULT_LANG
    ) -> str | None:
        # Key by language too: the same place is searched once per session language so
        # an English session never reuses a Russian session's Russian facts. String
        # key ("lang:id") so the optional JSON disk cache stays serialisable.
        key = f"{normalize(language)}:{place.id}"
        if key in self._cache:
            return self._cache[key]
        facts: str | None = None
        try:
            text = await self._llm.web_facts(
                _ENRICH_SYSTEM + _lang_directive(language),
                self._query(place, context),
                max_results=self._max_results,
                max_tokens=self._max_tokens,
            )
            cleaned = text.strip()
            if cleaned and not _is_no_data(cleaned):
                facts = cleaned
        except Exception as e:  # network/provider hiccup — degrade to no facts
            _log.warning("enrich failed for %s: %s", place.id, e)
            return None  # transient: don't cache, retry on a later tick
        _bounded_set(self._cache, key, facts)
        self._persist()
        return facts

    def image_for(self, place_id: str) -> str | None:
        return None  # web-search facts carry no reliable image


class WikiEnricher:
    """Free facts from Wikipedia/Wikidata for OSM places tagged wikipedia=/wikidata=.
    Most landmarks carry these tags, so this covers them at no cost (and higher
    quality) — the paid web search is only needed for the untagged long tail."""

    def __init__(
        self, *, summary_chars: int = 700, prefer_langs: tuple[str, ...] = ("ru", "en")
    ) -> None:
        self._chars = summary_chars
        self._prefer = prefer_langs
        self._cache: dict[str, str | None] = {}
        # place_id -> lead-image URL (Wikipedia thumbnail), captured for free from the same
        # page/summary response we fetch for facts. Language-independent, so keyed by id only.
        self._images: dict[str, str] = {}

    def image_for(self, place_id: str) -> str | None:
        """The object's photo URL (Wikipedia lead image) if one was found during enrichment."""
        return self._images.get(place_id)

    def _langs(self, language: str) -> tuple[str, ...]:
        """Preferred Wikipedia languages for this session: the session language, then
        English, then the configured defaults — deduped, order-preserving. So an EN
        session reads the English article, not the Russian one the old default forced."""
        out: list[str] = []
        for code in (normalize(language), "en", *self._prefer):
            if code not in out:
                out.append(code)
        return tuple(out)

    async def facts_for(
        self, place: Place, context: str | None = None, language: str = _DEFAULT_LANG
    ) -> str | None:
        wp = place.tags.get("wikipedia")
        wd = place.tags.get("wikidata")
        if not wp and not wd:
            return None
        key = f"{normalize(language)}:{place.id}"
        if key in self._cache:
            return self._cache[key]
        prefer = self._langs(language)
        facts: str | None = None
        try:
            # Wikimedia requires a descriptive User-Agent (a bare one is 403'd).
            async with httpx.AsyncClient(
                timeout=10.0,
                # Wiki article-title lookups legitimately 302 (redirect pages), so keep
                # redirects ON but bound them — a fixed Wikimedia host, no user URL, so
                # SSRF-via-redirect isn't a concern; the cap just stops redirect loops.
                follow_redirects=True,
                max_redirects=3,
                headers={
                    "User-Agent": "AI-Audio-Guide/0.1 "
                    "(https://github.com/ai-audio-guide; audioguide@example.org)"
                },
            ) as client:
                # Wikidata FIRST when available: its sitelinks let us pick the article
                # in the session language (the `wikipedia=` tag is pinned to one
                # language — often `ru:` here — and would otherwise force that locale).
                image: str | None = None
                if wd:
                    facts, image = await self._from_wikidata(client, wd, prefer)
                if not facts and wp:
                    lang, _, title = wp.partition(":")
                    if not title:  # tag was just a title, no "lang:" prefix
                        lang, title = prefer[0], wp
                    facts, image = await self._summary(client, lang, title)
        except Exception as e:  # transient network/parse — don't cache, retry later
            _log.warning("wiki enrich failed for %s: %s", place.id, e)
            return None
        if image:  # the page's lead photo — free, straight from the summary response
            self._images[place.id] = image
        _bounded_set(self._cache, key, facts)
        return facts

    async def _summary(
        self, client: httpx.AsyncClient, lang: str, title: str
    ) -> tuple[str | None, str | None]:
        """(extract, image_url) from the Wikipedia REST summary. The image is the page's lead
        photo — `thumbnail` (already sized, ideal for a card), falling back to `originalimage`."""
        t = urllib.parse.quote(title.replace(" ", "_"), safe="")
        r = await client.get(f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{t}")
        if r.status_code != 200:
            return None, None
        data = r.json()
        extract = (data.get("extract") or "").strip()
        image = (data.get("thumbnail") or {}).get("source") or (
            data.get("originalimage") or {}
        ).get("source")
        return (extract[: self._chars] if extract else None), image

    async def _from_wikidata(
        self, client: httpx.AsyncClient, qid: str, prefer: tuple[str, ...]
    ) -> tuple[str | None, str | None]:
        r = await client.get(f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json")
        if r.status_code != 200:
            return None, None
        links = r.json().get("entities", {}).get(qid, {}).get("sitelinks", {})
        for lang in prefer:
            sl = links.get(f"{lang}wiki")
            if sl:
                return await self._summary(client, lang, sl["title"])
        return None, None


class CompositeEnricher:
    """Wikipedia first (free), then the paid web search only for places without a
    wiki article and notable enough (type weight >= ``web_min_weight``).

    Tier gate (feature: account tiers): the paid web-search fallback is the dominant
    per-tour cost, so it runs for **paid** sessions only. Free sessions are wiki-only —
    which keeps free ≈ ad-revenue-neutral and makes richer facts a paid differentiator.
    """

    def __init__(self, wiki: Enricher, web: Enricher, *, web_min_weight: float = 0.0) -> None:
        self._wiki = wiki
        self._web = web
        self._web_min_weight = web_min_weight

    def image_for(self, place_id: str) -> str | None:
        return self._wiki.image_for(place_id)  # photos come from the wiki path only

    async def facts_for(
        self, place: Place, context: str | None = None, language: str = _DEFAULT_LANG
    ) -> str | None:
        from app.services.metrics import GUIDE

        t0 = time.monotonic()
        facts = await self._wiki.facts_for(place, context, language)
        if facts:
            GUIDE.enrich("wiki")  # free facts — the cheap, preferred path
            tick_bump("wiki")
            _wlog.info("enrich %r -> wiki (t=%dms)",
                       place.name, int((time.monotonic() - t0) * 1000))
            return facts
        from app.services.geo.categories import weight_for
        from app.services.llm.client import SESSION_TIER

        if SESSION_TIER.get() == "paid" and weight_for(place.category) >= self._web_min_weight:
            GUIDE.enrich("web")  # paid web-search fallback (paid tier only)
            tick_bump("web")
            web_facts = await self._web.facts_for(place, context, language)
            _log.info(
                "web-search enrich %s %r -> %s",
                place.id, place.name, "facts" if web_facts else "empty",
            )
            _wlog.info("enrich %r -> web %s (t=%dms)", place.name,
                       "facts" if web_facts else "empty",
                       int((time.monotonic() - t0) * 1000))
            return web_facts
        GUIDE.enrich("miss")
        _wlog.info("enrich %r -> miss (no wiki; not paid/notable for web)", place.name)
        return None


async def prefetch(
    candidates: list[Candidate],
    enricher: Enricher,
    cache: EnrichmentCache,
    *,
    top_k: int | None = None,
    timeout_s: float | None = None,
    context: str | None = None,
    language: str = _DEFAULT_LANG,
) -> None:
    """Populate the cache with facts (in ``language``) for the uncached candidates.

    Only the top ``top_k`` (ranking-ordered, best first) are fetched — concurrently
    and bounded by ``timeout_s`` so a slow/real provider can't stall the tick. Any
    fetch that hasn't finished in time is dropped; its place is retried next tick.
    With ``top_k=None`` and ``timeout_s=None`` every candidate is fetched (the cheap
    mock/fixture path used by tests).
    """
    pending = [c for c in candidates if not cache.has(c.place.id, language)]
    hits = len(candidates) - len(pending)
    if top_k is not None:
        pending = pending[:top_k]
    if hits:
        tick_bump("enrich_hit", hits)  # already-cached facts reused (no fetch)
    if not pending:
        return
    _wlog.debug("prefetch: %d considered, %d cached, %d fetching",
                len(candidates), hits, len(pending))

    async def _one(c: Candidate) -> tuple[str, str | None]:
        return c.place.id, await enricher.facts_for(c.place, context, language)

    tasks = [asyncio.ensure_future(_one(c)) for c in pending]
    done, not_done = await asyncio.wait(tasks, timeout=timeout_s)
    for t in not_done:
        t.cancel()
    for t in done:
        try:
            place_id, facts = t.result()
        except Exception:  # noqa: BLE001 — one bad fetch shouldn't sink the rest
            continue
        if facts:
            cache.put(place_id, facts, language)


def attach_facts(
    candidates: list[Candidate], cache: EnrichmentCache, language: str = _DEFAULT_LANG
) -> list[Candidate]:
    """Return candidates with facts_available/facts_snippet filled from the cache."""
    out: list[Candidate] = []
    for c in candidates:
        facts = cache.get(c.place.id, language)
        out.append(
            c.model_copy(update={"facts_available": facts is not None, "facts_snippet": facts})
        )
    return out
