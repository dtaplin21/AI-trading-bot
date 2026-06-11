"""Tests for TradeExitOptimizer MFE/MAE and TP/SL sweep."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ml.features.trade_exit_optimizer import (
    LevelExitStrategy,
    TouchExcursion,
    compute_excursions,
    optimize_tp_sl,
)


def _sample_df(n: int = 50) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC")
    close = 100.0 + np.linspace(0, 1, n) * 0.5
    high = close + 0.3
    low = close - 0.3
    return pd.DataFrame(
        {
            "open": close - 0.05,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.full(n, 1000.0),
        },
        index=idx,
    )


def test_compute_excursions_from_above_bounce():
    df = _sample_df()
    # After bar 10, price rallies — favorable for from_above (long bounce)
    df.loc[df.index[11:20], "high"] = df.loc[df.index[11:20], "close"] + 0.8
    mfe, mae = compute_excursions(df, 100.0, "from_above", 10, window=15)
    assert mfe > 0.5
    assert mae >= 0.0


def test_compute_excursions_from_below_bounce():
    df = _sample_df()
    df.loc[df.index[11:20], "low"] = df.loc[df.index[11:20], "close"] - 0.8
    mfe, mae = compute_excursions(df, 100.0, "from_below", 10, window=15)
    assert mfe > 0.5
    assert mae >= 0.0


def test_optimize_tp_sl_finds_positive_ev():
    excursions = [
        TouchExcursion(1, 100.0, "from_above", "hold", mfe_pct=0.40, mae_pct=0.08, price_at_touch=100.0),
        TouchExcursion(2, 100.0, "from_above", "hold", mfe_pct=0.35, mae_pct=0.10, price_at_touch=100.0),
        TouchExcursion(3, 100.0, "from_above", "hold", mfe_pct=0.45, mae_pct=0.07, price_at_touch=100.0),
        TouchExcursion(4, 100.0, "from_above", "break", mfe_pct=0.12, mae_pct=0.25, price_at_touch=100.0),
    ]
    result = optimize_tp_sl(
        excursions,
        tp_range=(0.10, 0.50),
        sl_range=(0.05, 0.20),
        n_steps=20,
    )
    assert result
    assert result["optimal_tp_pct"] > 0
    assert result["optimal_sl_pct"] > 0
    assert result["expected_value_pct"] > 0


def test_level_exit_strategy_summary():
    s = LevelExitStrategy(
        level_price=1.0843,
        symbol="EURUSD",
        n_touches=12,
        optimal_tp_pct=0.28,
        optimal_sl_pct=0.12,
        optimal_rr=2.33,
        expected_value_pct=0.18,
        win_rate=0.702,
        avg_mfe=0.42,
        avg_mae=0.18,
        p75_mfe=0.55,
        p25_mae=0.10,
        is_reliable=True,
    )
    text = s.summary()
    assert "TP=0.280%" in text
    assert "EV=+0.180%" in text
    assert "n=12" in text
