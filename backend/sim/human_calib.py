"""Human calibration of the interestingness judge (Block 4 Part A3/B3).

The judge is only trustworthy once it agrees with a human. This utility (1) exports a
stratified sample of blurbs for one-time manual labeling, and (2) computes inter-rater
agreement — **percent agreement AND Cohen's κ** (80% agreement can hide κ=0.62 under class
imbalance, so we always report both). Target κ ≳ 0.6 before the judge is used to gate.

Offline and dependency-free. Because the live judge is unreachable under our regional
geoblock, the compare step works on FILES of labels (human column vs judge column), so it
is fully testable without a model.

    python -m sim.human_calib export --n 100 --out labels.jsonl   # then label 'human' by hand
    python -m sim.human_calib score  --labels labels.jsonl        # human vs judge agreement
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sim.interest_corpus import load_e2e, split


# --------------------------------------------------------------------------- #
# agreement statistics (pure)
# --------------------------------------------------------------------------- #
def percent_agreement(a: list, b: list) -> float:
    """Fraction of items where the two raters gave the same label. 1.0 = identical."""
    if not a or len(a) != len(b):
        return 0.0
    return sum(1 for x, y in zip(a, b, strict=True) if x == y) / len(a)


def cohens_kappa(a: list, b: list) -> float:
    """Cohen's κ for two raters over categorical labels. Corrects agreement for chance:
    1.0 perfect, 0 = chance, <0 worse than chance. Returns 1.0 when both raters are
    constant AND identical (no disagreement, no variance)."""
    if not a or len(a) != len(b):
        return 0.0
    n = len(a)
    po = percent_agreement(a, b)
    labels = set(a) | set(b)
    pe = 0.0
    for label in labels:
        pa = sum(1 for x in a if x == label) / n
        pb = sum(1 for x in b if x == label) / n
        pe += pa * pb
    if pe >= 1.0:  # both raters constant & identical -> perfect (avoid 0/0)
        return 1.0 if po >= 1.0 else 0.0
    return (po - pe) / (1 - pe)


def binarize(score: float, threshold: int = 3) -> int:
    """Map a 0-4 rubric score to pass(1)/fail(0). Pairwise/binary agreement is more robust
    than exact 5-way agreement (which is noisy for adjacent scores)."""
    return 1 if score >= threshold else 0


# --------------------------------------------------------------------------- #
# export / import
# --------------------------------------------------------------------------- #
def export_blurbs(out_path: str | Path, *, n: int = 100, source: list | None = None) -> int:
    """Write up to ``n`` stratified blurbs to a JSONL file with an empty ``human`` field to
    fill in by hand (0-4). Draws from train+dev (never test/holdout) so labeling can't leak
    the gate. Returns the number written."""
    samples = source if source is not None else load_e2e()
    parts = split(samples)
    pool = parts["train"] + parts["dev"]
    pool = sorted(pool, key=lambda s: s.id)[:n]
    lines = []
    for s in pool:
        lines.append(json.dumps({
            "id": s.id, "region": s.region, "language": s.language,
            "place": s.place, "significance": s.significance,
            "facts": s.facts, "blurb": s.text,
            "human": None,   # <- label 0-4 by hand
            "judge": None,   # <- fill from a judge run (its `overall`)
        }, ensure_ascii=False))
    Path(out_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)


def read_labeled(path: str | Path) -> list[dict]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def score_labeled(rows: list[dict], *, threshold: int = 3) -> dict:
    """Compare the human and judge columns of a labeled file. Returns exact + binary
    agreement and κ; ignores rows missing either label."""
    pairs = [(r["human"], r["judge"]) for r in rows
             if r.get("human") is not None and r.get("judge") is not None]
    if not pairs:
        return {"n": 0}
    human = [int(h) for h, _ in pairs]
    judge = [int(j) for _, j in pairs]
    hb = [binarize(h, threshold) for h in human]
    jb = [binarize(j, threshold) for j in judge]
    return {
        "n": len(pairs),
        "exact_agreement": percent_agreement(human, judge),
        "exact_kappa": cohens_kappa(human, judge),
        "binary_agreement": percent_agreement(hb, jb),
        "binary_kappa": cohens_kappa(hb, jb),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Judge human-calibration (Block 4)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export", help="write blurbs to label by hand")
    e.add_argument("--n", type=int, default=100)
    e.add_argument("--out", default="labels.jsonl")
    s = sub.add_parser("score", help="human vs judge agreement + kappa")
    s.add_argument("--labels", required=True)
    s.add_argument("--threshold", type=int, default=3)
    args = ap.parse_args()

    if args.cmd == "export":
        n = export_blurbs(args.out, n=args.n)
        print(f"wrote {n} blurbs to {args.out} — label the 'human' field 0-4 by hand, "
              "then fill 'judge' from a judge run and re-run `score`.")
    else:
        stats = score_labeled(read_labeled(args.labels), threshold=args.threshold)
        if not stats.get("n"):
            print("no rows with BOTH human and judge labels — nothing to compare.")
            return
        print(f"\n  n={stats['n']}")
        print(f"  exact  : agreement {stats['exact_agreement']:.2f}  κ {stats['exact_kappa']:.2f}")
        print(f"  binary : agreement {stats['binary_agreement']:.2f}  κ {stats['binary_kappa']:.2f}"
              "   (pass/fail @ threshold)")
        verdict = "OK to gate" if stats["binary_kappa"] >= 0.6 else "NOT YET (target κ≥0.6)"
        print(f"  -> {verdict}\n")


if __name__ == "__main__":
    main()
