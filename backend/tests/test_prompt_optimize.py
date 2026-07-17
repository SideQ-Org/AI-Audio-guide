"""Self-improvement loop (Block 4 Phase 5). Fully offline — fake narrator/judges/proposer
exercise the gate / stop / reward-hacking logic deterministically. No network."""

from __future__ import annotations

import asyncio
import re

from app.services.agent.interest_judge import AXES, JudgeVerdict
from app.services.agent.prompts import active_overrides, clear_prompt_overrides
from app.services.quality.registry import Experiment, PromptRegistry
from app.shared.schemas import GeoPoint, Place, Significance
from sim.prompt_optimize import (
    EvalItem,
    Proposer,
    SplitEval,
    beats_champion,
    check_and_rollback,
    coherence_not_degraded,
    coverage_not_degraded,
    evaluate_prompt,
    gates_not_degraded,
    is_reward_hacking,
    optimize,
    write_candidate,
)
from sim.prompt_optimize import _gates_hold as gates_hold


# --- fakes ----------------------------------------------------------------- #
def _item(name: str, facts: str | None) -> EvalItem:
    from app.shared.schemas import NarratorInput
    inp = NarratorInput(
        place=Place(id=name, name=name, category="x", location=GeoPoint(lat=55.0, lon=37.0)),
        significance=Significance.MEDIUM,
        distance_m=30.0,
        facts=facts,
        language="ru",
    )
    return EvalItem(inp=inp, facts=facts)


class FakeNarrator:
    """Output depends on the ACTIVE prompt override. With facts: a grounded blurb. Without facts:
    FATAL ⇒ [SILENCE] (the degenerate 'go quiet' fix — grounded but kills coverage);
    NAMEONLY ⇒ names the visible object (grounded AND non-silent — the good direction);
    HACK ⇒ flashy dated claim tagged HACKSAFE (fools a lenient judge); else ⇒ plain fabrication."""

    async def narrate(self, inp):
        ov = active_overrides().get("narrator", "")
        if inp.facts:
            return f"{inp.place.name}: {inp.facts}"
        if "FATAL" in ov:
            return "[SILENCE]"
        if "NAMEONLY" in ov:
            return f"Проходишь мимо: {inp.place.name}."
        if "HACK" in ov:
            return f"{inp.place.name} HACKSAFE — построили в тридцатых."
        return f"{inp.place.name} построили в тридцатых годах прошлого века."


class _Judge:
    def __init__(self, lenient: bool = False):
        self.lenient = lenient

    async def score(self, text, *, facts=None, language="ru", tier=None):
        silence = text.strip() in ("[SILENCE]", "")
        dated = bool(re.search(r"тридцат|1[5-9]\d\d|20\d\d", text))
        grounded = bool(facts) or silence or (self.lenient and "HACKSAFE" in text) or not dated
        overall = 1 if silence else (3 if grounded else 1)
        return JudgeVerdict("", {ax: overall for ax in AXES}, grounded, False, overall)


class _Proposer(Proposer):
    """Returns fixed candidate texts (no LLM)."""
    def __init__(self, cands):
        self._cands = cands

    async def propose(self, current, failure_analysis, trajectory, *, n):
        return self._cands[:n]


def _run(coro):
    try:
        return asyncio.run(coro)
    finally:
        clear_prompt_overrides()


# --- coherence objective (walk-aware) -------------------------------------- #
def _sv(coherence: float, *, mean: float = 0.5) -> SplitEval:
    return SplitEval(n=2, mean=mean, grounded_rate=1.0, cliche_rate=0.0, coherence=coherence)


def test_coherence_not_degraded_tolerates_noise_but_rejects_drops():
    champ = _sv(0.5)
    assert coherence_not_degraded(_sv(0.6), champ)        # improvement
    assert coherence_not_degraded(_sv(0.47), champ)       # within eps 0.05 (noise)
    assert not coherence_not_degraded(_sv(0.3), champ)    # real regression


def test_gates_hold_rejects_a_disjointness_regression():
    # a candidate that improves the mean but makes walks MORE disjoint must not pass the gate
    champ = _sv(0.5, mean=0.5)
    cand = _sv(0.2, mean=0.9)
    assert not gates_hold(cand, champ)
    assert gates_hold(_sv(0.5, mean=0.9), champ)          # same coherence, higher mean → ok


def _witem(name, walk_id, seq, category="place_of_worship"):
    from app.shared.schemas import NarratorInput
    inp = NarratorInput(
        place=Place(id=name, name=name, category=category, location=GeoPoint(lat=55.0, lon=37.0)),
        significance=Significance.MEDIUM, distance_m=30.0, facts="факт", language="ru",
    )
    return EvalItem(inp=inp, facts="факт", walk_id=walk_id, seq=seq, category=category)


class _ConnNarrator:
    """CONNECTED override ⇒ blurbs that open with a connective + call back; else plain cards."""
    async def narrate(self, inp):
        ov = active_overrides().get("narrator", "")
        if "CONNECTED" in ov:
            return f"А чуть дальше, как и та церковь, стоит {inp.place.name}."
        return f"{inp.place.name}."


class _OkJudge:
    async def score(self, text, *, facts=None, language="ru", tier=None):
        return JudgeVerdict("", {ax: 3 for ax in AXES}, True, False, 3)


def test_evaluate_prompt_walk_grouping_scores_coherence():
    items = [_witem("часовня", "w1", 0), _witem("храм", "w1", 1)]  # one walk, ordered
    conn = _run(evaluate_prompt("narrator", "CONNECTED", items, _ConnNarrator(), _OkJudge()))
    plain = _run(evaluate_prompt("narrator", "PLAIN", items, _ConnNarrator(), _OkJudge()))
    assert conn.coherence > plain.coherence
    # ungrouped items (default walk_id None → singleton walks) ⇒ neutral coherence, no crash
    solo = [_witem("a", None, 0), _witem("b", None, 0)]
    solo_ev = _run(evaluate_prompt("narrator", "PLAIN", solo, _ConnNarrator(), _OkJudge()))
    assert solo_ev.coherence == 0.0


# --- pure gate logic ------------------------------------------------------- #
def test_beats_champion_needs_ci_separation():
    champ = SplitEval(n=6, mean=0.5, grounded_rate=1, cliche_rate=0, scores=[0.5] * 6)
    better = SplitEval(n=6, mean=0.7, grounded_rate=1, cliche_rate=0, scores=[0.7] * 6)
    noisy = SplitEval(n=6, mean=0.52, grounded_rate=1, cliche_rate=0,
                      scores=[0.9, 0.1, 0.9, 0.1, 0.9, 0.1])
    assert beats_champion(better, champ, margin=0.02) is True
    assert beats_champion(noisy, champ, margin=0.02) is False  # gain within noise


def test_gates_not_degraded_blocks_fabrication_trade():
    champ = SplitEval(n=4, mean=0.5, grounded_rate=1.0, cliche_rate=0.0)
    worse = SplitEval(n=4, mean=0.9, grounded_rate=0.75, cliche_rate=0.0)
    assert gates_not_degraded(worse, champ) is False  # can't buy interest with fabrication
    assert gates_not_degraded(champ, champ) is True


def test_reward_hacking_detector():
    assert is_reward_hacking(search_gain=0.1, gold_gain=0.0) is True
    assert is_reward_hacking(search_gain=0.1, gold_gain=0.08) is False


def test_evaluate_prompt_survives_none_narration():
    class _NoneNarrator:
        async def narrate(self, inp):
            return None  # narrator can return None/[SILENCE] — must not crash the eval
    items = [_item("A", "факт"), _item("B", None)]
    ev = _run(evaluate_prompt("narrator", "p", items, _NoneNarrator(), _Judge()))
    assert ev.n == 2
    assert ev.silence_rate == 1.0  # both None -> silent


def test_coverage_gate_blocks_going_quiet():
    champ = SplitEval(n=4, mean=0.5, grounded_rate=1.0, cliche_rate=0.0, silence_rate=0.0)
    quieter = SplitEval(n=4, mean=0.9, grounded_rate=1.0, cliche_rate=0.0, silence_rate=0.5)
    # even with higher mean + perfect grounding, more silence is NOT allowed (the fatalistic fix)
    assert coverage_not_degraded(quieter, champ) is False
    assert coverage_not_degraded(champ, champ) is True


# --- evaluate_prompt round-trips the override ------------------------------ #
def test_evaluate_prompt_nameonly_beats_fabricating_keeps_coverage():
    items = [_item("A", "факт про А"), _item("B", None), _item("C", None), _item("D", None)]
    n = FakeNarrator()
    j = _Judge()
    lax = _run(evaluate_prompt("narrator", "обычный промпт", items, n, j))
    good = _run(evaluate_prompt("narrator", "промпт NAMEONLY: назови видимое", items, n, j))
    fatal = _run(evaluate_prompt("narrator", "промпт FATAL: без фактов [SILENCE]", items, n, j))
    assert good.grounded_rate > lax.grounded_rate          # fabrication caught
    assert good.mean > lax.mean                             # naming beats fabrication
    assert good.silence_rate == 0.0                         # and it stays audible (coverage)
    assert fatal.silence_rate > 0.0                         # the fatalistic fix goes quiet
    assert active_overrides() == {}                         # override cleared after eval


# --- the loop -------------------------------------------------------------- #
def test_optimize_promotes_a_genuinely_better_prompt():
    dev = [_item(f"d{i}", None) for i in range(6)] + [_item("df", "факт")]
    holdout = [_item(f"h{i}", None) for i in range(6)] + [_item("hf", "факт")]
    res = _run(optimize(
        "narrator", "обычный промпт (фабрикует)", dev, holdout,
        narrator=FakeNarrator(), search_judge=_Judge(), gold_judge=_Judge(),
        proposer=_Proposer(["промпт NAMEONLY: назови видимое, не выдумывай"]),
        rounds=3, n_candidates=1, margin=0.02,
    ))
    assert res.improved is True
    assert res.champion.id != "baseline"
    assert res.champion_gold > res.baseline_gold          # improvement transfers to held-out
    assert "NAMEONLY" in res.champion.text


def test_optimize_rejects_fatalistic_silence_fix():
    # the objective fix: a candidate that trades fabrication for SILENCE must NOT be promoted,
    # even though it perfects grounding — it kills coverage (the product failure the user flagged).
    dev = [_item(f"d{i}", None) for i in range(6)]
    holdout = [_item(f"h{i}", None) for i in range(6)]
    res = _run(optimize(
        "narrator", "обычный промпт (фабрикует)", dev, holdout,
        narrator=FakeNarrator(), search_judge=_Judge(), gold_judge=_Judge(),
        proposer=_Proposer(["промпт FATAL: нет фактов — [SILENCE]"]),
        rounds=2, n_candidates=1, margin=0.02,
    ))
    assert res.improved is False              # the coverage gate blocks the fatalistic fix
    assert res.champion.id == "baseline"


def test_optimize_prefers_coverage_over_silence():
    # offered BOTH a fatalistic-silence and a name-the-object candidate, the loop picks coverage.
    dev = [_item(f"d{i}", None) for i in range(6)] + [_item("df", "факт")]
    holdout = [_item(f"h{i}", None) for i in range(6)] + [_item("hf", "факт")]
    res = _run(optimize(
        "narrator", "обычный промпт (фабрикует)", dev, holdout,
        narrator=FakeNarrator(), search_judge=_Judge(), gold_judge=_Judge(),
        proposer=_Proposer([
            "промпт FATAL: нет фактов — [SILENCE]",
            "промпт NAMEONLY: назови видимое, не выдумывай",
        ]),
        rounds=2, n_candidates=2, margin=0.02,
    ))
    assert res.improved is True
    assert "NAMEONLY" in res.champion.text    # coverage-preserving candidate wins


def test_optimize_rejects_reward_hacker_at_gold_gate():
    dev = [_item(f"d{i}", None) for i in range(6)]
    holdout = [_item(f"h{i}", None) for i in range(6)]
    # search judge is LENIENT (fooled by HACKSAFE), gold judge is STRICT -> dev rises, gold doesn't
    res = _run(optimize(
        "narrator", "обычный промпт", dev, holdout,
        narrator=FakeNarrator(),
        search_judge=_Judge(lenient=True), gold_judge=_Judge(lenient=False),
        proposer=_Proposer(["промпт HACK: звучи уверенно"]),
        rounds=2, n_candidates=1, margin=0.02,
    ))
    assert res.improved is False                           # the hacker never promotes
    assert res.stop_reason in ("reward_hacking", "gold_gate_failed")


def test_optimize_with_registry_remembers_and_activates(tmp_path):
    reg = PromptRegistry(tmp_path / "reg")
    dev = [_item(f"d{i}", None) for i in range(6)] + [_item("df", "факт")]
    holdout = [_item(f"h{i}", None) for i in range(6)] + [_item("hf", "факт")]
    res = _run(optimize(
        "narrator", "обычный промпт (фабрикует)", dev, holdout,
        narrator=FakeNarrator(), search_judge=_Judge(), gold_judge=_Judge(),
        proposer=_Proposer(["промпт NAMEONLY: назови видимое"]),
        registry=reg, tier="free", rounds=2, n_candidates=1,
    ))
    assert res.improved is True
    # an offline win is STAGED AS CANARY (not active) — live monitor promotes it if it wins live too
    assert reg.canary_version("narrator", "free") == res.champion.id
    assert reg.canary_text("narrator", "free") == res.champion.text
    assert reg.active_version("narrator", "free") != res.champion.id   # NOT full-rolled-out yet
    past = reg.past_attempts("narrator", "free")
    assert any(p["verdict"] == "accepted" and p["id"] == res.champion.id for p in past)


def test_optimize_skips_known_bad_candidate(tmp_path):
    reg = PromptRegistry(tmp_path / "reg")
    baseline = "обычный промпт (фабрикует)"
    cand = "промпт NAMEONLY: назови видимое"
    reg.ensure_baseline("narrator", "free", baseline)
    # pre-mark the only candidate as a known loser -> the loop must not re-try it
    reg.record_experiment(Experiment(
        id=PromptRegistry.version_id(cand), target="narrator", tier="free", verdict="rejected",
    ))
    res = _run(optimize(
        "narrator", baseline, [_item(f"d{i}", None) for i in range(6)],
        [_item(f"h{i}", None) for i in range(6)],
        narrator=FakeNarrator(), search_judge=_Judge(), gold_judge=_Judge(),
        proposer=_Proposer([cand]), registry=reg, tier="free", rounds=2, n_candidates=1,
    ))
    assert res.improved is False                 # the known-bad candidate was skipped, none left


def test_check_and_rollback_reverts_on_live_regression(tmp_path):
    reg = PromptRegistry(tmp_path / "reg")
    base = reg.ensure_baseline("narrator", "free", "baseline prompt")
    v1 = reg.save_version("narrator", "free", "promoted candidate")
    reg.set_active("narrator", "free", v1)
    # live score regressed well below what it was promoted at -> auto-rollback to baseline
    reverted = check_and_rollback(
        reg, "narrator", "free", live_score=0.2, promoted_score=0.6, margin=0.02,
    )
    assert reverted == base
    assert reg.active_version("narrator", "free") == base
    assert v1 in reg.known_bad("narrator", "free")     # the bad version won't be re-proposed
    # holding up -> no rollback
    reg.set_active("narrator", "free", v1)
    assert check_and_rollback(
        reg, "narrator", "free", live_score=0.59, promoted_score=0.6,
    ) is None


def test_write_candidate(tmp_path):
    res = _run(optimize(
        "narrator", "обычный промпт (фабрикует)",
        [_item(f"d{i}", None) for i in range(6)] + [_item("df", "факт")],
        [_item(f"h{i}", None) for i in range(6)] + [_item("hf", "факт")],
        narrator=FakeNarrator(), search_judge=_Judge(), gold_judge=_Judge(),
        proposer=_Proposer(["промпт NAMEONLY: назови видимое, не выдумывай"]),
        rounds=2, n_candidates=1,
    ))
    d = write_candidate(res, tmp_path / "cand")
    assert (d / "candidate.txt").exists()
    assert (d / "evidence.json").exists()
    assert "NAMEONLY" in (d / "candidate.txt").read_text(encoding="utf-8")
