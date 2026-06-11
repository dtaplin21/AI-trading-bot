"""
ml/features/trade_exit_optimizer.py

Finds optimal take-profit and stop-loss per price level using historical
MFE (max favorable excursion) and MAE (max adverse excursion) from OHLCV.

Run via: python scripts/compute_exit_optimizer.py --symbols EURUSD,BTCUSD
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional, cast

import numpy as np
import pandas as pd

from config.settings import get_settings
from data.storage.pg_connect import connect_psycopg2, is_database_url_placeholder

logger = logging.getLogger("exit_optimizer")

EXIT_COLUMNS_SQL = """
ALTER TABLE price_levels ADD COLUMN IF NOT EXISTS optimal_tp_pct FLOAT;
ALTER TABLE price_levels ADD COLUMN IF NOT EXISTS optimal_sl_pct FLOAT;
ALTER TABLE price_levels ADD COLUMN IF NOT EXISTS optimal_rr FLOAT;
ALTER TABLE price_levels ADD COLUMN IF NOT EXISTS expected_value_pct FLOAT;
ALTER TABLE price_levels ADD COLUMN IF NOT EXISTS exit_win_rate FLOAT;
ALTER TABLE price_levels ADD COLUMN IF NOT EXISTS avg_mfe_pct FLOAT;
ALTER TABLE price_levels ADD COLUMN IF NOT EXISTS avg_mae_pct FLOAT;
"""

_exit_columns_ready = False


@dataclass
class TouchExcursion:
    """MFE and MAE for one historical touch."""

    touch_id: int
    level_price: float
    approach: str
    outcome: str
    mfe_pct: float
    mae_pct: float
    price_at_touch: float


@dataclass
class LevelExitStrategy:
    """Optimal exit strategy for one price level."""

    level_price: float
    symbol: str
    n_touches: int
    optimal_tp_pct: float
    optimal_sl_pct: float
    optimal_rr: float
    expected_value_pct: float
    win_rate: float
    avg_mfe: float
    avg_mae: float
    p75_mfe: float
    p25_mae: float
    is_reliable: bool

    def summary(self) -> str:
        return (
            f"TP={self.optimal_tp_pct:.3f}%  "
            f"SL={self.optimal_sl_pct:.3f}%  "
            f"R:R={self.optimal_rr:.1f}  "
            f"EV={self.expected_value_pct:+.3f}%  "
            f"win={self.win_rate * 100:.1f}%  "
            f"n={self.n_touches}"
        )


def _database_url() -> str:
    return (get_settings().database_url or os.getenv("DATABASE_URL", "")).strip()


def _db_available() -> bool:
    url = _database_url()
    return bool(url) and not is_database_url_placeholder(url)


def _get_conn():
    return connect_psycopg2(_database_url())


def ensure_exit_columns() -> None:
    global _exit_columns_ready
    if _exit_columns_ready or not _db_available():
        return
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(EXIT_COLUMNS_SQL)
        conn.commit()
        cur.close()
        conn.close()
        _exit_columns_ready = True
    except Exception as exc:
        logger.error("ensure_exit_columns: %s", exc)


def compute_excursions(
    df: pd.DataFrame,
    level_price: float,
    approach: str,
    bar_index: int,
    window: int = 30,
) -> tuple[float, float]:
    """
    Compute MFE and MAE for one touch at bar_index.

    MFE = max move in the expected direction (favorable).
    MAE = max move against us (adverse).
    """
    _ = level_price  # level context for callers; entry uses bar close
    n = len(df)
    if bar_index < 0 or bar_index >= n:
        return 0.0, 0.0

    close_s = cast(pd.Series, df["close"])
    high_s = cast(pd.Series, df["high"])
    low_s = cast(pd.Series, df["low"])
    entry = float(close_s.iloc[bar_index])
    end_idx = min(bar_index + window, n)
    future_h = high_s.iloc[bar_index + 1 : end_idx].values
    future_l = low_s.iloc[bar_index + 1 : end_idx].values

    if len(future_h) == 0:
        return 0.0, 0.0

    max_high = float(np.max(future_h))
    min_low = float(np.min(future_l))
    up_move = (max_high - entry) / (entry + 1e-10) * 100
    down_move = (entry - min_low) / (entry + 1e-10) * 100

    if approach == "from_above":
        mfe = max(0.0, up_move)
        mae = max(0.0, down_move)
    else:
        mfe = max(0.0, down_move)
        mae = max(0.0, up_move)

    return round(mfe, 4), round(mae, 4)


def optimize_tp_sl(
    excursions: list[TouchExcursion],
    tp_range: tuple[float, float] = (0.05, 2.0),
    sl_range: tuple[float, float] = (0.03, 1.0),
    n_steps: int = 40,
) -> dict[str, float]:
    """Sweep TP/SL combinations and return the pair with maximum EV."""
    if not excursions:
        return {}

    mfe_arr = np.array([e.mfe_pct for e in excursions])
    mae_arr = np.array([e.mae_pct for e in excursions])

    tp_values = np.linspace(tp_range[0], tp_range[1], n_steps)
    sl_values = np.linspace(sl_range[0], sl_range[1], n_steps)

    best_ev = -999.0
    best_tp = float(tp_values[0])
    best_sl = float(sl_values[0])
    best_wr = 0.0

    for tp in tp_values:
        for sl in sl_values:
            hit_tp = mfe_arr >= tp
            hit_sl = mae_arr >= sl
            wins = hit_tp & (~hit_sl)
            losses = hit_sl & (~hit_tp)
            ambig = ~hit_tp & ~hit_sl
            ambig_wins = sum(
                1 for i, e in enumerate(excursions) if ambig[i] and e.outcome == "hold"
            )

            total_wins = int(wins.sum()) + ambig_wins
            total_losses = int(losses.sum()) + (int(ambig.sum()) - ambig_wins)
            total = total_wins + total_losses
            if total == 0:
                continue

            win_rate = total_wins / total
            ev = win_rate * tp - (1 - win_rate) * sl
            if ev > best_ev:
                best_ev = ev
                best_tp = float(tp)
                best_sl = float(sl)
                best_wr = win_rate

    if best_ev <= -999.0:
        return {}

    return {
        "optimal_tp_pct": round(best_tp, 4),
        "optimal_sl_pct": round(best_sl, 4),
        "optimal_rr": round(best_tp / (best_sl + 1e-10), 2),
        "expected_value_pct": round(float(best_ev), 4),
        "win_rate": round(float(best_wr), 4),
    }


def _cell_present(val: Any) -> bool:
    if isinstance(val, pd.Series):
        val = val.iloc[0] if len(val) else None
    if val is None:
        return False
    if isinstance(val, (float, np.floating)):
        return not bool(np.isnan(float(val)))
    return True


def _bar_index_for_touch(df: pd.DataFrame, touched_at: Any) -> Optional[int]:
    """Map a touch timestamp to the nearest bar index in df."""
    if df.empty:
        return None

    index = pd.DatetimeIndex(pd.to_datetime(cast(Any, df.index), utc=True))
    bar_time = pd.to_datetime(touched_at, utc=True)

    idx = int(index.searchsorted(bar_time))
    if idx >= len(index):
        idx = len(index) - 1
    if idx < 0:
        return None

    if bar_time not in index:
        if idx > 0:
            prev_delta = abs((index[idx - 1] - bar_time).total_seconds())
            next_delta = abs((index[idx] - bar_time).total_seconds())
            if prev_delta <= next_delta:
                idx -= 1

    return idx


class TradeExitOptimizer:
    """Computes optimal TP/SL for every price level using historical MFE/MAE."""

    def __init__(self, symbol: str, asset_class: str):
        self.symbol = symbol.upper()
        self.asset_class = asset_class
        self.strategies: dict[float, LevelExitStrategy] = {}

        self._window = {"futures": 24, "forex": 20, "crypto": 16, "equity": 20}.get(
            asset_class, 20
        )
        self._tp_range = {
            "futures": (0.10, 1.00),
            "forex": (0.05, 0.60),
            "crypto": (0.20, 2.00),
            "equity": (0.15, 1.50),
        }.get(asset_class, (0.05, 1.0))
        self._sl_range = {
            "futures": (0.05, 0.50),
            "forex": (0.03, 0.30),
            "crypto": (0.10, 1.00),
            "equity": (0.08, 0.60),
        }.get(asset_class, (0.03, 0.50))

    def run(self, df: pd.DataFrame, min_touches: int = 5) -> None:
        """Load levels, compute MFE/MAE, optimize TP/SL, save to DB."""
        if not _db_available():
            logger.warning("%s: DATABASE_URL not configured — skipping", self.symbol)
            return

        ensure_exit_columns()
        logger.info("%s: loading levels from database...", self.symbol)
        levels = self._load_levels(min_touches)
        if not levels:
            logger.warning("%s: no levels with %d+ touches", self.symbol, min_touches)
            return

        logger.info("%s: computing MFE/MAE for %d levels...", self.symbol, len(levels))
        for level_price in levels:
            strategy = self._optimize_level(df, level_price)
            if strategy:
                self.strategies[level_price] = strategy
                self._save_strategy(strategy)

        logger.info(
            "%s: optimization complete — %d levels with exit strategies",
            self.symbol,
            len(self.strategies),
        )

    def _load_levels(self, min_touches: int) -> list[float]:
        try:
            conn = _get_conn()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT level_price FROM price_levels
                WHERE symbol = %s AND touch_count >= %s
                ORDER BY touch_count DESC
                """,
                (self.symbol, min_touches),
            )
            levels = [float(row[0]) for row in cur.fetchall()]
            cur.close()
            conn.close()
            return levels
        except Exception as exc:
            logger.error("_load_levels: %s", exc)
            return []

    def _load_touches_for_level(self, level_price: float) -> list[dict[str, Any]]:
        try:
            conn = _get_conn()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, touched_at, price_at_touch, approach, outcome
                FROM level_touches
                WHERE symbol = %s
                  AND level_price = %s
                  AND outcome IN ('hold', 'break')
                ORDER BY touched_at ASC
                """,
                (self.symbol, level_price),
            )
            cols = ["id", "touched_at", "price_at_touch", "approach", "outcome"]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
            cur.close()
            conn.close()
            return rows
        except Exception as exc:
            logger.error("_load_touches_for_level: %s", exc)
            return []

    def _optimize_level(
        self, df: pd.DataFrame, level_price: float
    ) -> Optional[LevelExitStrategy]:
        touches = self._load_touches_for_level(level_price)
        if len(touches) < 3:
            return None

        excursions: list[TouchExcursion] = []
        for touch in touches:
            idx = _bar_index_for_touch(df, touch["touched_at"])
            if idx is None or idx >= len(df) - self._window:
                continue

            mfe, mae = compute_excursions(
                df, level_price, str(touch["approach"]), idx, self._window
            )
            excursions.append(
                TouchExcursion(
                    touch_id=int(touch["id"]),
                    level_price=level_price,
                    approach=str(touch["approach"]),
                    outcome=str(touch["outcome"]),
                    mfe_pct=mfe,
                    mae_pct=mae,
                    price_at_touch=float(touch["price_at_touch"]),
                )
            )

        if len(excursions) < 3:
            return None

        mfe_vals = [e.mfe_pct for e in excursions]
        mae_vals = [e.mae_pct for e in excursions]
        opt = optimize_tp_sl(excursions, self._tp_range, self._sl_range)
        if not opt:
            return None

        return LevelExitStrategy(
            level_price=level_price,
            symbol=self.symbol,
            n_touches=len(excursions),
            optimal_tp_pct=opt["optimal_tp_pct"],
            optimal_sl_pct=opt["optimal_sl_pct"],
            optimal_rr=opt["optimal_rr"],
            expected_value_pct=opt["expected_value_pct"],
            win_rate=opt["win_rate"],
            avg_mfe=round(float(np.mean(mfe_vals)), 4),
            avg_mae=round(float(np.mean(mae_vals)), 4),
            p75_mfe=round(float(np.percentile(mfe_vals, 75)), 4),
            p25_mae=round(float(np.percentile(mae_vals, 25)), 4),
            is_reliable=len(excursions) >= 10,
        )

    def _save_strategy(self, s: LevelExitStrategy) -> None:
        try:
            conn = _get_conn()
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE price_levels
                SET optimal_tp_pct     = %s,
                    optimal_sl_pct     = %s,
                    optimal_rr           = %s,
                    expected_value_pct = %s,
                    exit_win_rate      = %s,
                    avg_mfe_pct        = %s,
                    avg_mae_pct        = %s
                WHERE symbol = %s AND level_price = %s
                """,
                (
                    s.optimal_tp_pct,
                    s.optimal_sl_pct,
                    s.optimal_rr,
                    s.expected_value_pct,
                    s.win_rate,
                    s.avg_mfe,
                    s.avg_mae,
                    s.symbol,
                    s.level_price,
                ),
            )
            conn.commit()
            cur.close()
            conn.close()
        except Exception as exc:
            logger.error("_save_strategy: %s", exc)

    def get_strategy(self, price: float) -> Optional[LevelExitStrategy]:
        if not self.strategies:
            self._load_from_db()
        if not self.strategies:
            return None
        nearest = min(self.strategies, key=lambda lp: abs(lp - price))
        return self.strategies.get(nearest)

    def _load_from_db(self) -> None:
        if not _db_available():
            return
        try:
            conn = _get_conn()
            df = pd.read_sql(
                """
                SELECT level_price, touch_count, hold_rate, role,
                       optimal_tp_pct, optimal_sl_pct, optimal_rr,
                       expected_value_pct, exit_win_rate, avg_mfe_pct, avg_mae_pct
                FROM price_levels
                WHERE symbol = %s
                  AND optimal_tp_pct IS NOT NULL
                ORDER BY touch_count DESC
                """,
                conn,
                params=(self.symbol,),
            )
            conn.close()

            for _, row in df.iterrows():
                lp = float(row["level_price"])
                self.strategies[lp] = LevelExitStrategy(
                    level_price=lp,
                    symbol=self.symbol,
                    n_touches=int(row["touch_count"]),
                    optimal_tp_pct=float(row["optimal_tp_pct"] or 0),
                    optimal_sl_pct=float(row["optimal_sl_pct"] or 0),
                    optimal_rr=float(row["optimal_rr"] or 0),
                    expected_value_pct=float(row["expected_value_pct"] or 0),
                    win_rate=float(row["exit_win_rate"] or 0),
                    avg_mfe=float(row["avg_mfe_pct"] or 0),
                    avg_mae=float(row["avg_mae_pct"] or 0),
                    p75_mfe=0.0,
                    p25_mae=0.0,
                    is_reliable=int(row["touch_count"]) >= 10,
                )
        except Exception as exc:
            logger.error("_load_from_db: %s", exc)

    def print_all(self, top_n: int = 15) -> None:
        if not self.strategies:
            self._load_from_db()
        if not self.strategies:
            print(f"{self.symbol}: no exit strategies computed yet")
            return

        sorted_strats = sorted(
            self.strategies.values(),
            key=lambda s: s.expected_value_pct,
            reverse=True,
        )[:top_n]

        print(f"\n{'=' * 85}")
        print(f"  {self.symbol} — Optimal Exit Strategies (sorted by EV)")
        print(f"{'=' * 85}")
        print(
            f"  {'Price':>10} {'Hits':>5} {'TP%':>7} {'SL%':>6} "
            f"{'R:R':>5} {'EV%':>7} {'Win%':>6} {'MFE':>7} {'MAE':>7} {'Reliable'}"
        )
        print(f"  {'-' * 82}")

        for s in sorted_strats:
            rel = "YES" if s.is_reliable else "low n"
            ev_sign = "+" if s.expected_value_pct > 0 else ""
            print(
                f"  {s.level_price:>10.5f} "
                f"{s.n_touches:>5} "
                f"{s.optimal_tp_pct:>6.3f}% "
                f"{s.optimal_sl_pct:>5.3f}% "
                f"{s.optimal_rr:>5.1f} "
                f"{ev_sign}{s.expected_value_pct:>6.3f}% "
                f"{s.win_rate * 100:>5.1f}% "
                f"{s.avg_mfe:>6.3f}% "
                f"{s.avg_mae:>6.3f}%  "
                f"{rel}"
            )

        positive_ev = [s for s in sorted_strats if s.expected_value_pct > 0]
        if positive_ev:
            avg_ev = float(np.mean([s.expected_value_pct for s in positive_ev]))
            avg_rr = float(np.mean([s.optimal_rr for s in positive_ev]))
            print(f"\n  {len(positive_ev)}/{len(sorted_strats)} levels have positive EV")
            print(f"  Average EV on positive levels: +{avg_ev:.3f}%")
            print(f"  Average R:R on positive levels: {avg_rr:.1f}")

        print(f"{'=' * 85}\n")

    def get_watchlist_with_exits(self) -> pd.DataFrame:
        if not _db_available():
            return pd.DataFrame()
        try:
            conn = _get_conn()
            df = pd.read_sql(
                """
                SELECT
                    w.level_price,
                    w.hold_rate,
                    w.touch_count,
                    w.strength_score,
                    w.role,
                    w.entry_side,
                    p.optimal_tp_pct,
                    p.optimal_sl_pct,
                    p.optimal_rr,
                    p.expected_value_pct,
                    p.exit_win_rate,
                    p.avg_mfe_pct,
                    p.avg_mae_pct
                FROM level_watchlist w
                LEFT JOIN price_levels p
                    ON w.symbol = p.symbol AND w.level_price = p.level_price
                WHERE w.symbol = %s AND w.is_active = TRUE
                ORDER BY p.expected_value_pct DESC NULLS LAST
                """,
                conn,
                params=(self.symbol,),
            )
            conn.close()
            return df
        except Exception as exc:
            logger.error("get_watchlist_with_exits: %s", exc)
            return pd.DataFrame()

    def print_watchlist_with_exits(self) -> None:
        df = self.get_watchlist_with_exits()
        if df.empty:
            print(f"{self.symbol}: no watchlist entries")
            return

        print(f"\n{'=' * 90}")
        print(f"  {self.symbol} — ACTIONABLE WATCHLIST (entry + exit levels)")
        print(f"{'=' * 90}")
        print(
            f"  {'Price':>10} {'Role':<12} {'Entry':>6} "
            f"{'TP%':>6} {'SL%':>5} {'R:R':>5} "
            f"{'EV%':>7} {'Win%':>6} {'Hits':>5}"
        )
        print(f"  {'-' * 85}")

        for _, row in df.iterrows():
            tp_val = cast(Any, row["optimal_tp_pct"])
            sl_val = cast(Any, row["optimal_sl_pct"])
            rr_val = cast(Any, row["optimal_rr"])
            ev_val = cast(Any, row["expected_value_pct"])
            wr_val = cast(Any, row["exit_win_rate"])
            tp = f"{float(tp_val):.3f}" if _cell_present(tp_val) else "—"
            sl = f"{float(sl_val):.3f}" if _cell_present(sl_val) else "—"
            rr = f"{float(rr_val):.1f}" if _cell_present(rr_val) else "—"
            ev = f"{float(ev_val):+.3f}%" if _cell_present(ev_val) else "—"
            wr = f"{float(wr_val) * 100:.1f}%" if _cell_present(wr_val) else "—"

            print(
                f"  {float(row['level_price']):>10.5f} "
                f"{str(row['role']):<12} "
                f"{str(row['entry_side']):>6} "
                f"{tp:>6}% "
                f"{sl:>5}% "
                f"{rr:>5} "
                f"{ev:>7} "
                f"{wr:>6} "
                f"{int(row['touch_count']):>5}"
            )

        print(f"{'=' * 90}\n")
        print("  How to read this:")
        print("  Price  = enter when price returns to this level")
        print("  Entry  = BUY (support bounce) or SELL (resistance rejection)")
        print("  TP%    = take profit this % away from entry price")
        print("  SL%    = stop loss this % away from entry price")
        print("  R:R    = risk/reward ratio (TP / SL)")
        print("  EV%    = expected profit per trade (positive = edge)")
        print("  Win%   = % of trades that hit TP before SL")
