"""
ml/features/level_history.py

Finds ALL natural price levels from historical bars and tracks
how many times price touched each level, reversed, or broke through.

No predefined round numbers — the market tells us what levels matter.

How it works:
  1. Identify all swing highs and lows (local price extrema)
  2. Cluster nearby swing points into "levels" using price tolerance
  3. For each level, scan all bars to find every touch
  4. Classify each touch as HOLD (reversed) or BREAK (continued through)
  5. Rank levels by touch count and hold rate

A level is significant when:
  - It has been touched >= MIN_TOUCHES times
  - Its hold rate is meaningfully different from the base rate

Usage:
  tracker = LevelHistoryTracker(symbol="EURUSD", asset_class="forex")
  tracker.fit(df_5m)                    # scan bars, build level database
  features = tracker.get_features(price) # get features for one price point
  tracker.save("models/levels/EURUSD_levels.json")
  tracker.load("models/levels/EURUSD_levels.json")
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("level_history")


# ─── Config ───────────────────────────────────────────────────────────────────


@dataclass
class LevelConfig:
    """
    Parameters for level detection and clustering.
    Varies by asset class because volatility differs.
    """

    # How close price must come to a level to count as a "touch" (% of price)
    touch_tolerance_pct: float

    # How far price must move after a touch to classify as HOLD or BREAK (% of price)
    reversal_threshold_pct: float

    # Minimum number of bars to look ahead after a touch for the outcome
    outcome_window: int

    # How close two swing points must be to be clustered into one level (% of price)
    cluster_tolerance_pct: float

    # Minimum touches required to consider a level statistically significant
    min_touches: int

    # Swing detection: how many bars each side must be lower/higher
    swing_lookback: int


LEVEL_CONFIGS = {
    "futures": LevelConfig(
        touch_tolerance_pct=0.08,
        reversal_threshold_pct=0.20,
        outcome_window=20,
        cluster_tolerance_pct=0.10,
        min_touches=3,
        swing_lookback=5,
    ),
    "forex": LevelConfig(
        touch_tolerance_pct=0.04,
        reversal_threshold_pct=0.10,
        outcome_window=20,
        cluster_tolerance_pct=0.06,
        min_touches=3,
        swing_lookback=5,
    ),
    "crypto": LevelConfig(
        touch_tolerance_pct=0.15,
        reversal_threshold_pct=0.30,
        outcome_window=12,
        cluster_tolerance_pct=0.20,
        min_touches=3,
        swing_lookback=5,
    ),
    "equity": LevelConfig(
        touch_tolerance_pct=0.10,
        reversal_threshold_pct=0.25,
        outcome_window=16,
        cluster_tolerance_pct=0.12,
        min_touches=3,
        swing_lookback=5,
    ),
}


# ─── Level dataclass ──────────────────────────────────────────────────────────


@dataclass
class Level:
    """
    One discovered price level with its full touch history.
    """

    price: float  # Level price (center of cluster)
    price_min: float  # Lowest price in cluster
    price_max: float  # Highest price in cluster
    touch_count: int = 0  # Total times price touched this level
    hold_count: int = 0  # Times price reversed (held the level)
    break_count: int = 0  # Times price broke through
    touch_dates: list = field(default_factory=list)
    hold_dates: list = field(default_factory=list)
    break_dates: list = field(default_factory=list)

    @property
    def hold_rate(self) -> float:
        """P(reversal | touch) — the key probability metric."""
        if self.touch_count == 0:
            return 0.0
        return self.hold_count / self.touch_count

    @property
    def is_significant(self) -> bool:
        return self.touch_count >= 3

    @property
    def strength_score(self) -> float:
        """
        Combined score 0-1.
        Balances hold rate with touch count confidence.
        More touches = more confidence in the hold rate.
        """
        if self.touch_count == 0:
            return 0.0
        # Wilson score lower bound — conservative estimate
        n = self.touch_count
        p = self.hold_rate
        z = 1.96  # 95% confidence
        numerator = p + z * z / (2 * n) - z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
        denominator = 1 + z * z / n
        return max(0.0, float(numerator / denominator))

    def to_dict(self) -> dict:
        return {
            "price": self.price,
            "price_min": self.price_min,
            "price_max": self.price_max,
            "touch_count": self.touch_count,
            "hold_count": self.hold_count,
            "break_count": self.break_count,
            "hold_rate": round(self.hold_rate, 4),
            "strength": round(self.strength_score, 4),
            "touch_dates": self.touch_dates[-10:],
            "hold_dates": self.hold_dates[-10:],
            "break_dates": self.break_dates[-10:],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Level":
        return cls(
            price=d["price"],
            price_min=d["price_min"],
            price_max=d["price_max"],
            touch_count=d["touch_count"],
            hold_count=d["hold_count"],
            break_count=d["break_count"],
            touch_dates=d.get("touch_dates", []),
            hold_dates=d.get("hold_dates", []),
            break_dates=d.get("break_dates", []),
        )


# ─── Main tracker ─────────────────────────────────────────────────────────────


class LevelHistoryTracker:
    """
    Discovers and tracks all natural price levels from historical bars.

    Does NOT use predefined round numbers. Instead finds where price
    actually clusters and reverses most frequently.
    """

    def __init__(self, symbol: str, asset_class: str):
        self.symbol = symbol
        self.asset_class = asset_class
        self.cfg = LEVEL_CONFIGS.get(asset_class, LEVEL_CONFIGS["equity"])
        self.levels: list[Level] = []
        self._is_fitted = False

    # ── Step 1: Find swing points ─────────────────────────────────────────────

    def _find_swing_highs(self, df: pd.DataFrame) -> list[float]:
        """Find swing highs — local maxima over swing_lookback window."""
        lb = self.cfg.swing_lookback
        highs = []
        for i in range(lb, len(df) - lb):
            window = df["high"].iloc[i - lb : i + lb + 1]
            if df["high"].iloc[i] == window.max():
                highs.append(float(df["high"].iloc[i]))
        return highs

    def _find_swing_lows(self, df: pd.DataFrame) -> list[float]:
        """Find swing lows — local minima over swing_lookback window."""
        lb = self.cfg.swing_lookback
        lows = []
        for i in range(lb, len(df) - lb):
            window = df["low"].iloc[i - lb : i + lb + 1]
            if df["low"].iloc[i] == window.min():
                lows.append(float(df["low"].iloc[i]))
        return lows

    # ── Step 2: Cluster swing points into levels ──────────────────────────────

    def _cluster_prices(self, prices: list[float]) -> list[Level]:
        """Cluster nearby swing prices into discrete levels."""
        if not prices:
            return []

        sorted_prices = sorted(prices)
        tol = self.cfg.cluster_tolerance_pct / 100.0

        clusters: list[list[float]] = [[sorted_prices[0]]]

        for price in sorted_prices[1:]:
            center = float(np.mean(clusters[-1]))
            if abs(price - center) / (center + 1e-10) <= tol:
                clusters[-1].append(price)
            else:
                clusters.append([price])

        levels = []
        for cluster in clusters:
            center = float(np.mean(cluster))
            levels.append(
                Level(
                    price=center,
                    price_min=float(min(cluster)),
                    price_max=float(max(cluster)),
                )
            )

        return levels

    # ── Step 3: Find all touches and classify them ────────────────────────────

    def _classify_touches(self, df: pd.DataFrame, levels: list[Level]) -> None:
        """Scan bars, count touches, classify each as HOLD or BREAK."""
        tol = self.cfg.touch_tolerance_pct / 100.0
        rev = self.cfg.reversal_threshold_pct / 100.0
        ow = self.cfg.outcome_window
        n = len(df)

        close = np.asarray(df["close"].to_numpy(), dtype=np.float64)
        high = np.asarray(df["high"].to_numpy(), dtype=np.float64)
        low = np.asarray(df["low"].to_numpy(), dtype=np.float64)
        index = df.index

        for level in levels:
            lp = level.price
            last_touch_idx = -ow

            for i in range(ow, n - ow):
                bar_high = high[i]
                bar_low = low[i]
                bar_mid = (bar_high + bar_low) / 2

                touched = (
                    abs(bar_mid - lp) / (lp + 1e-10) <= tol
                    or (
                        bar_low <= lp * (1 + tol)
                        and bar_high >= lp * (1 - tol)
                    )
                )

                if not touched:
                    continue

                if i - last_touch_idx < self.cfg.swing_lookback:
                    continue

                last_touch_idx = i
                touch_date = str(index[i])[:10]
                level.touch_count += 1
                level.touch_dates.append(touch_date)

                current_price = close[i]
                future_high = float(high[i + 1 : i + ow + 1].max())
                future_low = float(low[i + 1 : i + ow + 1].min())

                up_move = (future_high - current_price) / (current_price + 1e-10)
                down_move = (current_price - future_low) / (current_price + 1e-10)

                came_from_above = close[i - self.cfg.swing_lookback] > lp
                came_from_below = close[i - self.cfg.swing_lookback] < lp

                if came_from_above:
                    if up_move >= rev:
                        level.hold_count += 1
                        level.hold_dates.append(touch_date)
                    elif down_move >= rev:
                        level.break_count += 1
                        level.break_dates.append(touch_date)
                elif came_from_below:
                    if down_move >= rev:
                        level.hold_count += 1
                        level.hold_dates.append(touch_date)
                    elif up_move >= rev:
                        level.break_count += 1
                        level.break_dates.append(touch_date)

    # ── Main fit method ───────────────────────────────────────────────────────

    def fit(self, df: pd.DataFrame) -> "LevelHistoryTracker":
        """
        Scan all bars, discover levels, classify all touches.

        Args:
            df: OHLCV DataFrame with columns open/high/low/close/volume
        """
        logger.info(
            "%s: scanning %d bars for natural price levels...",
            self.symbol,
            len(df),
        )

        swing_highs = self._find_swing_highs(df)
        swing_lows = self._find_swing_lows(df)
        all_swings = swing_highs + swing_lows

        logger.info(
            "%s: found %d swing highs, %d swing lows",
            self.symbol,
            len(swing_highs),
            len(swing_lows),
        )

        raw_levels = self._cluster_prices(all_swings)
        logger.info("%s: clustered into %d levels", self.symbol, len(raw_levels))

        self._classify_touches(df, raw_levels)

        self.levels = [
            lvl for lvl in raw_levels if lvl.touch_count >= self.cfg.min_touches
        ]
        self.levels.sort(key=lambda x: x.strength_score, reverse=True)

        logger.info(
            "%s: %d significant levels | top level: price=%.5f "
            "touches=%d hold_rate=%.1f%% strength=%.3f",
            self.symbol,
            len(self.levels),
            self.levels[0].price if self.levels else 0,
            self.levels[0].touch_count if self.levels else 0,
            self.levels[0].hold_rate * 100 if self.levels else 0,
            self.levels[0].strength_score if self.levels else 0,
        )

        self._is_fitted = True
        return self

    # ── Feature extraction ────────────────────────────────────────────────────

    def get_features(self, price: float) -> dict:
        """Get level-based features for a given price."""
        if not self._is_fitted or not self.levels:
            return self._empty_features()

        tol = self.cfg.touch_tolerance_pct / 100.0

        distances = [abs(lvl.price - price) / (price + 1e-10) for lvl in self.levels]

        nearest_idx = int(np.argmin(distances))
        nearest_dist = distances[nearest_idx]
        nearest_lvl = self.levels[nearest_idx]

        sorted_by_dist = sorted(enumerate(distances), key=lambda x: x[1])[:5]

        at_level = nearest_dist <= tol

        nearby_levels = [
            self.levels[i] for i, d in sorted_by_dist if d <= tol * 3
        ]
        best_nearby = (
            max(nearby_levels, key=lambda x: x.strength_score)
            if nearby_levels
            else None
        )

        if nearby_levels:
            avg_hold_rate = float(np.mean([l.hold_rate for l in nearby_levels]))
            max_hold_rate = max(l.hold_rate for l in nearby_levels)
            max_strength = max(l.strength_score for l in nearby_levels)
            max_touches = max(l.touch_count for l in nearby_levels)
            total_touches = sum(l.touch_count for l in nearby_levels)
        else:
            avg_hold_rate = max_hold_rate = max_strength = 0.0
            max_touches = total_touches = 0

        return {
            "level_nearest_dist_pct": round(nearest_dist * 100, 4),
            "level_nearest_hold_rate": round(nearest_lvl.hold_rate, 4),
            "level_nearest_touches": nearest_lvl.touch_count,
            "level_nearest_strength": round(nearest_lvl.strength_score, 4),
            "level_nearest_hold_count": nearest_lvl.hold_count,
            "level_nearest_break_count": nearest_lvl.break_count,
            "level_at_level": int(at_level),
            "level_at_strong": int(at_level and nearest_lvl.strength_score > 0.6),
            "level_at_weak": int(at_level and nearest_lvl.strength_score < 0.4),
            "level_best_hold_rate": round(
                best_nearby.hold_rate if best_nearby else 0, 4
            ),
            "level_best_strength": round(
                best_nearby.strength_score if best_nearby else 0, 4
            ),
            "level_best_touches": best_nearby.touch_count if best_nearby else 0,
            "level_avg_hold_rate": round(avg_hold_rate, 4),
            "level_max_hold_rate": round(max_hold_rate, 4),
            "level_max_strength": round(max_strength, 4),
            "level_max_touches": max_touches,
            "level_nearby_count": len(nearby_levels),
            "level_total_touches": total_touches,
            "level_zone_quality": round(
                max_strength * min(max_touches / 10.0, 1.0), 4
            ),
        }

    def get_features_series(self, df: pd.DataFrame) -> pd.DataFrame:
        """Compute level features for every row in a DataFrame."""
        if not self._is_fitted:
            raise RuntimeError("Call .fit() first")

        features = [self.get_features(float(price)) for price in df["close"]]
        return pd.DataFrame(features, index=df.index)

    def _empty_features(self) -> dict:
        return {
            "level_nearest_dist_pct": 0.0,
            "level_nearest_hold_rate": 0.0,
            "level_nearest_touches": 0,
            "level_nearest_strength": 0.0,
            "level_nearest_hold_count": 0,
            "level_nearest_break_count": 0,
            "level_at_level": 0,
            "level_at_strong": 0,
            "level_at_weak": 0,
            "level_best_hold_rate": 0.0,
            "level_best_strength": 0.0,
            "level_best_touches": 0,
            "level_avg_hold_rate": 0.0,
            "level_max_hold_rate": 0.0,
            "level_max_strength": 0.0,
            "level_max_touches": 0,
            "level_nearby_count": 0,
            "level_total_touches": 0,
            "level_zone_quality": 0.0,
        }

    # ── Top levels summary ────────────────────────────────────────────────────

    def top_levels(self, n: int = 20) -> pd.DataFrame:
        """Return the top N levels sorted by strength score."""
        if not self.levels:
            return pd.DataFrame()

        rows = []
        for lvl in self.levels[:n]:
            rows.append(
                {
                    "price": round(lvl.price, 5),
                    "touches": lvl.touch_count,
                    "holds": lvl.hold_count,
                    "breaks": lvl.break_count,
                    "hold_rate": f"{lvl.hold_rate * 100:.1f}%",
                    "strength": round(lvl.strength_score, 3),
                    "zone": f"{lvl.price_min:.5f} — {lvl.price_max:.5f}",
                }
            )
        return pd.DataFrame(rows)

    def print_top_levels(self, n: int = 20) -> None:
        """Print a table of the top levels."""
        df = self.top_levels(n)
        if df.empty:
            print(f"{self.symbol}: no significant levels found")
            return
        print(f"\n{'=' * 70}")
        print(f"  {self.symbol} — Top {n} Natural Price Levels")
        print(f"{'=' * 70}")
        print(df.to_string(index=False))
        print(f"{'=' * 70}\n")

    # ── Persistence ───────────────────────────────────────────────────────────

    def save(self, path: str) -> None:
        """Save levels to JSON file."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "symbol": self.symbol,
            "asset_class": self.asset_class,
            "level_count": len(self.levels),
            "levels": [lvl.to_dict() for lvl in self.levels],
        }
        p.write_text(json.dumps(data, indent=2))
        logger.info("%s: saved %d levels to %s", self.symbol, len(self.levels), path)

    def load(self, path: str) -> "LevelHistoryTracker":
        """Load levels from JSON file."""
        data = json.loads(Path(path).read_text())
        self.symbol = data.get("symbol", self.symbol)
        self.asset_class = data.get("asset_class", self.asset_class)
        self.levels = [Level.from_dict(d) for d in data["levels"]]
        self._is_fitted = True
        logger.info(
            "%s: loaded %d levels from %s",
            self.symbol,
            len(self.levels),
            path,
        )
        return self
