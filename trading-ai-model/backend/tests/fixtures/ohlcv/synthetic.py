"""
Synthetic OHLCV for rolling level-discovery integration tests.

Known swing clusters on the MES ~5000 handle (deterministic triangle wave):
  - support  ~4980
  - pivot    ~5000
  - resistance ~5020

``mes_discovery_ohlcv_1m`` — 600+ 1-minute bars (CSV / DB import).
``mes_discovery_ohlcv_5m`` — 520+ 5-minute bars (in-memory discovery mocks).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

SUPPORT = 4980.0
PIVOT = 5000.0
RESISTANCE = 5020.0
EXPECTED_MES_SWING_CLUSTERS: tuple[float, float, float] = (SUPPORT, PIVOT, RESISTANCE)

MES_1M_CSV_PATH = Path(__file__).resolve().parent / "MES_1m.csv"

_DEFAULT_START = pd.Timestamp("2025-01-06T14:30:00", tz="UTC")
_WICK = 0.75


def _triangle_close(n_bars: int) -> np.ndarray:
    """Oscillate between SUPPORT and RESISTANCE; pivot at midpoint each half-cycle."""
    period = max(24, n_bars // 5)
    idx = np.arange(n_bars, dtype=np.float64)
    phase = (idx % period) / period
    up = phase < 0.5
    close = np.empty(n_bars, dtype=np.float64)
    close[up] = SUPPORT + (RESISTANCE - SUPPORT) * (phase[up] * 2.0)
    close[~up] = RESISTANCE - (RESISTANCE - SUPPORT) * ((phase[~up] - 0.5) * 2.0)
    return close


def _ohlcv_from_close(
    close: np.ndarray,
    *,
    start: pd.Timestamp = _DEFAULT_START,
    freq: str,
) -> pd.DataFrame:
    n_bars = len(close)
    idx = pd.date_range(start, periods=n_bars, freq=freq, tz="UTC")
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    high = np.maximum.reduce([open_, close]) + _WICK
    low = np.minimum.reduce([open_, close]) - _WICK
    volume = np.full(n_bars, 1500.0)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def mes_discovery_ohlcv_1m(n_bars: int = 600) -> pd.DataFrame:
    """Synthetic 1m OHLCV with revisits at 4980 / 5000 / 5020."""
    if n_bars < 500:
        raise ValueError("mes_discovery_ohlcv_1m requires at least 500 bars")
    return _ohlcv_from_close(_triangle_close(n_bars), freq="1min")


def mes_discovery_ohlcv_5m(n_bars: int = 520) -> pd.DataFrame:
    """Synthetic 5m OHLCV for in-memory discovery (matches rolling resample output)."""
    if n_bars < 500:
        raise ValueError("mes_discovery_ohlcv_5m requires at least 500 bars")
    return _ohlcv_from_close(_triangle_close(n_bars), freq="5min")


def write_mes_1m_csv(
    path: Path | None = None,
    *,
    n_bars: int = 600,
) -> Path:
    """Write replay-compatible ``MES_1m.csv`` (timestamp, OHLCV columns)."""
    out_path = path or MES_1M_CSV_PATH
    df = mes_discovery_ohlcv_1m(n_bars=n_bars)
    df.index.name = "timestamp"
    export = df.reset_index()
    export.to_csv(out_path, index=False)
    return out_path


def load_mes_1m_csv(path: Path | None = None) -> pd.DataFrame:
    """Load ``MES_1m.csv`` using the same rules as csv_ohlcv_import."""
    from data.providers.csv_ohlcv_import import load_ohlcv_csv

    return load_ohlcv_csv(path or MES_1M_CSV_PATH)
