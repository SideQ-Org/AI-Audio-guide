"""Product / walk KPI accounting — the counterpart to ``llm.client.METER``.

``METER`` tracks tokens and cost; ``GUIDE`` tracks what the *guide* actually did:
how many objects it narrated, how often it switched, how often it fell back to a
floor mention or went silent, which languages/categories/significance tiers came up,
plus the economy of enrichment (free wiki facts vs the paid web-search fallback) and
Overpass mirror health. All of it feeds the ``/dashboard`` ops view.

Pure in-process counters (no I/O, no locks — mutated only from the single asyncio
loop, exactly like ``METER``), incremented at the points where the orchestrator and
services already emit their ``aiguide.agent`` decision logs, so the dashboard shows
real numbers instead of a log-parse.
"""

from __future__ import annotations

import time
from collections import Counter
from typing import Any

from app.services.llm.client import SESSION_ID

# Wall-clock process start, so the dashboard can show uptime.
_STARTED_AT = time.time()
_STARTED_MONO = time.monotonic()

_SESSION_CAP = 2000  # FIFO bound on the per-session rollup, mirrors METER.by_session


class GuideMetrics:
    """Cumulative product KPIs for the whole process + a per-session rollup."""

    def __init__(self) -> None:
        # narration outcomes
        self.narrations = 0  # a real SUMMARY was emitted for a place
        self.switches = 0  # narration that seamlessly switched to a better object
        self.elaborations = 0  # "tell me more" follow-up on the current place
        self.floors = 0  # deterministic floor mention (passing, no rich facts)
        self.silences = 0  # a passing object yielded nothing to say
        self.suppressed_repeats = 0  # no-repeat net dropped a near-duplicate
        self.area_beats = 0  # area/district connective monologue beats

        # distributions (kept small — bounded key space)
        self.by_language: Counter[str] = Counter()
        self.by_significance: Counter[str] = Counter()
        self.by_category: Counter[str] = Counter()

        # enrichment economy
        self.enrich_wiki_hits = 0  # free facts from Wikipedia/Wikidata
        self.enrich_web_calls = 0  # paid web-search fallback invoked
        self.enrich_misses = 0  # neither produced facts

        # Overpass (the single point of failure that can silence the guide)
        self.overpass_ok = 0
        self.overpass_fail = 0
        self.overpass_last_error = ""

        # session_id -> per-walk rollup
        self.by_session: dict[str, dict[str, Any]] = {}

    # -- per-session helper ------------------------------------------------- #

    def _sess(self) -> dict[str, Any] | None:
        sid = SESSION_ID.get()
        if not sid:
            return None
        s = self.by_session.get(sid)
        if s is None:
            if len(self.by_session) >= _SESSION_CAP:
                self.by_session.pop(next(iter(self.by_session)), None)  # FIFO cap
            s = {
                "narrations": 0,
                "switches": 0,
                "silences": 0,
                "languages": set(),
                "first_seen": time.time(),
                "last_seen": time.time(),
            }
            self.by_session[sid] = s
        s["last_seen"] = time.time()
        return s

    # -- instrumentation entry points (called from the log points) ---------- #

    def narrate(
        self,
        *,
        significance: str | None = None,
        category: str | None = None,
        language: str | None = None,
        switching: bool = False,
    ) -> None:
        self.narrations += 1
        if switching:
            self.switches += 1
        if significance:
            self.by_significance[significance] += 1
        if category:
            self.by_category[category] += 1
        if language:
            self.by_language[language] += 1
        s = self._sess()
        if s is not None:
            s["narrations"] += 1
            if switching:
                s["switches"] += 1
            if language:
                s["languages"].add(language)

    def elaborate(self) -> None:
        self.elaborations += 1

    def floor(self) -> None:
        self.floors += 1

    def silence(self) -> None:
        self.silences += 1
        s = self._sess()
        if s is not None:
            s["silences"] += 1

    def suppress_repeat(self) -> None:
        self.suppressed_repeats += 1

    def area_beat(self) -> None:
        self.area_beats += 1

    def enrich(self, kind: str) -> None:
        if kind == "wiki":
            self.enrich_wiki_hits += 1
        elif kind == "web":
            self.enrich_web_calls += 1
        else:
            self.enrich_misses += 1

    def overpass(self, ok: bool, error: str = "") -> None:
        if ok:
            self.overpass_ok += 1
        else:
            self.overpass_fail += 1
            if error:
                self.overpass_last_error = error[:200]

    # -- read side ---------------------------------------------------------- #

    def snapshot(self) -> dict[str, Any]:
        total_enrich = (
            self.enrich_wiki_hits + self.enrich_web_calls + self.enrich_misses
        )
        wiki_share = (
            round(self.enrich_wiki_hits / total_enrich, 3) if total_enrich else None
        )
        overpass_total = self.overpass_ok + self.overpass_fail
        overpass_ok_rate = (
            round(self.overpass_ok / overpass_total, 3) if overpass_total else None
        )
        # top sessions by objects narrated (most-active walks)
        top = sorted(
            self.by_session.items(), key=lambda kv: kv[1]["narrations"], reverse=True
        )[:20]
        return {
            "uptime_s": round(time.monotonic() - _STARTED_MONO, 1),
            "started_at": _STARTED_AT,
            "narrations": self.narrations,
            "switches": self.switches,
            "elaborations": self.elaborations,
            "floors": self.floors,
            "silences": self.silences,
            "suppressed_repeats": self.suppressed_repeats,
            "area_beats": self.area_beats,
            "by_language": dict(self.by_language.most_common()),
            "by_significance": dict(self.by_significance),
            "by_category": dict(self.by_category.most_common(12)),
            "enrich_wiki_hits": self.enrich_wiki_hits,
            "enrich_web_calls": self.enrich_web_calls,
            "enrich_misses": self.enrich_misses,
            "enrich_wiki_share": wiki_share,
            "overpass_ok": self.overpass_ok,
            "overpass_fail": self.overpass_fail,
            "overpass_ok_rate": overpass_ok_rate,
            "overpass_last_error": self.overpass_last_error,
            "tracked_walks": len(self.by_session),
            "top_walks": [
                {
                    "session": k,
                    "narrations": v["narrations"],
                    "switches": v["switches"],
                    "silences": v["silences"],
                    "languages": sorted(v["languages"]),
                    "duration_s": round(v["last_seen"] - v["first_seen"], 1),
                }
                for k, v in top
            ],
        }


GUIDE = GuideMetrics()
