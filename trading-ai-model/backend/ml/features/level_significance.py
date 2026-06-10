"""
ml/features/level_significance.py

Ranks price levels by how many times they were hit, classifies each
as support or resistance, and analyzes what volume did when price
arrived at that level.

Connects to:
  - level_history.py (LevelHistoryTracker — provides the raw touch data)
  - train_reversal_models.py (adds significance features to training)
  - FeaturePipeline (adds live significance features per bar)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("level_significance")


class LevelRole:
    SUPPORT = "SUPPORT"
    RESISTANCE = "RESISTANCE"
    MIXED = "MIXED"
    FLIP = "FLIP"


class VolumeImpact:
    HIGH_VOL_REVERSAL = "High volume → reversal"
    HIGH_VOL_BREAKOUT = "High volume → breakout"
    LOW_VOL_WEAK_HOLD = "Low volume → weak hold"
    LOW_VOL_DRIFT = "Low volume → drift through"
    NEUTRAL = "Volume neutral"


@dataclass
class TouchRecord:
    timestamp: str
    price_at_touch: float
    approach: str
    outcome: str
    volume_at_touch: float
    volume_ratio: float
    price_move_after: float
    role_at_touch: str


@dataclass
class SignificantLevel:
    price: float
    rank: int
    total_hits: int
    support_hits: int
    resistance_hits: int
    support_breaks: int
    resistance_breaks: int
    hold_count: int
    break_count: int
    hold_rate: float
    touches: list[TouchRecord] = field(default_factory=list)
    _cached_role: str = ""
    _cached_volume_impact: str = ""
    _cached_role_confidence: float = 0.0
    _cached_avg_volume_ratio: float = 1.0
    _cached_high_vol_hold_rate: float = 0.0
    _cached_low_vol_hold_rate: float = 0.0

    @property
    def role(self) -> str:
        if self._cached_role:
            return self._cached_role

        total_approach = (
            self.support_hits
            + self.resistance_hits
            + self.support_breaks
            + self.resistance_breaks
        )
        if total_approach == 0:
            return LevelRole.MIXED

        from_below = self.support_hits + self.support_breaks
        from_above = self.resistance_hits + self.resistance_breaks
        below_pct = from_below / total_approach
        above_pct = from_above / total_approach

        if len(self.touches) >= 6:
            early_half = self.touches[: len(self.touches) // 2]
            late_half = self.touches[len(self.touches) // 2 :]
            early_roles = [t.role_at_touch for t in early_half]
            late_roles = [t.role_at_touch for t in late_half]
            early_dominant = (
                "SUPPORT"
                if early_roles.count("SUPPORT") > len(early_roles) / 2
                else "RESISTANCE"
            )
            late_dominant = (
                "SUPPORT"
                if late_roles.count("SUPPORT") > len(late_roles) / 2
                else "RESISTANCE"
            )
            if early_dominant != late_dominant:
                return LevelRole.FLIP

        if below_pct >= 0.65:
            return LevelRole.SUPPORT
        if above_pct >= 0.65:
            return LevelRole.RESISTANCE
        return LevelRole.MIXED

    @property
    def role_confidence(self) -> float:
        if not self.touches and self._cached_role_confidence:
            return self._cached_role_confidence

        total = (
            self.support_hits
            + self.resistance_hits
            + self.support_breaks
            + self.resistance_breaks
        )
        if total == 0:
            return 0.0
        from_below = self.support_hits + self.support_breaks
        from_above = self.resistance_hits + self.resistance_breaks
        dominant = max(from_below, from_above)
        return round(dominant / total, 3)

    @property
    def volume_impact(self) -> str:
        if self._cached_volume_impact:
            return self._cached_volume_impact

        if len(self.touches) < 4:
            return VolumeImpact.NEUTRAL

        high_vol_touches = [t for t in self.touches if t.volume_ratio >= 1.3]
        low_vol_touches = [t for t in self.touches if t.volume_ratio <= 0.7]

        if len(high_vol_touches) >= 3:
            high_vol_holds = sum(1 for t in high_vol_touches if t.outcome == "hold")
            high_vol_hold_rt = high_vol_holds / len(high_vol_touches)
            if high_vol_hold_rt >= 0.65:
                return VolumeImpact.HIGH_VOL_REVERSAL
            if high_vol_hold_rt <= 0.35:
                return VolumeImpact.HIGH_VOL_BREAKOUT

        if len(low_vol_touches) >= 3:
            low_vol_holds = sum(1 for t in low_vol_touches if t.outcome == "hold")
            low_vol_hold_rt = low_vol_holds / len(low_vol_touches)
            if low_vol_hold_rt >= 0.60:
                return VolumeImpact.LOW_VOL_WEAK_HOLD
            if low_vol_hold_rt <= 0.35:
                return VolumeImpact.LOW_VOL_DRIFT

        return VolumeImpact.NEUTRAL

    @property
    def avg_volume_ratio(self) -> float:
        if self.touches:
            return round(float(np.mean([t.volume_ratio for t in self.touches])), 3)
        return self._cached_avg_volume_ratio

    @property
    def high_vol_hold_rate(self) -> float:
        if not self.touches:
            return self._cached_high_vol_hold_rate
        hv = [t for t in self.touches if t.volume_ratio >= 1.3]
        if len(hv) < 2:
            return self.hold_rate
        return round(sum(1 for t in hv if t.outcome == "hold") / len(hv), 3)

    @property
    def low_vol_hold_rate(self) -> float:
        if not self.touches:
            return self._cached_low_vol_hold_rate
        lv = [t for t in self.touches if t.volume_ratio <= 0.7]
        if len(lv) < 2:
            return self.hold_rate
        return round(sum(1 for t in lv if t.outcome == "hold") / len(lv), 3)

    def summary_line(self) -> str:
        return (
            f"#{self.rank:<2} | {self.price:>10.5f} | "
            f"{self.total_hits:>4} hits | "
            f"{self.role:<10} | "
            f"hold={self.hold_rate * 100:.1f}% | "
            f"{self.volume_impact}"
        )

    def to_dict(self) -> dict:
        return {
            "rank": self.rank,
            "price": self.price,
            "total_hits": self.total_hits,
            "role": self.role,
            "role_confidence": self.role_confidence,
            "support_hits": self.support_hits,
            "resistance_hits": self.resistance_hits,
            "support_breaks": self.support_breaks,
            "resistance_breaks": self.resistance_breaks,
            "hold_count": self.hold_count,
            "break_count": self.break_count,
            "hold_rate": round(self.hold_rate, 4),
            "volume_impact": self.volume_impact,
            "avg_volume_ratio": self.avg_volume_ratio,
            "high_vol_hold_rate": self.high_vol_hold_rate,
            "low_vol_hold_rate": self.low_vol_hold_rate,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SignificantLevel":
        return cls(
            price=data["price"],
            rank=data["rank"],
            total_hits=data["total_hits"],
            support_hits=data["support_hits"],
            resistance_hits=data["resistance_hits"],
            support_breaks=data["support_breaks"],
            resistance_breaks=data["resistance_breaks"],
            hold_count=data["hold_count"],
            break_count=data["break_count"],
            hold_rate=data["hold_rate"],
            _cached_role=data.get("role", ""),
            _cached_volume_impact=data.get("volume_impact", ""),
            _cached_role_confidence=float(data.get("role_confidence", 0.0)),
            _cached_avg_volume_ratio=float(data.get("avg_volume_ratio", 1.0)),
            _cached_high_vol_hold_rate=float(data.get("high_vol_hold_rate", 0.0)),
            _cached_low_vol_hold_rate=float(data.get("low_vol_hold_rate", 0.0)),
        )


class LevelSignificanceAnalyzer:
    """Discovers significant price levels and analyzes touch/volume behavior."""

    def __init__(
        self,
        symbol: str,
        asset_class: str,
        cluster_pct: float = 0.10,
        touch_tolerance_pct: float = 0.08,
        reversal_pct: float = 0.15,
        outcome_window: int = 20,
        min_hits: int = 3,
    ):
        self.symbol = symbol
        self.asset_class = asset_class
        self.cluster_pct = cluster_pct
        self.touch_tolerance_pct = touch_tolerance_pct
        self.reversal_pct = reversal_pct
        self.outcome_window = outcome_window
        self.min_hits = min_hits

        self.levels: list[SignificantLevel] = []
        self.top5: list[SignificantLevel] = []
        self._is_fitted = False

    def _find_swing_points(self, df: pd.DataFrame) -> list[float]:
        highs: list[float] = []
        lows: list[float] = []
        lb = 5

        for i in range(lb, len(df) - lb):
            if df["high"].iloc[i] == df["high"].iloc[i - lb : i + lb + 1].max():
                highs.append(float(df["high"].iloc[i]))
            if df["low"].iloc[i] == df["low"].iloc[i - lb : i + lb + 1].min():
                lows.append(float(df["low"].iloc[i]))

        return highs + lows

    def _cluster(self, prices: list[float]) -> list[float]:
        if not prices:
            return []

        sorted_prices = sorted(prices)
        tol = self.cluster_pct / 100.0
        clusters: list[list[float]] = [[sorted_prices[0]]]

        for price in sorted_prices[1:]:
            center = float(np.mean(clusters[-1]))
            if abs(price - center) / (center + 1e-10) <= tol:
                clusters[-1].append(price)
            else:
                clusters.append([price])

        return [float(np.mean(cluster)) for cluster in clusters]

    def _analyze_touches(self, df: pd.DataFrame, level_price: float) -> list[TouchRecord]:
        tol = self.touch_tolerance_pct / 100.0
        rev = self.reversal_pct / 100.0
        ow = self.outcome_window
        n = len(df)

        close = np.asarray(df["close"], dtype=float)
        high = np.asarray(df["high"], dtype=float)
        low = np.asarray(df["low"], dtype=float)
        volume = np.asarray(df["volume"], dtype=float)
        vol_ma = np.asarray(
            pd.Series(volume, index=df.index).rolling(20).mean(),
            dtype=float,
        )
        index = df.index
        lb = 5

        touches: list[TouchRecord] = []
        last_touch_i = -ow

        for i in range(lb, n - ow):
            bar_mid = (high[i] + low[i]) / 2
            touched = abs(bar_mid - level_price) / (level_price + 1e-10) <= tol or (
                low[i] <= level_price * (1 + tol)
                and high[i] >= level_price * (1 - tol)
            )
            if not touched or i - last_touch_i < lb:
                continue

            last_touch_i = i
            prev_close = close[i - lb]
            came_from_above = prev_close > level_price * (1 + tol * 0.5)
            came_from_below = prev_close < level_price * (1 - tol * 0.5)
            if not came_from_above and not came_from_below:
                continue

            approach = "from_above" if came_from_above else "from_below"
            touch_role = LevelRole.RESISTANCE if came_from_above else LevelRole.SUPPORT

            current_price = float(close[i])
            future_slice_high = high[i + 1 : i + ow + 1]
            future_slice_low = low[i + 1 : i + ow + 1]
            future_high = float(np.max(future_slice_high))
            future_low = float(np.min(future_slice_low))
            up_move = (future_high - current_price) / (current_price + 1e-10)
            down_move = (current_price - future_low) / (current_price + 1e-10)

            if came_from_above:
                outcome = "hold" if up_move >= rev else "break"
                price_move_after = up_move if outcome == "hold" else -down_move
            else:
                outcome = "hold" if down_move >= rev else "break"
                price_move_after = -down_move if outcome == "hold" else up_move

            vol_now = float(volume[i])
            vol_avg = float(vol_ma[i]) if not np.isnan(vol_ma[i]) else vol_now
            vol_ratio = vol_now / (vol_avg + 1e-10)

            touches.append(
                TouchRecord(
                    timestamp=str(index[i])[:19],
                    price_at_touch=round(current_price, 6),
                    approach=approach,
                    outcome=outcome,
                    volume_at_touch=round(vol_now, 2),
                    volume_ratio=round(float(vol_ratio), 3),
                    price_move_after=round(float(price_move_after) * 100, 3),
                    role_at_touch=touch_role,
                )
            )

        return touches

    def fit(self, df: pd.DataFrame) -> "LevelSignificanceAnalyzer":
        logger.info("%s: scanning %d bars for significant levels...", self.symbol, len(df))

        swings = self._find_swing_points(df)
        level_prices = self._cluster(swings)
        logger.info(
            "%s: %d swings → %d clustered levels",
            self.symbol,
            len(swings),
            len(level_prices),
        )

        all_levels: list[SignificantLevel] = []
        for level_price in level_prices:
            touches = self._analyze_touches(df, level_price)
            if len(touches) < self.min_hits:
                continue

            support_hits = sum(
                1
                for t in touches
                if t.role_at_touch == LevelRole.SUPPORT and t.outcome == "hold"
            )
            resistance_hits = sum(
                1
                for t in touches
                if t.role_at_touch == LevelRole.RESISTANCE and t.outcome == "hold"
            )
            support_breaks = sum(
                1
                for t in touches
                if t.role_at_touch == LevelRole.SUPPORT and t.outcome == "break"
            )
            resistance_breaks = sum(
                1
                for t in touches
                if t.role_at_touch == LevelRole.RESISTANCE and t.outcome == "break"
            )
            hold_count = sum(1 for t in touches if t.outcome == "hold")
            break_count = sum(1 for t in touches if t.outcome == "break")

            all_levels.append(
                SignificantLevel(
                    price=round(level_price, 5),
                    rank=0,
                    total_hits=len(touches),
                    support_hits=support_hits,
                    resistance_hits=resistance_hits,
                    support_breaks=support_breaks,
                    resistance_breaks=resistance_breaks,
                    hold_count=hold_count,
                    break_count=break_count,
                    hold_rate=round(hold_count / len(touches), 4),
                    touches=touches,
                )
            )

        self.levels = sorted(all_levels, key=lambda level: level.total_hits, reverse=True)
        for i, level in enumerate(self.levels):
            level.rank = i + 1

        self.top5 = self.levels[:5]
        self._is_fitted = True

        if self.levels:
            logger.info(
                "%s: found %d significant levels | top level: %.5f "
                "(%d hits, %s, hold=%.1f%%)",
                self.symbol,
                len(self.levels),
                self.levels[0].price,
                self.levels[0].total_hits,
                self.levels[0].role,
                self.levels[0].hold_rate * 100,
            )
        else:
            logger.info("%s: no significant levels found", self.symbol)

        return self

    def print_top5(self) -> None:
        if not self._is_fitted:
            print("Not fitted — call .fit(df) first")
            return

        print(f"\n{'═' * 80}")
        print(f"  {self.symbol} — Top 5 Most-Hit Price Levels")
        print(f"{'═' * 80}")
        print(
            f"  {'#':<3} {'Price':>10} {'Hits':>6} {'Role':<12} "
            f"{'Hold%':>7} {'Avg Vol':>8} {'Volume Impact'}"
        )
        print(f"  {'─' * 75}")

        for level in self.top5:
            print(
                f"  {level.rank:<3} "
                f"{level.price:>10.5f} "
                f"{level.total_hits:>6} "
                f"{level.role:<12} "
                f"{level.hold_rate * 100:>6.1f}% "
                f"{level.avg_volume_ratio:>7.2f}x "
                f"{level.volume_impact}"
            )
        print(f"\n{'═' * 80}\n")

    def get_features(self, price: float) -> dict:
        if not self._is_fitted or not self.levels:
            return self._empty_features()

        tol = self.touch_tolerance_pct / 100.0
        distances = [abs(level.price - price) / (price + 1e-10) for level in self.levels]
        nearest_idx = int(np.argmin(distances))
        nearest = self.levels[nearest_idx]
        nearest_dist = distances[nearest_idx]
        at_level = nearest_dist <= tol

        top5_prices = [level.price for level in self.top5]
        top5_dists = [abs(price - p) / (price + 1e-10) for p in top5_prices]
        at_top5 = any(d <= tol for d in top5_dists)
        top5_rank = next((i + 1 for i, d in enumerate(top5_dists) if d <= tol), 0)

        vi_map = {
            VolumeImpact.HIGH_VOL_REVERSAL: 1.0,
            VolumeImpact.HIGH_VOL_BREAKOUT: -1.0,
            VolumeImpact.LOW_VOL_WEAK_HOLD: 0.3,
            VolumeImpact.LOW_VOL_DRIFT: -0.3,
            VolumeImpact.NEUTRAL: 0.0,
        }
        role_map = {
            LevelRole.SUPPORT: 1.0,
            LevelRole.RESISTANCE: -1.0,
            LevelRole.MIXED: 0.0,
            LevelRole.FLIP: 0.5,
        }

        return {
            "sig_nearest_hits": nearest.total_hits,
            "sig_nearest_hold_rate": nearest.hold_rate,
            "sig_nearest_role": role_map.get(nearest.role, 0.0),
            "sig_nearest_dist_pct": round(nearest_dist * 100, 4),
            "sig_nearest_rank": nearest.rank,
            "sig_at_level": int(at_level),
            "sig_vol_impact": vi_map.get(nearest.volume_impact, 0.0),
            "sig_high_vol_hold_rate": nearest.high_vol_hold_rate,
            "sig_low_vol_hold_rate": nearest.low_vol_hold_rate,
            "sig_avg_vol_ratio": nearest.avg_volume_ratio,
            "sig_is_support": int(nearest.role == LevelRole.SUPPORT),
            "sig_is_resistance": int(nearest.role == LevelRole.RESISTANCE),
            "sig_is_mixed": int(nearest.role == LevelRole.MIXED),
            "sig_role_confidence": nearest.role_confidence,
            "sig_at_top5_level": int(at_top5),
            "sig_top5_rank": top5_rank,
            "sig_dist_to_top1": round(
                abs(price - self.top5[0].price) / (price + 1e-10) * 100, 4
            )
            if self.top5
            else 5.0,
        }

    def get_features_series(self, df: pd.DataFrame) -> pd.DataFrame:
        if not self._is_fitted:
            raise RuntimeError("Call .fit(df) first")
        rows = [self.get_features(float(price)) for price in df["close"]]
        return pd.DataFrame(rows, index=df.index)

    def _empty_features(self) -> dict:
        return {
            "sig_nearest_hits": 0,
            "sig_nearest_hold_rate": 0.0,
            "sig_nearest_role": 0.0,
            "sig_nearest_dist_pct": 5.0,
            "sig_nearest_rank": 999,
            "sig_at_level": 0,
            "sig_vol_impact": 0.0,
            "sig_high_vol_hold_rate": 0.0,
            "sig_low_vol_hold_rate": 0.0,
            "sig_avg_vol_ratio": 1.0,
            "sig_is_support": 0,
            "sig_is_resistance": 0,
            "sig_is_mixed": 0,
            "sig_role_confidence": 0.0,
            "sig_at_top5_level": 0,
            "sig_top5_rank": 0,
            "sig_dist_to_top1": 5.0,
        }

    def save(self, path: str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "symbol": self.symbol,
            "asset_class": self.asset_class,
            "n_levels": len(self.levels),
            "top5": [level.to_dict() for level in self.top5],
            "all_levels": [level.to_dict() for level in self.levels[:50]],
        }
        p.write_text(json.dumps(data, indent=2))
        logger.info("%s: saved %d levels to %s", self.symbol, len(self.levels), path)

    def load(self, path: str) -> "LevelSignificanceAnalyzer":
        data = json.loads(Path(path).read_text())
        self.symbol = data.get("symbol", self.symbol)
        self.asset_class = data.get("asset_class", self.asset_class)
        self.levels = [SignificantLevel.from_dict(level) for level in data.get("all_levels", [])]
        self.top5 = self.levels[:5]
        self._is_fitted = True
        return self


def analyze_symbol(
    symbol: str,
    asset_class: str,
    df: pd.DataFrame,
    print_report: bool = True,
) -> LevelSignificanceAnalyzer:
    analyzer = LevelSignificanceAnalyzer(symbol, asset_class)
    analyzer.fit(df)
    if print_report:
        analyzer.print_top5()
    return analyzer
