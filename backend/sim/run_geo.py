"""Run the virtual walk through the Geo layer and print ranked candidates.

    python -m sim.run_geo                      # uses cached fixtures (offline)
    python -m sim.run_geo --live               # hits real Overpass API

This is the Stage 1 deliverable: sim -> geo -> ranked candidates.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Windows consoles default to a legacy code page; force UTF-8 for Cyrillic.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from app.config import settings
from app.services.geo.discovery import Discovery
from app.services.geo.providers import OverpassProvider, StaticPlaceProvider
from sim.routes import RED_SQUARE
from sim.walk import walk

_FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "places_red_square.json"


async def main(live: bool) -> None:
    provider = OverpassProvider() if live else StaticPlaceProvider.from_json(_FIXTURE)
    discovery = Discovery(provider)
    seen: list[str] = []

    for step in walk(RED_SQUARE, speed_mps=1.3, step_s=8.0):
        result = await discovery.discover_adaptive(
            step.position, step.heading, seen, settings.default_radius_m
        )
        top = result.candidates[:3]
        flags = []
        if result.expanded:
            flags.append(f"radius->{result.radius_m:.0f}m")
        if result.exhausted:
            flags.append("EXHAUSTED")
        header = (
            f"t={step.t:5.0f}s  ({step.position.lat:.4f},{step.position.lon:.4f})  "
            f"hdg={step.heading.direction_deg:3.0f}  {' '.join(flags)}"
        )
        print(header)
        for c in top:
            cone = "[gaze]" if c.in_gaze_cone else "      "
            print(
                f"    {cone} {c.distance_m:5.0f}m  w={c.type_weight:.2f}  "
                f"{c.place.category:16s} {c.place.name}"
            )
        # Mark a place "seen" only once we're genuinely close to it (within the
        # base radius) — this mirrors the orchestrator narrating on approach and
        # exercises dedup without instantly exhausting the small fixture set.
        if top and top[0].distance_m <= settings.default_radius_m:
            chosen = top[0]
            if chosen.place.id not in seen:
                seen.append(chosen.place.id)
                print(f"    -> seen: {chosen.place.name}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="query real Overpass API")
    args = ap.parse_args()
    asyncio.run(main(args.live))
