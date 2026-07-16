"""Judge + human-calibration tests (Block 4 Phase 2). Offline via FakeLLM — no network."""

from __future__ import annotations

import asyncio
import json

from app.services.agent.interest_judge import AXES, JudgeVerdict, LLMJudge
from app.services.llm.client import FakeLLM
from sim.human_calib import (
    binarize,
    cohens_kappa,
    percent_agreement,
    score_labeled,
)


def _verdict(**kw) -> dict:
    base = {ax: 3 for ax in AXES}
    base.update({"rationale": "ок", "grounded": True, "cliche": False, "overall": 4})
    base.update(kw)
    return base


def test_score_parses_verdict_and_axes():
    judge = LLMJudge(FakeLLM(json_response=_verdict()))
    v = asyncio.run(judge.score("Маяк построили в 1910 году.", facts="Маяк, 1910."))
    assert isinstance(v, JudgeVerdict)
    assert set(v.axes) == set(AXES)
    assert v.overall == 4
    assert v.gates_ok
    assert v.score == 1.0


def test_hard_gates_dominate_score():
    # ungrounded: high overall must NOT translate to a high anchor score
    ungrounded = LLMJudge(FakeLLM(json_response=_verdict(grounded=False, overall=4)))
    v = asyncio.run(ungrounded.score("выдуманная история", facts=None))
    assert not v.gates_ok
    assert v.score <= 0.25

    cliched = LLMJudge(FakeLLM(json_response=_verdict(cliche=True, overall=4)))
    v2 = asyncio.run(cliched.score("время застыло"))
    assert v2.score <= 0.25


def test_compare_order_swap_consistent_winner():
    # A judge that always prefers the blurb mentioning "маяк", regardless of A/B position.
    def pick(role, system, user):
        data = json.loads(user)
        a, b = data.get("A", ""), data.get("B", "")
        if "маяк" in a and "маяк" not in b:
            return {"rationale": "", "winner": "A"}
        if "маяк" in b and "маяк" not in a:
            return {"rationale": "", "winner": "B"}
        return {"rationale": "", "winner": "tie"}

    judge = LLMJudge(FakeLLM(json_response=pick))
    winner = asyncio.run(judge.compare("старый маяк на мысу", "парк, тут гуляют"))
    assert winner == "A"  # consistent across both presentation orders


def test_compare_position_biased_judge_resolves_to_tie():
    # A judge that ALWAYS says the first-shown wins -> the two orders disagree -> tie.
    judge = LLMJudge(FakeLLM(json_response={"rationale": "", "winner": "A"}))
    assert asyncio.run(judge.compare("текст один", "текст два")) == "tie"


def test_percent_agreement_and_kappa():
    assert percent_agreement([1, 0, 1, 1], [1, 0, 1, 1]) == 1.0
    assert percent_agreement([1, 1, 1, 1], [0, 0, 0, 0]) == 0.0
    # perfect agreement -> kappa 1.0
    assert cohens_kappa([1, 0, 1, 0], [1, 0, 1, 0]) == 1.0
    # chance-level agreement -> kappa near 0
    k = cohens_kappa([1, 0, 1, 0], [0, 1, 0, 1])
    assert k < 0.0  # worse than chance (systematic disagreement)
    # both raters constant & identical -> defined as 1.0 (no variance, no disagreement)
    assert cohens_kappa([1, 1, 1], [1, 1, 1]) == 1.0


def test_binarize_threshold():
    assert binarize(4) == 1
    assert binarize(3) == 1
    assert binarize(2) == 0


def test_score_labeled_reports_both_metrics():
    rows = [
        {"human": 4, "judge": 4},
        {"human": 1, "judge": 2},
        {"human": 3, "judge": 3},
        {"human": 0, "judge": 1},
        {"human": 2, "judge": None},  # incomplete -> ignored
    ]
    stats = score_labeled(rows, threshold=3)
    assert stats["n"] == 4  # the incomplete row is dropped
    assert "exact_kappa" in stats and "binary_kappa" in stats
    # human/judge binarize identically here (4,1,3,0 vs 4,2,3,1 @thr3) -> perfect binary agreement
    assert stats["binary_agreement"] == 1.0
