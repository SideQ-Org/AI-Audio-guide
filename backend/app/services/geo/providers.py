"""Place providers: a real Overpass client and a static one for tests/sim.

Both satisfy the ``PlaceProvider`` protocol so the discovery layer is agnostic
to the source (live API vs. cached fixtures vs. virtual walk).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Protocol, runtime_checkable

import httpx

from app.config import settings
from app.services.metrics import GUIDE
from app.shared.geo_math import haversine_m
from app.shared.schemas import GeoPoint, Place

from .categories import KEEP_TAGS, classify


@runtime_checkable
class PlaceProvider(Protocol):
    async def fetch_places(self, center: GeoPoint, radius_m: float) -> list[Place]: ...


# --------------------------------------------------------------------------- #
# Overpass (live)
# --------------------------------------------------------------------------- #
_SELECTORS = (
    # culture & sightseeing: every tourism feature (museum, gallery, artwork,
    # attraction, viewpoint, zoo, aquarium, theme_park, ...) and everything historic
    # (monument, memorial, castle, ruins, city walls/gate, aqueduct, ...).
    '"tourism"',
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
    # parks, gardens, civic green & sports venues (incl. pitches/tracks/grounds — a
    # neighbourhood football field is usually leisure=pitch, not =stadium; it's often
    # unnamed, so _element_to_place synthesizes a name for the notable sports below)
    '"leisure"~"park|garden|nature_reserve|common|marina|stadium|sports_centre|pitch|track|sports_hall|recreation_ground"',
    # nature & water — reservoirs, rivers, lakes, forests, hills, caves, rock features
    '"natural"~"water|wood|peak|hill|ridge|bay|beach|cape|cliff|cave_entrance|arch|rock|spring|geyser|waterfall|volcano|glacier|wetland"',
    '"water"',
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
    body = "".join(
        f"{kind}(around:{r},{center.lat},{center.lon})[{sel}];"
        for sel in _SELECTORS
        for kind in ("node", "way")
    )
    # "geom" (not "center") so linear/area features (rivers, canals, bays) report
    # their geometry — we then snap to the point nearest the user, not the way's
    # midpoint, which for a long canal sits kilometres away.
    return f"[out:json][timeout:15];({body});out tags geom;"


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
    if not name:
        return None
    geometry: list[list[float]] | None = None
    if el.get("type") == "node":
        lat, lon = el.get("lat"), el.get("lon")
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


async def fetch_overpass_elements(query: str, *, per_timeout: float = 8.0) -> list[dict]:
    """POST a query to each mirror in turn; first JSON-200 wins. A slow/blocked
    mirror fails over fast (per_timeout) instead of stalling the whole tick."""
    last_exc: Exception | None = None
    async with httpx.AsyncClient(
        timeout=per_timeout,
        headers={"User-Agent": _OVERPASS_UA},
        follow_redirects=False,  # Overpass endpoints don't redirect; refuse any (SSRF floor)
    ) as client:
        for url in overpass_mirrors():
            try:
                resp = await client.post(url, data={"data": query})
                resp.raise_for_status()
                elements = resp.json().get("elements", [])
                GUIDE.overpass(True)  # a mirror answered — the guide won't go dark
                return elements
            except Exception as e:  # noqa: BLE001 — timeout/non-200/non-JSON -> next mirror
                last_exc = e
                continue
    if last_exc is not None:
        GUIDE.overpass(False, f"{type(last_exc).__name__}: {last_exc}")
        raise last_exc
    return []


# Short-lived cache of Overpass results, keyed by (rounded position, radius). A
# walking user re-queries almost the same circle every tick; without this the heavy
# multi-selector query (and its 1.5-8s latency) runs on every tick and every
# adaptive-radius step. 4-decimal rounding ≈ 11 m, so we reuse within a step or two.
_OVERPASS_CACHE: dict[tuple[float, float, int], tuple[float, list[Place]]] = {}
_OVERPASS_CACHE_TTL_S = 90.0
_OVERPASS_CACHE_MAX = 512


class OverpassProvider:
    def __init__(self, url: str | None = None) -> None:
        self.url = url or settings.overpass_url

    async def fetch_places(self, center: GeoPoint, radius_m: float) -> list[Place]:
        key = (round(center.lat, 4), round(center.lon, 4), int(radius_m))
        now = time.monotonic()
        hit = _OVERPASS_CACHE.get(key)
        if hit is not None and now - hit[0] < _OVERPASS_CACHE_TTL_S:
            return hit[1]
        query = build_query(center, radius_m)
        # Multi-mirror with fast failover (see fetch_overpass_elements): a slow/down
        # endpoint can't stack multi-minute stalls across adaptive-radius expansions.
        elements = await fetch_overpass_elements(query)
        places = parse_elements(elements, center)
        if len(_OVERPASS_CACHE) >= _OVERPASS_CACHE_MAX:
            _OVERPASS_CACHE.pop(next(iter(_OVERPASS_CACHE)), None)  # FIFO trim
        _OVERPASS_CACHE[key] = (now, places)
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
