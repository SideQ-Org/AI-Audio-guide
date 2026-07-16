# Block 4 — Fixer hardening: memory, versioning, rollback + failure-mode model

**Design + spec.** Date: 2026-07-16. Covers: (1) making "get facts" real (enrichment
optimization scope), (2) **memory** of the system's own edits (what worked / what didn't),
(3) prompt **versioning + rollback** on regression, and (4) a systematic model of every way a
self-improving system can go wrong, each with the mechanic that contains it.

> Context: the offline loop (`sim/prompt_optimize.py`) proposes prompt rewrites, the evaluator
> (`interest_metrics` + `interest_judge` + `interest_score`) scores them, gated by held-out gold
> + hard-gates incl. the coverage/silence gate. This doc adds the **durability layer** (memory +
> versions + rollback) and closes the "narrator asks for facts it never gets" gap.

---

## Part A — The core principle

A self-improving system that can change production is only as safe as the **weakest failure it
does not model**. So we enumerate the failures first, then build exactly the mechanics that
contain them. The guiding invariants:

- **Nothing reaches prod that hasn't passed the offline held-out gold gate.** (Already true.)
- **Every change is reversible.** A promoted version that regresses live is auto-rolled-back to a
  pinned, always-present baseline. (This doc.)
- **The system remembers.** Every experiment — accepted, rejected, rolled-back — is recorded, so
  the optimizer never re-tries a known loser and a human can audit the whole trajectory. (This doc.)
- **Silence and fabrication are both failures.** The fix for "no facts" is to *get facts*, not to
  go quiet. (Enrichment scope, below.)

---

## Part B — Failure-mode model (every problem → its mechanic)

### B1. Reward-hacking / metric-gaming
| Failure | Mechanic |
|---|---|
| Interest bought with **fabrication** | groundedness hard-gate (judge verifies claims vs FACTS) — gate can't be relaxed by the prompt |
| Interest bought with **cliché** | cliché hard-gate (code blocklist + judge) |
| **Silence** as a free win (grounding up, coverage dead) | **coverage/silence gate** — a candidate may not raise silence_rate |
| **Coverage collapse** ("only talk at landmarks") | same coverage gate + per-walk coverage tracked |
| Candidate games the **cheap search judge** | promotion gated by the **gold** judge on **held-out**; search↔gold divergence detector |
| **Overfit to dev** | held-out gold gate + a sacred holdout the optimizer never sees |
| Judge **verbosity/style bias** exploited | length-control in the rubric; pairwise order-swap; judge ensemble on the gold gate |

### B2. Judge / evaluator drift & error
| Failure | Mechanic |
|---|---|
| Judge model silently updates → scores shift | **pin model+version**; store the judge id with every experiment; periodic re-validation |
| Judge disagrees with reality | human spot-check in the gold gate; real interest signals (follow-up/skip) as ground-truth-lite; κ tracking (`human_calib`) |
| Judge drifts over time | keep a frozen human-labeled set; re-measure κ periodically; alert on κ drop |
| Judge cost/latency | cheap search judge in the loop; expensive gold panel only at the gate |

### B3. Optimization instability / regression  ← **the rollback core**
| Failure | Mechanic |
|---|---|
| A promoted change makes **prod worse** than before | **version store + active pointer + live monitor → auto-rollback** to the previous active |
| **Oscillation** (A→B→A→B) | the **memory ledger** marks known-bad versions; the proposer is fed them and must not re-propose; a cooldown on recently-rolled-back lineages |
| **Plateau / local optimum** | portfolio of optimizers; diversity in candidates; restart from a different seed prompt; stop by held-out plateau |
| **Compounding prompt interactions** (narrator × area × companion) | optimize one prompt at a time; re-check the whole-walk metric after each; never promote on a single-prompt local win that hurts the system |
| **Regression slips through the gate** (gate too loose) | multi-seed dev + bootstrap-CI; hard-gates; gold panel + human; and the live monitor as the last net |

### B4. Corpus / data problems
| Failure | Mechanic |
|---|---|
| Corpus too small → false "improvements" | bootstrap-CI beats-champion rule; a **min-sample floor** before any promotion |
| Corpus **stale** (distribution shift) | record the corpus snapshot id with each experiment; refresh from live `narration_samples`; re-baseline on refresh |
| **Tier confound** (free DeepSeek vs paid premium) | tier-segmented corpus + optimization + judge context (built) |
| **Selection bias** (only authed walks captured) | documented; the gold gate + live monitor catch what the corpus misses |
| **Prompt-injection via corpus** (a narration says "ignore your rules") | corpus is **data, not instructions**; the judge/optimizer are told to treat blurb text as content; strip/curtail suspicious control-looking text |

### B5. Deployment / operational
| Failure | Mechanic |
|---|---|
| Prompt swapped under **all** live traffic at once | **canary fraction** (5–10%), never 100% before the monitor clears it |
| A **bad candidate reaches prod** | offline gold gate is a hard precondition for canary (invariant) |
| **Rollback fails / version lost** | immutable version files; a **baseline always pinned** and never deletable; a one-flag kill-switch → force baseline |
| **Cost blowup** (loop = thousands of calls) | budget cap + iteration cap + minibatch + cheap search judge; the worker's per-walk judge is sampled |
| **Concurrent optimizer runs** clash on the active pointer | single-writer lock on the registry; the active pointer is CAS-updated |
| **Model unreachable** (geoblock / outage) | the loop degrades gracefully (proposer/judge failure ends the round, not the process); the worker keeps observing; live behaviour unchanged (override empty) |

### B6. Safety / invariants
| Failure | Mechanic |
|---|---|
| Optimizer **weakens a hard invariant** in the prompt | invariants pinned in the meta-prompt AND enforced as code hard-gates the prompt can't touch |
| **Runaway autonomy** (prod changed with no human) | human-veto window before 100% promotion; notification on every promote/rollback; canary + auto-rollback bound the blast radius |
| **Right-to-be-forgotten** vs stored narration samples | FK cascade deletes samples with the walk (built) |

### B7. The memory itself
| Failure | Mechanic |
|---|---|
| Ledger grows **unbounded** | append-only JSONL with a rotation cap |
| Ledger **corruption** (partial write) | line-oriented JSONL; malformed lines skipped on read, never crash |
| Memory **misleads** (a past "win" was a fluke) | store CI + n + judge id with each record; the proposer weighs confidence, not just the point score |

---

## Part C — The durability layer (what we build)

### C1. PromptRegistry (`app/services/quality/registry.py`)
File-based, git-friendly, per **(target, tier)** — e.g. `(narrator, free)`, `(area, paid)`:

```
prompt_registry/
  <target>/<tier>/
    versions/<version_id>.txt      # immutable version texts; baseline always present
    ledger.jsonl                   # append-only experiment records (the MEMORY)
    active.json                    # {active, updated_at, history:[...]}  (the POINTER)
```

- **Version** = an immutable prompt text keyed by a content hash id; the `baseline` is pinned and
  never deleted (rollback floor).
- **Ledger record** (the memory of "what worked / what didn't"): `{id, parent, target, tier,
  dev, gold, silence, gates_ok, verdict: accepted|rejected|rolled_back, stop_reason, n, judge,
  corpus_ref, ts}`.
- **Active pointer**: the currently-chosen version + a `history` stack enabling rollback.

API: `save_version`, `set_active`, `rollback`, `record_experiment`, `past_attempts`,
`known_bad`, `active_version`, `active_text`.

### C2. Memory-driven optimization
The optimizer reads `past_attempts(target, tier)` and passes it to the proposer as a **persistent
trajectory** (survives across runs, not just within one loop): what was tried, its gold score, and
whether it was accepted/rejected/rolled-back. `known_bad` versions are handed to the proposer with
an explicit "do NOT re-propose these" — killing oscillation. Every round records an experiment;
promotion `save_version` + `set_active`.

### C3. Rollback
`check_and_rollback(target, tier, live_score, *, margin)`: compares the active version's **live**
(canary/prod) score to the score it was promoted at (and to the previous active). If it regressed
beyond `margin`, `rollback()` reverts the active pointer to the previous version, records a
`rolled_back` experiment, and flags the lineage known-bad. A `kill_switch(target, tier)` forces
`baseline`. This is the Phase-6 safety net; it works the same for a canary or a full rollout.

### C4. Enrichment optimization scope (fix #3 — "get facts", don't go silent)
The root cause of the dominant failure (fabrication, 65% on real walks) is **facts-less objects**.
The narrator prompt can *ask* for facts, but delivery is upstream. So:

- **Config knobs** make research aggressiveness tunable: `fact_warm_tier_min` (paid→free) and
  `fact_warm_sig_min` (MEDIUM→LOW) widen when `_start_fact_warm` fires, so the pipeline actually
  fetches facts for more facts-less objects instead of leaving the narrator to fabricate/silence.
- **Candidate = `{prompt_text?, config_patch?}`**: the optimizer can propose an enrichment-config
  patch, not just prompt text. `apply_config_patch` overrides the knobs during evaluation and
  reverts after; the registry records config candidates alongside prompt ones. (Live config-patch
  evaluation re-runs the full pipeline — heavier, human-gated — but the mechanism + memory are the
  same, so a promising patch is captured, evidenced, and reversible like any other change.)

---

## Part E — Deployment runbook (separate, removable container)

The analysis system ships as a **separate container** (`quality-worker`) that reads the DB and
writes its own `walk_quality` — it never shares the backend event loop or prompts, so it can be
removed instantly without touching the live tour. The one backend change is **additive + flag-gated**
capture (Phase 0).

**Pre-flight (confirm before deploying):**
- Durable layer ON in prod (`accounts_enabled()` True, `DATABASE_URL` set) — capture writes only
  for authed users. (Prod: confirmed True, 29 walks in DB, migration at `0007`.)
- Prod prompts == repo prompts (so a full `rsync` won't clobber a tuned prod prompt). (Confirmed by
  md5.)
- Cost posture: the judge (`qwen/qwen3-max`) runs per blurb per walk — set `USD_HARD_CAP`.

**Deploy steps (each is a gated prod op — name host + effects to authorize):**
1. `rsync` `backend/` (excl. `.env`/`.venv`/caches, **no `--delete`**) + the updated
   `deploy/docker-compose.yml` to `/root/aiguide`. Back up the prod compose first.
2. Build the image without touching the running backend: `docker compose build backend`.
3. Apply migrations (**irreversible on the live DB**): inside the backend container,
   `alembic upgrade head` → `0008` narration_samples, `0009` walk_quality, `0010` tier. Then apply
   `db/rls.sql` (defence-in-depth; the new tables are service-role-only so this is non-blocking).
4. Restart the backend on the new image to enable capture: `docker compose up -d backend`, then
   verify `/health` is healthy (rollback if not — see below).
5. Start the sidecar: `docker compose up -d quality-worker`. It scores the backlog of finished
   walks (via `walk_events.narration` when no samples yet) and every new one.
6. Verify: `docker logs -f ai-guide-quality` shows `WALK <id> … score=…/100`; `select count(*)
   from walk_quality` grows; a new authed walk writes `narration_samples`.

**Config for the run:** `CAPTURE_NARRATION_SAMPLES=1`, `CAPTURE_INTEREST_SIGNALS=1` (backend),
`QUALITY_WORKER_USE_JUDGE=1`, `OPENAI_MODEL_JUDGE=qwen/qwen3-max`, `QUALITY_LOG_DIR=/data/quality`
(worker). `OPENAI_MODEL_OPTIMIZER=deepseek/deepseek-r1` for the (on-demand) loop.

**Remove instantly (no impact on the tour):**
`docker compose stop quality-worker && docker compose rm -f quality-worker` (optionally
`docker volume rm deploy_quality-data`). To also stop capturing: set the two `CAPTURE_*` flags to
`0` and restart the backend. The migrations/tables can stay (inert) or be dropped by downgrading.

**Rollback (if the backend redeploy misbehaves):** prod is not a git checkout, so keep the previous
image tagged / the compose backup (`docker-compose.yml.bak.preblock4`); revert the backend service
to the prior image and `up -d backend`. The capture code is best-effort (never raises into
narration), and the worker is isolated, so the blast radius of a bad deploy is the backend restart
itself, not the tour logic.

## Part D — Invariant summary (one line)
The system may only make a change it can **measure** (gold gate), **remember** (ledger), and
**undo** (rollback to a pinned baseline) — and silence is never an acceptable "fix".
