#!/usr/bin/env python3
"""
Import local OHLCV CSV files into TimescaleDB (e.g. Render Postgres later).

Use after CSV-only Polygon backfill (--skip-db). Replay still prefers local CSV;
this script copies the same files into the remote DB when you are ready.

Usage (from backend/):
  # Import all data/ohlcv/*_1m.csv files
  python scripts/import_ohlcv_csv.py --timeframe 1m

  # Specific symbols
  python scripts/import_ohlcv_csv.py --symbols MES,ES,BTCUSD --timeframe 1m

  # Point DATABASE_URL at Render, then:
  python scripts/import_ohlcv_csv.py --timeframe 1m

  # Preview without writing
  python scripts/import_ohlcv_csv.py --timeframe 1m --dry-run

Env:
  DATABASE_URL       required (unless --dry-run)
  WATCHER_DATA_PATH  default data/ohlcv
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

load_dotenv(_BACKEND / ".env")

from data.providers.csv_ohlcv_import import DEFAULT_BATCH_ROWS, discover_ohlcv_csvs, import_ohlcv_csv
from data.storage.timescale_store import TimescaleStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("import_ohlcv_csv")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Import OHLCV CSV files into TimescaleDB")
    p.add_argument(
        "--symbols",
        default=os.getenv("IMPORT_SYMBOLS", ""),
        help="Comma-separated symbols (default: all CSVs in data-path)",
    )
    p.add_argument(
        "--timeframe",
        default=os.getenv("IMPORT_TIMEFRAME", os.getenv("BACKFILL_TIMEFRAME", "1m")),
        help="Only import files matching this timeframe (e.g. 1m)",
    )
    p.add_argument(
        "--data-path",
        default=os.getenv("WATCHER_DATA_PATH", "data/ohlcv"),
        help="Directory containing {SYMBOL}_{timeframe}.csv files",
    )
    p.add_argument(
        "--batch-rows",
        type=int,
        default=int(os.getenv("IMPORT_BATCH_ROWS", str(DEFAULT_BATCH_ROWS))),
        help="Rows per DB upsert batch",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Count rows only; do not connect to the database",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    data_path = Path(args.data_path)
    if not data_path.is_absolute():
        data_path = _BACKEND / data_path

    symbols: set[str] | None = None
    if args.symbols.strip():
        symbols = {s.strip().upper() for s in args.symbols.split(",") if s.strip()}

    files = discover_ohlcv_csvs(data_path, symbols=symbols, timeframe=args.timeframe)
    if not files:
        logger.error(
            "No CSV files found in %s (timeframe=%s%s)",
            data_path,
            args.timeframe,
            f", symbols={sorted(symbols)}" if symbols else "",
        )
        return 1

    logger.info(
        "Found %d CSV file(s) in %s | timeframe=%s | dry_run=%s",
        len(files),
        data_path,
        args.timeframe,
        args.dry_run,
    )

    store: TimescaleStore | None = None
    if not args.dry_run:
        store = TimescaleStore()
        if not store.available:
            logger.error("DATABASE_URL unavailable — set it or use --dry-run")
            return 1
        logger.info("TimescaleDB connected — importing into ohlcv_candles")

    total_rows = 0
    for path, symbol, tf in files:
        total_rows += import_ohlcv_csv(
            store,
            path,
            symbol,
            tf,
            batch_rows=args.batch_rows,
            dry_run=args.dry_run,
        )

    logger.info(
        "Import complete | files=%d rows=%s%d",
        len(files),
        "~" if args.dry_run else "",
        total_rows,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
