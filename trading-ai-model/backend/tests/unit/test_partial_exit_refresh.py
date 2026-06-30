"""Unit tests for partial exit refresh (no live DB)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.features.partial_exit_refresh import recompute_after_discovery, recompute_levels


def _empty_df() -> pd.DataFrame:
    return pd.DataFrame({"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0], "volume": [1.0]})


@patch("ml.features.partial_exit_refresh.recompute_levels")
@patch("ml.features.trade_exit_optimizer._get_conn")
def test_recompute_after_discovery_filters_by_touch_count(mock_conn, mock_recompute):
    cur = MagicMock()
    cur.fetchall.return_value = [(100.0,), (101.5,)]
    conn = MagicMock()
    conn.cursor.return_value = cur
    mock_conn.return_value = conn
    mock_recompute.return_value = 2

    count = recompute_after_discovery(
        "TSLA",
        "equity",
        _empty_df(),
        merged_level_prices=[100.0, 102.0],
        reactivated_level_prices=[101.5],
        min_touches=5,
    )

    assert count == 2
    mock_recompute.assert_called_once_with("TSLA", "equity", _empty_df(), [100.0, 101.5])


@patch("ml.features.trade_exit_optimizer._db_available", return_value=False)
def test_recompute_levels_skips_without_db(mock_db):
    assert recompute_levels("TSLA", "equity", _empty_df(), [100.0]) == 0


@patch("ml.features.trade_exit_optimizer.TradeExitOptimizer")
@patch("ml.features.trade_exit_optimizer._db_available", return_value=True)
@patch("ml.features.trade_exit_optimizer.ensure_exit_columns")
def test_recompute_levels_calls_optimizer(mock_ensure, mock_db, mock_optimizer_cls):
    strategy = MagicMock()
    opt = MagicMock()
    opt._optimize_level.return_value = strategy
    mock_optimizer_cls.return_value = opt

    count = recompute_levels("TSLA", "equity", _empty_df(), [100.0, 200.0])

    assert count == 2
    assert opt._save_strategy.call_count == 2
