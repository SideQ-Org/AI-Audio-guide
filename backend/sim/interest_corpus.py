"""Build the interestingness eval corpus (Block 4 Phase 0/D2).

A ``Sample`` is one narrated blurb + enough context to score it. Two sources:
  * the offline sim bank — ``e2e_results.json`` from ``sim.e2e_regions`` (no keys/DB
    needed; the default source for CI). Carries place/significance/text but NOT facts.
  * the durable ``narration_samples`` table (prod capture, Phase 0) — carries the FACTS,
    so groundedness can be scored. Loaded via ``load_db`` only when a DATABASE_URL is set.

``split`` gives a DETERMINISTIC, stratified train/dev/test/sacred-holdout partition
(stratified by region × kind × language, stable-hashed by sample id so the same sample
always lands in the same split — the optimizer must never see test/holdout, and a random
split would leak across runs). The optimizer trains on ``train``+``dev`` only.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_E2E = Path(__file__).resolve().parent / "e2e_results.json"

# train / dev / test / sacred-holdout. Optimizer sees train+dev; gate uses test; the
# sacred holdout is touched only right before a rollout (so nothing leaks even via test).
SPLITS = ("train", "dev", "test", "holdout")
_SPLIT_WEIGHTS = (50, 20, 20, 10)  # percentages, summing to 100


@dataclass
class Sample:
    id: str
    region: str          # scenario key (msk-red-square, …) — the stratification axis
    kind: str            # scenario kind (турист/окраина) or blurb kind (object/area)
    language: str
    text: str
    place: str | None = None
    significance: str | None = None
    facts: str | None = None   # None for e2e-sourced samples; set for DB-sourced ones
    tier: str = "free"         # free|paid — the walk's tier (different generator models)
    # Walk grouping for the coherence objective: blurbs sharing walk_id, ordered by seq, are one
    # walk. e2e: walk_id=region, seq=index (a scenario's narrations ARE the walk, in order). db:
    # walk_id=str(walk_id), seq=narration_samples.seq. category feeds the callback signal.
    walk_id: str | None = None
    seq: int = 0
    category: str | None = None

    @property
    def has_facts(self) -> bool:
        return bool((self.facts or "").strip())


def load_e2e(path: str | Path | None = None) -> list[Sample]:
    """Load samples from an ``e2e_results.json`` produced by ``sim.e2e_regions``."""
    p = Path(path) if path else _DEFAULT_E2E
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    scenarios = data if isinstance(data, list) else data.get("scenarios", [])
    out: list[Sample] = []
    for sc in scenarios:
        region = sc.get("key", "unknown")
        kind = sc.get("kind", "")
        lang = sc.get("lang", "ru")
        for i, n in enumerate(sc.get("narrations", [])):
            text = (n.get("text") or "").strip()
            if not text:
                continue
            out.append(
                Sample(
                    id=f"{region}:{i}",
                    region=region,
                    kind=kind,
                    language=lang,
                    text=text,
                    place=n.get("place"),
                    significance=n.get("sig"),
                    walk_id=region,            # a scenario = one walk
                    seq=i,                     # narration order within the scenario
                    category=n.get("category"),
                )
            )
    return out


async def load_db(limit: int = 5000) -> list[Sample]:
    """Load samples from the durable ``narration_samples`` table (prod capture). Requires
    the accounts extra + a DATABASE_URL; returns [] otherwise. Ordered newest-first."""
    from app.services.accounts.db import accounts_enabled, session_scope

    if not accounts_enabled():
        return []
    from sqlalchemy import select

    from app.services.accounts.models import NarrationSample

    out: list[Sample] = []
    async with session_scope() as session:
        rows = (
            await session.scalars(
                select(NarrationSample)
                .order_by(NarrationSample.created_at.desc())
                .limit(limit)
            )
        ).all()
    for r in rows:
        text = (r.narration or "").strip()
        if not text:
            continue
        region = (r.input_json or {}).get("district") or (r.input_json or {}).get("city") or "prod"
        out.append(
            Sample(
                id=str(r.id),
                region=str(region),
                kind=r.kind,
                language=r.language,
                text=text,
                place=(r.input_json or {}).get("place", {}).get("name") if r.input_json else None,
                significance=r.significance,
                facts=r.facts,
                tier=getattr(r, "tier", "free"),
                walk_id=str(getattr(r, "walk_id", "") or "") or None,
                seq=int(getattr(r, "seq", 0) or 0),
                category=getattr(r, "category", None),
            )
        )
    return out


def by_tier(samples: list[Sample], tier: str) -> list[Sample]:
    """Filter the corpus to one tier — the optimizer improves the RIGHT prompt against the
    RIGHT tier's data (free/paid run different generator models)."""
    return [s for s in samples if s.tier == tier]


def _bucket(sample_id: str) -> str:
    """Stable split assignment: hash the id to [0,100) and map into the weighted bands."""
    h = int(hashlib.sha1(sample_id.encode("utf-8")).hexdigest(), 16) % 100
    acc = 0
    for name, w in zip(SPLITS, _SPLIT_WEIGHTS, strict=True):
        acc += w
        if h < acc:
            return name
    return SPLITS[-1]


def split(samples: list[Sample]) -> dict[str, list[Sample]]:
    """Deterministic, stratified train/dev/test/holdout partition. Stratifies by
    (region, kind, language) so each stratum is represented in every split, and assigns
    within a stratum by stable id-hash (reproducible across runs, no leakage)."""
    strata: dict[tuple, list[Sample]] = defaultdict(list)
    for s in samples:
        strata[(s.region, s.kind, s.language)].append(s)
    result: dict[str, list[Sample]] = {name: [] for name in SPLITS}
    for group in strata.values():
        for s in sorted(group, key=lambda x: x.id):
            result[_bucket(s.id)].append(s)
    return result
