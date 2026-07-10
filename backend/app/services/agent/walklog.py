"""Walk logging — one structured logger for the whole agent pipeline.

Goal: a single walk can be reconstructed end-to-end from
``docker logs ai-guide | grep aiguide.agent`` (or the persistent file sink, see
below) — including the TEXT the agents actually produce (narration, area beats,
replies), the coordinates walked, every external call and how many, and the reason
the guide stayed silent when it did. This is the diagnostic layer for live
debugging: markers alone weren't enough to see *what* the guide said or *why* it
went quiet, only *that* something happened.

Complements ``llm.client.METER`` (token/cost) and ``metrics.GUIDE`` (counters).

The logger is named ``aiguide.agent`` and configured with its own handlers at INFO
with ``propagate=False`` — uvicorn silences app loggers otherwise, so every module
that logs walk events must go through :func:`get_logger` (idempotent) rather than
re-attach a handler of its own.

Two extras for debugging (both cheap, both additive):

* **Session id on every line.** :data:`CURRENT_SID` is a context var set at the top
  of each tick (and around barge-in); a filter stamps it onto every record so
  concurrent walks stay separable in one stream — grep ``sid=<id>``.
* **Persistent file sink.** When ``settings.walk_log_dir`` is set, the full trace is
  *also* written to ``<dir>/walk.log`` (rotating), so a long walk survives the
  docker-logs ring buffer and can be pulled whole for analysis.
* **Per-tick call counters.** :func:`tick_reset` / :func:`tick_bump` /
  :func:`tick_snapshot` count the expensive external calls (Overpass, enrichment)
  made within one tick, so the orchestrator can log "how many calls this tick".
"""

from __future__ import annotations

import logging
import os
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler

from app.config import settings

_LOGGER_NAME = "aiguide.agent"
_CLIP_DEFAULT = 240  # spoken lines are short; this shows most of a narration in one line

# The session whose tick is currently executing — stamped onto every log record so
# one stream can carry many walks. Set in orchestrator.on_position / the ws handlers.
CURRENT_SID: ContextVar[str] = ContextVar("aiguide_walk_sid", default="-")

# Per-tick external-call counters (Overpass / enrichment). Reset at the top of a tick
# and snapshotted at the end, so a tick summary can report call volume. A ContextVar
# so concurrent sessions don't cross-count; None outside a tick (bumps are no-ops).
_TICK_COUNTERS: ContextVar[dict | None] = ContextVar("aiguide_tick_counters", default=None)


class _SidFilter(logging.Filter):
    """Inject the current session id onto every record (as ``%(sid)s``)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.sid = CURRENT_SID.get()
        return True


def _formatter() -> logging.Formatter:
    return logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s [sid=%(sid)s]: %(message)s"
    )


def get_logger() -> logging.Logger:
    """Return the shared walk logger, attaching our handlers once (idempotent)."""
    log = logging.getLogger(_LOGGER_NAME)
    if not getattr(log, "_walk_configured", False):
        log.addFilter(_SidFilter())
        h = logging.StreamHandler()
        h.setFormatter(_formatter())
        log.addHandler(h)
        # Optional persistent sink so a full walk outlives the docker-logs buffer.
        _dir = (settings.walk_log_dir or "").strip()
        if _dir:
            try:
                os.makedirs(_dir, exist_ok=True)
                fh = RotatingFileHandler(
                    os.path.join(_dir, "walk.log"),
                    maxBytes=32 * 1024 * 1024,  # 32 MiB per file
                    backupCount=5,  # keep ~160 MiB of history
                    encoding="utf-8",
                )
                fh.setFormatter(_formatter())
                log.addHandler(fh)
            except OSError:
                # A bad path must never take the walk down — degrade to stream only.
                log.warning("walk_log_dir=%r not writable — file sink disabled", _dir)
        # DEBUG when verbose so the per-candidate / per-place detail lines flow too.
        log.setLevel(logging.DEBUG if settings.walk_log_verbose else logging.INFO)
        log.propagate = False
        log._walk_configured = True  # type: ignore[attr-defined]
    return log


def clip(text: str | None, limit: int = _CLIP_DEFAULT) -> str:
    """One-line, whitespace-collapsed, length-capped snippet of agent text for the log."""
    if not text:
        return ""
    s = " ".join(text.split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


# -- per-tick call counters ------------------------------------------------- #
def tick_reset() -> None:
    """Start counting external calls for a fresh tick."""
    _TICK_COUNTERS.set({})


def tick_bump(key: str, n: int = 1) -> None:
    """Count one external call of kind ``key`` (e.g. 'overpass', 'wiki', 'web',
    'enrich_hit') within the current tick. No-op outside a tick."""
    c = _TICK_COUNTERS.get()
    if c is not None:
        c[key] = c.get(key, 0) + n


def tick_snapshot() -> dict[str, int]:
    """The calls counted since the last :func:`tick_reset` (empty outside a tick)."""
    return dict(_TICK_COUNTERS.get() or {})
