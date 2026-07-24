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

from app.config import settings
from app.services.agent.languages import looks_foreign_facts, normalize, prompt_language
from app.services.agent.walklog import get_logger, tick_bump
from app.shared.schemas import Candidate, Place

from .fact_buffer import FactBatchMeta, FactBuffer

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


def _spoken_fact_text(text: str | None, language: str) -> str | None:
    """Best-effort normalization for facts that may later be SPOKEN verbatim via reserve,
    startup fallback, or direct factual floor paths. Keep it cheap and conservative: when the
    text looks wrong for the session language, drop it rather than leak a foreign-language line."""
    cleaned = (text or '').strip()
    if not cleaned:
        return None
    if looks_foreign_facts(cleaned, language):
        return None
    return cleaned


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
    an English one. ``place_id in cache`` still answers "any language cached?".

    Backed by the shared fact buffer when configured, so object facts survive process
    restarts and can seed guided continuity / startup prewarm without a fresh web call.
    """

    def __init__(self, fact_buffer: FactBuffer | None = None) -> None:
        self._cache: dict[tuple[str, str], str] = {}
        self._buffer = fact_buffer

    def get(self, place_id: str, language: str = _DEFAULT_LANG) -> str | None:
        key = (place_id, normalize(language))
        facts = self._cache.get(key)
        if facts is not None:
            return facts
        if self._buffer is not None:
            facts = self._buffer.get_place(place_id, language)
            if facts is not None:
                self._cache[key] = facts
            return facts
        return None

    def put(
        self,
        place_id: str,
        facts: str,
        language: str = _DEFAULT_LANG,
        *,
        meta: FactBatchMeta | None = None,
    ) -> None:
        key = (place_id, normalize(language))
        _bounded_set(self._cache, key, facts)
        if self._buffer is not None:
            self._buffer.put_place(place_id, facts, language, meta=meta)

    def has(self, place_id: str, language: str = _DEFAULT_LANG) -> bool:
        return self.get(place_id, language) is not None

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
    "different places. From the web-search results, give as many short, reliable facts "
    "as the sources genuinely support — aim for 4-8 (history, who/when built it, what "
    "makes it notable, curious details, people and events tied to it). ALSO "
    "include, when known, what the place is or houses TODAY — its current function, "
    "tenant or use (what a passer-by is actually looking at now), not only its past. "
    "Facts only — no filler, no opinions, no invention. If there is no reliable "
    "information about this exact place at this location, reply with exactly: NONE."
)


def _is_no_data(text: str) -> bool:
    """True if the model signalled 'no reliable facts'. Accepts the neutral NONE
    sentinel and the legacy Russian НЕТ, ignoring leading bullets/punctuation."""
    head = text.upper().lstrip("*•-. ")
    return head.startswith("NONE") or head.startswith("НЕТ")


# --- instant facts straight from OSM tags (zero network) --------------------------- #
# An OSM object often CARRIES ready, verifiable facts: the memorial's inscription, the
# build year, the architect, what it is (description). They cost nothing, are exact for
# THIS object, and land before any wiki/web round-trip — for the long tail of small
# monuments they're frequently the only real facts available at all. English labels on
# purpose: the narrator re-expresses facts in the session language anyway (see CORE).


def _osm_instant_facts(tags: dict[str, str] | None) -> str | None:
    if not tags:
        return None
    out: list[str] = []
    desc = (tags.get("description") or "").strip()
    if desc:
        out.append(f"Description (from the map data): {desc.rstrip('.')}.")
    insc = (tags.get("inscription") or "").strip()
    if insc:
        out.append(f"Inscription on the object: «{insc.rstrip('.')}».")
    start = (tags.get("start_date") or "").strip()
    if start:
        out.append(f"Built/established: {start}.")
    arch = (tags.get("architect") or "").strip()
    if arch:
        out.append(f"Architect: {arch}.")
    height = (tags.get("height") or "").strip()
    if height:
        unit = "" if any(c.isalpha() for c in height) else " m"
        out.append(f"Height: {height}{unit}.")
    hours = (tags.get("opening_hours") or "").strip()
    if hours and len(hours) <= 40:  # keep raw OSM syntax only when it's short/readable
        out.append(f"Opening hours: {hours}.")
    return " ".join(out)[:400] or None


# One shared keep-alive client for all Wikimedia calls: the enricher used to open a
# fresh TLS connection per object (prefetch warms dozens per disc) — the handshake tax
# alone was ~0.1-0.3 s × object. Lazy so it binds to the running event loop.
_WIKI_UA = (
    "AI-Audio-Guide/0.1 (https://github.com/ai-audio-guide; audioguide@example.org)"
)
_wiki_http: httpx.AsyncClient | None = None


def _wiki_client() -> httpx.AsyncClient:
    global _wiki_http
    if _wiki_http is None or _wiki_http.is_closed:
        _wiki_http = httpx.AsyncClient(
            timeout=10.0,
            # Wiki article-title lookups legitimately 302 (redirect pages), so keep
            # redirects ON but bound them — fixed Wikimedia hosts, no user URL, so
            # SSRF-via-redirect isn't a concern; the cap just stops redirect loops.
            follow_redirects=True,
            max_redirects=3,
            headers={"User-Agent": _WIKI_UA},
        )
    return _wiki_http


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
        # value: str (facts) | {"neg": ts} (TTL'd negative) | None (legacy permanent
        # negative from an old cache file — treated as expired, see facts_for)
        self._cache: dict[str, str | dict | None] = {}
        if self._path and self._path.exists():
            try:
                self._cache = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._cache = {}

    # The raw OSM category token ("memorial", "artwork") is a poor search term — a small
    # local monument only surfaces under its natural-language type. Localized (RU-first:
    # the long tail of untagged places is in the home region); anything unmapped falls
    # back to the de-underscored token.
    _TYPE_WORDS: dict[str, str] = {
        "memorial": "памятник мемориал",
        "monument": "памятник монумент",
        "statue": "скульптура памятник",
        "artwork": "арт-объект скульптура",
        "museum": "музей",
        "ruins": "руины усадьба история",
        "manor": "усадьба",
        "church": "храм церковь",
        "chapel": "часовня",
    }

    @classmethod
    def _query(cls, place: Place, context: str | None, *, broad: bool = False) -> str:
        # Always pin the location with coordinates so the model can reject a
        # same-named place elsewhere (e.g. an OSM "Eurocity" in Moscow vs Gibraltar).
        # Include BOTH the session context (city, country) and the OSM addr:city — the
        # context used to mask the tag, and a bare context misses the suburb name.
        typ = cls._TYPE_WORDS.get(place.category or "", (place.category or "").replace("_", " "))
        addr_city = place.tags.get("addr:city") or ""
        where = " ".join(p for p in (context or "", addr_city) if p and p not in (context or ""))
        where = where or context or addr_city
        coords = f"coordinates {place.location.lat:.4f}, {place.location.lon:.4f}"
        if broad:
            # Broadened retry (no parentheses/quotes, type words up front): "памятник
            # Человеку Труда Долгопрудный история" finds what the exact form missed.
            parts = [typ, place.name, where, "история"]
        else:
            parts = [place.name, f"({typ})" if typ else "", where, coords]
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
            hit = self._cache[key]
            if isinstance(hit, str):
                return hit
            # Negative entry: {"neg": ts} (new format) or the legacy bare None (treated
            # as expired so an old permanent negative heals itself on first re-touch). A
            # FRESH negative still answers None; an expired one falls through to a
            # re-search — a new object with no web presence today may gain one.
            ts = hit.get("neg", 0.0) if isinstance(hit, dict) else 0.0
            if time.time() - ts < settings.enrich_negative_ttl_s:
                return None
        facts: str | None = None
        try:
            facts = await self._search(place, context, language, broad=False)
            # One broadened retry before committing the negative cache: the exact query
            # form misses small local objects the loose form finds (the "Человеку Труда
            # -> web empty forever" case). Costs one extra search only on a first miss;
            # a double miss is cached negative (with TTL) as before.
            if facts is None and settings.enrich_retry_broaden:
                facts = await self._search(place, context, language, broad=True)
        except Exception as e:  # network/provider hiccup — degrade to no facts
            _log.warning("enrich failed for %s: %s", place.id, e)
            return None  # transient: don't cache, retry on a later tick
        _bounded_set(self._cache, key, facts if facts is not None else {"neg": time.time()})
        self._persist()
        return facts

    async def _search(
        self, place: Place, context: str | None, language: str, *, broad: bool
    ) -> str | None:
        text = await self._llm.web_facts(
            _ENRICH_SYSTEM + _lang_directive(language),
            self._query(place, context, broad=broad),
            max_results=self._max_results,
            max_tokens=self._max_tokens,
        )
        cleaned = _spoken_fact_text(text, language)
        return cleaned if cleaned and not _is_no_data(cleaned) else None

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
        """The object's photo URL if one was found during enrichment — a Wikipedia lead image,
        a Wikidata P18 photo, or a Commons/URL image tag straight off the OSM object."""
        return self._images.get(place_id)

    @staticmethod
    def _commons_thumb(filename: str, width: int = 640) -> str | None:
        """A sized Commons thumbnail URL for a `File:` name, via Special:FilePath (which
        redirects to the scaled image). None for an empty name."""
        filename = filename.strip()
        if not filename:
            return None
        q = urllib.parse.quote(filename.replace(" ", "_"), safe="")
        return f"https://commons.wikimedia.org/wiki/Special:FilePath/{q}?width={width}"

    @classmethod
    def _p18_image(cls, entity: dict) -> str | None:
        """Wikidata P18 (image) claim -> a Commons thumbnail URL, or None when unset. Covers
        the many wikidata-tagged objects that have a photo but NO Wikipedia article."""
        try:
            fname = entity["claims"]["P18"][0]["mainsnak"]["datavalue"]["value"]
        except (KeyError, IndexError, TypeError):
            return None
        return cls._commons_thumb(fname)

    @classmethod
    def _osm_tag_image(cls, tags: dict[str, str]) -> str | None:
        """A photo straight from the OSM object's own tags (free, no network): a
        `wikimedia_commons=File:…` -> Commons thumbnail, or a direct `image=https://…` URL.
        Only https for a bare URL (a mobile card can load it and the web build stays mixed-
        content-clean)."""
        commons = (tags.get("wikimedia_commons") or "").strip()
        if commons.startswith("File:"):
            return cls._commons_thumb(commons[len("File:"):])
        img = (tags.get("image") or "").strip()
        if img.startswith("https://"):
            return img
        return None

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
        # #2 OSM image tags (free, no network, exact): capture BEFORE the wiki gate, so even a
        # non-wiki object carrying image=/wikimedia_commons= gets a card photo. A Wikipedia/P18
        # lead image (below) overrides it when found (canonical); otherwise this one stands.
        osm_img = self._osm_tag_image(place.tags)
        if osm_img and place.id not in self._images:
            self._images[place.id] = osm_img
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
            # Shared keep-alive client (Wikimedia requires a descriptive User-Agent).
            client = _wiki_client()
            image: str | None = None
            if wd and wp:
                # BOTH tags: race the two lookups concurrently instead of the old
                # wd-then-wp sequence. Wikidata still wins when it lands (its sitelinks
                # pick the session-language article); the direct wp summary is the
                # instant fallback — one round-trip saved on most tagged landmarks.
                lang, _, title = wp.partition(":")
                if not title:
                    lang, title = prefer[0], wp
                wd_res, wp_res = await asyncio.gather(
                    self._from_wikidata(client, wd, prefer),
                    self._summary(client, lang, title),
                    return_exceptions=True,
                )
                if isinstance(wd_res, BaseException):
                    wd_res = (None, None)
                if isinstance(wp_res, BaseException):
                    wp_res = (None, None)
                facts = wd_res[0] or wp_res[0]
                image = wd_res[1] or wp_res[1]
            elif wd:
                facts, image = await self._from_wikidata(client, wd, prefer)
            elif wp:
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
        spoken = _spoken_fact_text(extract[: self._chars] if extract else None, lang)
        return spoken, image

    # Claim ids worth speaking about a building/monument, cheap to read from the entity
    # JSON we ALREADY fetch for sitelinks: time/quantity literals are free; entity-valued
    # claims (architect/style/heritage) cost ONE batched label lookup.
    _CLAIM_TIME = {"P571": "Built/established", "P1619": "Officially opened"}
    _CLAIM_ENTITY = {"P84": "Architect", "P149": "Architectural style",
                     "P1435": "Heritage status"}

    @staticmethod
    def _claim_values(entity: dict, prop: str) -> list[dict]:
        out = []
        for c in (entity.get("claims", {}).get(prop) or [])[:3]:
            v = ((c.get("mainsnak") or {}).get("datavalue") or {}).get("value")
            if v is not None:
                out.append(v)
        return out

    async def _claim_facts(
        self, client: httpx.AsyncClient, entity: dict, prefer: tuple[str, ...]
    ) -> str | None:
        """Synthesize facts from Wikidata CLAIMS for an entity with NO article in a
        preferred language — previously such objects yielded only a photo and the
        narrator got nothing (silence / a bare naming line). Dates/height are literal
        (zero extra network); architect/style/heritage labels come from one batched
        wbgetentities call. The entity's own description line grounds "what is this"."""
        lines: list[str] = []
        descs = entity.get("descriptions", {})
        for lang in prefer:
            d = (descs.get(lang) or {}).get("value")
            if d:
                lines.append(f"What it is: {d.rstrip('.')}.")
                break
        for prop, label in self._CLAIM_TIME.items():
            for v in self._claim_values(entity, prop):
                t = str(v.get("time", ""))  # "+1937-06-00T00:00:00Z"
                year = t.lstrip("+")[:4]
                if year.isdigit():
                    lines.append(f"{label}: {year}.")
                    break
        for v in self._claim_values(entity, "P2048"):  # height (quantity literal)
            amount = str(v.get("amount", "")).lstrip("+")
            if amount:
                lines.append(f"Height: {amount} m.")
                break
        # Entity-valued claims -> one batched label fetch (session language, then en).
        want: dict[str, str] = {}  # qid -> label prefix
        for prop, label in self._CLAIM_ENTITY.items():
            for v in self._claim_values(entity, prop):
                qid = v.get("id") if isinstance(v, dict) else None
                if qid and qid not in want:
                    want[qid] = label
                break  # first value per property is enough for speech
        if want:
            try:
                r = await client.get(
                    "https://www.wikidata.org/w/api.php",
                    params={
                        "action": "wbgetentities", "ids": "|".join(list(want)[:5]),
                        "props": "labels", "languages": f"{prefer[0]}|en",
                        "format": "json",
                    },
                )
                if r.status_code == 200:
                    ents = r.json().get("entities", {})
                    for qid, label in want.items():
                        labels = (ents.get(qid) or {}).get("labels", {})
                        name = (labels.get(prefer[0]) or labels.get("en") or {}).get("value")
                        if name:
                            lines.append(f"{label}: {name}.")
            except Exception:  # noqa: BLE001 — labels are a bonus, never fail the path
                pass
        return _spoken_fact_text(" ".join(lines), prefer[0])

    async def _from_wikidata(
        self, client: httpx.AsyncClient, qid: str, prefer: tuple[str, ...]
    ) -> tuple[str | None, str | None]:
        r = await client.get(f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json")
        if r.status_code != 200:
            return None, None
        entity = r.json().get("entities", {}).get(qid, {})
        # #1 Wikidata P18: the entity's own image, from the JSON we already fetched for sitelinks
        # (no extra request) — covers wikidata-tagged objects with a photo but no Wiki article.
        p18 = self._p18_image(entity)
        links = entity.get("sitelinks", {})
        for lang in prefer:
            sl = links.get(f"{lang}wiki")
            if sl:
                facts, image = await self._summary(client, lang, sl["title"])
                return facts, (image or p18)  # article lead image preferred, else the P18 photo
        # No article in a preferred language -> synthesize facts from the entity's own
        # claims (dates/height/architect/style) instead of returning nothing.
        facts = await self._claim_facts(client, entity, prefer)
        return facts, p18


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
        # In-flight dedup: two concurrent ticks racing the SAME cold object used to fire
        # the paid web search twice (the cache fills only after the ~15 s search lands —
        # a wide race window). The second caller now awaits the first's task.
        self._inflight: dict[str, asyncio.Task] = {}

    def image_for(self, place_id: str) -> str | None:
        return self._wiki.image_for(place_id)  # photos come from the wiki path only

    async def facts_for(
        self, place: Place, context: str | None = None, language: str = _DEFAULT_LANG
    ) -> str | None:
        key = f"{normalize(language)}:{place.id}"
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(self._facts_impl(place, context, language))
            self._inflight[key] = task
            task.add_done_callback(lambda _t, k=key: self._inflight.pop(k, None))
        # shield: a barge-in cancelling ONE caller's step must not kill the shared
        # lookup another tick (or the cache) is waiting on.
        return await asyncio.shield(task)

    async def _facts_impl(
        self, place: Place, context: str | None, language: str
    ) -> str | None:
        from app.services.metrics import GUIDE

        t0 = time.monotonic()
        # Instant facts straight off the OSM tags (inscription/build year/architect/…):
        # zero network, exact for THIS object. They stand alone for the long tail the
        # web has nothing on, and prepend to wiki/web facts otherwise.
        instant = _spoken_fact_text(_osm_instant_facts(place.tags), language)
        facts = await self._wiki.facts_for(place, context, language)
        if facts:
            GUIDE.enrich("wiki")  # free facts — the cheap, preferred path
            tick_bump("wiki")
            _wlog.info("enrich %r -> wiki%s (t=%dms)",
                       place.name, "+tags" if instant else "",
                       int((time.monotonic() - t0) * 1000))
            return f"{instant} {facts}" if instant else facts
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
            _wlog.info("enrich %r -> web %s%s (t=%dms)", place.name,
                       "facts" if web_facts else "empty",
                       "+tags" if instant else "",
                       int((time.monotonic() - t0) * 1000))
            if web_facts:
                return f"{instant} {web_facts}" if instant else web_facts
            return instant  # the tags still ground a fact-less narration
        if instant:
            GUIDE.enrich("wiki")  # free facts from the object's own tags
            tick_bump("wiki")
            _wlog.info("enrich %r -> tags-only (t=%dms)",
                       place.name, int((time.monotonic() - t0) * 1000))
            return instant
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
