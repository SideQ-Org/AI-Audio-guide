# Block 4 — Interestingness metric + self-improvement loop

The audio guide **measures the quality of its own narration as an objective number** and can
**improve its own prompts** — safely, in a separate, removable container, with a validated judge,
a held-out gate, canary rollout, and auto-rollback. This is the authoritative doc: architecture,
the evaluator, the autonomous loop, every config knob, and the operations runbook.

Related: `Блок4_Интересность_метрики_и_луп_самоулучшения.md` (original design/research, Russian),
`BLOCK4_FIXER_HARDENING.md` (failure-mode model + deploy runbook), `README.md` §Quality.

---

## 1. What it does (one paragraph)

After each walk, a **quality worker** (separate container) scores every narrated blurb with a
reference-free **metrics panel** + an **LLM judge** and writes a per-walk `walk_quality` row
(interestingness score, hard-gate results, failure taxonomy, worst blurbs) — a followable trace of
what the guide is doing well and badly. An offline **optimizer** can rewrite a system prompt against
that signal (OPRO + TextGrad), gated by a held-out **gold judge** + hard-gates. A validated
candidate is staged as a **canary** for a fraction of live sessions; the worker compares canary vs
control and **auto-promotes on a live win or auto-rolls-back on a regression** — no human. Every
version is immutable and reversible; every attempt is remembered.

---

## 2. Architecture (data flow)

```
  ┌── backend (live tour) ───────────────────────────────────────────────┐
  │  narrate → capture (flag-gated, best-effort):                         │
  │    narration_samples (FACTS + context → narration)  interest_signals  │
  │  per-session CANARY prompt override (Phase 6, dormant unless enabled) │
  └──────────────────────────────────────────────────────────────────────┘
                    │ Postgres (walks / walk_events / narration_samples / walk_quality)
                    ▼
  ┌── quality-worker (separate container) ───────────────────────────────┐
  │  sweep finished walks → score each blurb:                            │
  │    interest_metrics (panel) + interest_judge (LLM) → interest_score  │
  │    → walk_quality row + failure taxonomy + decision log              │
  │  canary monitor: canary vs control → promote / rollback              │
  └──────────────────────────────────────────────────────────────────────┘
                    ▲ reads corpus                    │ set_active / rollback
                    │                                 ▼
  ┌── optimizer (on-demand, prod host) ──────────────────────────────────┐
  │  OPRO+TextGrad propose → re-generate → search judge (dev) → GOLD      │
  │  judge (held-out) → PromptRegistry: version + memory + set_canary     │
  └──────────────────────────────────────────────────────────────────────┘

  PromptRegistry (shared volume /registry): versions/  ledger.jsonl  active.json{active,baseline,canary,history}
```

The autonomous cycle: **measure** (worker) → **optimize** (offline gold gate) → **canary** (live
fraction) → **monitor** (worker) → **promote / rollback**.

---

## 3. Components

| Piece | File | Role |
|---|---|---|
| Instrumentation | `accounts/history.py`, `models.py`, migrations `0008/0009/0010`, `db/rls.sql` | capture `narration_samples` (FACTS+context→narration) + `interest_signals`; per-tier |
| Metrics panel | `agent/interest_metrics.py` | reference-free, stdlib, 8-lang: distinct-n, self-repetition, MTLD, NIDF, number-density (incl. dates-as-words), speakability, novelty, cliché, object-repeat |
| LLM judge | `agent/interest_judge.py` + `prompts/judge.txt` (role `JUDGE`) | G-Eval rubric → 8 axes + hard-gates; pointwise + pairwise; **different model family than the generator**; **temp 0** |
| Composite | `agent/interest_score.py` | `score = interestingness · Π(hard_gates)`; gates: grounded, cliché, non-repeat, **coverage/silence** |
| Quality worker | `app/services/quality/` (`worker.py`, `__main__.py`, `qlog.py`) | sweep + score + `walk_quality` + decision log + canary monitor |
| Optimizer | `sim/prompt_optimize.py` + `prompts/optimizer.txt` (role `OPTIMIZER`) | OPRO+TextGrad, dev/holdout, gold gate, coverage gate, `apply_config_patch` (fix #3) |
| Registry | `app/services/quality/registry.py` | immutable versions, experiment ledger (memory), active + **canary** pointers, rollback, kill-switch |
| Canary | `app/services/quality/canary.py` | sid-hash membership, `canary_prompt_for`, `monitor_and_rollback` |
| Judge validation | `sim/judge_validation.py` + `sim/judge_validate.py` | 12 web-verified gold cases; iterate the rubric to correctness |

DB tables: `narration_samples`, `interest_signals`, `walk_quality` (all per-`tier`, RLS, service-role writes).

---

## 4. The judge — a trustworthy quality standard

The judge is the anchor everything optimizes against, so it MUST be right. Two hard-won properties:

- **FACTS-absent ⇒ judge by PLAUSIBILITY + world knowledge, not "no FACTS string = fabrication".**
  The original rubric marked every unsourced historical claim as fabrication. On real walks that
  called TRUE facts (Долгопрудный «Волга» stratostat / ДКБА, Аллея Космонавтов 1967 — web-verified)
  "fabricated" → the "92% fabrication" was largely a **measurement artifact** (the worker fed the
  judge `FACTS=None` on `walk_events`-fallback walks). The rubric now judges plausibility;
  anachronisms / implausible specifics still fail. Effect on the same walks: avg grounded **0.36 → 0.90**.
- **Deterministic.** `JUDGE` routes to `openai_judge_temperature = 0.0` — a gold standard can't
  flip-flop on borderline cases.

**Validate it any time** (catches drift): `python -m sim.judge_validate --model qwen/qwen3-max`
(prod host — the judge model is geoblocked elsewhere). It runs the 12 hand-labeled, web-verified
gold cases and prints per-axis accuracy + every disagreement. A disagreement means the rubric (or a
label) is wrong — fix and re-run. Current: grounded 12/12, cliché 12/12, interest 8/8.
`human_calib.py` computes Cohen's κ against a larger human-labeled set when you have one.

**Bias mitigations:** judge ≠ generator family (self-preference); pairwise order-swap for A/B
selection; length-control in the rubric; hard-gates the prompt can't relax.

---

## 5. The autonomous loop + safety

`score = interestingness · Π(gates)`. The gates are non-negotiable and can't be bought back:

- **groundedness** (verified vs FACTS, or plausibility when absent),
- **cliché / ad-speak** (poetic filler + promotional copy for any commercial place),
- **non-repeat** (Jaccard + object-level),
- **coverage / silence** — silence is a FAILURE, not a fix. A candidate may not raise the silence
  rate; the answer to "no facts" is research (fix #3), not going quiet.

Optimizer safety (BLOCK4_FIXER_HARDENING.md has the full failure-mode model): a cheap **search
judge** ranks candidates on dev; a **gold judge** gates promotion on **held-out** only (optimizer
never sees held-out); **stop by the gold judge**; a reward-hacking detector (search rises but gold
doesn't → reject); bootstrap-CI so a single-point win doesn't count.

Deployment safety: an offline-gold-gate win is staged as **canary**, not active. A fraction of
sessions (`canary_fraction`, by stable sid-hash) use it; the worker's monitor promotes to active
only on a live win, else **auto-rolls-back** (and marks the version known-bad so it's never
re-proposed). `kill_switch` forces the pinned **baseline**. Every version is immutable; the baseline
is never deletable.

---

## 6. Config knobs (all Block-4)

| Env / setting | Default | Meaning |
|---|---|---|
| `CAPTURE_NARRATION_SAMPLES` / `CAPTURE_INTEREST_SIGNALS` | `0` | capture the corpus + signals (durable-layer + auth only) |
| `QUALITY_WORKER_USE_JUDGE` | `0` | worker runs the LLM judge (else code panel only) |
| `QUALITY_WORKER_INTERVAL_S` / `_LIMIT` | `60` / `50` | sweep cadence / walks per sweep |
| `QUALITY_LOG_DIR` | `""` | rotating decision-log file (`aiguide.quality*`) |
| `OPENAI_MODEL_JUDGE` | `""` | judge model — reachable NON-generator family (prod: `qwen/qwen3-max`) |
| `OPENAI_MODEL_OPTIMIZER` | `""` | proposer model (prod: `deepseek/deepseek-r1`) |
| `OPENAI_JUDGE_TEMPERATURE` | `0.0` | judge determinism |
| `FACT_WARM_TIER_MIN` / `FACT_WARM_SIG_MIN` | `paid` / `MEDIUM` | fix #3: how aggressively the pipeline fetches facts for facts-less objects (prod: `free`/`MEDIUM`) |
| `PROMPT_REGISTRY_DIR` | `prompt_registry` | shared registry path (prod: `/registry`) |
| `CANARY_ENABLED` | `False` | Phase 6 master switch |
| `CANARY_FRACTION` | `0.0` | share of sessions on the canary prompt (prod: `0.1`) |
| `CANARY_MIN_WALKS` / `_MARGIN` / `_WINDOW` | `8` / `0.05` / `60` | monitor: min walks per arm / decisive gap / recent window |

---

## 7. Operations runbook

**Follow what it decides after each walk**
```
ssh root@178.83.121.62 'docker logs -f ai-guide-quality'
# WALK <id> tier=<> score=<>/100 | grounded/cliche/novelty/object_repeat | провалы: {...} | худшее: <text>
# canary monitor (narrator/free): promoted | rolled_back
```

**Re-validate the judge (catch drift)** — prod host, geoblock:
```
docker exec ai-guide python -m sim.judge_validate --model qwen/qwen3-max
```

**Run the optimizer** (on-demand; writes to the shared `/registry`, stages a win as canary):
```
docker cp optrun.py ai-guide:/tmp/ && docker exec -d ai-guide sh -c 'python /tmp/optrun.py > /registry/opt.log 2>&1'
```

**Turn the canary loop on / off**
```
# on:  set CANARY_ENABLED=1, CANARY_FRACTION=0.1 in backend/.env; docker compose up -d backend quality-worker
# off: CANARY_ENABLED=0 (or CANARY_FRACTION=0), restart — instantly stops routing sessions to any canary
```

**Manual rollback / kill-switch** (Python in the container):
```
from app.services.quality.registry import PromptRegistry
r = PromptRegistry('/registry'); r.rollback('narrator','free')      # undo last active
r.kill_switch('narrator','free')                                    # force pinned baseline, drop canary
```

**Remove the whole analysis container** (zero impact on the tour):
```
cd /root/aiguide/deploy && docker compose rm -sf quality-worker
# also stop capturing: set CAPTURE_*=0 in backend/.env and restart backend
```

**Deploy / migrations / rollback of a bad deploy:** see `BLOCK4_FIXER_HARDENING.md` Part E.

---

## 8. Models & the regional geoblock

The prod region geoblocks OpenAI / Google / Anthropic (and a dev machine may be fully
OpenRouter-blocked). Reachable from prod, so the judge/optimizer run **only on the prod host**:
generator `deepseek-chat` / `mistral-large`, **judge `qwen/qwen3-max`** (different family), search
judge `qwen3-max`, gold judge `z-ai/glm-4.6` (different lab), **optimizer `deepseek/deepseek-r1`**.
Source of truth for reachability: `sim/ttft_probe.py` from the prod host.

---

## 9. Gotchas paid for

- **macOS AppleDouble `._*.py`** in `alembic/versions/` (from a macOS deploy) contain null bytes →
  alembic `SyntaxError: source code string cannot contain null bytes`. Purge with
  `find . -name '._*' -delete` before `docker compose build`; rsync with `--exclude='._*'`.
- **Judge at text temperature (0.8)** flip-flops on borderline cases → route `JUDGE` to temp 0.
- **`FACTS=None` → false fabrication**: the worker feeds the judge no facts on `walk_events`-fallback
  walks (historical / uncaptured). Fixed in the rubric (plausibility), but groundedness is most
  reliable on walks scored from `narration_samples` (with captured facts). Prefer those.
- **Shared registry**: backend, worker, and optimizer must mount the SAME `/registry` volume, else
  the canary the optimizer stages isn't the one the backend serves or the worker monitors.
- **Prompt `@cache`**: `prompts._load` is memoized; a candidate reaches a live session only via the
  session-scoped canary override (ContextVar), never by editing the file under a running process.

---

## 10. Status & limitations

- Deployed to prod: capture on, worker scoring with the validated judge, canary armed at 10%
  (inert until a candidate is staged). The autonomous loop is closed against a trustworthy signal.
- The judge is reliable on clear cases; a single genuinely-borderline case (folk-legend attribution)
  still has residual variance — acceptable, and caught by the validation harness.
- Groundedness is most trustworthy with captured facts; the world-knowledge fallback is a strong
  model's best guess, not ground truth.
- The optimizer is conservative on small dev sets (bootstrap-CI); landing a promoted candidate wants
  a 30–50-object dev set. Rejected-candidate texts aren't persisted yet (only their metrics).
