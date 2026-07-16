"""PromptRegistry — the self-improvement durability layer (Block 4 hardening).

Gives the fixer a MEMORY (what it tried, what worked, what didn't), VERSIONING (immutable
prompt texts), an ACTIVE pointer, and ROLLBACK — so no change is unmeasurable, unremembered,
or irreversible. File-based and git-friendly, one tree per (target, tier):

    prompt_registry/<target>/<tier>/
        versions/<version_id>.txt    # immutable texts; the baseline is pinned, never deleted
        ledger.jsonl                 # append-only experiment records (the memory)
        active.json                  # {active, baseline, updated_at, history:[...]}

Design notes tied to the failure model (BLOCK4_FIXER_HARDENING.md):
  * append-only JSONL → a partial/corrupt line is skipped on read, never crashes (B7).
  * baseline is always present and is the rollback floor / kill-switch target (B5).
  * known_bad(target,tier) feeds the proposer so a rolled-back/rejected version is never
    re-proposed → kills oscillation (B3).
  * a coarse mkdir-based lock serialises writes so concurrent optimizer runs can't corrupt the
    active pointer (B5).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_LEDGER_CAP = 2000  # rotate the memory so it can't grow unbounded (B7)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class Experiment:
    """One recorded attempt — the unit of the system's memory."""

    id: str                       # version id of the candidate
    target: str
    tier: str
    verdict: str                  # accepted | rejected | rolled_back
    parent: str | None = None
    dev: float | None = None
    gold: float | None = None
    silence: float | None = None
    gates_ok: bool | None = None
    stop_reason: str = ""
    n: int | None = None
    judge: str = ""               # judge model id (drift auditing, B2)
    corpus_ref: str = ""          # corpus snapshot (staleness auditing, B4)
    ts: str = ""

    def as_record(self) -> dict:
        d = {k: v for k, v in self.__dict__.items()}
        d["ts"] = d["ts"] or _now_iso()
        return d


class PromptRegistry:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    # -- paths ------------------------------------------------------------- #
    def _dir(self, target: str, tier: str) -> Path:
        return self.root / target / tier

    @staticmethod
    def version_id(text: str) -> str:
        return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]

    # -- coarse write lock (B5) ------------------------------------------- #
    def _lock(self, target: str, tier: str, *, timeout_s: float = 10.0):
        d = self._dir(target, tier)
        d.mkdir(parents=True, exist_ok=True)
        lock = d / ".lock"
        return _MkdirLock(lock, timeout_s)

    # -- versions ---------------------------------------------------------- #
    def save_version(
        self, target: str, tier: str, text: str, *, version_id: str | None = None
    ) -> str:
        vid = version_id or self.version_id(text)
        with self._lock(target, tier):
            vdir = self._dir(target, tier) / "versions"
            vdir.mkdir(parents=True, exist_ok=True)
            f = vdir / f"{vid}.txt"
            if not f.exists():  # immutable — never rewrite an existing version
                f.write_text(text, encoding="utf-8")
        return vid

    def version_text(self, target: str, tier: str, version_id: str) -> str | None:
        f = self._dir(target, tier) / "versions" / f"{version_id}.txt"
        return f.read_text(encoding="utf-8") if f.exists() else None

    def ensure_baseline(self, target: str, tier: str, text: str) -> str:
        """Register ``text`` as the baseline (and active) if this (target,tier) is new. The
        baseline is the pinned rollback floor. Idempotent — returns the baseline id."""
        state = self._active_state(target, tier)
        if state.get("baseline"):
            return state["baseline"]
        vid = self.save_version(target, tier, text)
        with self._lock(target, tier):
            self._write_active({"active": vid, "baseline": vid, "updated_at": _now_iso(),
                                "history": []}, target, tier)
        return vid

    # -- active pointer + rollback ---------------------------------------- #
    def _active_path(self, target: str, tier: str) -> Path:
        return self._dir(target, tier) / "active.json"

    def _active_state(self, target: str, tier: str) -> dict:
        f = self._active_path(target, tier)
        if not f.exists():
            return {}
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _write_active(self, state: dict, target: str, tier: str) -> None:
        self._dir(target, tier).mkdir(parents=True, exist_ok=True)
        self._active_path(target, tier).write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def active_version(self, target: str, tier: str) -> str | None:
        return self._active_state(target, tier).get("active")

    def active_text(self, target: str, tier: str) -> str | None:
        vid = self.active_version(target, tier)
        return self.version_text(target, tier, vid) if vid else None

    def set_active(self, target: str, tier: str, version_id: str) -> None:
        """Promote a version to active, pushing the previous active onto the rollback history."""
        with self._lock(target, tier):
            state = self._active_state(target, tier)
            prev = state.get("active")
            if prev and prev != version_id:
                state.setdefault("history", []).append(prev)
            state["active"] = version_id
            state.setdefault("baseline", version_id)
            state["updated_at"] = _now_iso()
            self._write_active(state, target, tier)

    def rollback(self, target: str, tier: str) -> str | None:
        """Revert the active pointer to the previous version (or the baseline if no history).
        Returns the version rolled back TO, or None if nothing to do."""
        with self._lock(target, tier):
            state = self._active_state(target, tier)
            hist = state.get("history", [])
            target_id = hist.pop() if hist else state.get("baseline")
            if not target_id or target_id == state.get("active"):
                return None
            state["active"] = target_id
            state["history"] = hist
            state["updated_at"] = _now_iso()
            self._write_active(state, target, tier)
            return target_id

    def kill_switch(self, target: str, tier: str) -> str | None:
        """Force the active pointer back to the pinned baseline (the one-flag emergency revert)."""
        with self._lock(target, tier):
            state = self._active_state(target, tier)
            base = state.get("baseline")
            if not base:
                return None
            if state.get("active") != base:
                state.setdefault("history", []).append(state["active"])
            state["active"] = base
            state["updated_at"] = _now_iso()
            self._write_active(state, target, tier)
            return base

    # -- memory (ledger) --------------------------------------------------- #
    def _ledger_path(self, target: str, tier: str) -> Path:
        return self._dir(target, tier) / "ledger.jsonl"

    def record_experiment(self, exp: Experiment) -> None:
        with self._lock(exp.target, exp.tier):
            p = self._ledger_path(exp.target, exp.tier)
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(exp.as_record(), ensure_ascii=False) + "\n")
            self._rotate(p)

    @staticmethod
    def _rotate(path: Path) -> None:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return
        if len(lines) > _LEDGER_CAP:
            path.write_text("\n".join(lines[-_LEDGER_CAP:]) + "\n", encoding="utf-8")

    def past_attempts(self, target: str, tier: str, *, limit: int = 50) -> list[dict]:
        """The system's memory of what it tried, newest last. Malformed lines are skipped."""
        p = self._ledger_path(target, tier)
        if not p.exists():
            return []
        out: list[dict] = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # corrupt line — skip, never crash (B7)
        return out[-limit:]

    def known_bad(self, target: str, tier: str) -> list[str]:
        """Version ids that were rejected or rolled back — the proposer must NOT re-propose these
        (oscillation guard, B3). A later 'accepted' record on the same id clears it."""
        verdict_by_id: dict[str, str] = {}
        for r in self.past_attempts(target, tier, limit=_LEDGER_CAP):
            vid = r.get("id")
            if vid:
                verdict_by_id[vid] = r.get("verdict", "")
        return [vid for vid, v in verdict_by_id.items() if v in ("rejected", "rolled_back")]


class _MkdirLock:
    """A coarse cross-process lock via atomic mkdir. Best-effort: times out rather than deadlock."""

    def __init__(self, path: Path, timeout_s: float) -> None:
        self._path = path
        self._timeout = timeout_s
        self._held = False

    def __enter__(self):
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                os.mkdir(self._path)
                self._held = True
                return self
            except FileExistsError:
                if time.monotonic() > deadline:
                    # stale lock — take it rather than block the optimizer forever
                    self._held = True
                    return self
                time.sleep(0.02)

    def __exit__(self, *exc):
        if self._held:
            try:
                os.rmdir(self._path)
            except OSError:
                pass
