#!/usr/bin/env python3
"""
scripts/simulate_tolerance_pct.py

Backtests LevelEntryGate.check() logic against the last N days of real bars,
at multiple tolerance_pct values, WITHOUT touching live code or env vars.

Answers: "If we widened LEVEL_GATE_TOLERANCE_PCT from 0.15% to X%, how many
more gate-passing opportunities would have fired in the last week?"

This is read-only simulation — no trades, no DB writes.

Usage (from backend/):
    python scripts/simulate_tolerance_pct.py --symbols ALL --days 7
    python scripts/simulate_tolerance_pct.py --symbols MES,EURUSD --days 7 --tolerances 0.15,0.25,0.35,0.50

Env:
  DATABASE_URL              required
  DATABASE_SSL_DISABLE      honored for localhost only (see pg_connect.py)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import psycopg2.extras

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.settings import get_settings
from data.storage.pg_connect import connect_psycopg2, is_database_url_placeholder
from pipeline.level_setup import LevelSetup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("simulate_tolerance")

# Production defaults from LevelEntryGate — not read from env so the sweep is clean.
DEFAULT_MIN_TOUCHES = 5
DEFAULT_MIN_HOLD_RATE = 0.55
DEFAULT_MIN_EV_PCT = 0.0
DEFAULT_MIN_RR = 1.0
DEFAULT_TOLERANCE_PCT = 0.15


@dataclass(frozen=True)
class GateParams:
    min_touches: int = DEFAULT_MIN_TOUCHES
    min_hold_rate: float = DEFAULT_MIN_HOLD_RATE
    min_ev_pct: float = DEFAULT_MIN_EV_PCT
    min_rr: float = DEFAULT_MIN_RR


@dataclass
class SimResult:
    symbol: str
    tolerance_pct: float
    bars_checked: int
    bars_passed: int
    unique_levels_hit: int
    avg_ev_pct_of_hits: float
    avg_touch_count_of_hits: float


@dataclass
class LoadDiagnostics:
    symbol: str
    watchlist_loaded: int
    join_miss: int
    either_side: int
    sql_directional_ev: int
    sql_gate_match: int
    actionable: int
    gate_ready: int
    bars_raw: int
    bars_valid: int
    bars_zero_skipped: int
    close_min: float | None
    close_max: float | None
    level_min: float | None
    level_max: float | None
    closest_approach_pct: float | None
    rejections: dict[str, int]


def _database_url() -> str:
    url = (get_settings().database_url or os.getenv("DATABASE_URL", "")).strip()
    if not url or is_database_url_placeholder(url):
        raise RuntimeError("DATABASE_URL is not configured")
    return url


def _get_conn():
    return connect_psycopg2(_database_url())


def _as_float(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _is_actionable(row: dict, gate: GateParams) -> bool:
    """Same rules as LevelEntryGate / is_actionable_watchlist_row, without reading env."""
    side = str(row.get("entry_side", "")).upper()
    if side not in ("BUY", "SELL"):
        return False

    tp = _as_float(row.get("optimal_tp_pct"))
    sl = _as_float(row.get("optimal_sl_pct"))
    ev = _as_float(row.get("expected_value_pct"))
    rr = _as_float(row.get("optimal_rr"))
    if tp is None or sl is None or ev is None or rr is None:
        return False
    if tp <= 0 or sl <= 0 or ev <= 0:
        return False
    if rr < gate.min_rr:
        return False

    return LevelSetup.from_watchlist_row("", row) is not None


def _gate_ready(row: dict, gate: GateParams) -> bool:
    """Actionable row that also passes touch/hold filters used at bar time."""
    if not _is_actionable(row, gate):
        return False
    touches = int(row.get("touch_count") or 0)
    hold_rate = float(row.get("hold_rate") or 0)
    return touches >= gate.min_touches and hold_rate >= gate.min_hold_rate


def _actionable_rejections(rows: list[dict], gate: GateParams) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        side = str(row.get("entry_side", "")).upper()
        if side not in ("BUY", "SELL"):
            counts["either_or_unknown_side"] = counts.get("either_or_unknown_side", 0) + 1
            continue

        tp = _as_float(row.get("optimal_tp_pct"))
        sl = _as_float(row.get("optimal_sl_pct"))
        ev = _as_float(row.get("expected_value_pct"))
        rr = _as_float(row.get("optimal_rr"))
        if tp is None or sl is None or ev is None or rr is None:
            counts["missing_exit_fields"] = counts.get("missing_exit_fields", 0) + 1
            continue
        if tp <= 0 or sl <= 0 or ev <= 0:
            counts["non_positive_exit"] = counts.get("non_positive_exit", 0) + 1
            continue
        if rr < gate.min_rr:
            counts["rr_below_min"] = counts.get("rr_below_min", 0) + 1
            continue
        if LevelSetup.from_watchlist_row("", row) is None:
            counts["level_setup_rejected"] = counts.get("level_setup_rejected", 0) + 1
            continue
        counts["actionable"] = counts.get("actionable", 0) + 1
    return counts


def _closest_approach_pct(closes: list[float], levels: list[dict]) -> float | None:
    if not closes or not levels:
        return None
    best: float | None = None
    for price in closes:
        for row in levels:
            level_price = float(row["level_price"])
            if level_price <= 0:
                continue
            dist = abs(price - level_price) / level_price * 100.0
            if best is None or dist < best:
                best = dist
    return round(best, 4) if best is not None else None


def _filter_valid_closes(closes: list[float]) -> tuple[list[float], int]:
    """Drop non-positive closes (bad tick/bar data) and return skip count."""
    valid = [c for c in closes if c > 0]
    return valid, len(closes) - len(valid)


def load_watchlist_conn(conn, symbol: str) -> list[dict]:
    """Load active watchlist rows with exit optimizer fields (same join as gate)."""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT
            w.level_price,
            w.entry_side,
            w.role,
            w.hold_rate,
            w.touch_count,
            p.optimal_tp_pct,
            p.optimal_sl_pct,
            p.optimal_rr,
            p.expected_value_pct,
            p.exit_win_rate
        FROM level_watchlist w
        LEFT JOIN price_levels p
            ON w.symbol = p.symbol AND w.level_price = p.level_price
        WHERE w.symbol = %s
          AND w.is_active = TRUE
        ORDER BY p.expected_value_pct DESC NULLS LAST
        """,
        (symbol.upper(),),
    )
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    return rows


def load_watchlist(symbol: str) -> list[dict]:
    conn = _get_conn()
    try:
        return load_watchlist_conn(conn, symbol)
    finally:
        conn.close()


def load_recent_closes_conn(conn, symbol: str, days: int, *, valid_only: bool) -> list[float]:
    cur = conn.cursor()
    valid_clause = "AND close > 0" if valid_only else ""
    cur.execute(
        f"""
        SELECT close FROM ohlcv_candles
        WHERE symbol = %s AND timeframe = '1m'
          AND time >= NOW() - make_interval(days => %s)
          {valid_clause}
        ORDER BY time ASC
        """,
        (symbol.upper(), days),
    )
    closes = [float(r[0]) for r in cur.fetchall()]
    cur.close()
    return closes


def load_recent_closes(symbol: str, days: int) -> list[float]:
    """All valid 1m close prices for the symbol in the last N days (close > 0 only)."""
    conn = _get_conn()
    try:
        return load_recent_closes_conn(conn, symbol, days, valid_only=True)
    finally:
        conn.close()


def load_recent_closes_raw(symbol: str, days: int) -> list[float]:
    conn = _get_conn()
    try:
        return load_recent_closes_conn(conn, symbol, days, valid_only=False)
    finally:
        conn.close()


def count_sql_gate_rows_conn(conn, symbol: str, gate: GateParams) -> tuple[int, int]:
    """Cross-check counts against SQL diagnostics on an existing connection."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            COUNT(*) FILTER (
                WHERE w.entry_side IN ('BUY', 'SELL')
                  AND p.optimal_tp_pct IS NOT NULL
                  AND p.optimal_sl_pct IS NOT NULL
                  AND p.optimal_rr IS NOT NULL
                  AND p.expected_value_pct > 0
            ) AS directional_ev,
            COUNT(*) FILTER (
                WHERE w.entry_side IN ('BUY', 'SELL')
                  AND p.optimal_tp_pct IS NOT NULL
                  AND p.optimal_sl_pct IS NOT NULL
                  AND p.optimal_rr IS NOT NULL
                  AND p.expected_value_pct > 0
                  AND p.optimal_rr >= %s
            ) AS gate_match
        FROM level_watchlist w
        JOIN price_levels p
            ON w.symbol = p.symbol AND w.level_price = p.level_price
        WHERE w.symbol = %s AND w.is_active = TRUE
        """,
        (gate.min_rr, symbol.upper()),
    )
    row = cur.fetchone()
    cur.close()
    return int(row[0]), int(row[1])


def count_sql_gate_rows(symbol: str, gate: GateParams) -> tuple[int, int]:
    conn = _get_conn()
    try:
        return count_sql_gate_rows_conn(conn, symbol, gate)
    finally:
        conn.close()


def build_load_diagnostics(
    symbol: str,
    watchlist: list[dict],
    closes_raw: list[float],
    gate: GateParams,
    *,
    sql_directional_ev: int | None = None,
    sql_gate_match: int | None = None,
) -> LoadDiagnostics:
    closes, zero_skipped = _filter_valid_closes(closes_raw)
    actionable = [r for r in watchlist if _is_actionable(r, gate)]
    gate_ready = [r for r in actionable if _gate_ready(r, gate)]
    join_miss = sum(
        1
        for r in watchlist
        if r.get("optimal_tp_pct") is None and r.get("expected_value_pct") is None
    )
    either_side = sum(1 for r in watchlist if str(r.get("entry_side", "")).upper() == "EITHER")
    if sql_directional_ev is None or sql_gate_match is None:
        sql_directional_ev, sql_gate_match = count_sql_gate_rows(symbol, gate)
    level_prices = [float(r["level_price"]) for r in gate_ready] or [
        float(r["level_price"]) for r in actionable
    ]

    return LoadDiagnostics(
        symbol=symbol.upper(),
        watchlist_loaded=len(watchlist),
        join_miss=join_miss,
        either_side=either_side,
        sql_directional_ev=sql_directional_ev,
        sql_gate_match=sql_gate_match,
        actionable=len(actionable),
        gate_ready=len(gate_ready),
        bars_raw=len(closes_raw),
        bars_valid=len(closes),
        bars_zero_skipped=zero_skipped,
        close_min=min(closes) if closes else None,
        close_max=max(closes) if closes else None,
        level_min=min(level_prices) if level_prices else None,
        level_max=max(level_prices) if level_prices else None,
        closest_approach_pct=_closest_approach_pct(closes, gate_ready or actionable),
        rejections=_actionable_rejections(watchlist, gate),
    )


def print_load_diagnostics(diag: LoadDiagnostics, gate: GateParams) -> None:
    print(f"\n  --- load diagnostics: {diag.symbol} ---")
    print(
        f"  watchlist={diag.watchlist_loaded}  join_miss={diag.join_miss}  "
        f"either_side={diag.either_side}"
    )
    print(
        f"  sql_directional_ev={diag.sql_directional_ev}  "
        f"sql_gate_match(rr>={gate.min_rr})={diag.sql_gate_match}  "
        f"python_actionable={diag.actionable}"
    )
    if diag.actionable != diag.sql_gate_match:
        print(
            f"  WARNING: actionable ({diag.actionable}) != sql_gate_match ({diag.sql_gate_match})"
        )
    print(f"  gate_ready(touches+hold)={diag.gate_ready}")
    rej = {k: v for k, v in diag.rejections.items() if k != "actionable"}
    if rej:
        parts = ", ".join(f"{k}={v}" for k, v in sorted(rej.items()))
        print(f"  rejections: {parts}")
    print(
        f"  bars: raw={diag.bars_raw}  valid={diag.bars_valid}  "
        f"zero_skipped={diag.bars_zero_skipped}"
    )
    if diag.close_min is not None:
        print(f"  close range: {diag.close_min:.5f} .. {diag.close_max:.5f}")
    if diag.level_min is not None:
        print(f"  level range: {diag.level_min:.5f} .. {diag.level_max:.5f}")
    if diag.closest_approach_pct is not None:
        print(f"  closest approach (gate-ready levels): {diag.closest_approach_pct:.3f}%")
        if diag.closest_approach_pct > DEFAULT_TOLERANCE_PCT:
            print(
                f"  note: nearest level is {diag.closest_approach_pct:.3f}% away "
                f"(default tolerance {DEFAULT_TOLERANCE_PCT}%)"
            )
    if diag.bars_zero_skipped and diag.bars_valid == 0:
        print("  WARNING: all recent bars had close<=0 — fix OHLCV ingestion for this symbol")


def pick_gate_level(
    price: float,
    watchlist: list[dict],
    tolerance_pct: float,
    gate: GateParams,
) -> dict | None:
    """
    Mirror LevelEntryGate.check(): among actionable rows within tolerance,
    return the row with highest expected_value_pct.
    """
    best: dict | None = None
    best_ev = float("-inf")

    for row in watchlist:
        if not _is_actionable(row, gate):
            continue

        level_price = float(row["level_price"])
        if level_price <= 0:
            continue

        dist_pct = abs(price - level_price) / level_price * 100.0
        if dist_pct > tolerance_pct:
            continue

        touches = int(row.get("touch_count") or 0)
        if touches < gate.min_touches:
            continue

        hold_rate = float(row.get("hold_rate") or 0)
        if hold_rate < gate.min_hold_rate:
            continue

        ev = _as_float(row.get("expected_value_pct"))
        if ev is None or ev < gate.min_ev_pct:
            continue

        rr = _as_float(row.get("optimal_rr"))
        if rr is None or rr < gate.min_rr:
            continue

        if ev > best_ev:
            best_ev = ev
            best = row

    return best


def simulate_symbol(
    symbol: str,
    days: int,
    tolerances: list[float],
    gate: GateParams,
    *,
    show_diagnostics: bool = True,
) -> list[SimResult]:
    conn = _get_conn()
    try:
        watchlist = load_watchlist_conn(conn, symbol)
        closes_raw = load_recent_closes_conn(conn, symbol, days, valid_only=False)
        sql_ev, sql_gate = count_sql_gate_rows_conn(conn, symbol, gate)
        diag = build_load_diagnostics(
            symbol,
            watchlist,
            closes_raw,
            gate,
            sql_directional_ev=sql_ev,
            sql_gate_match=sql_gate,
        )
    finally:
        conn.close()

    if show_diagnostics:
        print_load_diagnostics(diag, gate)

    closes, _ = _filter_valid_closes(closes_raw)

    if not watchlist:
        logger.warning("%s: no watchlist rows — skipping", symbol)
        return []
    if not closes:
        logger.warning("%s: no valid recent bars (raw=%d) — skipping", symbol, len(closes_raw))
        return []

    actionable = [r for r in watchlist if _is_actionable(r, gate)]
    if not actionable:
        logger.warning("%s: no actionable watchlist rows — skipping", symbol)
        return []

    results: list[SimResult] = []
    for tol in tolerances:
        bars_passed = 0
        hit_levels: set[float] = set()
        hit_ev: list[float] = []
        hit_touch: list[int] = []

        for price in closes:
            best = pick_gate_level(price, actionable, tol, gate)
            if best is None:
                continue

            bars_passed += 1
            hit_levels.add(float(best["level_price"]))
            hit_ev.append(float(best["expected_value_pct"] or 0))
            hit_touch.append(int(best["touch_count"] or 0))

        results.append(
            SimResult(
                symbol=symbol.upper(),
                tolerance_pct=tol,
                bars_checked=len(closes),
                bars_passed=bars_passed,
                unique_levels_hit=len(hit_levels),
                avg_ev_pct_of_hits=round(sum(hit_ev) / len(hit_ev), 4) if hit_ev else 0.0,
                avg_touch_count_of_hits=round(sum(hit_touch) / len(hit_touch), 1) if hit_touch else 0.0,
            )
        )

    return results


def print_results(symbol: str, results: list[SimResult]) -> None:
    if not results:
        return
    print(f"\n{symbol}")
    print(
        f"  {'tolerance%':>10} {'bars_chk':>9} {'bars_pass':>9} {'pass%':>7} "
        f"{'levels_hit':>10} {'avg_ev%':>8} {'avg_touches':>11}"
    )
    for r in results:
        pass_pct = (r.bars_passed / r.bars_checked * 100) if r.bars_checked else 0.0
        print(
            f"  {r.tolerance_pct:>10.2f} {r.bars_checked:>9} {r.bars_passed:>9} "
            f"{pass_pct:>6.2f}% {r.unique_levels_hit:>10} "
            f"{r.avg_ev_pct_of_hits:>8.3f} {r.avg_touch_count_of_hits:>11.1f}"
        )


def main() -> None:
    p = argparse.ArgumentParser(
        description="Simulate gate pass rate at different tolerance_pct values",
    )
    p.add_argument("--symbols", required=True, help="Comma-separated symbols, or ALL")
    p.add_argument("--days", type=int, default=7)
    p.add_argument(
        "--tolerances",
        default="0.15,0.25,0.35,0.50,0.75,1.00",
        help="Comma-separated tolerance_pct values to test",
    )
    p.add_argument("--min-touches", type=int, default=DEFAULT_MIN_TOUCHES)
    p.add_argument("--min-hold-rate", type=float, default=DEFAULT_MIN_HOLD_RATE)
    p.add_argument("--min-ev-pct", type=float, default=DEFAULT_MIN_EV_PCT)
    p.add_argument("--min-rr", type=float, default=DEFAULT_MIN_RR)
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-symbol load diagnostics",
    )
    args = p.parse_args()

    tolerances = [float(t.strip()) for t in args.tolerances.split(",") if t.strip()]
    gate = GateParams(
        min_touches=args.min_touches,
        min_hold_rate=args.min_hold_rate,
        min_ev_pct=args.min_ev_pct,
        min_rr=args.min_rr,
    )

    if args.symbols.upper() == "ALL":
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT symbol FROM level_watchlist WHERE is_active = TRUE ORDER BY symbol"
        )
        symbols = [r[0] for r in cur.fetchall()]
        cur.close()
        conn.close()
    else:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    print(f"\nSimulating LevelEntryGate.check() over last {args.days} days")
    print(f"Tolerance values tested: {tolerances}")
    print(f"Live default tolerance: {DEFAULT_TOLERANCE_PCT}%")
    print(
        f"Gate filters: min_touches={gate.min_touches} min_hold={gate.min_hold_rate:.0%} "
        f"min_ev={gate.min_ev_pct:.3f}% min_rr={gate.min_rr:.1f}"
    )
    print("=" * 100)

    all_results: dict[str, list[SimResult]] = {}
    for sym in symbols:
        results = simulate_symbol(
            sym, args.days, tolerances, gate, show_diagnostics=not args.quiet
        )
        if results:
            all_results[sym] = results
            print_results(sym, results)

    print("\n" + "=" * 100)
    print("AGGREGATE ACROSS ALL SYMBOLS")
    print("=" * 100)
    for tol in tolerances:
        total_passed = sum(
            r.bars_passed for results in all_results.values() for r in results if r.tolerance_pct == tol
        )
        total_checked = sum(
            r.bars_checked for results in all_results.values() for r in results if r.tolerance_pct == tol
        )
        total_levels = sum(
            r.unique_levels_hit for results in all_results.values() for r in results if r.tolerance_pct == tol
        )
        pass_pct = (total_passed / total_checked * 100) if total_checked else 0.0
        print(
            f"  tolerance={tol:.2f}%  total_bars_passed={total_passed:>6}  "
            f"total_bars_checked={total_checked:>6}  pass_rate={pass_pct:.2f}%  "
            f"total_unique_levels_hit={total_levels}"
        )


if __name__ == "__main__":
    main()
