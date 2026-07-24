"""Place providers: a real Overpass client and a static one for tests/sim.

Both satisfy the ``PlaceProvider`` protocol so the discovery layer is agnostic
to the source (live API vs. cached fixtures vs. virtual walk).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Protocol, runtime_checkable

import httpx

from app.config import settings
from app.services.metrics import GUIDE
from app.shared.geo_math import haversine_m
from app.shared.schemas import GeoPoint, Place

from .categories import KEEP_TAGS, classify, is_junk


@runtime_checkable
class PlaceProvider(Protocol):
    async def fetch_places(self, center: GeoPoint, radius_m: float) -> list[Place]: ...


# --------------------------------------------------------------------------- #
# Overpass (live)
# --------------------------------------------------------------------------- #
_SELECTORS = (
    # culture & sightseeing: POSITIVE tourism list (museum, gallery, artwork,
    # attraction, viewpoint, ...) — a bare "tourism" also dragged in hotels, hostels,
    # apartments, camp sites and information boards: pure low-weight noise that bloated
    # every response and survived as weight-0.2 filler. Everything historic stays broad
    # (monument, memorial, castle, ruins, city walls/gate, aqueduct, ...).
    '"tourism"~"museum|gallery|artwork|attraction|viewpoint|zoo|aquarium|theme_park"',
    '"historic"',
    # worship + civic / cultural / education / health institutions — the landmark
    # buildings people SEE and expect a word about (schools/hospitals were missing).
    '"amenity"="place_of_worship"',
    '"amenity"~"theatre|cinema|arts_centre|concert_hall|fountain|university|college|library|marketplace|townhall|courthouse|monastery|exhibition_centre|school|kindergarten|hospital|clinic|community_centre"',
    '"amenity"="grave_yard"',
    # clubs (biker/social/sport club-houses) and heritage-listed / protected sites
    '"club"',
    '"heritage"',
    # standalone ruins tagged only ruins=* (stable/church ruins without a historic= key)
    '"ruins"',
    # named squares & pedestrian promenades — the spine of a city walk
    '"place"="square"',
    '"highway"="pedestrian"',
    # major roads + named interchanges (МКАД, крупные шоссе, развязки) — ONLY motorway/
    # trunk + motorway_junction, so ordinary streets never come in; named/ref-only (see
    # _element_to_place), deduped by name (LINEAR). Narrated as a SECONDARY fallback when
    # you come near one — you can't walk it, but a famous highway is worth a word.
    '"highway"~"motorway|trunk"',
    '"highway"="motorway_junction"',
    # parks, gardens, civic green & sports venues (incl. pitches/tracks/grounds — a
    # neighbourhood football field is usually leisure=pitch, not =stadium; it's often
    # unnamed, so _element_to_place synthesizes a name for the notable sports below)
    '"leisure"~"park|garden|nature_reserve|common|marina|stadium|sports_centre|pitch|track|sports_hall|recreation_ground"',
    # nature & water — reservoirs, rivers, lakes, forests, hills, caves, rock features.
    # (a bare `"water"` selector was dropped: it triple-duplicated natural~water +
    # waterway and matched every water=* fragment — extra scan, zero new objects)
    '"natural"~"water|wood|peak|hill|ridge|bay|beach|cape|cliff|cave_entrance|arch|rock|spring|geyser|waterfall|volcano|glacier|wetland"',
    '"waterway"~"river|canal|waterfall|dam|lock|weir"',
    '"landuse"~"reservoir|forest|orchard|vineyard|allotments|cemetery"',
    # notable man-made structures
    '"man_made"~"bridge|tower|lighthouse|watermill|windmill|pier|obelisk|aqueduct|water_tower|city_gate|gasometer|telescope"',
    # landmark buildings that carry no other interesting tag (cathedral, palace,
    # manor/estate house, old stables/barn, school...)
    '"building"~"cathedral|church|chapel|temple|mosque|synagogue|monastery|palace|castle|fort|government|townhall|train_station|stadium|university|library|theatre|museum|tower|triumphal_arch|gatehouse|windmill|manor|farmhouse|barn|stable|ruins|school"',
)


def build_query(center: GeoPoint, radius_m: float) -> str:
    r = int(radius_m)
    nw = "".join(
        f"{kind}(around:{r},{center.lat},{center.lon})[{sel}];"
        for sel in _SELECTORS
        for kind in ("node", "way")
    )
    # `rel` too: big parks/forests/water are often mapped as MULTIPOLYGON relations
    # (e.g. "Городской парк Мысово" is a leisure=park relation, not a way) — without this
    # they were never fetched and a walk along their edge never surfaced them at all. All
    # selectors are feature tags (leisure/natural/…), never admin boundaries, so a relation
    # query can't pull in a whole city/district outline.
    rel = "".join(
        f"rel(around:{r},{center.lat},{center.lon})[{sel}];" for sel in _SELECTORS
    )
    # Two output statements on purpose: nodes+ways get "geom" so a line/area feature reports
    # its shape (we then snap to the point nearest the user, not a far midpoint). Relations
    # get "center" (centroid) — the prod Overpass mirror won't combine geom+center on one
    # `out` (it drops the geometry) and returns no member geometry for a relation, so the
    # centroid is the robust cross-mirror point. _element_to_place still prefers a relation's
    # member geometry (nearest edge) when a mirror DOES expand it, falling back to this center.
    # [timeout:12] matches the client-side per-mirror timeout (fetch_overpass_elements):
    # with 15 the server kept computing after the client had already failed over.
    return (
        f"[out:json][timeout:12];"
        f"({nw})->.nw;({rel})->.r;"
        f".nw out tags geom;.r out tags center;"
    )


def _nearest(origin: GeoPoint, geometry: list[dict]) -> tuple[float, float] | None:
    best: tuple[float, float] | None = None
    best_d = float("inf")
    for pt in geometry:
        la, lo = pt.get("lat"), pt.get("lon")
        if la is None or lo is None:
            continue
        d = haversine_m(origin, GeoPoint(lat=la, lon=lo))
        if d < best_d:
            best_d, best = d, (la, lo)
    return best


_GEOM_MAX_PTS = 32  # cap the stored outline so a big polygon can't bloat session state


def _downsample_geometry(geometry: list[dict]) -> list[list[float]] | None:
    """Way geometry -> a bounded [[lat, lon], ...] outline for live distance-to-shape.
    None for < 2 points. Strides evenly for big shapes, keeping the last (closing) vertex
    so a ring stays closed."""
    pts = [
        [g["lat"], g["lon"]]
        for g in geometry
        if g.get("lat") is not None and g.get("lon") is not None
    ]
    if len(pts) < 2:
        return None
    if len(pts) <= _GEOM_MAX_PTS:
        return pts
    step = len(pts) / _GEOM_MAX_PTS
    out = [pts[int(i * step)] for i in range(_GEOM_MAX_PTS)]
    if out[-1] != pts[-1]:
        out.append(pts[-1])
    return out


def _pick_name(tags: dict) -> str | None:
    """The object's name, with a localized-tag fallback: an object tagged only
    `name:ru`/`name:en`/`int_name` (common in RU data) used to be dropped as nameless."""
    for k in ("name", "int_name", "name:ru", "name:en"):
        v = tags.get(k)
        if v:
            return v
    for k, v in tags.items():  # any other name:<lang>
        if k.startswith("name:") and v:
            return v
    return None


# Notable-when-unnamed sports features: a curated map from OSM `sport=` to a generic
# Russian name, so an unnamed football field (leisure=pitch sport=soccer) isn't dropped.
# Kept tight on purpose — table_tennis / volleyball / basketball-in-a-yard etc. are NOT
# here, so they stay nameless and get filtered out (no flooding the tour with tiny courts).
_SPORT_RU = {
    "soccer": "футбольное поле",
    "football": "футбольное поле",
    "athletics": "легкоатлетический стадион",
    "ice_hockey": "хоккейная коробка",
    "tennis": "теннисный корт",
}


def _synth_name(tags: dict) -> str | None:
    """A generic name for a notable UNNAMED feature, so it survives instead of being
    dropped. Only for a stadium or a curated sport ground — everything else returns None
    (and is filtered out)."""
    if tags.get("leisure") == "stadium":
        return "стадион"
    if tags.get("leisure") in ("pitch", "track", "sports_hall", "recreation_ground"):
        sport = (tags.get("sport") or "").split(";")[0].strip()
        return _SPORT_RU.get(sport)
    return None


def _element_to_place(el: dict, origin: GeoPoint) -> Place | None:
    tags = el.get("tags") or {}
    name = _pick_name(tags) or _synth_name(tags)
    # A numbered highway with no name still identifies by its route ref (e.g. an
    # unnamed trunk with ref="М-10"). ONLY for the road classes we fetch — keeps
    # ref-fallback from resurrecting other unnamed junk.
    if not name and tags.get("highway") in {"motorway", "trunk"} and tags.get("ref"):
        name = tags["ref"].split(";")[0].strip()
    if not name:
        return None
    # Drop private service/commerce (clinics/dentists/kindergartens/…) before it ever becomes a
    # Place — removes it from the map, narration and routes in one spot (see categories.is_junk).
    if settings.filter_junk_objects and is_junk(tags):
        return None
    geometry: list[list[float]] | None = None
    if el.get("type") == "node":
        lat, lon = el.get("lat"), el.get("lon")
    else:
        if el.get("type") == "relation":
            # A multipolygon relation carries geometry on its MEMBER ways (out geom), not a
            # flat top-level array — flatten every member's points so a big park's outline
            # feeds the same nearest-edge snap + live distance-to-shape as a way.
            raw_geom = [
                pt
                for m in (el.get("members") or [])
                for pt in (m.get("geometry") or [])
            ]
        else:
            raw_geom = el.get("geometry") or []
        geometry = _downsample_geometry(raw_geom)  # full-shape distance from live pos (B1)
        near = _nearest(origin, raw_geom)
        if near is not None:
            lat, lon = near
        else:
            c = el.get("center") or el.get("bounds") or {}
            lat, lon = c.get("lat"), c.get("lon")
    if lat is None or lon is None:
        return None
    category, _ = classify(tags)
    kept = {k: v for k, v in tags.items() if k in KEEP_TAGS}
    return Place(
        id=f'{el.get("type")}/{el.get("id")}',
        name=name,
        category=category,
        location=GeoPoint(lat=lat, lon=lon),
        geometry=geometry,
        tags=kept,
    )


def parse_elements(elements: list[dict], origin: GeoPoint) -> list[Place]:
    places: list[Place] = []
    seen_ids: set[str] = set()
    for el in elements:
        place = _element_to_place(el, origin)
        if place and place.id not in seen_ids:
            seen_ids.add(place.id)
            places.append(place)
    return places


# Public Overpass fallbacks, in preference order. A single endpoint is a single
# point of failure: when the configured mirror is slow or down, BOTH discovery and
# (Overpass-based) geocoding stall and the guide goes completely silent — the
# "вообще всё пропало" outage. We try mirrors in turn and the first JSON-200 wins,
# so one degraded endpoint fails over in seconds instead of stalling the tick.
# (Curated for real global coverage + reachability: overpass.osm.ch is fast but
# Switzerland-only — it returns empty for the rest of the planet — so it's omitted.)
_FALLBACK_OVERPASS_MIRRORS = (
    "https://z.overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
)
# Some mirrors 406/403 a bare/default User-Agent — keep this meaningful.
_OVERPASS_UA = "AI-Audio-Guide/1.0 (real-time walking audio guide; POI discovery)"


def overpass_mirrors() -> list[str]:
    """Endpoint priority: the configured primary (`overpass_url`, e.g. a paid/self-hosted
    box for prod), then any operator-listed backups (`overpass_mirrors`), then the built-in
    public fallbacks. Deduped, order-preserving."""
    out: list[str] = []
    if settings.overpass_url:
        out.append(settings.overpass_url)
    for m in settings.overpass_mirrors.split(","):
        m = m.strip()
        if m and m not in out:
            out.append(m)
    for m in _FALLBACK_OVERPASS_MIRRORS:
        if m not in out:
            out.append(m)
    return out


# --- L2 disk cache for Overpass responses (optional, `overpass_disk_cache` dir) ------
# Survives process restarts (prod restarts wiped every warm disc — the next walk paid
# the full cold 5-12 s again) and is shared across sessions. Keyed by query hash; OSM
# data changes on the scale of days, so a TTL of hours-to-a-day is safe.


def _disk_cache_path(query: str) -> Path | None:
    root = settings.overpass_disk_cache
    if not root:
        return None
    return Path(root) / (hashlib.sha1(query.encode("utf-8")).hexdigest() + ".json")


def _disk_cache_read(query: str) -> list[dict] | None:
    p = _disk_cache_path(query)
    if p is None:
        return None
    try:
        if not p.exists():
            return None
        if time.time() - p.stat().st_mtime > settings.overpass_disk_ttl_s:
            return None
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _disk_cache_write(query: str, elements: list[dict]) -> None:
    p = _disk_cache_path(query)
    if p is None:
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(elements, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)  # atomic — a killed process can't leave a torn cache file
        # Occasional prune: keep the directory bounded (oldest-by-mtime out first).
        files = list(p.parent.glob("*.json"))
        if len(files) > 2048:
            for old in sorted(files, key=lambda f: f.stat().st_mtime)[:256]:
                old.unlink(missing_ok=True)
    except OSError:
        pass  # cache is best-effort — never break a fetch over disk trouble


async def _fetch_one(client: httpx.AsyncClient, url: str, query: str) -> list[dict]:
    resp = await client.post(url, data={"data": query})
    resp.raise_for_status()
    return resp.json().get("elements", [])


async def fetch_overpass_elements(query: str, *, per_timeout: float = 12.0) -> list[dict]:
    """Fetch a query's elements: L2 disk cache first, then a RACE of the first
    `overpass_race` mirrors (staggered by `overpass_race_stagger_s` so a fast healthy
    primary usually wins alone), then the remaining mirrors sequentially. The measured
    reality this serves: the primary's server-side execution is ~5 s on a dense disc
    and mirrors fail unpredictably — a race turns p90 (slow/failed primary + failover)
    into min(alive mirrors), and the stagger keeps the extra public load near zero
    when the primary answers fast."""
    cached = await asyncio.to_thread(_disk_cache_read, query)
    if cached is not None:
        GUIDE.overpass(True)
        return cached
    mirrors = overpass_mirrors()
    last_exc: Exception | None = None
    # An EMPTY 200 is only accepted as the FINAL answer: a REGION-CLIPPED self-hosted
    # primary answers instantly-but-empty outside its clip, and an empty race win there
    # would blind discovery for the whole walk. A non-empty result from ANY mirror wins;
    # if every mirror agrees the area is empty (genuinely sparse), empty it is.
    saw_empty = False
    async with httpx.AsyncClient(
        timeout=per_timeout,
        headers={"User-Agent": _OVERPASS_UA},
        follow_redirects=False,  # Overpass endpoints don't redirect; refuse any (SSRF floor)
    ) as client:
        race_n = max(1, int(settings.overpass_race))
        racers, rest = mirrors[:race_n], mirrors[race_n:]

        async def _staggered(i: int, url: str) -> list[dict]:
            if i:
                await asyncio.sleep(settings.overpass_race_stagger_s * i)
            return await _fetch_one(client, url, query)

        tasks = [
            asyncio.ensure_future(_staggered(i, u)) for i, u in enumerate(racers)
        ]
        try:
            while tasks:
                done, pending = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED
                )
                tasks = list(pending)
                winner: list[dict] | None = None
                for t in done:
                    try:
                        result = t.result()
                        if result:
                            winner = result
                        else:
                            saw_empty = True
                    except Exception as e:  # noqa: BLE001 — a lost racer, try the rest
                        last_exc = e
                if winner is not None:
                    GUIDE.overpass(True)  # a mirror answered — the guide won't go dark
                    await asyncio.to_thread(_disk_cache_write, query, winner)
                    return winner
        finally:
            for t in tasks:
                t.cancel()
        for url in rest:  # the race lost entirely — walk the remaining mirrors
            try:
                elements = await _fetch_one(client, url, query)
                if not elements:
                    saw_empty = True
                    continue
                GUIDE.overpass(True)
                await asyncio.to_thread(_disk_cache_write, query, elements)
                return elements
            except Exception as e:  # noqa: BLE001 — timeout/non-200/non-JSON -> next mirror
                last_exc = e
                continue
    if saw_empty:
        # Every reachable mirror said "nothing here" — a real (sparse) empty, not an
        # outage. Cache it too, so a genuinely empty area doesn't refetch every disc.
        GUIDE.overpass(True)
        await asyncio.to_thread(_disk_cache_write, query, [])
        return []
    if last_exc is not None:
        GUIDE.overpass(False, f"{type(last_exc).__name__}: {last_exc}")
        raise last_exc
    return []


# Short-lived cache of RAW Overpass elements, keyed by (snapped position, radius). A
# walking user re-queries almost the same circle every tick; without this the heavy
# multi-selector query (and its 1.5-8s latency) runs on every tick and every
# adaptive-radius step. Raw elements (not Places) so a hit re-parses against the
# caller's TRUE center — nearest-edge anchor points stay correct as the walker moves.
_OVERPASS_CACHE: dict[tuple[float, float, int], tuple[float, list[dict]]] = {}
_OVERPASS_CACHE_TTL_S = 90.0
_OVERPASS_CACHE_MAX = 512


class OverpassProvider:
    def __init__(self, url: str | None = None) -> None:
        self.url = url or settings.overpass_url

    async def fetch_places(self, center: GeoPoint, radius_m: float) -> list[Place]:
        # SNAP the query center to a ~110/55 m grid: the disc is hundreds of metres wide,
        # so a ≤70 m shift is immaterial — but it makes the query text (and thus the L1
        # memory cache AND the L2 disk cache) reusable across ticks, sessions and whole
        # repeat walks, instead of a fresh cache key per GPS wobble. Parsing still snaps
        # anchor points against the TRUE center.
        qcenter = GeoPoint(lat=round(center.lat, 3), lon=round(center.lon, 3))
        key = (qcenter.lat, qcenter.lon, int(radius_m))
        now = time.monotonic()
        hit = _OVERPASS_CACHE.get(key)
        if hit is not None and now - hit[0] < _OVERPASS_CACHE_TTL_S:
            return parse_elements(hit[1], center)
        query = build_query(qcenter, radius_m)
        # Multi-mirror race with failover (see fetch_overpass_elements): a slow/down
        # endpoint can't stack multi-minute stalls across adaptive-radius expansions.
        elements = await fetch_overpass_elements(query)
        places = parse_elements(elements, center)
        if len(_OVERPASS_CACHE) >= _OVERPASS_CACHE_MAX:
            _OVERPASS_CACHE.pop(next(iter(_OVERPASS_CACHE)), None)  # FIFO trim
        _OVERPASS_CACHE[key] = (now, elements)
        return places


# --------------------------------------------------------------------------- #
# Static (fixtures / virtual walk)
# --------------------------------------------------------------------------- #
class StaticPlaceProvider:
    """Returns a fixed set of places, regardless of radius (radius filtering
    happens downstream in ranking)."""

    def __init__(self, places: list[Place]) -> None:
        self._places = places

    async def fetch_places(self, center: GeoPoint, radius_m: float) -> list[Place]:
        return list(self._places)

    @classmethod
    def from_json(cls, path: str | Path) -> StaticPlaceProvider:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        places = [
            Place(
                id=item.get("id") or f'{item["category"]}/{i}',
                name=item["name"],
                category=item["category"],
                location=GeoPoint(lat=item["lat"], lon=item["lon"]),
                tags=item.get("tags", {}),
            )
            for i, item in enumerate(raw)
        ]
        return cls(places)
