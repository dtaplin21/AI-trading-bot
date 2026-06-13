#!/usr/bin/env python3
"""
Seed level_touches and price_levels from historical OHLCV in TimescaleDB.

Run once after backfill completes. After seeding, LevelIntelligenceSystem can
answer top levels, P(reversal) at a price, volume analysis, and watchlist.

Usage (from backend/):
  DATABASE_URL="..." DATABASE_SSL_DISABLE=true python scripts/seed_level_intelligence.py
  DATABASE_URL="..." python scripts/seed_level_intelligence.py --symbols EURUSD,BTCUSD
  DATABASE_URL="..." python scripts/seed_level_intelligence.py --replace --symbols TSLA

Env:
  DATABASE_URL              required
  DATABASE_SSL_DISABLE      set true for local Postgres without SSL
  LEVEL_INTEL_WATCH_MIN_*   same thresholds as live LevelIntelligenceSystem
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import get_settings
from config.symbols import SYMBOLS
from data.storage.pg_connect import connect_psycopg2, is_database_url_placeholder
from ml.features.level_intelligence import SCHEMA_SQL, LevelIntelligenceSystem
from ml.training.train_reversal_models import ASSET_CONFIGS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)
logger = logging.getLogger("seed_levels")

SYMBOL_ASSET_CLASS = {sym: spec.asset_class for sym, spec in SYMBOLS.items()}
DEFAULT_SYMBOLS = list(SYMBOL_ASSET_CLASS.keys())

WATCHLIST_MIN_HOLD = float(os.getenv("LEVEL_INTEL_WATCH_MIN_HOLD", "0.62"))
WATCHLIST_MIN_TOUCHES = int(os.getenv("LEVEL_INTEL_WATCH_MIN_TOUCHES", "5"))
WATCHLIST_MIN_STRENGTH = float(os.getenv("LEVEL_INTEL_WATCH_MIN_STRENGTH", "0.55"))


def _database_url() -> str:
    return (get_settings().database_url or os.getenv("DATABASE_URL", "")).strip()


def get_conn():
    url = _database_url()
    if not url or is_database_url_placeholder(url):
        raise RuntimeError("DATABASE_URL is not configured")
    return connect_psycopg2(url)


def load_all_bars(symbol: str) -> pd.DataFrame:
    """Load all 1m bars and resample to 5m (same as train_reversal_models)."""
    from data.storage.timeseries_store import TimeseriesStore

    store = TimeseriesStore()
    if not store._available:
        conn = get_conn()
        query = """
            SELECT time, open, high, low, close, volume
            FROM ohlcv_candles
            WHERE symbol = %s AND timeframe = '1m'
            ORDER BY time ASC
        """
        df = pd.read_sql(query, conn, params=(symbol.upper(),))
        conn.close()
        if df.empty:
            return df
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df = df.set_index("time")
    else:
        df = store.read(symbol.upper(), "1m")
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
    if not isinstance(df_5m, pd.DataFrame):
        raise TypeError(f"{symbol}: 5m resample did not produce a DataFrame")
    logger.info("%s: %d 1m bars → %d 5m bars", symbol, len(df), len(df_5m))
    return df_5m


def _compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / (loss + 1e-10)
    return cast(pd.Series, 100.0 - (100.0 / (1.0 + rs)))


def _compute_macd_hist(series: pd.Series) -> pd.Series:
    ef = series.ewm(span=12).mean()
    es = series.ewm(span=26).mean()
    ml = ef - es
    sl = ml.ewm(span=9).mean()
    return ml - sl


def _compute_atr_pct(df: pd.DataFrame) -> pd.Series:
    close = df["close"]
    atr = (
        pd.concat(
            [
                df["high"] - df["low"],
                (df["high"] - close.shift()).abs(),
                (df["low"] - close.shift()).abs(),
            ],
            axis=1,
        )
        .max(axis=1)
        .rolling(14)
        .mean()
    )
    return atr / (close + 1e-10)


def _compute_bb_pos(series: pd.Series, period: int = 20) -> pd.Series:
    sma = series.rolling(period).mean()
    sd = series.rolling(period).std()
    upper = sma + 2 * sd
    lower = sma - 2 * sd
    return (series - lower) / (upper - lower + 1e-10)


def _outcome_move(
    approach: str,
    outcome: str,
    up_move: float,
    down_move: float,
) -> float:
    if approach == "from_above":
        return up_move if outcome == "hold" else -down_move
    return -down_move if outcome == "hold" else up_move


def find_reversal_touches(df: pd.DataFrame, asset_class: str) -> list[dict[str, Any]]:
    """Scan all bars and return every reversal touch with full snapshot."""
    cfg = ASSET_CONFIGS.get(asset_class, ASSET_CONFIGS["equity"])
    rev_pct = cfg.min_move_pct / 100.0
    trend_pct = cfg.min_trend_pct / 100.0
    fw = cfg.forward_window
    lb = cfg.prior_trend_bars

    close = np.array(df["close"], dtype=float)
    high = np.array(df["high"], dtype=float)
    low = np.array(df["low"], dtype=float)
    volume = np.array(df["volume"], dtype=float)
    close_s = df.loc[:, "close"]
    n = len(df)

    vol_ma = np.asarray(pd.Series(volume, dtype=float).rolling(20).mean(), dtype=float)
    rsi_s = _compute_rsi(close_s)
    macd_h = _compute_macd_hist(close_s)
    atr_pct = _compute_atr_pct(df)
    bb_pos = _compute_bb_pos(close_s)

    touches: list[dict[str, Any]] = []

    for i in range(lb, n - fw):
        current = float(close[i])
        prior_high = float(np.max(high[i - lb : i]))
        prior_low = float(np.min(low[i - lb : i]))
        prior_down = (prior_high - current) / (prior_high + 1e-10)
        prior_up = (current - prior_low) / (prior_low + 1e-10)

        if prior_down < trend_pct and prior_up < trend_pct:
            continue

        approach = "from_above" if prior_down >= trend_pct else "from_below"

        future_high = float(np.max(high[i + 1 : i + fw + 1]))
        future_low = float(np.min(low[i + 1 : i + fw + 1]))
        up_move = (future_high - current) / (current + 1e-10)
        down_move = (current - future_low) / (current + 1e-10)

        if approach == "from_above":
            outcome = "hold" if up_move >= rev_pct else "break"
        else:
            outcome = "hold" if down_move >= rev_pct else "break"

        vm = vol_ma[i] if not np.isnan(vol_ma[i]) else volume[i]
        vol_ratio = volume[i] / (vm + 1e-10)

        bar_ts = cast(pd.Timestamp, df.index[i])
        hour = int(bar_ts.hour)
        sess = (
            "OVERLAP"
            if 13 <= hour < 16
            else "NEW_YORK"
            if 13 <= hour < 21
            else "LONDON"
            if 7 <= hour < 16
            else "ASIA"
        )

        move = _outcome_move(approach, outcome, up_move, down_move)
        touches.append(
            {
                "touched_at": bar_ts.isoformat(),
                "price_at_touch": round(current, 6),
                "approach": approach,
                "outcome": outcome,
                "volume_at_touch": round(float(volume[i]), 2),
                "volume_ratio": round(float(vol_ratio), 3),
                "rsi_14": round(
                    float(rsi_s.iloc[i]) if not np.isnan(rsi_s.iloc[i]) else 50.0, 2
                ),
                "macd_histogram": round(
                    float(macd_h.iloc[i]) if not np.isnan(macd_h.iloc[i]) else 0.0, 6
                ),
                "atr_pct": round(
                    float(atr_pct.iloc[i]) if not np.isnan(atr_pct.iloc[i]) else 0.0, 4
                ),
                "bb_position": round(
                    float(bb_pos.iloc[i]) if not np.isnan(bb_pos.iloc[i]) else 0.5, 4
                ),
                "session": sess,
                "price_move_after": round(float(move) * 100, 3),
                "bars_to_outcome": fw,
            }
        )

    return touches


def _cluster_prices(
    prices: list[float],
    cluster_pct: float,
) -> tuple[dict[float, float], dict[float, tuple[float, float]]]:
    """Return price→level mapping and level→(min,max) zones."""
    if not prices:
        return {}, {}

    sorted_prices = sorted(prices)
    tol = cluster_pct / 100.0
    clusters: list[list[float]] = [[sorted_prices[0]]]
    for p in sorted_prices[1:]:
        center = float(np.mean(clusters[-1]))
        if abs(p - center) / (center + 1e-10) <= tol:
            clusters[-1].append(p)
        else:
            clusters.append([p])

    price_to_level: dict[float, float] = {}
    level_zones: dict[float, tuple[float, float]] = {}
    for cluster in clusters:
        center = round(float(np.mean(cluster)), 5)
        cmin = round(float(min(cluster)) * (1 - tol * 0.5), 6)
        cmax = round(float(max(cluster)) * (1 + tol * 0.5), 6)
        for p in cluster:
            price_to_level[round(p, 6)] = center
        level_zones[center] = (cmin, cmax)

    return price_to_level, level_zones


def _nearest_level(raw_p: float, level_zones: dict[float, tuple[float, float]]) -> float:
    best = raw_p
    min_dist = 1e9
    for lp in level_zones:
        dist = abs(raw_p - lp)
        if dist < min_dist:
            min_dist = dist
            best = lp
    return best


def _symbol_already_seeded(symbol: str) -> bool:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM level_touches WHERE symbol = %s", (symbol,))
    count = int(cur.fetchone()[0])
    cur.close()
    conn.close()
    return count > 0


def _clear_symbol(symbol: str) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM level_watchlist WHERE symbol = %s", (symbol,))
    cur.execute("DELETE FROM level_touches WHERE symbol = %s", (symbol,))
    cur.execute("DELETE FROM price_levels WHERE symbol = %s", (symbol,))
    conn.commit()
    cur.close()
    conn.close()
    logger.info("%s: cleared existing level intelligence rows", symbol)


def cluster_and_upsert(
    symbol: str,
    asset_class: str,
    touches: list[dict[str, Any]],
    cluster_pct: float,
) -> None:
    """Cluster touches into levels and upsert to Postgres."""
    if not touches:
        logger.warning("%s: no touches found", symbol)
        return

    price_to_level, level_zones = _cluster_prices(
        [t["price_at_touch"] for t in touches],
        cluster_pct,
    )

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(SCHEMA_SQL)
    conn.commit()

    for lp, (lmin, lmax) in level_zones.items():
        cur.execute(
            """
            INSERT INTO price_levels (symbol, level_price, price_min, price_max)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (symbol, level_price) DO UPDATE SET
                price_min = LEAST(price_levels.price_min, EXCLUDED.price_min),
                price_max = GREATEST(price_levels.price_max, EXCLUDED.price_max)
            """,
            (symbol, lp, lmin, lmax),
        )
    conn.commit()

    batch_size = 1000
    inserted = 0
    rows: list[tuple[Any, ...]] = []
    for t in touches:
        raw_p = round(t["price_at_touch"], 6)
        best_level = price_to_level.get(raw_p) or _nearest_level(raw_p, level_zones)
        rows.append(
            (
                symbol,
                best_level,
                t["touched_at"],
                t["price_at_touch"],
                t["approach"],
                t["outcome"],
                t["volume_at_touch"],
                t["volume_ratio"],
                t["rsi_14"],
                t["macd_histogram"],
                t["atr_pct"],
                t["bb_position"],
                t["session"],
                t["price_move_after"],
                t["bars_to_outcome"],
            )
        )

    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        cur.executemany(
            """
            INSERT INTO level_touches
                (symbol, level_price, touched_at, price_at_touch, approach,
                 outcome, volume_at_touch, volume_ratio, rsi_14, macd_histogram,
                 atr_pct, bb_position, session, price_move_after, bars_to_outcome)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            batch,
        )
        conn.commit()
        inserted += len(batch)
        logger.info("%s: inserted %d / %d touches", symbol, inserted, len(touches))

    cur.execute(
        """
        UPDATE price_levels pl
        SET
            touch_count      = sub.total,
            hold_count       = sub.holds,
            break_count      = sub.breaks,
            support_count    = sub.support,
            resistance_count = sub.resistance,
            hold_rate        = sub.holds::FLOAT / NULLIF(sub.total, 0),
            last_touched     = sub.last_ts,
            role = CASE
                WHEN sub.support::FLOAT / NULLIF(sub.support + sub.resistance, 0) >= 0.65
                    THEN 'SUPPORT'
                WHEN sub.resistance::FLOAT / NULLIF(sub.support + sub.resistance, 0) >= 0.65
                    THEN 'RESISTANCE'
                WHEN sub.total >= 3 THEN 'MIXED'
                ELSE 'UNKNOWN'
            END
        FROM (
            SELECT
                level_price,
                COUNT(*) AS total,
                SUM(CASE WHEN outcome='hold' THEN 1 ELSE 0 END) AS holds,
                SUM(CASE WHEN outcome='break' THEN 1 ELSE 0 END) AS breaks,
                SUM(CASE WHEN approach='from_below' THEN 1 ELSE 0 END) AS support,
                SUM(CASE WHEN approach='from_above' THEN 1 ELSE 0 END) AS resistance,
                MAX(touched_at) AS last_ts
            FROM level_touches
            WHERE symbol = %s
            GROUP BY level_price
        ) sub
        WHERE pl.symbol = %s AND pl.level_price = sub.level_price
        """,
        (symbol, symbol),
    )

    cur.execute(
        """
        UPDATE level_watchlist
        SET is_active = FALSE
        WHERE symbol = %s
        """,
        (symbol,),
    )

    cur.execute(
        """
        INSERT INTO level_watchlist
            (symbol, level_price, hold_rate, touch_count, strength_score, role, entry_side, is_active)
        SELECT
            symbol,
            level_price,
            hold_rate,
            touch_count,
            GREATEST(0,
                (hold_rate + 1.96*1.96/(2*touch_count)
                 - 1.96 * SQRT(hold_rate*(1-hold_rate)/touch_count
                               + 1.96*1.96/(4*touch_count*touch_count)))
                / (1 + 1.96*1.96/touch_count)
            ) AS strength_score,
            role,
            CASE role
                WHEN 'SUPPORT' THEN 'BUY'
                WHEN 'RESISTANCE' THEN 'SELL'
                ELSE 'EITHER'
            END AS entry_side,
            TRUE
        FROM price_levels
        WHERE symbol = %s
          AND touch_count >= %s
          AND hold_rate >= %s
        ON CONFLICT (symbol, level_price) DO UPDATE SET
            hold_rate      = EXCLUDED.hold_rate,
            touch_count    = EXCLUDED.touch_count,
            strength_score = EXCLUDED.strength_score,
            role           = EXCLUDED.role,
            entry_side     = EXCLUDED.entry_side,
            is_active      = TRUE,
            added_at       = NOW()
        """,
        (symbol, WATCHLIST_MIN_TOUCHES, WATCHLIST_MIN_HOLD),
    )

    cur.execute(
        """
        UPDATE level_watchlist
        SET is_active = FALSE
        WHERE symbol = %s
          AND strength_score < %s
        """,
        (symbol, WATCHLIST_MIN_STRENGTH),
    )

    conn.commit()
    cur.close()
    conn.close()

    logger.info(
        "%s: seeding complete | %d touches | %d levels",
        symbol,
        len(touches),
        len(level_zones),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed level intelligence from historical OHLCV bars"
    )
    parser.add_argument(
        "--symbols",
        default=",".join(DEFAULT_SYMBOLS),
        help="Comma-separated symbols (default: all 23)",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Delete existing level data for each symbol before seeding",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan bars and count touches without writing to the database",
    )
    args = parser.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    if not args.dry_run and not _database_url():
        logger.error("DATABASE_URL is required (unless --dry-run)")
        sys.exit(1)

    for symbol in symbols:
        asset_class = SYMBOL_ASSET_CLASS.get(symbol, "equity")
        cluster_pct = LevelIntelligenceSystem.CLUSTER_PCT.get(asset_class, 0.10)

        logger.info("─" * 60)
        logger.info("Seeding %s (%s)", symbol, asset_class)

        try:
            if not args.replace and not args.dry_run and _symbol_already_seeded(symbol):
                logger.info(
                    "%s: already seeded — use --replace to re-seed", symbol
                )
                continue

            df = load_all_bars(symbol)
            if df.empty or len(df) < 500:
                logger.warning("%s: insufficient bars — skipping", symbol)
                continue

            touches = find_reversal_touches(df, asset_class)
            logger.info("%s: found %d reversal touches", symbol, len(touches))

            if args.dry_run:
                logger.info("%s: dry-run — no database writes", symbol)
                continue

            if args.replace:
                _clear_symbol(symbol)

            cluster_and_upsert(symbol, asset_class, touches, cluster_pct)

            system = LevelIntelligenceSystem(symbol, asset_class)
            top = system.get_top_levels(10)
            if not top.empty:
                logger.info("%s top levels:\n%s", symbol, top.to_string(index=False))
            watchlist = system.get_watchlist()
            if not watchlist.empty:
                logger.info("%s watchlist:\n%s", symbol, watchlist.to_string(index=False))

        except Exception as exc:
            logger.error("%s: failed: %s", symbol, exc, exc_info=True)

    logger.info("Seeding complete.")


if __name__ == "__main__":
    main()
