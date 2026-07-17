"""Composite interestingness score with hard-gates (Block 4 Part A4 / Part B Principle 2).

Fuses the reference-free code panel (``interest_metrics.BlurbMetrics``) and the LLM judge
(``interest_judge.JudgeVerdict``) into ONE number, then applies the invariant hard-gates:

    score = interestingness · Π(hard_gates_passed)

The gates cannot be bought back by interestingness (a fabricated or clichéd blurb scores ~0
no matter how "novel") — this is the project's "facts only, no cliché, don't inflate"
invariant expressed as a reward, and the main defence against reward-hacking toward hype.

Non-monotonic axes (NIDF specificity — extreme rarity is junk, not interest) pass through an
inverted-U transform (Wundt curve) so the middle is rewarded and both extremes penalised.

Weights: DEFAULT_WEIGHTS are hand-set (effortful/valuable axes higher). ``fit_weights`` learns
them by ridge least-squares on human labels (Phase 3) — pure-Python, no numpy. Both produce a
plain feature→weight dict consumed by ``composite``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .interest_judge import JudgeVerdict, WalkVerdict
from .interest_metrics import BlurbMetrics, CorpusMetrics

# Feature order used by the regression + the default weights. Semantic axes come from the
# judge when present; code axes are the reference-free panel. All normalised to ~0-1.
FEATURES = (
    "novelty",          # judge or code novelty-vs-corpus
    "specificity",      # inverted-U of NIDF (code) and/or judge specificity
    "hook",             # judge only (0 without a judge)
    "vividness",        # judge only
    "in_place",         # judge only
    "number_density",   # code, capped
    "mtld",             # code, squashed
    "speakability",     # code and/or judge
)

DEFAULT_WEIGHTS: dict[str, float] = {
    "novelty": 0.22,
    "specificity": 0.18,
    "hook": 0.16,
    "vividness": 0.10,
    "in_place": 0.12,
    "number_density": 0.08,
    "mtld": 0.06,
    "speakability": 0.08,
}

# Gate thresholds for the code-derived gates.
_NOVELTY_MIN = 0.5       # below ⇒ a near-duplicate (interest not via reworded repeats)


def inverted_u(x: float, peak: float = 0.55) -> float:
    """Wundt/Bayesian-surprise curve: 1.0 at ``peak``, →0 toward 0 and 1. Rewards the middle
    (moderately specific/surprising) and penalises both extremes (generic OR junk-rare)."""
    x = max(0.0, min(1.0, x))
    d = abs(x - peak)
    span = max(peak, 1 - peak)
    return max(0.0, 1 - d / span)


def _squash(x: float, scale: float) -> float:
    return x / (x + scale) if x > 0 else 0.0


def feature_vector(
    bm: BlurbMetrics, verdict: JudgeVerdict | None = None
) -> dict[str, float]:
    """Assemble the normalised feature dict from the code panel + optional judge verdict.
    With a judge, semantic axes use the judge (÷4); specificity/speakability blend both."""
    j = {ax: v / 4.0 for ax, v in verdict.axes.items()} if verdict else {}
    code_spec = inverted_u(bm.specificity)
    return {
        "novelty": j.get("novelty", bm.novelty),
        "specificity": (0.5 * code_spec + 0.5 * j["specificity"]) if j else code_spec,
        "hook": j.get("hook", 0.0),
        "vividness": j.get("vividness", 0.0),
        "in_place": j.get("in_place", 0.0),
        "number_density": min(1.0, bm.number_density * 10),
        "mtld": _squash(bm.mtld, 20.0),
        "speakability": (
            0.5 * bm.speakability + 0.5 * j["speakability"] if j else bm.speakability
        ),
    }


@dataclass
class CompositeScore:
    interest: float                 # 0-1 weighted blend BEFORE gates
    gates: dict[str, bool] = field(default_factory=dict)
    score: float = 0.0              # 0-1 final = interest · Π(gates)

    @property
    def passed(self) -> bool:
        return all(self.gates.values())


def _gates(bm: BlurbMetrics, verdict: JudgeVerdict | None) -> dict[str, bool]:
    """The invariant hard-gates. ``grounded`` needs the judge (it verifies claims vs FACTS);
    without a judge we cannot verify, so grounded defaults True and the caller should treat a
    judge-less score as advisory only."""
    cliche_free = bm.cliche_hits == 0 and (not verdict.cliche if verdict else True)
    grounded = verdict.grounded if verdict else True
    novel = bm.novelty >= _NOVELTY_MIN
    return {"grounded": grounded, "cliche_free": cliche_free, "novel": novel}


def composite(
    bm: BlurbMetrics,
    verdict: JudgeVerdict | None = None,
    *,
    weights: dict[str, float] | None = None,
) -> CompositeScore:
    """The final gated interestingness score for one blurb."""
    w = weights or DEFAULT_WEIGHTS
    fv = feature_vector(bm, verdict)
    wsum = sum(w.get(f, 0.0) for f in FEATURES) or 1.0
    interest = sum(w.get(f, 0.0) * fv[f] for f in FEATURES) / wsum
    interest = max(0.0, min(1.0, interest))
    gates = _gates(bm, verdict)
    factor = 1.0
    for ok in gates.values():
        factor *= 1.0 if ok else 0.0
    return CompositeScore(interest=interest, gates=gates, score=interest * factor)


# --------------------------------------------------------------------------- #
# walk-level coherence (cross-object) — бесшовность / связность / интеграция в арку
# A SEPARATE 0-1 quantity from the per-blurb composite: consumed by the worker (as a bounded
# ±15% dial on the walk score) and the optimizer (as a secondary objective). Deliberately NOT a
# hard gate and NOT part of composite() — a disjoint-but-grounded walk must never be zeroed, and
# coherence must never buy back facts-only/coverage.
# --------------------------------------------------------------------------- #
def walk_coherence(
    cm: CorpusMetrics, wv: WalkVerdict | None = None, *, judge_weight: float = 0.6
) -> float:
    """Blend the code coherence signals (transitions / adjacency / callbacks) with the optional
    walk-level judge verdict into one 0-1 score. Code-only when no judge (advisory); with a judge,
    its multilingual seamlessness/arc verdict leads (``judge_weight``). Adjacency ramps to full
    credit at a modest 0.25 (content-word overlap between neighbours is small in practice) and is
    HALVED past 0.6 (near-identical consecutive blurbs = rewording, not a theme — the one case where
    'more overlap' is worse)."""
    coh_adj = min(1.0, cm.adjacent_cohesion / 0.25)
    if cm.adjacent_cohesion > 0.6:
        coh_adj *= 0.5
    trans = min(1.0, cm.transition_rate / 0.5)   # ~half the blurbs linking = full credit; capped
    callbacks = min(1.0, cm.callback_rate * 3.0)  # callbacks are rare; a few is a strong signal
    code_score = 0.5 * coh_adj + 0.25 * trans + 0.25 * callbacks
    code_score = max(0.0, min(1.0, code_score))
    if wv is None:
        return code_score
    return max(0.0, min(1.0, judge_weight * wv.score + (1.0 - judge_weight) * code_score))


# --------------------------------------------------------------------------- #
# weight fitting — ridge least-squares on human labels (pure Python, no numpy)
# --------------------------------------------------------------------------- #
def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting for a small dense system A x = b."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[piv] = m[piv], m[col]
        if abs(m[col][col]) < 1e-12:
            continue
        for r in range(n):
            if r != col:
                f = m[r][col] / m[col][col]
                for c in range(col, n + 1):
                    m[r][c] -= f * m[col][c]
    return [m[i][n] / m[i][i] if abs(m[i][i]) > 1e-12 else 0.0 for i in range(n)]


def fit_weights(
    rows: list[tuple[dict[str, float], float]], *, ridge: float = 0.1
) -> dict[str, float]:
    """Learn feature weights by ridge-regularised least squares on ``(feature_vector, human)``
    pairs (human label normalised to 0-1). Ridge keeps it stable on small/collinear data.
    Returns a feature→weight dict; falls back to DEFAULT_WEIGHTS on too little data."""
    if len(rows) < len(FEATURES) + 1:
        return dict(DEFAULT_WEIGHTS)
    xs = [[fv.get(f, 0.0) for f in FEATURES] for fv, _ in rows]
    ys = [max(0.0, min(1.0, y)) for _, y in rows]
    n = len(FEATURES)
    # normal equations (XᵀX + ridge·I) w = Xᵀy
    ata = [[sum(xs[k][i] * xs[k][j] for k in range(len(xs))) for j in range(n)] for i in range(n)]
    for i in range(n):
        ata[i][i] += ridge
    atb = [sum(xs[k][i] * ys[k] for k in range(len(xs))) for i in range(n)]
    w = _solve(ata, atb)
    return {f: w[i] for i, f in enumerate(FEATURES)}
