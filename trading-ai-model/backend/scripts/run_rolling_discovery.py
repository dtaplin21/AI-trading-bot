#!/usr/bin/env python3
"""
scripts/run_rolling_discovery.py

Manual or cron entry point for rolling level discovery.
Safe to run standalone before wiring into the live worker (Phase 3).

Usage:
    python scripts/run_rolling_discovery.py --symbols MES,TSLA --days 60
    python scripts/run_rolling_discovery.py --symbols ALL --days 60 --dry-run
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("run_rolling_discovery")

# Symbol -> asset_class mapping (matches your existing watcher symbol list)
ASSET_CLASS_MAP = {
    "BTCUSD": "crypto", "ETHUSD": "crypto", "SOLUSD": "crypto",
    "XRPUSD": "crypto", "BNBUSD": "crypto",
    "EURUSD": "forex", "GBPUSD": "forex", "AUDUSD": "forex",
    "USDCHF": "forex", "USDJPY": "forex",
    "ES": "futures", "MES": "futures", "MNQ": "futures", "NQ": "futures",
    "RTY": "futures", "GC": "futures", "ZB": "futures",
    "AAPL": "equity", "AMZN": "equity", "MSFT": "equity",
    "NVDA": "equity", "TSLA": "equity",
}


def main() -> None:
    p = argparse.ArgumentParser(description="Run rolling level discovery")
    p.add_argument("--symbols", required=True, help="Comma-separated symbols, or ALL")
    p.add_argument("--days", type=int, default=60, help="Rolling window in days")
    p.add_argument("--dry-run", action="store_true", help="Compute but do not write to DB")
    args = p.parse_args()

    from ml.features.rolling_level_discovery import discover_symbol

    if args.symbols.upper() == "ALL":
        symbols = list(ASSET_CLASS_MAP.keys())
    else:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]

    print(f"\nRolling discovery | window={args.days}d | dry_run={args.dry_run}")
    print("=" * 90)

    for sym in symbols:
        asset_class = ASSET_CLASS_MAP.get(sym, "equity")
        result = discover_symbol(sym, asset_class=asset_class, window_days=args.days, dry_run=args.dry_run)

        if result.skipped_reason:
            print(f"  {sym:<8} SKIPPED — {result.skipped_reason}")
        elif result.error:
            print(f"  {sym:<8} ERROR — {result.error}")
        else:
            print(
                f"  {sym:<8} coverage={result.coverage_pct:>5.1f}%  "
                f"touches={result.levels_found:>5}  archived={result.levels_archived:>3}  "
                f"reactivated={result.levels_reactivated:>3}  active_watchlist={result.watchlist_active:>4}"
            )

    print("=" * 90)


if __name__ == "__main__":
    main()
