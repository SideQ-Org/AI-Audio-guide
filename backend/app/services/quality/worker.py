"""The quality-worker sweep (Block 4 Phase 4).

Runs OUT of the backend process (separate container): reads finished walks from Postgres,
scores each narrated blurb with the reference-free panel (+ optional LLM judge), and writes
one ``walk_quality`` row per walk with aggregates + a failure taxonomy. It never touches the
backend's event loop, session store, or prompts — pure read-DB / write-own-table analytics,
so it cannot destabilise the live tour.

``score_blurbs`` is a PURE function (no DB, no network unless a judge is passed) and is the
unit-tested core. ``sweep_once`` / ``run_forever`` add the DB I/O and the poll loop.

    python -m app.services.quality              # poll loop
    python -m app.services.quality --once       # single sweep (CI / cron)
    python -m app.services.quality --judge      # also run the LLM judge (needs a reachable
                                                #   non-generator model; else code-panel only)
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import asdict, dataclass, field

from app.services.agent.interest_metrics import build_idf, score_blurb, score_corpus
from app.services.agent.interest_score import composite, walk_coherence

_log = logging.getLogger("aiguide.quality")

# Cap the diagnostics payload so a huge walk doesn't bloat the row.
_WORST_KEEP = 5
_BLURB_CLIP = 160


@dataclass
class Blurb:
    text: str
    language: str = "ru"
    facts: str | None = None
    place: str | None = None
    significance: str | None = None
    category: str | None = None
    tier: str = "free"


@dataclass
class QualityResult:
    tier: str = "free"            # the tier this walk ran under (free|paid)
    n_blurbs: int = 0
    score: float = 0.0            # 0-100 walk interestingness (mean gated composite)
    interest_mean: float = 0.0    # 0-1 pre-gate mean
    grounded_rate: float = 1.0
    cliche_rate: float = 0.0
    novelty_mean: float = 0.0
    distinct_2: float = 0.0
    # 0-1 walk coherence (transitions/adjacency/callbacks + judge); None = not applicable
    # (<2 non-silent blurbs — nothing to cohere) or no judge.
    coherence_mean: float | None = None
    seamlessness: float | None = None   # judge axis 0-4; None when n/a or no judge
    arc_coherence: float | None = None  # judge axis 0-4; None when n/a or no judge
    used_judge: bool = False
    diagnostics: dict = field(default_factory=dict)

    def as_fields(self) -> dict:
        d = asdict(self)
        return d


async def score_blurbs(blurbs: list[Blurb], *, judge=None) -> QualityResult:
    """Score one walk's blurbs. Pure except for the optional ``judge`` (an LLMJudge). The
    judge, when present, supplies the semantic axes + the groundedness verdict (verified
    against each blurb's FACTS); without it the code panel runs alone (advisory: grounded
    can't be verified, so it defaults to pass)."""
    blurbs = [b for b in blurbs if (b.text or "").strip()]
    if not blurbs:
        return QualityResult()

    texts = [b.text for b in blurbs]
    place_ids = [b.place for b in blurbs]
    categories = [b.category for b in blurbs]
    lang = blurbs[0].language or "ru"
    idf = build_idf(texts)
    cm = score_corpus(texts, place_ids=place_ids, categories=categories, language=lang)
    prior: list[str] = []
    tax: Counter[str] = Counter()
    worst: list[tuple[float, str]] = []
    scores, interests, novelties = [], [], []
    grounded_ok = cliche_ok = 0

    for b in blurbs:
        bm = score_blurb(b.text, prior=prior, idf=idf, language=b.language)
        verdict = None
        if judge is not None:
            try:
                verdict = await judge.score(
                    b.text, facts=b.facts, language=b.language, tier=b.tier
                )
            except Exception as e:  # noqa: BLE001 — a judge hiccup must not abort the sweep
                _log.warning("judge failed on a blurb: %s", e)
        cs = composite(bm, verdict)
        prior.append(b.text)

        scores.append(cs.score)
        interests.append(cs.interest)
        novelties.append(bm.novelty)
        grounded_ok += 1 if cs.gates["grounded"] else 0
        cliche_ok += 1 if cs.gates["cliche_free"] else 0
        if not cs.gates["grounded"]:
            tax["ungrounded"] += 1
        if not cs.gates["cliche_free"]:
            tax["cliche"] += 1
        if not cs.gates["novel"]:
            tax["repeat"] += 1
        worst.append((cs.score, (b.text or "")[:_BLURB_CLIP]))

    n = len(blurbs)
    # Object-level repetition ("опять про руины") — re-narrating the same object, which
    # lexical novelty misses when the wording differs. Count occurrences for the taxonomy
    # and mildly penalise the walk score (re-narration is sometimes legitimate — the revisit
    # feature — so it's a soft penalty + a diagnostic, not a per-blurb hard-gate).
    seen_obj: set[str] = set()
    repeat_objs = 0
    for pid in place_ids:
        if not pid:
            continue
        if pid in seen_obj:
            repeat_objs += 1
        else:
            seen_obj.add(pid)
    if repeat_objs:
        tax["repeat_object"] = repeat_objs

    # Walk-level coherence (бесшовность / связность / арка): a SEPARATE cross-object quantity.
    # The judge (when present) scores the ordered NON-SILENT sequence; the code panel supplies the
    # transition/adjacency/callback signals. Folded as a BOUNDED ±15% dial — it can never zero a
    # grounded walk, and (computed over non-silent blurbs + the coverage gate elsewhere) silence
    # can't masquerade as smoothness.
    # Coherence is only defined for a SEQUENCE — a walk with <2 non-silent blurbs has nothing to
    # cohere, so it's "not applicable": neutral factor (no ±15% penalty) and NO `disjoint` flag
    # (a 1-object walk isn't disjoint, there's just nothing to connect).
    seq = [t for t in texts if t.strip() and t.strip().upper().strip("[]") != "SILENCE"]
    coh_applicable = len(seq) >= 2
    wv = None
    if judge is not None and coh_applicable:
        try:
            wv = await judge.score_walk(seq, language=lang)
        except Exception as e:  # noqa: BLE001 — a judge hiccup must not abort the sweep
            _log.warning("walk judge failed: %s", e)
    coherence = walk_coherence(cm, wv) if coh_applicable else None
    coh_factor = (0.85 + 0.15 * coherence) if coherence is not None else 1.0

    walk_score = 100 * sum(scores) / n * (1 - 0.4 * cm.object_repeat_rate) * coh_factor
    if coherence is not None and coherence < 0.4:
        tax["disjoint"] = 1
    worst.sort(key=lambda x: x[0])
    return QualityResult(
        tier=blurbs[0].tier,
        n_blurbs=n,
        score=round(walk_score, 1),
        interest_mean=round(sum(interests) / n, 3),
        grounded_rate=round(grounded_ok / n, 3),
        cliche_rate=round((n - cliche_ok) / n, 3),
        novelty_mean=round(sum(novelties) / n, 3),
        distinct_2=round(cm.distinct_2, 3),
        coherence_mean=round(coherence, 3) if coherence is not None else None,
        seamlessness=float(wv.seamlessness) if wv else None,
        arc_coherence=float(wv.arc_coherence) if wv else None,
        used_judge=judge is not None,
        diagnostics={
            "taxonomy": dict(tax),
            "object_repeat_rate": round(cm.object_repeat_rate, 3),
            "coherence": {
                "applicable": coh_applicable,
                "score": round(coherence, 3) if coherence is not None else None,
                "transition_rate": round(cm.transition_rate, 3),
                "adjacent_cohesion": round(cm.adjacent_cohesion, 3),
                "callback_rate": round(cm.callback_rate, 3),
                "seamlessness": float(wv.seamlessness) if wv else None,
                "arc_coherence": float(wv.arc_coherence) if wv else None,
            },
            "worst": [{"score": round(s, 3), "text": t} for s, t in worst[:_WORST_KEEP]],
        },
    )


def _make_judge():
    """Build an LLMJudge on the configured judge model (a non-generator family). Returns
    None if no OpenAI-compatible endpoint is configured."""
    from app.config import settings
    if not settings.openai_model and not settings.openai_model_judge:
        return None
    from app.services.agent.interest_judge import LLMJudge
    from app.services.llm.client import OpenAICompatLLM

    return LLMJudge(OpenAICompatLLM())


def _blurbs_for_walk(samples: list, events: list) -> list[Blurb]:
    """Prefer the captured narration_samples (they carry FACTS → groundedness can be
    judged); fall back to walk_events (narration text only) when capture was off."""
    if samples:
        return [
            Blurb(
                text=s.narration, language=s.language, facts=s.facts,
                place=s.place_id, significance=s.significance,
                category=getattr(s, "category", None),
                tier=getattr(s, "tier", "free"),
            )
            for s in samples
        ]
    return [
        Blurb(
            text=e.narration or "", language="ru", significance=e.significance,
            place=getattr(e, "place_id", None), category=getattr(e, "category", None),
        )
        for e in events
        if (e.narration or "").strip()
    ]


async def sweep_once(*, use_judge: bool = False, limit: int = 50) -> int:
    """Score every finished, not-yet-scored walk. Returns the number scored. No-op (0)
    when the durable layer is off."""
    from app.services.accounts import repository as repo
    from app.services.accounts.db import accounts_enabled, session_scope

    if not accounts_enabled():
        _log.info("accounts layer disabled — nothing to sweep")
        return 0

    judge = _make_judge() if use_judge else None

    # 1) gather plain work items inside a session (avoid detached-ORM access later).
    work: list[tuple[str, str, list[Blurb]]] = []
    async with session_scope() as session:
        walks = await repo.list_unscored_walks(session, limit=limit)
        for w in walks:
            samples = await repo.get_narration_samples(session, walk_id=w.id)
            work.append((str(w.id), str(w.user_id), _blurbs_for_walk(samples, w.events)))

    # 2) score outside the session (judge I/O may be slow), then 3) write each row.
    scored = 0
    for walk_id, user_id, blurbs in work:
        result = await score_blurbs(blurbs, judge=judge)
        try:
            async with session_scope() as session:
                await repo.append_walk_quality(
                    session, walk_id=walk_id, user_id=user_id, **result.as_fields()
                )
            scored += 1
            _log_decision(walk_id, result)
        except Exception as e:  # noqa: BLE001 — one bad write must not stop the sweep
            _log.warning("walk_quality write failed for %s: %s", walk_id, e)
    return scored


def _log_decision(walk_id: str, r: QualityResult) -> None:
    """Emit a followable record of what the worker DECIDED for one walk (the user-facing trace:
    "что система решает после прогулок")."""
    diag = r.diagnostics or {}
    coh = "n/a" if r.coherence_mean is None else (
        f"{r.coherence_mean:.2f} (seam={r.seamlessness:.0f} arc={r.arc_coherence:.0f})"
    )
    _log.info(
        "WALK %s tier=%s score=%.1f/100 | grounded=%.2f cliche=%.2f novelty=%.2f "
        "coherence=%s object_repeat=%.2f | n=%d judge=%s",
        walk_id, r.tier, r.score, r.grounded_rate, r.cliche_rate, r.novelty_mean,
        coh, diag.get("object_repeat_rate", 0.0), r.n_blurbs, r.used_judge,
    )
    tax = diag.get("taxonomy") or {}
    if tax:
        _log.info("  провалы: %s", tax)
    for w in (diag.get("worst") or [])[:2]:
        _log.info("  худшее [%.2f]: %s", w.get("score", 0.0), (w.get("text") or "")[:120])


async def run_forever(*, use_judge: bool = False, interval_s: float = 60.0) -> None:
    """Poll loop: sweep, sleep, repeat. The container's long-running entrypoint."""
    import asyncio

    _log.info("quality worker started (judge=%s, interval=%ss)", use_judge, interval_s)
    while True:
        try:
            n = await sweep_once(use_judge=use_judge)
            if n:
                _log.info("sweep scored %d walk(s)", n)
            await _canary_monitor_tick()
        except Exception as e:  # noqa: BLE001 — keep the loop alive across transient failures
            _log.warning("sweep failed: %s", e)
        await asyncio.sleep(interval_s)


async def _canary_monitor_tick() -> None:
    """Phase 6: after each sweep, let the canary monitor auto-rollback/promote. No-op unless
    canary is enabled + a version is staged (dormant by default)."""
    from app.config import settings
    if not settings.canary_enabled:
        return
    try:
        from .canary import monitor_and_rollback
        from .registry import PromptRegistry
        reg = PromptRegistry(settings.prompt_registry_dir)
        for tier in ("free", "paid"):
            action = await monitor_and_rollback(reg, target="narrator", tier=tier)
            if action:
                _log.info("canary monitor (narrator/%s): %s", tier, action)
    except Exception as e:  # noqa: BLE001 — monitoring must never crash the worker
        _log.warning("canary monitor failed: %s", e)
