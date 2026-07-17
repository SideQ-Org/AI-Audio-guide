"""Composite score + hard-gates + weight fitting (Block 4 Phase 3). Offline."""

from __future__ import annotations

from app.services.agent.interest_judge import AXES, JudgeVerdict, WalkVerdict
from app.services.agent.interest_metrics import BlurbMetrics, CorpusMetrics
from app.services.agent.interest_score import (
    FEATURES,
    composite,
    feature_vector,
    fit_weights,
    inverted_u,
    walk_coherence,
)


def _cm(**kw) -> CorpusMetrics:
    base = dict(
        distinct_1=1.0, distinct_2=1.0, distinct_3=1.0, self_repetition=0.0, silence_rate=0.0,
        object_repeat_rate=0.0, transition_rate=0.0, adjacent_cohesion=0.0, callback_rate=0.0,
    )
    base.update(kw)
    return CorpusMetrics(**base)


def test_walk_coherence_code_only_rewards_links():
    disjoint = walk_coherence(_cm())
    linked = walk_coherence(_cm(transition_rate=0.5, adjacent_cohesion=0.25, callback_rate=0.34))
    assert disjoint == 0.0
    assert linked > disjoint
    assert 0.0 <= linked <= 1.0


def test_walk_coherence_high_adjacency_is_penalised_as_repetition():
    thematic = walk_coherence(_cm(adjacent_cohesion=0.25))
    rewordy = walk_coherence(_cm(adjacent_cohesion=0.9))  # near-identical neighbours = repetition
    assert rewordy < thematic


def test_walk_coherence_judge_leads_when_present():
    cm = _cm(transition_rate=0.0, adjacent_cohesion=0.0, callback_rate=0.0)  # code says 0
    wv = WalkVerdict(rationale="", seamlessness=4, arc_coherence=4)          # judge says great
    blended = walk_coherence(cm, wv)
    assert blended >= 0.6 * wv.score  # judge (weight 0.6) dominates code-0
    assert walk_coherence(cm, None) == 0.0  # code-only path unaffected


def _bm(**kw) -> BlurbMetrics:
    base = dict(
        specificity=0.55, number_density=0.1, speakability=0.8,
        novelty=1.0, mtld=40.0, cliche_hits=0,
    )
    base.update(kw)
    return BlurbMetrics(**base)


def _verdict(**kw) -> JudgeVerdict:
    axes = {ax: 3 for ax in AXES}
    d = dict(rationale="", axes=axes, grounded=True, cliche=False, overall=4)
    d.update(kw)
    return JudgeVerdict(**d)


def test_inverted_u_rewards_middle():
    assert inverted_u(0.55) == 1.0            # at peak
    assert inverted_u(0.0) < 0.1              # generic extreme
    assert inverted_u(1.0) < 0.2              # junk-rare extreme
    assert inverted_u(0.55) > inverted_u(0.9) > inverted_u(1.0)


def test_composite_good_blurb_scores_positive():
    cs = composite(_bm(), _verdict())
    assert cs.passed
    assert cs.score > 0.4
    assert cs.score == cs.interest  # all gates pass -> no discount


def test_cliche_gate_zeroes_score():
    # code cliché hit
    assert composite(_bm(cliche_hits=2), _verdict()).score == 0.0
    # judge cliché flag
    assert composite(_bm(), _verdict(cliche=True)).score == 0.0


def test_grounded_gate_zeroes_score():
    cs = composite(_bm(), _verdict(grounded=False, overall=4))
    assert not cs.passed
    assert cs.score == 0.0


def test_novelty_gate_zeroes_near_duplicates():
    cs = composite(_bm(novelty=0.0), _verdict())
    assert cs.gates["novel"] is False
    assert cs.score == 0.0


def test_feature_vector_with_and_without_judge():
    fv_code = feature_vector(_bm())
    assert fv_code["hook"] == 0.0            # no judge -> semantic axes 0
    assert 0.0 <= fv_code["specificity"] <= 1.0
    fv_judge = feature_vector(_bm(), _verdict(axes={ax: 4 for ax in AXES}))
    assert fv_judge["hook"] == 1.0           # judge axis 4/4
    assert set(fv_judge) == set(FEATURES)


def test_fit_weights_falls_back_when_too_little_data():
    from app.services.agent.interest_score import DEFAULT_WEIGHTS
    assert fit_weights([]) == DEFAULT_WEIGHTS


def test_fit_weights_learns_dominant_feature():
    # Synthetic: human label == the 'novelty' feature. Fitted weights should make novelty
    # the strongest predictor -> a high-novelty vector outranks a low-novelty one.
    rows = []
    for i in range(40):
        nov = (i % 5) / 4.0
        fv = {f: 0.2 for f in FEATURES}
        fv["novelty"] = nov
        rows.append((fv, nov))
    w = fit_weights(rows)
    assert set(w) == set(FEATURES)
    hi = sum(w[f] * (1.0 if f == "novelty" else 0.2) for f in FEATURES)
    lo = sum(w[f] * (0.0 if f == "novelty" else 0.2) for f in FEATURES)
    assert hi > lo  # learned to rank high-novelty above low-novelty
