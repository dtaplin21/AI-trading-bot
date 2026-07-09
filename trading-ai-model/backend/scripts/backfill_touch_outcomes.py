#!/usr/bin/env python3
"""
scripts/backfill_touch_outcomes.py

Classify pending level_touches (outcome IS NULL or 'pending') using forward
1m OHLCV bars, then reaggregate price_levels hold/break stats.

Run after live watcher accumulated pending rows without resolving them.

Usage (from backend/):
  .venv/bin/python scripts/backfill_touch_outcomes.py --symbols ETHUSD,USDCHF
  .venv/bin/python scripts/backfill_touch_outcomes.py --symbols ALL
  .venv/bin/python scripts/backfill_touch_outcomes.py --symbols ETHUSD --dry-run

Optional follow-up:
  .venv/bin/python scripts/compute_exit_optimizer.py --symbols ETHUSD
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.symbols import SYMBOLS
from ml.features.touch_outcome_classifier import backfill_pending_outcomes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
logger = logging.getLogger("backfill_touch_outcomes")


def main() -> None:
    p = argparse.ArgumentParser(description="Backfill pending level_touch outcomes")
    p.add_argument("--symbols", required=True, help="Comma-separated symbols or ALL")
    p.add_argument("--batch-size", type=int, default=500)
    p.add_argument("--max-rows", type=int, default=None, help="Cap rows per symbol (testing)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if args.symbols.upper() == "ALL":
        symbols = sorted(SYMBOLS.keys())
    else:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    summary: dict[str, dict] = {}
    for sym in symbols:
        logger.info("Backfilling %s ...", sym)
        stats = backfill_pending_outcomes(
            sym,
            batch_size=args.batch_size,
            max_rows=args.max_rows,
            dry_run=args.dry_run,
        )
        summary[sym] = stats
        logger.info(
            "%s: seen=%d classified=%d skipped_no_bars=%d errors=%d",
            sym,
            stats["pending_seen"],
            stats["classified"],
            stats["skipped_no_bars"],
            stats["errors"],
        )

    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
