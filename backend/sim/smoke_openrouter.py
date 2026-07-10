"""Minimal smoke test: confirm the OpenAI-compatible client reaches the configured
provider (OpenRouter -> Gemini), that strict-JSON works, and that the token meter logs.

Run:  python -m sim.smoke_openrouter
"""

from __future__ import annotations

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8")

from app.config import settings
from app.services.llm.client import METER, OpenAICompatLLM
from app.services.llm.router import Role


async def main() -> None:
    print(f"base_url={settings.openai_base_url}  model={settings.openai_model}")
    llm = OpenAICompatLLM()

    text = await llm.complete_text(
        Role.NARRATOR,
        "Ты дружелюбный аудиогид. Отвечай одним коротким предложением.",
        "Поздоровайся с гуляющим человеком.",
        max_tokens=512,
    )
    print("\n[NARRATOR text]:", text)

    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "greeting": {"type": "string"},
            "ok": {"type": "boolean"},
        },
        "required": ["greeting", "ok"],
    }
    obj = await llm.complete_json(
        Role.SCORER,
        "Верни JSON по схеме.",
        "Поле greeting — слово 'привет', ok — true.",
        schema,
        max_tokens=512,
    )
    print("[SCORER json]:", obj)

    await llm.aclose()
    print(
        f"\nMETER: {METER.calls} calls, in={METER.tok_in} out={METER.tok_out} "
        f"tok, ~${METER.cost_usd:.5f}"
    )


if __name__ == "__main__":
    asyncio.run(main())
