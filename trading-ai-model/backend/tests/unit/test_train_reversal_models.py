"""Unit tests for ml.training.train_reversal_models."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from ml.features.cross_symbol_analysis import CrossSymbolAnalyzer
from ml.training.train_reversal_models import (
    ASSET_CONFIGS,
    compute_technical_features,
    fit_all_trackers,
    label_reversals,
    run_cross_symbol_analysis,
    train_symbol,
)


def _synthetic_bars(n_bars: int = 2000, base: float = 1.0800) -> pd.DataFrame:
    idx = pd.date_range("2025-01-01", periods=n_bars, freq="5min", tz="UTC")
    close = np.array(
        [base + 0.01 * np.sin(i / 8.0) for i in range(n_bars)],
        dtype=np.float64,
    )
    high = close + 0.0015
    low = close - 0.0015
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.full(n_bars, 1000.0),
        },
        index=idx,
    )


def test_compute_technical_features_has_core_columns():
    df = _synthetic_bars(300)
    feat = compute_technical_features(df)
    for col in ("rsi_14", "macd_line", "bb_position", "atr_14", "volume_ratio"):
        assert col in feat.columns


def test_label_reversals_produces_labeled_rows():
    df = _synthetic_bars(500)
    labels = label_reversals(df, ASSET_CONFIGS["forex"])
    labeled = labels.dropna()
    assert len(labeled) > 0
    assert set(labeled.unique()).issubset({0.0, 1.0})


def test_fit_all_trackers_and_cross_symbol(tmp_path: Path):
    bars = {
        "EURUSD": _synthetic_bars(),
        "GBPUSD": _synthetic_bars(base=1.2600),
    }
    trackers = fit_all_trackers(list(bars), bars, tmp_path)
    assert len(trackers) == 2
    analyzer = run_cross_symbol_analysis(trackers, tmp_path)
    assert analyzer.profile is not None
    assert (tmp_path / "cross_symbol_profile.json").exists()


def test_train_symbol_dry_run(tmp_path: Path):
    bars = {"EURUSD": _synthetic_bars()}
    trackers = fit_all_trackers(["EURUSD"], bars, tmp_path)
    analyzer = CrossSymbolAnalyzer().fit(trackers)

    result = train_symbol(
        symbol="EURUSD",
        df=bars["EURUSD"],
        tracker=trackers["EURUSD"],
        analyzer=analyzer,
        all_trackers=trackers,
        val_start="2025-11-01",
        model_dir=tmp_path,
        dry_run=True,
    )
    assert result["status"] == "dry_run"
    assert result["n_levels"] > 0
