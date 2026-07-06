"""
Integration tests for rolling level discovery.

Uses an in-memory DB harness (see discovery_db_harness.py) plus fixture OHLCV.
No live Postgres required.
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from ml.features.rolling_level_discovery import discover_symbol
from tests.fixtures.ohlcv import mes_discovery_ohlcv_5m
from tests.integration.discovery_db_harness import FakeDiscoveryStore


@pytest.fixture
def discovery_db(monkeypatch):
    store = FakeDiscoveryStore()
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
    monkeypatch.setenv("LEVEL_EXIT_RECOMPUTE_ON_DISCOVERY", "false")
    monkeypatch.setattr(
        "ml.features.rolling_level_discovery._get_conn",
        store.connect,
    )
    monkeypatch.setattr(
        "ml.features.rolling_level_discovery._ensure_discovery_schema",
        lambda _cur: None,
    )
    return store


def _full_coverage(window_days: int = 60) -> tuple[int, int, float]:
    expected = max(1, window_days * 1440)
    return expected, expected, 100.0


def test_dry_run_finds_levels_from_fixture_bars():
    fixture = mes_discovery_ohlcv_5m()

    with patch(
        "ml.features.rolling_level_discovery.check_window_coverage",
        return_value=_full_coverage(),
    ), patch(
        "ml.features.rolling_level_discovery.load_bars_window",
        return_value=fixture,
    ):
        result = discover_symbol(
            "MES",
            asset_class="futures",
            window_days=60,
            dry_run=True,
        )

    assert result.skipped_reason is None
    assert result.error is None
    assert result.levels_found > 0
    assert result.last_close == pytest.approx(float(fixture["close"].iloc[-1]))
    assert result.merge_mode is None


def test_coverage_skip_on_short_window(discovery_db):
    with patch(
        "ml.features.rolling_level_discovery.check_window_coverage",
        return_value=(4000, 86400, 4.6),
    ):
        result = discover_symbol(
            "MES",
            asset_class="futures",
            window_days=60,
            dry_run=False,
        )

    assert result.skipped_reason == "insufficient_coverage"
    assert result.levels_found == 0
    assert len(discovery_db.audit_runs) == 1


def test_drift_merge_archives_stale_seed_levels(discovery_db):
    fixture = mes_discovery_ohlcv_5m()
    last_close = float(fixture["close"].iloc[-1])
    discovery_db.seed_level("MES", 6000.0, touch_count=8, hold_rate=0.65)
    discovery_db.seed_level("MES", 5020.0, touch_count=8, hold_rate=0.65)

    with patch(
        "ml.features.rolling_level_discovery.check_window_coverage",
        return_value=_full_coverage(),
    ), patch(
        "ml.features.rolling_level_discovery.load_bars_window",
        return_value=fixture,
    ):
        result = discover_symbol(
            "MES",
            asset_class="futures",
            window_days=60,
            dry_run=False,
            trigger_reason="manual",
        )

    assert result.skipped_reason is None
    assert result.merge_mode == "drift"
    assert result.levels_archived >= 1
    archived_prices = {row["level_price"] for row in discovery_db.archived_levels("MES")}
    assert 6000.0 in archived_prices
    assert any(
        row.level_price == 6000.0 and not row.is_active
        for row in discovery_db.levels.values()
    )
    assert abs(last_close - 6000.0) / last_close > 0.03


def test_regime_shift_archives_levels_outside_discovered_band(discovery_db):
    fixture = mes_discovery_ohlcv_5m()
    last_close = 8500.0
    fixture = fixture.copy()
    fixture.iloc[-1, fixture.columns.get_loc("close")] = last_close
    fixture.iloc[-1, fixture.columns.get_loc("high")] = last_close + 1.0
    fixture.iloc[-1, fixture.columns.get_loc("low")] = last_close - 1.0

    discovery_db.seed_level("MES", 4500.0, touch_count=8, hold_rate=0.65)
    discovery_db.seed_level("MES", 4861.0, touch_count=8, hold_rate=0.65)
    discovery_db.seed_level("MES", 7321.0, touch_count=8, hold_rate=0.65)

    with patch(
        "ml.features.rolling_level_discovery.check_window_coverage",
        return_value=_full_coverage(),
    ), patch(
        "ml.features.rolling_level_discovery.load_bars_window",
        return_value=fixture,
    ):
        result = discover_symbol(
            "MES",
            asset_class="futures",
            window_days=60,
            dry_run=False,
            trigger_reason="startup",
        )

    assert result.skipped_reason is None
    assert result.merge_mode == "regime_shift"
    assert result.regime_gap_pct is not None
    assert result.regime_gap_pct >= 8.0
    assert result.levels_archived >= 1
    archived = discovery_db.archived_levels("MES")
    archived_prices = {row["level_price"] for row in archived}
    assert archived_prices.intersection({4500.0, 4861.0, 7321.0})
    assert any(row["archive_reason"] == "regime_shift" for row in archived)


def test_rearchive_same_level_does_not_error(discovery_db):
    """Re-archiving an already-archived level must not violate unique constraint."""
    fixture = mes_discovery_ohlcv_5m()
    stale_price = 6000.0
    discovery_db.seed_level("BTCUSD", stale_price, touch_count=8, hold_rate=0.65)
    discovery_db.archive.append(
        {
            "symbol": "BTCUSD",
            "level_price": stale_price,
            "archive_reason": "drift_stale",
        }
    )

    with patch(
        "ml.features.rolling_level_discovery.check_window_coverage",
        return_value=_full_coverage(),
    ), patch(
        "ml.features.rolling_level_discovery.load_bars_window",
        return_value=fixture,
    ):
        result = discover_symbol(
            "BTCUSD",
            asset_class="crypto",
            window_days=60,
            dry_run=False,
            trigger_reason="interval",
        )

    assert result.error is None
    assert result.levels_archived >= 1
    archived = discovery_db.archived_levels("BTCUSD")
    assert sum(1 for row in archived if row["level_price"] == stale_price) == 1


def test_drift_sweep_archives_matched_stale_levels(discovery_db):
    """Merged historical levels far from last close must not stay on the watchlist."""
    fixture = mes_discovery_ohlcv_5m()
    last_close = float(fixture["close"].iloc[-1])
    stale_near_band = last_close * 1.05
    discovery_db.seed_level("ETHUSD", stale_near_band, touch_count=8, hold_rate=0.65)

    with patch(
        "ml.features.rolling_level_discovery.check_window_coverage",
        return_value=_full_coverage(),
    ), patch(
        "ml.features.rolling_level_discovery.load_bars_window",
        return_value=fixture,
    ):
        result = discover_symbol(
            "ETHUSD",
            asset_class="crypto",
            window_days=60,
            dry_run=False,
            trigger_reason="interval",
        )

    assert result.error is None
    assert result.levels_archived >= 1
    assert not any(
        row.is_active and row.level_price == stale_near_band
        for row in discovery_db.levels.values()
    )
    assert abs(stale_near_band - last_close) / last_close > 0.03


def test_watchlist_sync_matches_gate_thresholds(discovery_db, monkeypatch):
    monkeypatch.setattr("ml.features.rolling_level_discovery.WATCHLIST_MIN_STRENGTH", 0.35)
    fixture = mes_discovery_ohlcv_5m()

    with patch(
        "ml.features.rolling_level_discovery.check_window_coverage",
        return_value=_full_coverage(),
    ), patch(
        "ml.features.rolling_level_discovery.load_bars_window",
        return_value=fixture,
    ):
        result = discover_symbol(
            "MES",
            asset_class="futures",
            window_days=60,
            dry_run=False,
        )

    assert result.skipped_reason is None
    expected_active = len(discovery_db.qualifying_active_levels("MES"))
    assert result.watchlist_active == discovery_db.watchlist_active_count("MES")
    assert result.watchlist_active == expected_active
    assert result.watchlist_active >= 2
