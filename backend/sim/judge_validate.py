"""Run the judge against the gold-standard validation set and report where it disagrees
with reality (Block 4). Each disagreement is a bug in the judge (rubric), to be fixed and
re-run — the iterate-until-correct loop for making the judge a trustworthy quality standard.

    python -m sim.judge_validate                 # uses openai_model_judge / base model
    python -m sim.judge_validate --model qwen/qwen3-max
"""

from __future__ import annotations

import argparse
import asyncio

from app.services.agent.interest_judge import LLMJudge
from app.services.llm.client import OpenAICompatLLM
from sim.judge_validation import CASES


def _interesting_ok(expected: str, overall: int) -> bool | None:
    if expected == "high":
        return overall >= 3
    if expected == "low":
        return overall <= 1
    return None  # "any" — not checked


async def run(model: str | None) -> None:
    llm = OpenAICompatLLM(default_model=model) if model else OpenAICompatLLM()
    judge = LLMJudge(llm)
    g_ok = c_ok = i_ok = i_tot = 0
    disagreements: list[str] = []

    for k, case in enumerate(CASES):
        try:
            v = await judge.score(case.blurb, facts=case.facts)
        except Exception as e:  # noqa: BLE001
            print(f"  [{k}] judge error: {e}")
            continue
        gok = v.grounded == case.grounded
        cok = v.cliche == case.cliche
        iok = _interesting_ok(case.interesting, v.overall)
        g_ok += gok
        c_ok += cok
        if iok is not None:
            i_tot += 1
            i_ok += iok
        flags = []
        if not gok:
            flags.append(f"grounded={v.grounded} exp={case.grounded}")
        if not cok:
            flags.append(f"cliche={v.cliche} exp={case.cliche}")
        if iok is False:
            flags.append(f"interest overall={v.overall} exp={case.interesting}")
        if flags:
            disagreements.append(
                f"  [{k}] {' | '.join(flags)}\n       «{case.blurb[:70]}…»\n       ({case.note})"
            )

    n = len(CASES)
    print(f"\nJUDGE VALIDATION ({model or 'default'}) — {n} cases")
    print(f"  grounded : {g_ok}/{n}  ({100*g_ok/n:.0f}%)")
    print(f"  cliche   : {c_ok}/{n}  ({100*c_ok/n:.0f}%)")
    print(f"  interest : {i_ok}/{i_tot}  ({100*i_ok/max(i_tot,1):.0f}%)")
    if disagreements:
        print("\n  DISAGREEMENTS (judge wrong -> fix rubric):")
        print("\n".join(disagreements))
    else:
        print("\n  ✓ judge matches ground truth on every axis")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None)
    args = ap.parse_args()
    asyncio.run(run(args.model))


if __name__ == "__main__":
    main()
