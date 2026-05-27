"""Sample OHLCV fixtures for tests."""

import pandas as pd


def sample_ohlcv(n: int = 50) -> pd.DataFrame:
    import numpy as np

    rng = np.random.default_rng(42)
    close = 5000 + np.cumsum(rng.normal(0, 5, n))
    high = close + rng.uniform(1, 10, n)
    low = close - rng.uniform(1, 10, n)
    open_ = close + rng.normal(0, 2, n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": rng.integers(100, 1000, n)}
    )


def sample_swings() -> list[tuple[float, float]]:
    return [(0, 4980), (10, 5020), (20, 4990), (30, 5010), (40, 4995)]
