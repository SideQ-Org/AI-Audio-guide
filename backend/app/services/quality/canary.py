"""Phase 6 — canary auto-apply + auto-rollback (Block 4).

A validated prompt candidate can be staged as the **canary** in the registry. A stable fraction
of live sessions (by sid-hash) then use it while everyone else stays on the file/active prompt; the
quality worker compares canary vs control `walk_quality` and **auto-rolls-back on regression** or
**promotes on a clear win**. DORMANT by default — nothing changes for any session unless
`canary_enabled` AND `canary_fraction > 0` AND a canary version is staged.

Canary membership is DERIVED from the walk's `sid` (deterministic hash), so the monitor can split
walks into canary/control after the fact without a per-walk flag/column.

Invariant (from BLOCK4_FIXER_HARDENING.md): a candidate only reaches the canary after passing the
offline held-out gold gate — canary is the *live* confirmation, not the first line of defence.
"""

from __future__ import annotations

import hashlib
import logging

from app.config import settings

from .registry import Experiment, PromptRegistry

_log = logging.getLogger("aiguide.quality.canary")


def _in_canary(sid: str, fraction: float) -> bool:
    """Stable membership: hash the sid to [0,1) and compare to the fraction. Deterministic across
    processes (sha1, not the salted built-in hash), so a session stays canary for its whole life
    and the monitor can re-derive membership from the walk's sid."""
    if fraction <= 0:
        return False
    if fraction >= 1:
        return True
    h = int(hashlib.sha1(sid.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF
    return h < fraction


def canary_prompt_for(
    registry: PromptRegistry, session_id: str, tier: str, *, target: str = "narrator"
) -> dict[str, str] | None:
    """The per-session prompt override for a canary session, or None. Wire the result into
    ``prompts.set_session_prompt_override`` at the session boundary. Returns None (no effect) when
    canary is off, no version is staged, or this session isn't in the canary fraction."""
    if not settings.canary_enabled or settings.canary_fraction <= 0:
        return None
    ctext = registry.canary_text(target, tier)
    if not ctext:
        return None
    if not _in_canary(session_id, settings.canary_fraction):
        return None
    return {target: ctext}


async def monitor_and_rollback(
    registry: PromptRegistry, *, target: str = "narrator", tier: str = "free"
) -> str | None:
    """Compare canary vs control `walk_quality` and act. Returns 'promoted' | 'rolled_back' | None
    (inconclusive / not enough data / off). Safe no-op when canary is off or nothing is staged."""
    if not settings.canary_enabled:
        return None
    canary_v = registry.canary_version(target, tier)
    if not canary_v:
        return None

    from app.services.accounts import repository as repo
    from app.services.accounts.db import accounts_enabled, session_scope

    if not accounts_enabled():
        return None
    async with session_scope() as s:
        rows = await repo.recent_walk_quality(s, tier=tier, limit=settings.canary_window)

    frac = settings.canary_fraction
    canary = [sc for sc, sid, _ in rows if _in_canary(sid, frac)]
    control = [sc for sc, sid, _ in rows if not _in_canary(sid, frac)]
    if len(canary) < settings.canary_min_walks or len(control) < settings.canary_min_walks:
        return None  # not enough evidence in either arm yet

    ca = sum(canary) / len(canary)
    co = sum(control) / len(control)
    margin = settings.canary_margin * 100.0  # walk_quality.score is 0-100
    if ca < co - margin:
        registry.clear_canary(target, tier)
        registry.record_experiment(Experiment(
            id=canary_v, target=target, tier=tier, verdict="rolled_back",
            gold=round(ca, 2), stop_reason="canary_regression",
            n=len(canary),
        ))
        _log.warning(
            "CANARY ROLLBACK %s/%s: canary %.1f < control %.1f (n=%d/%d) -> dropped %s",
            target, tier, ca, co, len(canary), len(control), canary_v,
        )
        return "rolled_back"
    if ca > co + margin:
        registry.set_active(target, tier, canary_v)
        registry.clear_canary(target, tier)
        registry.record_experiment(Experiment(
            id=canary_v, target=target, tier=tier, verdict="accepted",
            gold=round(ca, 2), stop_reason="canary_promoted", n=len(canary),
        ))
        _log.info(
            "CANARY PROMOTED %s/%s: canary %.1f > control %.1f (n=%d/%d) -> active %s",
            target, tier, ca, co, len(canary), len(control), canary_v,
        )
        return "promoted"
    return None  # inconclusive — keep the experiment running
