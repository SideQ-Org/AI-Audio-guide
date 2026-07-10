"""Per-session object inventory: fetch a wide disc of places ONCE and reuse it on
every tick, so Overpass leaves the narration hot-path.

Why this exists: a single Overpass query per tick is slow (1.5-8 s) and a single
point of failure — when the endpoint stalled, the whole guide went silent (the
"вообще всё пропало" outage). By caching a ~800 m disc per session and re-fetching
only when the user walks past half its radius, almost every tick is served from
memory; ranking against the live position is microseconds.

It also remembers each object's distance over time (closest-approach), so the
guide can tell "coming up" from "already walked past" and prefer what's ahead.

The store lives on the Discovery instance (one per orchestrator / per process), so
it survives WebSocket reconnects like the resume design wants, while staying
isolated between tests (each test builds its own orchestrator).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.config import settings
from app.services.agent.walklog import get_logger, tick_bump
from app.shared.geo_math import haversine_m
from app.shared.schemas import GeoPoint, Place

from .providers import PlaceProvider

log = get_logger()


@dataclass
class ApproachState:
    """Per-object distance memory — tells 'approaching' from 'already passed'."""

    last_distance_m: float
    min_distance_m: float
    passed: bool = False


@dataclass
class SessionInventory:
    anchor: GeoPoint  # centre the current disc was fetched around
    places: list[Place]  # everything found within inventory_radius_m of the anchor
    last_fetch_at: float  # monotonic time of the last fetch attempt (success or empty)
    version: int = 0  # bumped whenever `places` changes (drives the map-pin push)
    approach: dict[str, ApproachState] = field(default_factory=dict)


class InventoryStore:
    """A bounded per-session cache of `SessionInventory`, mirroring the TTL+LRU
    discipline of the in-memory session store and the Overpass HTTP cache."""

    def __init__(self) -> None:
        # session_id -> (last_access_monotonic, inventory)
        self._data: dict[str, tuple[float, SessionInventory]] = {}
        # session_id -> last inventory version pushed to that client (map pins)
        self._pulled: dict[str, int] = {}

    def _evict_expired(self, now: float) -> None:
        ttl = settings.inventory_ttl_s
        if ttl <= 0:
            return
        cutoff = now - ttl
        for sid in [s for s, (ts, _) in self._data.items() if ts < cutoff]:
            self._data.pop(sid, None)

    def _cap(self) -> None:
        cap = settings.inventory_max_sessions
        while cap and len(self._data) > cap:
            oldest = min(self._data, key=lambda s: self._data[s][0])  # LRU
            self._data.pop(oldest, None)

    def _should_fetch(self, inv: SessionInventory | None, position: GeoPoint) -> bool:
        if inv is None:
            return True
        edge = settings.inventory_radius_m * settings.inventory_refetch_frac
        # We always re-anchor to the fetch position (see `ensure`), so crossing the
        # edge can't hammer Overpass: after a fetch the user is back inside the disc.
        return haversine_m(position, inv.anchor) >= edge

    async def ensure(
        self, session_id: str, position: GeoPoint, provider: PlaceProvider
    ) -> SessionInventory:
        """Return the session's inventory, (re)fetching the wide disc only when the
        user has walked past half its radius from the anchor it was centred on."""
        now = time.monotonic()
        self._evict_expired(now)
        entry = self._data.get(session_id)
        inv = entry[1] if entry is not None else None
        if self._should_fetch(inv, position):
            reason = "cold" if inv is None else "walked-past-edge"
            t0 = time.monotonic()
            tick_bump("overpass")
            places = await provider.fetch_places(position, settings.inventory_radius_m)
            log.info(
                "overpass fetch reason=%s r=%.0f -> %d places (t=%dms)%s",
                reason, settings.inventory_radius_m, len(places),
                int((time.monotonic() - t0) * 1000),
                "" if places else " EMPTY (endpoint miss or genuinely no coverage here)",
            )
            if inv is None:
                inv = SessionInventory(
                    anchor=position, places=places, last_fetch_at=now, version=1
                )
            else:
                # Always re-centre to where we just looked (so we don't refetch next
                # tick); keep the stale disc only if the new fetch came back empty
                # (transient Overpass miss) rather than blanking a usable inventory.
                inv.anchor = position
                inv.last_fetch_at = now
                if places:
                    inv.places = places
                    inv.version += 1  # disc changed -> client should refresh its pins
                    ids = {p.id for p in places}
                    inv.approach = {k: v for k, v in inv.approach.items() if k in ids}
        assert inv is not None
        self._data[session_id] = (now, inv)
        self._cap()
        return inv

    def peek(self, session_id: str) -> SessionInventory | None:
        """The session's cached disc WITHOUT fetching — for the cheap per-frame bubble
        check that drives object preemption (no Overpass on the hot path)."""
        entry = self._data.get(session_id)
        return entry[1] if entry is not None else None

    def update_approach(self, inv: SessionInventory, position: GeoPoint) -> None:
        """Refresh each object's distance memory from the live position."""
        close = settings.weave_radius_m
        margin = settings.inventory_pass_margin_m
        for p in inv.places:
            d = haversine_m(position, p.location)
            st = inv.approach.get(p.id)
            if st is None:
                inv.approach[p.id] = ApproachState(last_distance_m=d, min_distance_m=d)
                continue
            if d < st.min_distance_m:
                st.min_distance_m = d
            # passed = came within the weave radius, now receding clearly past that min
            if not st.passed and st.min_distance_m <= close and d > st.min_distance_m + margin:
                st.passed = True
            st.last_distance_m = d

    @staticmethod
    def passed_ids(inv: SessionInventory) -> set[str]:
        return {pid for pid, st in inv.approach.items() if st.passed}

    def take_places_update(self, session_id: str) -> list[Place] | None:
        """Return the session's inventory places IF the disc has changed since the
        last pull (else None) — so the producer pushes a fresh set of map pins only
        when there's something new to draw."""
        entry = self._data.get(session_id)
        if entry is None:
            return None
        inv = entry[1]
        if self._pulled.get(session_id) == inv.version:
            return None
        self._pulled[session_id] = inv.version
        return list(inv.places)
