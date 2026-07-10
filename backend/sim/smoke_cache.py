"""Probe prompt caching: call the same role with an identical system prefix
several times and watch the per-call cached_tokens / cost from the provider."""

from __future__ import annotations

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from app.config import settings
from app.services.agent.prompts import system_for
from app.services.llm.client import METER, OpenAICompatLLM
from app.services.llm.router import Role


async def main() -> None:
    print(f"prompt_cache={settings.openai_prompt_cache} model={settings.openai_model}")
    system = system_for(Role.NARRATOR, settings.default_language)
    print(f"system prefix ~{len(system)} chars")
    llm = OpenAICompatLLM()
    for i in range(5):
        await llm.complete_text(
            Role.NARRATOR,
            system,
            f"Поздоровайся с прохожим (вариант {i}).",
            max_tokens=200,
        )
    await llm.aclose()
    print(
        f"\nTOTAL: {METER.calls} calls, in={METER.tok_in} out={METER.tok_out} "
        f"cached={METER.tok_cached}, cost=${METER.cost_usd:.5f}"
    )


if __name__ == "__main__":
    asyncio.run(main())
