#!/usr/bin/env python3
"""
Compute optimal TP/SL per price level from historical MFE/MAE.

Requires:
  - DATABASE_URL with seeded level_touches / price_levels (seed_level_intelligence.py)
  - OHLCV backfill in ohlcv_candles

Usage (from backend/):
  python scripts/compute_exit_optimizer.py --symbols EURUSD,BTCUSD
  python scripts/compute_exit_optimizer.py --min-touches 8
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import get_settings
from config.symbols import SYMBOLS
from data.storage.pg_connect import is_database_url_placeholder
from ml.features.trade_exit_optimizer import TradeExitOptimizer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
logger = logging.getLogger("compute_exit_optimizer")

SYMBOL_ASSET_CLASS = {sym: spec.asset_class for sym, spec in SYMBOLS.items()}
DEFAULT_SYMBOLS = list(SYMBOL_ASSET_CLASS.keys())


def _database_url() -> str:
    return (get_settings().database_url or "").strip()


def load_all_bars(symbol: str) -> pd.DataFrame:
    """Load all 1m bars and resample to 5m."""
    from data.storage.pg_connect import connect_psycopg2
    from data.storage.timeseries_store import TimeseriesStore

    sym = symbol.upper()
    store = TimeseriesStore()
    if store._available:
        df = store.read(sym, "1m")
    else:
        url = _database_url()
        conn = connect_psycopg2(url)
        df = pd.read_sql(
            """
            SELECT time, open, high, low, close, volume
            FROM ohlcv_candles
            WHERE symbol = %s AND timeframe = '1m'
            ORDER BY time ASC
            """,
            conn,
            params=(sym,),
        )
        conn.close()
        if not df.empty:
            df["time"] = pd.to_datetime(df["time"], utc=True)
            df = df.set_index("time")

    if df.empty:
        return df

    df_5m = (
        df.resample("5min")
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        .dropna()
    )
    logger.info("%s: %d 1m bars → %d 5m bars", sym, len(df), len(df_5m))
    return df_5m


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compute optimal TP/SL per level from historical MFE/MAE"
    )
    parser.add_argument(
        "--symbols",
        default=",".join(DEFAULT_SYMBOLS),
        help="Comma-separated symbols (default: all 23)",
    )
    parser.add_argument(
        "--min-touches",
        type=int,
        default=5,
        help="Minimum touches on a level before optimizing (default: 5)",
    )
    args = parser.parse_args()

    if not _database_url() or is_database_url_placeholder(_database_url()):
        logger.error("DATABASE_URL is required")
        sys.exit(1)

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    for symbol in symbols:
        asset_class = SYMBOL_ASSET_CLASS.get(symbol, "equity")
        logger.info("─" * 60)
        logger.info("Optimizing exits for %s (%s)", symbol, asset_class)

        try:
            df = load_all_bars(symbol)
            if df.empty or len(df) < 500:
                logger.warning("%s: insufficient OHLCV bars — skipping", symbol)
                continue

            optimizer = TradeExitOptimizer(symbol, asset_class)
            optimizer.run(df, min_touches=args.min_touches)
            optimizer.print_all()
            optimizer.print_watchlist_with_exits()

        except Exception as exc:
            logger.error("%s: failed: %s", symbol, exc, exc_info=True)

    logger.info("Exit optimization complete.")


if __name__ == "__main__":
    main()
