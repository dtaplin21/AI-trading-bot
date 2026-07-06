"""
tests/unit/test_rolling_level_discovery.py  (Phase 5)

Tests for gap detection, archiving, reactivation, and discovery gates.
Uses mocked DB connections — no live DB required.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ml.features.rolling_level_discovery import (
    bars_expected_5m,
    classify_discovery_mode,
    is_outside_envelope,
)


def test_bars_expected_5m():
    assert bars_expected_5m(60) == 60 * 24 * 12


def test_is_outside_envelope_above():
    assert is_outside_envelope(101.0, 100.0, 100.5, buffer_pct=0.15)


def test_is_outside_envelope_inside():
    assert not is_outside_envelope(100.2, 100.0, 100.5, buffer_pct=0.15)


def test_classify_regime_shift():
    mode = classify_discovery_mode(120.0, (100.0, 105.0), buffer_pct=0.15)
    assert mode == "regime_shift"


def test_classify_drift():
    mode = classify_discovery_mode(106.0, (100.0, 105.0), buffer_pct=0.15)
    assert mode == "drift"


def test_check_window_coverage_full():
    with patch("ml.features.rolling_level_discovery._get_conn") as mock_conn:
        cur = MagicMock()
        cur.fetchone.return_value = (60 * 1440,)  # exactly full coverage
        mock_conn.return_value.cursor.return_value = cur

        from ml.features.rolling_level_discovery import check_window_coverage

        loaded, expected, pct = check_window_coverage("TSLA", 60)

        assert loaded == 60 * 1440
        assert expected == 60 * 1440
        assert pct == 100.0


def test_check_window_coverage_partial():
    with patch("ml.features.rolling_level_discovery._get_conn") as mock_conn:
        cur = MagicMock()
        cur.fetchone.return_value = (4 * 1440,)  # only 4 days of 60
        mock_conn.return_value.cursor.return_value = cur

        from ml.features.rolling_level_discovery import check_window_coverage

        loaded, expected, pct = check_window_coverage("AUDUSD", 60)

        assert loaded == 4 * 1440
        assert pct < 10.0  # well below MIN_COVERAGE_PCT


def test_discover_symbol_skips_on_insufficient_coverage():
    with patch("ml.features.rolling_level_discovery.check_window_coverage") as mock_cov:
        mock_cov.return_value = (4000, 86400, 4.6)  # matches real AUDUSD gap scenario

        with patch("ml.features.rolling_level_discovery.log_discovery_run"):
            from ml.features.rolling_level_discovery import discover_symbol

            result = discover_symbol(
                "AUDUSD",
                asset_class="forex",
                window_days=60,
                dry_run=True,
                trigger_reason="manual",
            )

    assert result.skipped_reason is not None
    assert "insufficient_coverage" in result.skipped_reason
    assert result.levels_found == 0
    assert result.trigger_reason == "manual"


def test_discover_symbol_dry_run_does_not_write():
    with patch("ml.features.rolling_level_discovery.check_window_coverage") as mock_cov:
        mock_cov.return_value = (86400, 86400, 100.0)

        with patch("ml.features.rolling_level_discovery.load_bars_window") as mock_load:
            mock_load.return_value = pd.DataFrame(
                {
                    "open": [1.0, 2.0, 3.0],
                    "high": [1.0, 2.0, 3.0],
                    "low": [1.0, 2.0, 3.0],
                    "close": [1.0, 2.0, 3.0],
                    "volume": [1.0, 1.0, 1.0],
                }
            )

            with patch("ml.features.rolling_level_discovery.LevelHistoryTracker") as mock_tracker_cls:
                mock_tracker = MagicMock()
                mock_tracker.levels = [MagicMock()]
                mock_tracker_cls.return_value = mock_tracker

                with patch("ml.features.rolling_level_discovery._insert_discovered_level") as mock_insert:
                    from ml.features.rolling_level_discovery import discover_symbol

                    result = discover_symbol(
                        "MES", asset_class="futures", window_days=60, dry_run=True
                    )

                    mock_insert.assert_not_called()

    assert result.levels_found == 1


def test_archive_stale_levels_archives_far_quiet_levels():
    with patch("ml.features.rolling_level_discovery._get_conn") as mock_conn:
        cur = MagicMock()
        stale_row = {
            "symbol": "BTCUSD",
            "level_price": 95000.0,
            "touch_count": 50,
            "hold_rate": 0.7,
            "last_touched": None,
        }
        cur.fetchall.return_value = [stale_row]
        mock_conn.return_value.cursor.return_value = cur

        from ml.features.rolling_level_discovery import archive_stale_levels

        count = archive_stale_levels("BTCUSD", last_close=65000.0)

    assert count == 1


def test_archive_stale_levels_no_stale_rows():
    with patch("ml.features.rolling_level_discovery._get_conn") as mock_conn:
        cur = MagicMock()
        cur.fetchall.return_value = []
        mock_conn.return_value.cursor.return_value = cur

        from ml.features.rolling_level_discovery import archive_stale_levels

        count = archive_stale_levels("AAPL", last_close=200.0)

    assert count == 0


def test_archive_level_upserts_when_already_archived():
    cur = MagicMock()
    from ml.features.rolling_level_discovery import _archive_level

    _archive_level(cur, "BTCUSD", 81382.84287, "drift_stale")
    insert_sql = cur.execute.call_args_list[0][0][0]
    assert "ON CONFLICT (symbol, level_price)" in insert_sql

    _archive_level(cur, "BTCUSD", 81382.84287, "regime_shift")
    assert cur.execute.call_count == 6


def test_reactivate_requires_price_inside_zone():
    """Current price must be inside the archived level's price_min/price_max zone."""
    with patch("ml.features.rolling_level_discovery._get_conn") as mock_conn:
        cur = MagicMock()
        archived_row = {
            "symbol": "BTCUSD",
            "level_price": 95000.0,
            "price_min": 94500.0,
            "price_max": 95500.0,
            "touch_count": 50,
            "hold_rate": 0.7,
        }
        cur.fetchall.return_value = [archived_row]
        mock_conn.return_value.cursor.return_value = cur

        from ml.features.rolling_level_discovery import reactivate_if_price_returns

        count = reactivate_if_price_returns("BTCUSD", current_price=95000.0)

    assert count == 1


def test_reactivate_no_match_when_price_outside_all_zones():
    with patch("ml.features.rolling_level_discovery._get_conn") as mock_conn:
        cur = MagicMock()
        cur.fetchall.return_value = []  # SQL WHERE clause already filters by zone
        mock_conn.return_value.cursor.return_value = cur

        from ml.features.rolling_level_discovery import reactivate_if_price_returns

        count = reactivate_if_price_returns("BTCUSD", current_price=65000.0)

    assert count == 0


def test_discover_symbol_handles_exception_gracefully():
    with patch("ml.features.rolling_level_discovery.check_window_coverage") as mock_cov:
        mock_cov.return_value = (86400, 86400, 100.0)

        with patch(
            "ml.features.rolling_level_discovery.load_bars_window",
            side_effect=Exception("DB down"),
        ):
            with patch("ml.features.rolling_level_discovery.log_discovery_run"):
                from ml.features.rolling_level_discovery import discover_symbol

                result = discover_symbol(
                    "ES", asset_class="futures", window_days=60, dry_run=False
                )

    assert result.error is not None
    assert "DB down" in result.error
