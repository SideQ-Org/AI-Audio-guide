"""Decision logger for the self-improvement system (Block 4).

One followable trace of what the system DOES: the quality worker's per-walk decisions (score,
taxonomy, worst blurbs, gates) and the optimizer's tuning (propose / accept / reject / rollback).
Read it with ``docker logs ai-guide-quality`` or, with ``quality_log_dir`` set, from the rotating
file at ``<dir>/quality.log`` (survives the docker-logs ring buffer).

``aiguide.quality`` is the worker channel; ``aiguide.quality.optimize`` is the optimizer channel —
both flow through the same handlers so one file/stream is the whole story.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import settings

_configured = False


def get_quality_logger() -> logging.Logger:
    """Idempotently configure the ``aiguide.quality`` logger family (stream + optional file)."""
    global _configured
    log = logging.getLogger("aiguide.quality")
    if _configured:
        return log
    log.setLevel(logging.INFO)
    log.propagate = False  # don't double-log through the root logger
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    log.addHandler(sh)
    if settings.quality_log_dir:
        Path(settings.quality_log_dir).mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            Path(settings.quality_log_dir) / "quality.log",
            maxBytes=32 * 1024 * 1024, backupCount=5, encoding="utf-8",
        )
        fh.setFormatter(fmt)
        log.addHandler(fh)
    _configured = True
    return log
