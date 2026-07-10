"""Discovery: fetch places via a provider and rank them, with adaptive radius.

Adaptive radius (business logic 1.5): if nothing new is found at the current
radius, expand step-by-step up to ``max_radius_m`` so the user isn't left in
silence.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import settings
from app.services.agent.walklog import get_logger
from app.shared.schemas import Candidate, GeoPoint, Heading

from .inventory import InventoryStore
from .providers import PlaceProvider
from .ranking import build_candidates

log = get_logger()


@dataclass
class DiscoveryResult:
    candidates: list[Candidate]
    radius_m: float
    expanded: bool  # radius was grown beyond the requested one
    exhausted: bool  # hit max_radius with still nothing


class Discovery:
    def __init__(
        self,
        provider: PlaceProvider,
        max_radius_m: float | None = None,
        # Bigger jumps => fewer sequential Overpass calls when an area is sparse
        # (80 -> 200 -> 500 = 3 calls, vs. 5 at 1.6). Faster to find or give up.
        expand_factor: float = 2.5,
    ) -> None:
        self.provider = provider
        self.max_radius_m = max_radius_m or settings.max_radius_m
        self.expand_factor = expand_factor
        # Per-session inventory cache (Overpass off the hot-path). Held here so it
        # survives reconnects (one Discovery per orchestrator/process) yet stays
        # isolated between tests (each builds its own orchestrator).
        self.inventory = InventoryStore()

    async def discover(
        self,
        position: GeoPoint,
        heading: Heading,
        seen: list[str],
        radius_m: float,
    ) -> list[Candidate]:
        places = await self.provider.fetch_places(position, radius_m)
        return build_candidates(position, heading, places, radius_m, seen)

    async def discover_adaptive(
        self,
        position: GeoPoint,
        heading: Heading,
        seen: list[str],
        radius_m: float,
    ) -> DiscoveryResult:
        # One tight query (cheap, fast — covers dense city centres). If it's empty,
        # jump STRAIGHT to the max radius in a single wide query instead of stepping
        # 80 -> 200 -> 500: each step is a slow heavy Overpass call, and three of them
        # in a sparse suburb blew the per-tick deadline, so objects never surfaced
        # (the "talks about the district but never reaches any object" bug). At most
        # two queries now; proximity is handled by ranking, not by re-querying.
        candidates = await self.discover(position, heading, seen, radius_m)
        if candidates:
            return DiscoveryResult(candidates, radius_m, expanded=False, exhausted=False)
        if radius_m >= self.max_radius_m:
            log.info("adaptive: 0 at r=%.0f (already max) -> exhausted", radius_m)
            return DiscoveryResult([], radius_m, expanded=False, exhausted=True)
        wide = self.max_radius_m
        candidates = await self.discover(position, heading, seen, wide)
        log.info("adaptive: 0 at r=%.0f -> expand to %.0f -> %d found",
                 radius_m, wide, len(candidates))
        return DiscoveryResult(candidates, wide, expanded=True, exhausted=not candidates)

    async def discover_inventory(
        self,
        session_id: str,
        position: GeoPoint,
        heading: Heading,
        seen: list[str],
    ) -> DiscoveryResult:
        """Inventory-backed discovery: serve candidates from a per-session cached
        disc instead of hitting Overpass every tick. The candidate set is still a
        tight window around the LIVE position (so the orchestrator's fingerprint
        gate keeps working); only the network call is amortised. A sparse window
        widens to the whole cached disc (no extra Overpass), mirroring the
        tight->wide shape of `discover_adaptive`."""
        inv = await self.inventory.ensure(session_id, position, self.provider)
        self.inventory.update_approach(inv, position)
        if not inv.places:
            # Cold/empty disc (Overpass failed or genuinely nothing here) — fall back
            # to the live adaptive search so the guide never goes dark.
            log.info("inventory empty -> live adaptive fallback")
            return await self.discover_adaptive(
                position, heading, seen, settings.default_radius_m
            )
        candidates = build_candidates(
            position, heading, inv.places, settings.default_radius_m, seen
        )
        if candidates:
            log.debug(
                "inventory: disc=%d cands@%.0fm=%d (of which unseen ranked)",
                len(inv.places), settings.default_radius_m, len(candidates),
            )
            return DiscoveryResult(
                candidates, settings.default_radius_m, expanded=False, exhausted=False
            )
        # Sparse window: widen to the full cached disc and prefer what's ahead
        # (drop objects already walked past), so the guide reaches the nearest real
        # object instead of looping on the area monologue.
        passed = self.inventory.passed_ids(inv)
        wide = build_candidates(
            position, heading, inv.places, settings.inventory_radius_m, seen
        )
        ahead = [c for c in wide if c.place.id not in passed]
        candidates = ahead or wide
        log.info(
            "inventory sparse: 0 within %.0fm -> widen %.0fm | disc=%d wide=%d passed=%d ahead=%d",
            settings.default_radius_m, settings.inventory_radius_m,
            len(inv.places), len(wide), len(passed), len(ahead),
        )
        return DiscoveryResult(
            candidates, settings.inventory_radius_m, expanded=True, exhausted=not candidates
        )
