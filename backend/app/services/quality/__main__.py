"""Quality-worker container entrypoint (Block 4 Phase 4).

    python -m app.services.quality            # poll loop (config-driven)
    python -m app.services.quality --once      # a single sweep then exit (CI / cron)
    python -m app.services.quality --judge     # force the LLM judge on for this run
"""

from __future__ import annotations

import argparse
import asyncio

from app.config import settings

from .qlog import get_quality_logger
from .worker import run_forever, sweep_once


def main() -> None:
    get_quality_logger()  # configure the followable decision log (stream + optional file sink)
    ap = argparse.ArgumentParser(description="Interestingness quality worker (Block 4)")
    ap.add_argument("--once", action="store_true", help="one sweep then exit")
    ap.add_argument("--judge", action="store_true", help="force the LLM judge on")
    args = ap.parse_args()

    use_judge = args.judge or settings.quality_worker_use_judge
    if args.once:
        n = asyncio.run(sweep_once(use_judge=use_judge, limit=settings.quality_worker_limit))
        print(f"scored {n} walk(s)")
    else:
        asyncio.run(
            run_forever(use_judge=use_judge, interval_s=settings.quality_worker_interval_s)
        )


if __name__ == "__main__":
    main()
