"""Route planner for the proactive "guided" mode: pick a handful of interesting
places near the walker and order them into a walkable route under a time/distance
budget.

This is the piece the reactive guide never had — it decides a *sequence of stops*
ahead of the walk instead of reacting to whatever is nearest each tick. It reuses
the existing "interestingness" machinery wholesale:
  * categories.weight_for      — a-priori interest of an object's category
  * significance_from_weight   — SKIP..LANDMARK ladder (the stop-worthiness floor)
  * ranking.Dedup              — drop the same real-world entity mapped twice
and orders stops with a pragmatic greedy prize-collecting insertion over a real
walking-distance matrix (RoutingProvider.table) — not a full optimum, which the
walk doesn't need.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config import settings
from app.services.agent.significance import at_least, significance_from_weight, tags_have_wiki
from app.shared.geo_math import bearing_deg, haversine_m, offset_point
from app.shared.schemas import GeoPoint, Place, Significance

from .categories import weight_for
from .providers import PlaceProvider
from .ranking import Dedup, _norm_name
from .routing import RouteLeg, RoutingProvider, StraightLineRouting

_EPS = 1e-6


@dataclass
class RouteStop:
    place: Place
    order: int
    significance: Significance
    interest: float
    cum_distance_m: float  # walking distance from origin to this stop along the route


@dataclass
class PlannedRoute:
    mode: str  # loop | destination
    origin: GeoPoint
    destination: GeoPoint | None
    stops: list[RouteStop] = field(default_factory=list)
    polyline: list[list[float]] = field(default_factory=list)  # [[lat, lon], ...]
    total_distance_m: float = 0.0
    total_duration_s: float = 0.0

    @property
    def enough(self) -> bool:
        return len(self.stops) >= settings.route_min_stops


def _midpoint(a: GeoPoint, b: GeoPoint) -> GeoPoint:
    return offset_point(a, bearing_deg(a, b), haversine_m(a, b) / 2.0)


def _interest(weight: float, has_wiki: bool) -> float:
    """A-priori draw of a place for the route: category weight, boosted when a
    Wikipedia/Wikidata link promises real facts to tell there."""
    return weight * (1.0 + 0.5 * has_wiki)


class RoutePlanner:
    def __init__(self, routing: RoutingProvider, provider: PlaceProvider) -> None:
        self._routing = routing
        self._provider = provider
        self._straight = StraightLineRouting()

    # -- POI fetch with a safety net (a slow/blocked Overpass must not crash planning) -- #
    async def _safe_fetch(self, center: GeoPoint, radius_m: float) -> list[Place]:
        try:
            return await self._provider.fetch_places(center, radius_m)
        except Exception:  # noqa: BLE001 — timeout/HTTP error -> no candidates here, not a crash
            return []

    # -- routing with a straight-line safety net (OSRM may be down / geo-blocked) -- #
    async def _safe_route(self, points: list[GeoPoint]) -> RouteLeg:
        try:
            return await self._routing.route(points)
        except Exception:  # noqa: BLE001 — any OSRM failure -> crow-flies, never silence
            return await self._straight.route(points)

    async def _safe_table(self, points: list[GeoPoint]) -> list[list[float]]:
        try:
            return (await self._routing.table(points)).distances_m
        except Exception:  # noqa: BLE001
            return (await self._straight.table(points)).distances_m

    async def build(
        self,
        origin: GeoPoint,
        *,
        mode: str,
        budget_m: float | None = None,
        budget_min: float | None = None,
        destination: GeoPoint | None = None,
        pick_landmark: bool = False,
        seen: list[str] | None = None,
        dedup: Dedup | None = None,
        language: str = "ru",
    ) -> PlannedRoute:
        budget = self._effective_budget_m(mode, budget_m, budget_min)
        pool = await self._candidate_pool(origin, mode, destination, budget, seen, dedup)

        # destination mode without an explicit point => let the guide pick the top landmark.
        if mode == "destination" and destination is None:
            pick_landmark = True
        end_item: _PoolItem | None = None
        if pick_landmark and pool:
            end_item = max(pool, key=lambda pc: pc.interest)
            pool = [pc for pc in pool if pc.place.id != end_item.place.id]
            destination = end_item.place.location

        end_place = end_item.place if end_item is not None else None
        return await self._materialize(origin, mode, destination, end_place, pool, budget)

    # ------------------------------------------------------------------ helpers -- #
    def _effective_budget_m(
        self, mode: str, budget_m: float | None, budget_min: float | None
    ) -> float:
        if budget_m and budget_m > 0:
            return budget_m
        if budget_min and budget_min > 0:
            return budget_min * 60.0 * settings.walk_speed_mps
        # No budget given: a loop still needs one (default ~30 min); a destination walk is
        # bounded by its endpoint + max_stops, so leave it effectively unbounded.
        if mode == "loop":
            return 30.0 * 60.0 * settings.walk_speed_mps
        return float("inf")

    async def _candidate_pool(
        self,
        origin: GeoPoint,
        mode: str,
        destination: GeoPoint | None,
        budget_m: float,
        seen: list[str] | None,
        dedup: Dedup | None,
    ) -> list[_PoolItem]:
        # Fetch a disc wide enough for the walk. A loop of perimeter L lives inside a circle
        # of radius ~L/(2π); we use L/4 for headroom. A destination walk covers the corridor
        # between origin and endpoint — sampled as several discs along the line when it's long,
        # so we don't inflate one disc (and miss the middle) for a far endpoint.
        if mode == "destination" and destination is not None:
            places = await self._corridor_places(origin, destination)
        else:
            budget_ref = budget_m if budget_m != float("inf") else settings.route_max_fetch_m * 4
            radius = max(
                settings.inventory_radius_m, min(budget_ref / 4.0, settings.route_max_fetch_m)
            )
            places = await self._safe_fetch(origin, radius)
        seen_set = set(seen or [])
        min_sig = Significance(settings.route_min_significance)

        pool: list[_PoolItem] = []
        dup_wiki: set[str] = set()
        dup_named: set[str] = set()
        for p in places:
            if p.id in seen_set:
                continue
            if dedup is not None and dedup.blocks(p):
                continue
            has_wiki = tags_have_wiki(p.tags)
            sig = significance_from_weight(
                weight_for(p.category), facts_available=has_wiki, has_wiki=has_wiki
            )
            if not at_least(sig, min_sig):
                continue
            # Intra-pool dedup: the same landmark can be mapped as several OSM objects.
            qid = (p.tags or {}).get("wikidata")
            if qid and qid in dup_wiki:
                continue
            nm = _norm_name(p.name)
            if nm and nm in dup_named:
                continue
            if qid:
                dup_wiki.add(qid)
            if nm:
                dup_named.add(nm)
            pool.append(
                _PoolItem(
                    place=p,
                    significance=sig,
                    interest=_interest(weight_for(p.category), has_wiki),
                )
            )

        # Cap the table request: keep the most interesting when the pool is large.
        cap = settings.routing_table_max_points - 2  # reserve origin (+ destination)
        if len(pool) > cap:
            pool.sort(key=lambda pc: pc.interest, reverse=True)
            pool = pool[:cap]
        return pool

    async def _corridor_places(self, origin: GeoPoint, destination: GeoPoint) -> list[Place]:
        """POIs along the origin->destination corridor. One disc around the midpoint for a
        short hop; several overlapping discs strung along the line for a long one (so the
        middle of the corridor is covered, not just the two ends). Deduped by place id."""
        span = haversine_m(origin, destination)
        pad = settings.route_corridor_pad_m
        disc_r = min(settings.inventory_radius_m, settings.route_max_fetch_m)
        if span / 2.0 + pad <= disc_r:  # one mid disc already reaches both ends
            return await self._safe_fetch(_midpoint(origin, destination), disc_r)
        brg = bearing_deg(origin, destination)
        step = disc_r * 1.5  # overlap consecutive discs
        n = int(span // step) + 1
        by_id: dict[str, Place] = {}
        for i in range(n + 1):
            centre = offset_point(origin, brg, min(i * step, span))
            for p in await self._safe_fetch(centre, disc_r):
                by_id[p.id] = p
        return list(by_id.values())

    async def _materialize(
        self,
        origin: GeoPoint,
        mode: str,
        destination: GeoPoint | None,
        end_place: Place | None,
        pool: list[_PoolItem],
        budget_m: float,
    ) -> PlannedRoute:
        route = PlannedRoute(mode=mode, origin=origin, destination=destination)
        if not pool and end_place is None:
            return route

        # Node layout for the matrix: 0=origin, 1..K=pool, then optional destination last.
        nodes: list[GeoPoint] = [origin] + [pc.place.location for pc in pool]
        has_end = destination is not None
        dest_idx = len(nodes) if has_end else -1
        if has_end:
            nodes.append(destination)  # type: ignore[arg-type]

        M = await self._safe_table(nodes)
        order_idx = _order_with_matrix(M, len(pool), dest_idx, pool, budget_m)

        # Build the visit sequence of GeoPoints and the stop list.
        seq_nodes = [0] + order_idx + ([dest_idx] if has_end else [0])
        seq_points = [nodes[i] for i in seq_nodes]
        leg = await self._safe_route(seq_points)
        route.polyline = leg.polyline
        route.total_distance_m = leg.distance_m
        route.total_duration_s = leg.duration_s

        cum = 0.0
        order = 0
        for pos, node_i in enumerate(seq_nodes[1:], start=1):
            cum += M[seq_nodes[pos - 1]][node_i]
            item: _PoolItem | None = None
            if node_i == dest_idx and end_place is not None:
                item = _PoolItem(
                    place=end_place,
                    significance=significance_from_weight(
                        weight_for(end_place.category),
                        facts_available=tags_have_wiki(end_place.tags),
                        has_wiki=tags_have_wiki(end_place.tags),
                    ),
                    interest=0.0,
                )
            elif 1 <= node_i <= len(pool):
                item = pool[node_i - 1]
            if item is not None:
                route.stops.append(
                    RouteStop(
                        place=item.place,
                        order=order,
                        significance=item.significance,
                        interest=item.interest,
                        cum_distance_m=cum,
                    )
                )
                order += 1
        return route


@dataclass
class _PoolItem:
    place: Place
    significance: Significance
    interest: float


def _order_with_matrix(
    M: list[list[float]],
    k: int,
    dest_idx: int,
    pool: list[_PoolItem],
    budget_m: float,
) -> list[int]:
    """Greedy insertion on the distance matrix. Node indices: 0=origin, 1..k=pool,
    dest_idx=destination (or -1 for a loop, which returns to 0). Returns the chosen
    pool node indices (1..k) in visit order."""
    end = dest_idx if dest_idx >= 0 else 0  # loop closes back on origin
    route: list[int] = [0, end]
    total = M[0][end]
    placed: set[int] = set()
    max_stops = settings.route_max_stops

    while len(placed) < max_stops and len(placed) < k:
        best: tuple[float, int, int, float] | None = None  # (ratio, cand_node, pos, cost)
        for ci in range(1, k + 1):
            if ci in placed:
                continue
            # cheapest edge to splice this candidate into
            best_cost = float("inf")
            best_pos = 1
            for pos in range(len(route) - 1):
                a, b = route[pos], route[pos + 1]
                cost = M[a][ci] + M[ci][b] - M[a][b]
                if cost < best_cost:
                    best_cost, best_pos = cost, pos + 1
            if total + best_cost > budget_m:
                continue
            ratio = pool[ci - 1].interest / max(best_cost, _EPS)
            if best is None or ratio > best[0]:
                best = (ratio, ci, best_pos, best_cost)
        if best is None:
            break
        _, ci, pos, cost = best
        route.insert(pos, ci)
        total += cost
        placed.add(ci)

    return [i for i in route if 1 <= i <= k]
