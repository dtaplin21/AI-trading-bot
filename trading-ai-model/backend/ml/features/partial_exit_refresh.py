"""
ml/features/partial_exit_refresh.py  (Phase 2)

Incremental exit optimizer refresh — recomputes TP/SL/EV only for level
prices that rolling discovery just touched (merged, archived, reactivated),
instead of re-running the full TradeExitOptimizer.run() scan over every
level for a symbol.

Reuses TradeExitOptimizer._optimize_level() and _save_strategy() directly —
does not duplicate the MFE/MAE computation logic.
"""
from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger("partial_exit_refresh")


def recompute_levels(
    symbol: str,
    asset_class: str,
    df: pd.DataFrame,
    level_prices: list[float],
) -> int:
    """
    Recompute exit strategy (TP/SL/EV/R:R) for a specific list of level
    prices only — used after a rolling discovery pass to refresh just the
    levels that changed, rather than scanning the whole symbol.

    Returns count of levels successfully recomputed.
    """
    if not level_prices:
        return 0

    from ml.features.trade_exit_optimizer import TradeExitOptimizer, _db_available, ensure_exit_columns

    if not _db_available():
        logger.warning("%s: DATABASE_URL not configured — skipping partial refresh", symbol)
        return 0

    ensure_exit_columns()
    optimizer = TradeExitOptimizer(symbol, asset_class)

    recomputed = 0
    for level_price in level_prices:
        try:
            strategy = optimizer._optimize_level(df, level_price)
            if strategy:
                optimizer.strategies[level_price] = strategy
                optimizer._save_strategy(strategy)
                recomputed += 1
        except Exception as exc:
            logger.warning(
                "%s: partial exit refresh failed for level %.5f: %s",
                symbol,
                level_price,
                exc,
            )

    logger.info(
        "%s: partial exit refresh — %d/%d levels recomputed",
        symbol,
        recomputed,
        len(level_prices),
    )
    return recomputed


def recompute_after_discovery(
    symbol: str,
    asset_class: str,
    df: pd.DataFrame,
    merged_level_prices: list[float],
    reactivated_level_prices: list[float],
    min_touches: int = 5,
) -> int:
    """
    Convenience wrapper called from rolling_level_discovery.discover_symbol()
    after a successful merge — recomputes exits only for levels that were
    actually merged or reactivated this run, filtered to those meeting the
    minimum touch threshold (matches TradeExitOptimizer._load_levels logic).
    """
    from ml.features.trade_exit_optimizer import _get_conn

    candidates = list(set(merged_level_prices) | set(reactivated_level_prices))
    if not candidates:
        return 0

    sym = symbol.upper()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT level_price FROM price_levels
        WHERE symbol = %s AND level_price = ANY(%s) AND touch_count >= %s
        """,
        (sym, candidates, min_touches),
    )
    qualifying = [float(r[0]) for r in cur.fetchall()]
    cur.close()
    conn.close()

    if not qualifying:
        logger.debug(
            "%s: no candidate levels met min_touches=%d for exit refresh",
            sym,
            min_touches,
        )
        return 0

    return recompute_levels(sym, asset_class, df, qualifying)


def recompute_symbol(
    symbol: str,
    asset_class: str,
    df: pd.DataFrame,
    min_touches: int = 5,
) -> int:
    """Full-symbol exit refresh after regime_shift discovery."""
    from ml.features.trade_exit_optimizer import TradeExitOptimizer, _db_available, ensure_exit_columns

    if not _db_available():
        logger.warning("%s: DATABASE_URL not configured — skipping full exit refresh", symbol)
        return 0

    ensure_exit_columns()
    optimizer = TradeExitOptimizer(symbol, asset_class)
    optimizer.run(df, min_touches=min_touches)
    count = len(optimizer.strategies)
    logger.info("%s: full exit refresh — %d levels recomputed", symbol.upper(), count)
    return count
