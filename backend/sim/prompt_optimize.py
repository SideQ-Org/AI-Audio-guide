"""Offline prompt self-improvement loop (Block 4 Part B, Phase 5) — the "fixer".

Rewrites a system prompt (e.g. narrator.txt) against the interestingness evaluator until it
plateaus, WITHOUT touching the live prompt. Hybrid OPRO(propose) + TextGrad(critique) +
DSPy-discipline(dev/holdout) + RLAIF-style guardrails. It produces a validated CANDIDATE +
evidence bundle; it never writes the live prompt (that's Phase 6 canary).

The safety architecture, straight from the research (Блок4 Part B):
  * TWO judges — a cheap SEARCH judge ranks candidates on dev (thousands of calls); a GOLD
    judge (a stronger/ensemble model + human) gates PROMOTION on the held-out set only. The
    optimizer never sees the held-out set.
  * HARD GATES never degrade — a candidate that lifts "interest" by weakening groundedness or
    cliché is rejected (the reward-hacking that matters for a guide).
  * STOP by the GOLD judge on held-out, not the search judge — plateau + a reward-hacking
    detector (search rises while gold doesn't) end the run. No "optimise until the proxy maxes".

The LLM pieces (proposer, re-generation, judges) go through LLMClient, so the loop is fully
unit-testable with fakes; a live run needs reachable generator + judge models.
"""

from __future__ import annotations

import contextlib
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.services.agent.interest_metrics import build_idf, score_blurb
from app.services.agent.interest_score import composite
from app.services.agent.prompts import (
    clear_prompt_overrides,
    set_prompt_override,
    system_for_optimizer,
)
from app.services.llm.client import LLMClient
from app.services.llm.router import Role
from app.services.quality.registry import Experiment, PromptRegistry
from app.shared.schemas import NarratorInput

# Child of aiguide.quality so the optimizer's decisions flow through the same followable log sink.
_log = logging.getLogger("aiguide.quality.optimize")

# The non-negotiable invariants handed to the proposer verbatim (it must not weaken them).
INVARIANTS = (
    "1) Только проверенные факты; НЕ выдумывать историю/даты/причины. "
    "2) Но и МОЛЧАНИЕ — продуктовый провал: гид не должен ничего не рассказать про объект. "
    "При нехватке фактов ПРАВИЛЬНОЕ решение — ДОБЫТЬ факты (агрессивный ресёрч/enrichment) "
    "или хотя бы назвать видимое; НЕЛЬЗЯ увеличивать долю [SILENCE]/«только-название». "
    "3) Без клише и «воды». 4) Не раздувать обычные места; без рекламы; уважение к мемориалам. "
    "5) Аудио: короткие фразы, числа словами, без разметки. 6) Не пересказывать объект дважды "
    "(revisit-фича — исключение)."
)


@dataclass
class EvalItem:
    """One dev/holdout example: the narrator input to re-generate from + the FACTS (ground
    truth for the groundedness gate)."""

    inp: NarratorInput
    facts: str | None = None
    tier: str = "free"

    @property
    def language(self) -> str:
        return self.inp.language


def _is_silence(text: str) -> bool:
    """A regenerated blurb that says (almost) nothing — actual [SILENCE] or empty. This is the
    product anti-pattern the coverage gate defends: the guide must not go quiet to look 'safe'."""
    t = (text or "").strip()
    return not t or t.upper().strip("[]") == "SILENCE"


@dataclass
class SplitEval:
    n: int
    mean: float                       # mean gated composite score (0-1)
    grounded_rate: float
    cliche_rate: float
    silence_rate: float = 0.0         # fraction of blurbs that went silent (coverage anti-pattern)
    scores: list[float] = field(default_factory=list)   # per-item, aligned to the split
    texts: list[str] = field(default_factory=list)       # the regenerated narrations


@dataclass
class Variant:
    id: str
    text: str
    gen: int
    parent: str | None = None


@dataclass
class OptimizeResult:
    target: str
    tier: str
    baseline_text: str
    champion: Variant
    improved: bool
    baseline_dev: float
    champion_dev: float
    baseline_gold: float
    champion_gold: float
    trajectory: list[dict] = field(default_factory=list)
    stop_reason: str = ""


# --------------------------------------------------------------------------- #
# proposer (OPRO + TextGrad)
# --------------------------------------------------------------------------- #
_PROPOSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "analysis": {"type": "string"},
        "candidates": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["analysis", "candidates"],
    "additionalProperties": False,
}


class Proposer:
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def propose(
        self,
        current: str,
        failure_analysis: str,
        trajectory: list[dict],
        *,
        n: int,
    ) -> list[str]:
        user = json.dumps({
            "CURRENT_PROMPT": current,
            "FAILURE_ANALYSIS": failure_analysis,
            "TRAJECTORY": trajectory,
            "INVARIANTS": INVARIANTS,
            "N": n,
        }, ensure_ascii=False)
        data = await self._llm.complete_json(
            Role.OPTIMIZER, system_for_optimizer(), user, _PROPOSE_SCHEMA, max_tokens=4096
        )
        cands = [c for c in data.get("candidates", []) if isinstance(c, str) and c.strip()]
        return cands[:n]


# --------------------------------------------------------------------------- #
# candidate evaluation (re-generate with the candidate prompt, then score)
# --------------------------------------------------------------------------- #
async def evaluate_prompt(
    target: str,
    prompt_text: str,
    items: list[EvalItem],
    narrator,
    judge,
) -> SplitEval:
    """Score a candidate ``prompt_text`` for the ``target`` template on ``items``: swap it in
    via the override, re-generate each narration, then run the panel + judge composite. The
    override is always cleared, even on error, so it never leaks into other calls."""
    set_prompt_override(target, prompt_text)
    try:
        texts = [await narrator.narrate(it.inp) for it in items]
    finally:
        clear_prompt_overrides()

    idf = build_idf([t for t in texts if t.strip()])
    prior: list[str] = []
    scores: list[float] = []
    g_ok = c_ok = 0
    for it, text in zip(items, texts, strict=True):
        bm = score_blurb(text, prior=prior, idf=idf, language=it.language)
        verdict = await judge.score(text, facts=it.facts, language=it.language, tier=it.tier)
        cs = composite(bm, verdict)
        prior.append(text)
        scores.append(cs.score)
        g_ok += 1 if cs.gates["grounded"] else 0
        c_ok += 1 if cs.gates["cliche_free"] else 0
    n = len(items) or 1
    return SplitEval(
        n=len(items),
        mean=sum(scores) / n,
        grounded_rate=g_ok / n,
        cliche_rate=(n - c_ok) / n,
        silence_rate=sum(1 for t in texts if _is_silence(t)) / n,
        scores=scores,
        texts=texts,
    )


def failure_analysis(items: list[EvalItem], ev: SplitEval, *, keep: int = 5) -> str:
    """A compact critique the proposer can act on: the worst regenerated blurbs + why, plus the
    aggregate hard-gate rates (TextGrad-style textual gradient)."""
    ranked = sorted(zip(ev.scores, ev.texts, items, strict=True), key=lambda x: x[0])
    lines = [
        f"grounded_rate={ev.grounded_rate:.2f} cliche_rate={ev.cliche_rate:.2f} "
        f"mean_score={ev.mean:.2f}",
        "Худшие фрагменты (score — текст — был ли факт):",
    ]
    for s, t, it in ranked[:keep]:
        has = "да" if (it.facts or "").strip() else "НЕТ"
        lines.append(f"  [{s:.2f}] facts={has} :: {t[:160]}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# gate + stop logic (pure)
# --------------------------------------------------------------------------- #
def _bootstrap_mean_ci(
    deltas: list[float], *, iters: int = 1000, alpha: float = 0.05, seed: int = 0
) -> tuple[float, float]:
    """95% bootstrap CI of the mean of ``deltas``. Deterministic (seeded LCG, no numpy)."""
    n = len(deltas)
    if n == 0:
        return 0.0, 0.0
    rng = seed or 1
    means = []
    for _ in range(iters):
        s = 0.0
        for _ in range(n):
            rng = (1103515245 * rng + 12345) & 0x7FFFFFFF  # LCG
            s += deltas[rng % n]
        means.append(s / n)
    means.sort()
    lo = means[int(alpha / 2 * iters)]
    hi = means[int((1 - alpha / 2) * iters) - 1]
    return lo, hi


def beats_champion(cand: SplitEval, champ: SplitEval, *, margin: float) -> bool:
    """A candidate beats the champion only if the PAIRED mean gain exceeds ``margin`` AND the
    bootstrap CI of the per-item gain excludes 0 (a single-point win doesn't count)."""
    if cand.n != champ.n or cand.n == 0:
        return cand.mean >= champ.mean + margin
    deltas = [c - p for c, p in zip(cand.scores, champ.scores, strict=True)]
    lo, _ = _bootstrap_mean_ci(deltas)
    return (sum(deltas) / len(deltas) >= margin) and lo > 0


def gates_not_degraded(cand: SplitEval, champ: SplitEval, *, eps: float = 1e-6) -> bool:
    """Hard-gate rates must not get worse — no buying interest with fabrication/cliché."""
    return (
        cand.grounded_rate >= champ.grounded_rate - eps
        and cand.cliche_rate <= champ.cliche_rate + eps
    )


def coverage_not_degraded(cand: SplitEval, champ: SplitEval, *, eps: float = 1e-6) -> bool:
    """The COVERAGE gate — a candidate must not make the guide quieter. This is what rejects the
    'fatalistic' fix (trading fabrication for silence): grounding rises but the walker gets
    silence, which is a product failure. The real answer to no-facts is research, not silence."""
    return cand.silence_rate <= champ.silence_rate + eps


def _gates_hold(cand: SplitEval, champ: SplitEval) -> bool:
    """All non-negotiables at once: groundedness, cliché, AND coverage don't degrade."""
    return gates_not_degraded(cand, champ) and coverage_not_degraded(cand, champ)


def is_reward_hacking(search_gain: float, gold_gain: float, *, tol: float = 0.01) -> bool:
    """Search score rose meaningfully but the gold (held-out) score didn't follow → the prompt
    is gaming the proxy. The early-warning that ends the run (Principle 8)."""
    return search_gain > tol and gold_gain <= tol


# --------------------------------------------------------------------------- #
# the loop
# --------------------------------------------------------------------------- #
async def optimize(
    target: str,
    baseline_text: str,
    dev: list[EvalItem],
    holdout: list[EvalItem],
    *,
    narrator,
    search_judge,
    gold_judge,
    proposer: Proposer,
    tier: str = "free",
    registry: PromptRegistry | None = None,
    rounds: int = 6,
    n_candidates: int = 8,
    margin: float = 0.02,
    patience: int = 2,
) -> OptimizeResult:
    """Run the loop for one tier. Returns the champion (baseline if nothing beat it) + evidence.

    With a ``registry`` the loop uses PERSISTENT memory: it seeds the proposer trajectory from
    past runs, refuses to re-try known-bad versions (oscillation guard), records every experiment,
    and on promotion saves the version + moves the active pointer."""
    champ_text = baseline_text
    champ_dev = await evaluate_prompt(target, champ_text, dev, narrator, search_judge)
    champ_gold = await evaluate_prompt(target, champ_text, holdout, narrator, gold_judge)
    base_dev, base_gold = champ_dev.mean, champ_gold.mean
    champion = Variant(id="baseline", text=champ_text, gen=0)

    known_bad: set[str] = set()
    if registry is not None:
        registry.ensure_baseline(target, tier, baseline_text)
        known_bad = set(registry.known_bad(target, tier))

    # Trajectory = persistent memory (past runs) + this run's steps. The proposer sees what was
    # tried before and how it fared, so it doesn't circle back to losers.
    trajectory: list[dict] = []
    if registry is not None:
        for a in registry.past_attempts(target, tier, limit=20):
            trajectory.append(
                {"id": a.get("id"), "gold": a.get("gold"), "verdict": a.get("verdict")}
            )
    trajectory.append({"id": "baseline", "dev": round(base_dev, 3), "gold": round(base_gold, 3)})
    plateau = 0
    stop = "rounds_exhausted"
    _log.info(
        "ОПТИМИЗАТОР %s/%s стартует: baseline dev=%.3f gold=%.3f (память: %d прошлых попыток, "
        "%d known-bad)", target, tier, base_dev, base_gold,
        len(trajectory) - 1, len(known_bad),
    )

    def _record(vid, ct, ev, gold, verdict, reason):
        if registry is None:
            return
        registry.record_experiment(Experiment(
            id=vid, target=target, tier=tier, verdict=verdict, parent=champion.id,
            dev=round(ev.mean, 4) if ev else None,
            gold=round(gold.mean, 4) if gold else None,
            silence=round(ev.silence_rate, 4) if ev else None,
            gates_ok=(_gates_hold(ev, champ_dev) if ev else None),
            stop_reason=reason, n=ev.n if ev else None,
        ))

    for r in range(1, rounds + 1):
        fa = failure_analysis(dev, champ_dev)
        try:
            cand_texts = await proposer.propose(champ_text, fa, trajectory, n=n_candidates)
        except Exception as e:  # noqa: BLE001 — a proposer hiccup ends the round, not the run
            _log.warning("proposer failed round %d: %s", r, e)
            cand_texts = []
        _log.info("раунд %d: предложено %d кандидатов-переписок", r, len(cand_texts))

        # rank candidates on DEV with the SEARCH judge; keep those that beat champ + hold gates
        winners = []
        for ct in cand_texts:
            vid = PromptRegistry.version_id(ct)
            if vid == champion.id:  # the current champion re-proposed — not a loser, skip quietly
                continue
            if vid in known_bad:  # oscillation guard — never re-try a known loser
                _log.info("round %d: skip known-bad candidate %s", r, vid)
                continue
            ev = await evaluate_prompt(target, ct, dev, narrator, search_judge)
            if _gates_hold(ev, champ_dev) and beats_champion(ev, champ_dev, margin=margin):
                winners.append((ev, ct, vid))
            else:
                known_bad.add(vid)
                _record(vid, ct, ev, None, "rejected", "dev_gate")
        if not winners:
            plateau += 1
            _log.info("round %d: no dev winner (plateau %d/%d)", r, plateau, patience)
            if plateau >= patience:
                stop = "plateau"
                break
            continue

        ev, ct, vid = max(winners, key=lambda x: x[0].mean)
        # verify the dev winner on the HELD-OUT set with the GOLD judge (reality check)
        gold = await evaluate_prompt(target, ct, holdout, narrator, gold_judge)
        search_gain = ev.mean - champ_dev.mean
        gold_gain = gold.mean - champ_gold.mean
        if (
            _gates_hold(gold, champ_gold)
            and gold.mean > champ_gold.mean
            and not is_reward_hacking(search_gain, gold_gain)
        ):
            champ_text, champ_dev, champ_gold = ct, ev, gold
            champion = Variant(id=vid, text=ct, gen=r, parent=champion.id)
            trajectory.append(
                {"id": champion.id, "dev": round(ev.mean, 3), "gold": round(gold.mean, 3),
                 "silence": round(gold.silence_rate, 3)}
            )
            plateau = 0
            _log.info(
                "round %d: promoted %s (dev %.3f gold %.3f)", r, champion.id, ev.mean, gold.mean
            )
            # MEMORY + VERSIONING: remember the win, store the version, and STAGE it as the CANARY
            # (not active). An offline-gold-gate win earns a LIVE trial on a traffic fraction; the
            # worker's canary monitor promotes it to active only if it also wins live, else rolls
            # back. This is the autonomous close: offline gate -> canary -> live monitor -> active.
            _record(vid, ct, ev, gold, "accepted", "promoted")
            if registry is not None:
                registry.save_version(target, tier, ct, version_id=vid)
                registry.set_canary(target, tier, vid)
        else:
            if not coverage_not_degraded(gold, champ_gold):
                stop = "coverage_regressed"
            elif is_reward_hacking(search_gain, gold_gain):
                stop = "reward_hacking"
            else:
                stop = "gold_gate_failed"
            known_bad.add(vid)
            _record(vid, ct, ev, gold, "rejected", stop)
            _log.info("round %d: dev winner rejected at gold gate (%s)", r, stop)
            break

    return OptimizeResult(
        target=target,
        tier=tier,
        baseline_text=baseline_text,
        champion=champion,
        improved=champion.id != "baseline",
        baseline_dev=base_dev,
        champion_dev=champ_dev.mean,
        baseline_gold=base_gold,
        champion_gold=champ_gold.mean,
        trajectory=trajectory,
        stop_reason=stop,
    )


@contextlib.contextmanager
def apply_config_patch(patch: dict):
    """Temporarily override settings knobs to evaluate an ENRICHMENT-CONFIG candidate (fix #3),
    reverting after — the loop can then propose "research harder" (e.g.
    ``{'fact_warm_tier_min': 'free', 'fact_warm_sig_min': 'LOW'}``) instead of only rewriting the
    narrator prompt. A config patch is versioned/rolled-back by the SAME registry as a prompt
    (store it under target='enrich_config', the version text being the JSON patch)."""
    from app.config import settings

    saved: dict = {}
    try:
        for k, v in patch.items():
            if hasattr(settings, k):
                saved[k] = getattr(settings, k)
                setattr(settings, k, v)
        yield
    finally:
        for k, v in saved.items():
            setattr(settings, k, v)


def check_and_rollback(
    registry: PromptRegistry,
    target: str,
    tier: str,
    *,
    live_score: float,
    promoted_score: float,
    margin: float = 0.02,
) -> str | None:
    """Live-monitoring rollback (the Phase-6 safety net). If the active version's LIVE
    (canary/prod) score regressed below what it was promoted at — by more than ``margin`` —
    revert the active pointer to the previous version and record a ``rolled_back`` experiment
    (which also marks the bad version known-bad, so it's never re-proposed). Returns the version
    rolled back TO, or None if the active version is holding up."""
    if live_score >= promoted_score - margin:
        return None
    active = registry.active_version(target, tier)
    reverted = registry.rollback(target, tier)
    if reverted is not None and active:
        registry.record_experiment(Experiment(
            id=active, target=target, tier=tier, verdict="rolled_back",
            gold=round(live_score, 4), stop_reason="live_regression",
        ))
        _log.warning(
            "ROLLBACK %s/%s: %s regressed live (%.3f < %.3f) -> reverted to %s",
            target, tier, active, live_score, promoted_score, reverted,
        )
    return reverted


def write_candidate(result: OptimizeResult, out_dir: str | Path) -> Path:
    """Persist the champion + evidence bundle for human review / the Phase 6 canary. Writes
    ``candidate.txt`` (the prompt) + ``evidence.json``. Does NOT touch the live prompt file."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / "candidate.txt").write_text(result.champion.text, encoding="utf-8")
    evidence = {k: v for k, v in asdict(result).items() if k not in ("baseline_text",)}
    evidence["champion"] = {"id": result.champion.id, "gen": result.champion.gen}
    (d / "evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return d
