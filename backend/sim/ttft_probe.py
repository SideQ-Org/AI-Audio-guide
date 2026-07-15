#!/usr/bin/env python3
"""ttft_probe.py — measure real time-to-first-token for a shortlist of models against any
OpenAI-compatible /chat/completions endpoint (OpenRouter, OpenAI, DeepSeek, local LM Studio).

Dependency-light: just `httpx` (already a backend dep).

    export LLM_BASE_URL=https://openrouter.ai/api/v1
    export LLM_API_KEY=sk-or-...
    python -m sim.ttft_probe

RUN IT FROM THE PROD REGION/HOST so the geoblock + real network path are captured (a laptop
elsewhere won't reproduce the 403s). Edit MODELS below. For OpenRouter you can pin a provider
(e.g. Groq/Cerebras) via the per-model "provider" field — that's what proves reachability past
the OpenAI/Google/Anthropic geoblock. See docs/MODEL_LATENCY_RESEARCH.md for the rationale.
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import time

import httpx

BASE_URL = os.environ.get("LLM_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
API_KEY = os.environ.get("LLM_API_KEY", "")
RUNS = int(os.environ.get("RUNS", "3"))
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "40"))
TIMEOUT_S = float(os.environ.get("TIMEOUT_S", "30"))

# A short, realistic barge-in style question (Russian, like the product).
PROMPT = "Что это за здание слева от меня? Ответь одним коротким предложением."

# Each entry: model id + optional OpenRouter provider pin (order/allow_fallbacks).
# The `provider` block is OpenRouter-specific and ignored by plain OpenAI/DeepSeek endpoints.
MODELS = [
    {"model": "meta-llama/llama-3.3-70b-instruct",
     "provider": {"order": ["Groq"], "allow_fallbacks": False}},
    {"model": "meta-llama/llama-3.3-70b-instruct",
     "provider": {"order": ["Cerebras"], "allow_fallbacks": False}},
    {"model": "qwen/qwen3-32b",
     "provider": {"order": ["Cerebras"], "allow_fallbacks": False}},
    {"model": "moonshotai/kimi-k2",
     "provider": {"order": ["Groq"], "allow_fallbacks": False}},
    {"model": "deepseek/deepseek-chat"},  # current prod answer model (reachable)
    # {"model": "openai/gpt-4o-mini"},    # control: expect 403/timeout from the blocked region
]


async def probe_once(client: httpx.AsyncClient, entry: dict) -> dict:
    body = {
        "model": entry["model"],
        "messages": [{"role": "user", "content": PROMPT}],
        "stream": True,
        "max_tokens": MAX_TOKENS,
        "temperature": 0.3,
    }
    if "provider" in entry:
        body["provider"] = entry["provider"]  # OpenRouter provider routing

    t0 = time.perf_counter()
    ttft: float | None = None
    ntok = 0
    text_len = 0
    try:
        async with client.stream(
            "POST", f"{BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json=body, timeout=TIMEOUT_S,
        ) as resp:
            if resp.status_code != 200:
                detail = (await resp.aread()).decode("utf-8", "replace")[:200]
                return {"ok": False, "err": f"HTTP {resp.status_code}: {detail}"}
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                delta = (chunk.get("choices") or [{}])[0].get("delta", {})
                piece = delta.get("content") or ""
                if piece:
                    if ttft is None:
                        ttft = time.perf_counter() - t0
                    ntok += 1
                    text_len += len(piece)
        total = time.perf_counter() - t0
        if ttft is None:
            return {"ok": False, "err": "no content tokens"}
        tps = (ntok - 1) / (total - ttft) if total > ttft and ntok > 1 else 0.0
        return {"ok": True, "ttft": ttft, "total": total, "tps": tps, "chars": text_len}
    except (httpx.TimeoutException, httpx.HTTPError) as e:
        return {"ok": False, "err": f"{type(e).__name__}: {e}"}


async def main() -> None:
    if not API_KEY:
        raise SystemExit("Set LLM_API_KEY (and optionally LLM_BASE_URL).")
    print(f"Endpoint: {BASE_URL}   runs/model: {RUNS}   max_tokens: {MAX_TOKENS}\n")
    async with httpx.AsyncClient() as client:
        for entry in MODELS:
            label = entry["model"]
            if "provider" in entry:
                label += f"  [{','.join(entry['provider'].get('order', []))}]"
            ttfts, totals, tpss, err = [], [], [], None
            for _ in range(RUNS):
                r = await probe_once(client, entry)
                if r["ok"]:
                    ttfts.append(r["ttft"])
                    totals.append(r["total"])
                    tpss.append(r["tps"])
                else:
                    err = r["err"]
                await asyncio.sleep(0.5)  # don't hammer
            if ttfts:
                print(f"{label:52s}  TTFT {statistics.median(ttfts):5.2f}s  "
                      f"total {statistics.median(totals):5.2f}s  "
                      f"~{statistics.median(tpss):4.0f} tok/s  (n={len(ttfts)}/{RUNS})")
            else:
                print(f"{label:52s}  FAILED — {err}")


if __name__ == "__main__":
    asyncio.run(main())
