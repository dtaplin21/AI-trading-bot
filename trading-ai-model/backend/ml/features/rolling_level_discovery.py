"""
Rolling swing level discovery — Phase 1 (manual / cron).

Discovers levels from a sliding OHLCV window, merges into price_levels,
archives stale rows, syncs level_watchlist, and writes level_discovery_runs.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from config.settings import get_settings
from config.symbols import get_symbol_or_none
from data.storage.pg_connect import connect_psycopg2, is_database_url_placeholder
from ml.features.level_history import LEVEL_CONFIGS, Level, LevelHistoryTracker
from ml.features.level_intelligence import SCHEMA_SQL

logger = logging.getLogger("rolling_level_discovery")

WATCHLIST_MIN_HOLD = float(os.getenv("LEVEL_INTEL_WATCH_MIN_HOLD", "0.62"))
WATCHLIST_MIN_TOUCHES = int(os.getenv("LEVEL_INTEL_WATCH_MIN_TOUCHES", "5"))
WATCHLIST_MIN_STRENGTH = float(os.getenv("LEVEL_INTEL_WATCH_MIN_STRENGTH", "0.55"))
STALE_PCT = float(os.getenv("LEVEL_DISCOVERY_STALE_PCT", "3.0"))
REGIME_GAP_PCT = float(os.getenv("LEVEL_DISCOVERY_REGIME_GAP_PCT", "8.0"))
MIN_BARS = int(os.getenv("LEVEL_DISCOVERY_MIN_BARS", "500"))
MIN_COVERAGE_PCT = float(os.getenv("LEVEL_DISCOVERY_MIN_COVERAGE_PCT", "80.0"))

_ARCHIVE_SOURCE_COLS = (
    "symbol",
    "level_price",
    "price_min",
    "price_max",
    "touch_count",
    "hold_count",
    "break_count",
    "support_count",
    "resistance_count",
    "hold_rate",
    "strength_score",
    "role",
    "first_seen",
    "last_touched",
)


@dataclass
class DiscoveryResult:
    symbol: str
    window_days: int
    bars_loaded: int = 0
    bars_expected: int = 0
    coverage_pct: float = 0.0
    levels_found: int = 0
    levels_merged: int = 0
    levels_archived: int = 0
    levels_reactivated: int = 0
    watchlist_active: int = 0
    last_close: float | None = None
    merge_mode: str | None = None
    trigger_reason: str | None = None
    regime_gap_pct: float | None = None
    envelope_min: float | None = None
    envelope_max: float | None = None
    runs_coalesced: int = 0
    skipped_reason: str | None = None
    error: str | None = None


def _database_url() -> str:
    return (get_settings().database_url or os.getenv("DATABASE_URL", "")).strip()


def _ensure_discovery_schema(cur) -> None:
    """Apply level intelligence base schema + discovery migrations if present."""
    cur.execute(SCHEMA_SQL)
    migrations_dir = Path(__file__).resolve().parents[2] / "db/migrations"
    for name in ("008_level_discovery.sql", "009_level_discovery_audit.sql"):
        migration = migrations_dir / name
        if migration.is_file():
            cur.execute(migration.read_text())


def _get_conn():
    url = _database_url()
    if not url or is_database_url_placeholder(url):
        raise RuntimeError("DATABASE_URL is not configured")
    return connect_psycopg2(url)


def bars_expected_5m(window_days: int) -> int:
    """Rough expected 5m bar count (24/7 markets)."""
    return max(1, window_days * 24 * 12)


def check_window_coverage(symbol: str, window_days: int) -> tuple[int, int, float]:
    """Return (1m bars loaded, 1m bars expected, coverage pct) for the sliding window."""
    sym = symbol.upper()
    expected = max(1, window_days * 1440)
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*)
            FROM ohlcv_candles
            WHERE symbol = %s
              AND timeframe = '1m'
              AND time >= NOW() - make_interval(days => %s)
              AND close > 0
            """,
            (sym, window_days),
        )
        row = cur.fetchone()
        loaded = int(row[0]) if row else 0
        cur.close()
    finally:
        conn.close()
    pct = min(100.0, loaded / expected * 100.0) if expected else 0.0
    return loaded, expected, pct


def load_bars_window(symbol: str, window_days: int) -> pd.DataFrame:
    """Load last N days of 1m bars, resample to 5m, drop invalid closes."""
    sym = symbol.upper()
    conn = _get_conn()
    try:
        df = pd.read_sql(
            """
            SELECT time, open, high, low, close, volume
            FROM ohlcv_candles
            WHERE symbol = %s
              AND timeframe = '1m'
              AND time >= NOW() - make_interval(days => %s)
              AND close > 0
            ORDER BY time ASC
            """,
            conn,
            params=(sym, window_days),
        )
    finally:
        conn.close()

    if df.empty:
        return df

    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df.set_index("time")
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
    if isinstance(df_5m, pd.DataFrame) and not df_5m.empty:
        df_5m = df_5m.loc[df_5m["close"] > 0]
    return df_5m if isinstance(df_5m, pd.DataFrame) else pd.DataFrame()


def price_levels_envelope(symbol: str, conn) -> tuple[float, float] | None:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT MIN(level_price), MAX(level_price)
        FROM price_levels
        WHERE symbol = %s AND COALESCE(is_active, TRUE) = TRUE
        """,
        (symbol.upper(),),
    )
    row = cur.fetchone()
    cur.close()
    if not row or row[0] is None or row[1] is None:
        return None
    return float(row[0]), float(row[1])


def is_outside_envelope(close: float, env_min: float, env_max: float, buffer_pct: float) -> bool:
    buf = buffer_pct / 100.0
    return close < env_min * (1.0 - buf) or close > env_max * (1.0 + buf)


def envelope_escape_gap_pct(
    close: float,
    envelope: tuple[float, float] | None,
    *,
    buffer_pct: float | None = None,
) -> float | None:
    """Percent outside buffered envelope; None if inside or no envelope."""
    if envelope is None:
        return None
    buf = buffer_pct if buffer_pct is not None else float(
        os.getenv("LEVEL_DISCOVERY_RANGE_BUFFER_PCT", "0.15")
    )
    env_min, env_max = envelope
    if not is_outside_envelope(close, env_min, env_max, buf):
        return None
    upside = max(0.0, (close - env_max) / env_max * 100.0) if env_max else 0.0
    downside = max(0.0, (env_min - close) / env_min * 100.0) if env_min else 0.0
    return max(upside, downside)


def classify_discovery_mode(
    close: float,
    envelope: tuple[float, float] | None,
    *,
    buffer_pct: float | None = None,
) -> str:
    if envelope is None:
        return "drift"
    gap_pct = envelope_escape_gap_pct(close, envelope, buffer_pct=buffer_pct)
    if gap_pct is None:
        return "drift"
    return "regime_shift" if gap_pct >= REGIME_GAP_PCT else "drift"


def _cluster_pct(asset_class: str) -> float:
    cfg = LEVEL_CONFIGS.get(asset_class, LEVEL_CONFIGS["equity"])
    return cfg.cluster_tolerance_pct


def _match_level_price(price: float, existing: list[tuple[float, bool]], cluster_pct: float) -> int | None:
    tol = cluster_pct / 100.0
    best_idx: int | None = None
    best_dist = float("inf")
    for i, (lp, _active) in enumerate(existing):
        dist = abs(lp - price) / (price + 1e-10)
        if dist <= tol and dist < best_dist:
            best_dist = dist
            best_idx = i
    return best_idx


def _archive_level(cur, symbol: str, level_price: float, reason: str) -> None:
    cols = ", ".join(_ARCHIVE_SOURCE_COLS)
    cur.execute(
        f"""
        INSERT INTO price_levels_archive ({cols}, archived_at, archive_reason)
        SELECT {cols}, NOW(), %s
        FROM price_levels
        WHERE symbol = %s AND level_price = %s
        """,
        (reason, symbol.upper(), level_price),
    )
    cur.execute(
        """
        UPDATE price_levels
        SET is_active = FALSE
        WHERE symbol = %s AND level_price = %s
        """,
        (symbol.upper(), level_price),
    )
    cur.execute(
        """
        UPDATE level_watchlist
        SET is_active = FALSE
        WHERE symbol = %s AND level_price = %s
        """,
        (symbol.upper(), level_price),
    )


def archive_stale_levels(symbol: str, last_close: float) -> int:
    """Archive active levels whose price is far from last_close (drift stale)."""
    sym = symbol.upper()
    stale_frac = STALE_PCT / 100.0
    conn = _get_conn()
    count = 0
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT symbol, level_price, touch_count, hold_rate, last_touched
            FROM price_levels
            WHERE symbol = %s AND COALESCE(is_active, TRUE) = TRUE
            """,
            (sym,),
        )
        rows = cur.fetchall()
        for row in rows:
            if isinstance(row, dict):
                level_price = float(row["level_price"])
            else:
                level_price = float(row[1])
            dist = abs(level_price - last_close) / (last_close + 1e-10)
            if dist > stale_frac:
                _archive_level(cur, sym, level_price, "drift_stale")
                count += 1
        conn.commit()
        cur.close()
    finally:
        conn.close()
    return count


def reactivate_if_price_returns(symbol: str, current_price: float) -> int:
    """Reactivate archived levels when current price is inside their price zone."""
    sym = symbol.upper()
    conn = _get_conn()
    count = 0
    try:
        cur = conn.cursor()
        _ensure_discovery_schema(cur)
        cur.execute(
            """
            SELECT symbol, level_price, price_min, price_max, touch_count, hold_rate
            FROM price_levels
            WHERE symbol = %s
              AND COALESCE(is_active, TRUE) = FALSE
              AND %s BETWEEN price_min AND price_max
            """,
            (sym, current_price),
        )
        rows = cur.fetchall()
        for row in rows:
            if isinstance(row, dict):
                level_price = float(row["level_price"])
            else:
                level_price = float(row[1])
            cur.execute(
                """
                UPDATE price_levels
                SET is_active = TRUE,
                    discovery_source = 'rolling',
                    last_discovery_at = NOW()
                WHERE symbol = %s AND level_price = %s
                """,
                (sym, level_price),
            )
            count += 1
        conn.commit()
        cur.close()
    finally:
        conn.close()
    return count


def _apply_level_stats(cur, symbol: str, level_price: float, level: Level) -> None:
    strength = round(level.strength_score, 4)
    cur.execute(
        """
        UPDATE price_levels
        SET price_min = LEAST(price_min, %s),
            price_max = GREATEST(price_max, %s),
            touch_count = %s,
            hold_count = %s,
            break_count = %s,
            hold_rate = %s,
            strength_score = %s,
            is_active = TRUE,
            discovery_source = 'rolling',
            last_discovery_at = NOW()
        WHERE symbol = %s AND level_price = %s
        """,
        (
            round(level.price_min, 6),
            round(level.price_max, 6),
            level.touch_count,
            level.hold_count,
            level.break_count,
            round(level.hold_rate, 4),
            strength,
            symbol.upper(),
            level_price,
        ),
    )


def _insert_discovered_level(cur, symbol: str, level: Level) -> None:
    strength = round(level.strength_score, 4)
    cur.execute(
        """
        INSERT INTO price_levels (
            symbol, level_price, price_min, price_max,
            touch_count, hold_count, break_count,
            hold_rate, strength_score, role,
            is_active, discovery_source, last_discovery_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE, 'rolling', NOW())
        ON CONFLICT (symbol, level_price) DO UPDATE SET
            price_min = LEAST(price_levels.price_min, EXCLUDED.price_min),
            price_max = GREATEST(price_levels.price_max, EXCLUDED.price_max),
            touch_count = EXCLUDED.touch_count,
            hold_count = EXCLUDED.hold_count,
            break_count = EXCLUDED.break_count,
            hold_rate = EXCLUDED.hold_rate,
            strength_score = EXCLUDED.strength_score,
            is_active = TRUE,
            discovery_source = 'rolling',
            last_discovery_at = NOW()
        """,
        (
            symbol.upper(),
            round(level.price, 5),
            round(level.price_min, 6),
            round(level.price_max, 6),
            level.touch_count,
            level.hold_count,
            level.break_count,
            round(level.hold_rate, 4),
            strength,
            "UNKNOWN",
        ),
    )


def _sync_watchlist(cur, symbol: str) -> int:
    cur.execute(
        """
        UPDATE level_watchlist
        SET is_active = FALSE
        WHERE symbol = %s
        """,
        (symbol.upper(),),
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
          AND COALESCE(is_active, TRUE) = TRUE
          AND touch_count >= %s
          AND hold_rate >= %s
        ON CONFLICT (symbol, level_price) DO UPDATE SET
            hold_rate = EXCLUDED.hold_rate,
            touch_count = EXCLUDED.touch_count,
            strength_score = EXCLUDED.strength_score,
            role = EXCLUDED.role,
            entry_side = EXCLUDED.entry_side,
            is_active = TRUE,
            added_at = NOW()
        """,
        (symbol.upper(), WATCHLIST_MIN_TOUCHES, WATCHLIST_MIN_HOLD),
    )
    cur.execute(
        """
        UPDATE level_watchlist
        SET is_active = FALSE
        WHERE symbol = %s AND strength_score < %s
        """,
        (symbol.upper(), WATCHLIST_MIN_STRENGTH),
    )
    cur.execute(
        """
        SELECT COUNT(*) FROM level_watchlist
        WHERE symbol = %s AND is_active = TRUE
        """,
        (symbol.upper(),),
    )
    row = cur.fetchone()
    return int(row[0]) if row else 0


def _write_audit_row(cur, result: DiscoveryResult) -> None:
    regime_gap_pct = (
        round(result.regime_gap_pct, 4) if result.regime_gap_pct is not None else None
    )
    cur.execute(
        """
        INSERT INTO level_discovery_runs (
            symbol, window_days, bars_loaded, bars_expected, coverage_pct,
            levels_found, levels_merged, levels_archived, levels_reactivated,
            watchlist_active, last_close, skipped_reason, runs_coalesced,
            trigger_reason, merge_mode, regime_gap_pct, envelope_min, envelope_max,
            finished_at, error
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s)
        """,
        (
            result.symbol.upper(),
            result.window_days,
            result.bars_loaded,
            result.bars_expected,
            round(result.coverage_pct, 2),
            result.levels_found,
            result.levels_merged,
            result.levels_archived,
            result.levels_reactivated,
            result.watchlist_active,
            result.last_close,
            result.skipped_reason,
            result.runs_coalesced,
            result.trigger_reason,
            result.merge_mode,
            regime_gap_pct,
            result.envelope_min,
            result.envelope_max,
            result.error,
        ),
    )


def log_discovery_run(result: DiscoveryResult) -> None:
    """Persist a level_discovery_runs audit row."""
    conn = _get_conn()
    try:
        cur = conn.cursor()
        _ensure_discovery_schema(cur)
        _write_audit_row(cur, result)
        conn.commit()
        cur.close()
    finally:
        conn.close()


def discover_symbol(
    symbol: str,
    *,
    asset_class: str | None = None,
    window_days: int = 60,
    dry_run: bool = False,
    trigger_reason: str | None = None,
    runs_coalesced: int = 0,
) -> DiscoveryResult:
    sym = symbol.upper()
    if asset_class:
        ac = asset_class
    else:
        spec = get_symbol_or_none(sym)
        ac = spec.asset_class if spec is not None else "equity"
    result = DiscoveryResult(
        symbol=sym,
        window_days=window_days,
        trigger_reason=trigger_reason,
        runs_coalesced=runs_coalesced,
    )

    try:
        loaded, expected, coverage_pct = check_window_coverage(sym, window_days)
        result.bars_loaded = loaded
        result.bars_expected = expected
        result.coverage_pct = coverage_pct

        if result.bars_loaded < MIN_BARS:
            result.skipped_reason = "insufficient_bars"
            if not dry_run:
                log_discovery_run(result)
            return result

        if result.coverage_pct < MIN_COVERAGE_PCT:
            result.skipped_reason = "insufficient_coverage"
            if not dry_run:
                log_discovery_run(result)
            return result

        df = load_bars_window(sym, window_days)
        result.bars_loaded = len(df)
        result.bars_expected = bars_expected_5m(window_days)
        result.coverage_pct = (
            min(100.0, result.bars_loaded / result.bars_expected * 100.0)
            if result.bars_expected
            else 0.0
        )

        result.last_close = float(df["close"].iloc[-1])
        tracker = LevelHistoryTracker(symbol=sym, asset_class=ac)
        tracker.fit(df)
        discovered: list[Level] = tracker.levels
        result.levels_found = len(discovered)

        if not discovered:
            result.skipped_reason = "no_levels_found"
            if not dry_run:
                log_discovery_run(result)
            return result

        if dry_run:
            return result

        conn = _get_conn()
        try:
            cur = conn.cursor()
            _ensure_discovery_schema(cur)

            envelope = price_levels_envelope(sym, conn)
            if envelope is not None:
                result.envelope_min, result.envelope_max = envelope
            result.regime_gap_pct = envelope_escape_gap_pct(result.last_close, envelope)
            result.merge_mode = classify_discovery_mode(result.last_close, envelope)

            cur.execute(
                """
                SELECT level_price, COALESCE(is_active, TRUE)
                FROM price_levels
                WHERE symbol = %s
                ORDER BY level_price
                """,
                (sym,),
            )
            existing_rows = [(float(r[0]), bool(r[1])) for r in cur.fetchall()]
            cluster = _cluster_pct(ac)

            disc_min = min(lvl.price for lvl in discovered)
            disc_max = max(lvl.price for lvl in discovered)
            buf = float(os.getenv("LEVEL_DISCOVERY_RANGE_BUFFER_PCT", "0.15")) / 100.0
            band_min = disc_min * (1.0 - buf)
            band_max = disc_max * (1.0 + buf)

            merged = 0
            reactivated = 0
            matched_existing: set[float] = set()
            merged_level_prices: list[float] = []
            reactivated_level_prices: list[float] = []

            for lvl in discovered:
                idx = _match_level_price(lvl.price, existing_rows, cluster)
                if idx is not None:
                    lp, was_active = existing_rows[idx]
                    matched_existing.add(lp)
                    merged_level_prices.append(lp)
                    _apply_level_stats(cur, sym, lp, lvl)
                    if not was_active:
                        reactivated += 1
                        reactivated_level_prices.append(lp)
                    merged += 1
                else:
                    lp = round(lvl.price, 5)
                    _insert_discovered_level(cur, sym, lvl)
                    merged_level_prices.append(lp)
                    merged += 1

            archived = 0
            stale_frac = STALE_PCT / 100.0
            close = result.last_close

            for lp, was_active in existing_rows:
                if not was_active:
                    continue
                if lp in matched_existing:
                    continue

                if result.merge_mode == "regime_shift":
                    if lp < band_min or lp > band_max:
                        _archive_level(cur, sym, lp, "regime_shift")
                        archived += 1
                    continue

                dist = abs(lp - close) / (close + 1e-10)
                if dist > stale_frac:
                    _archive_level(cur, sym, lp, "drift_stale")
                    archived += 1

            result.levels_merged = merged
            result.levels_archived = archived
            result.levels_reactivated = reactivated
            result.watchlist_active = _sync_watchlist(cur, sym)
            _write_audit_row(cur, result)
            conn.commit()
            cur.close()

            logger.info(
                "%s: discovery complete mode=%s found=%d merged=%d archived=%d watchlist=%d",
                sym,
                result.merge_mode,
                result.levels_found,
                result.levels_merged,
                result.levels_archived,
                result.watchlist_active,
            )

            if os.getenv("LEVEL_EXIT_RECOMPUTE_ON_DISCOVERY", "true").lower() in (
                "true",
                "1",
                "yes",
            ):
                from ml.features.partial_exit_refresh import (
                    recompute_after_discovery,
                    recompute_symbol,
                )

                if result.merge_mode == "regime_shift":
                    recompute_symbol(sym, ac, df)
                else:
                    recompute_after_discovery(
                        sym,
                        ac,
                        df,
                        merged_level_prices,
                        reactivated_level_prices,
                    )
        finally:
            conn.close()

    except Exception as exc:
        logger.error("%s: discovery failed: %s", sym, exc, exc_info=True)
        result.error = str(exc)
        try:
            log_discovery_run(result)
        except Exception:
            logger.debug("Could not write discovery audit row after error", exc_info=True)

    return result
