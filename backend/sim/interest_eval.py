"""Interestingness eval over the corpus — the code-panel "number" (Block 4 Phase 1).

Runs the reference-free metric panel (``interest_metrics``) over the corpus
(``interest_corpus``) and prints a per-region interestingness score, plus the underlying
axis means so a regression ("опять про берёзы" / silence creep) is diagnosable.

    python -m sim.interest_eval                 # offline, from sim/e2e_results.json
    python -m sim.interest_eval --db            # from the durable narration_samples table
    python -m sim.interest_eval --split dev     # score only one split

NOTE on the composite: the weights here are PROVISIONAL. The real weights are fit by
regression on human labels in Phase 3 (interest_score.py), and the semantic axes the code
panel can't see (hook / vividness / in-place-relevance / groundedness) come from the LLM
judge (Phase 2). This number is the CODE-PANEL PROXY — a fast regression signal, not the
final truth. It is intentionally computed from language-agnostic axes only (8 languages).
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from dataclasses import dataclass

from app.services.agent.interest_metrics import (
    build_idf,
    score_blurb,
    score_corpus,
)
from app.services.agent.interest_score import walk_coherence
from sim.interest_corpus import Sample, load_db, load_e2e, split

# Provisional axis weights (effortful/valuable axes higher, per the doc). Positive axes
# sum to 1.0; penalties subtract. Replaced by regression weights in Phase 3.
_W = {
    "novelty": 0.24,
    "specificity": 0.20,
    "number_density": 0.16,
    "speakability": 0.16,
    "mtld": 0.12,
    "corpus_diversity": 0.12,  # distinct-2 of the region, applied per blurb
}
_CLICHE_PENALTY = 0.5   # per blurb with any cliché hit (also a hard-gate in Phase 3)
_REPEAT_PENALTY = 0.3   # scaled by region self-repetition


def _squash(x: float, scale: float) -> float:
    """Map an unbounded-positive metric into 0-1 (x/(x+scale))."""
    return x / (x + scale) if x > 0 else 0.0


@dataclass
class RegionScore:
    region: str
    n: int
    score: float          # 0-100 code-panel proxy
    novelty: float
    specificity: float
    number_density: float
    speakability: float
    mtld: float
    distinct_2: float
    cliche_rate: float
    coherence: float      # 0-1 walk-level coherence (transitions/adjacency/callbacks; code-only)


def _score_region(region: str, samples: list[Sample], idf: dict[str, float]) -> RegionScore:
    # A region == one walk here; order by seq so adjacency/transitions are measured in walk order.
    samples = sorted(samples, key=lambda s: s.seq)
    texts = [s.text for s in samples]
    lang = samples[0].language if samples else "ru"
    cm = score_corpus(texts, categories=[s.category for s in samples], language=lang)
    prior: list[str] = []
    per_blurb: list[float] = []
    agg = defaultdict(float)
    cliche_blurbs = 0
    for s in samples:
        bm = score_blurb(s.text, prior=prior, idf=idf, language=s.language)
        prior.append(s.text)
        positive = (
            _W["novelty"] * bm.novelty
            + _W["specificity"] * bm.specificity
            + _W["number_density"] * min(1.0, bm.number_density * 10)
            + _W["speakability"] * bm.speakability
            + _W["mtld"] * _squash(bm.mtld, 20.0)
            + _W["corpus_diversity"] * cm.distinct_2
        )
        penalty = (
            (_CLICHE_PENALTY if bm.cliche_hits else 0.0)
            + _REPEAT_PENALTY * cm.self_repetition
        )
        per_blurb.append(max(0.0, min(1.0, positive - penalty)))
        agg["novelty"] += bm.novelty
        agg["specificity"] += bm.specificity
        agg["number_density"] += bm.number_density
        agg["speakability"] += bm.speakability
        agg["mtld"] += bm.mtld
        cliche_blurbs += 1 if bm.cliche_hits else 0
    n = len(samples) or 1
    return RegionScore(
        region=region,
        n=len(samples),
        score=100 * (sum(per_blurb) / n),
        novelty=agg["novelty"] / n,
        specificity=agg["specificity"] / n,
        number_density=agg["number_density"] / n,
        speakability=agg["speakability"] / n,
        mtld=agg["mtld"] / n,
        distinct_2=cm.distinct_2,
        cliche_rate=cliche_blurbs / n,
        coherence=walk_coherence(cm, None),
    )


def evaluate(samples: list[Sample]) -> list[RegionScore]:
    """Score every region. IDF is built over the WHOLE corpus so specificity is measured
    against the same reference vocabulary everywhere."""
    idf = build_idf([s.text for s in samples])
    by_region: dict[str, list[Sample]] = defaultdict(list)
    for s in samples:
        by_region[s.region].append(s)
    return [_score_region(r, ss, idf) for r, ss in sorted(by_region.items())]


def _bar(rate: float, width: int = 20) -> str:
    fill = round(max(0.0, min(1.0, rate)) * width)
    return "█" * fill + "·" * (width - fill)


async def _load(use_db: bool) -> list[Sample]:
    return await load_db() if use_db else load_e2e()


def main() -> None:
    ap = argparse.ArgumentParser(description="Interestingness code-panel eval (Block 4)")
    ap.add_argument("--db", action="store_true", help="load from narration_samples DB table")
    ap.add_argument("--split", choices=["all", "train", "dev", "test", "holdout"], default="all")
    args = ap.parse_args()

    samples = asyncio.run(_load(args.db))
    if not samples:
        src = "narration_samples (DB)" if args.db else "sim/e2e_results.json"
        print(f"no samples found in {src} — run `python -m sim.e2e_regions` first, "
              "or capture_narration_samples in prod.")
        return
    if args.split != "all":
        samples = split(samples)[args.split]

    scores = evaluate(samples)
    overall = sum(r.score * r.n for r in scores) / (sum(r.n for r in scores) or 1)
    print(f"\ninterestingness (code-panel proxy) — {len(samples)} blurbs, split={args.split}\n")
    print(f"  {'region':<20} {'n':>3}  {'score':>6}  {'bar':<20}  nov  spec  num  say  clich  cohr")
    for r in scores:
        print(
            f"  {r.region:<20} {r.n:>3}  {r.score:>6.1f}  {_bar(r.score / 100)}  "
            f"{r.novelty:.2f} {r.specificity:.2f} {min(1.0, r.number_density*10):.2f} "
            f"{r.speakability:.2f} {r.cliche_rate:.2f}  {r.coherence:.2f}"
        )
    print(f"\n  overall (blurb-weighted): {overall:.1f}/100   "
          "[provisional weights; hook/vividness/grounding come from the judge, Phase 2-3]\n")


if __name__ == "__main__":
    main()
