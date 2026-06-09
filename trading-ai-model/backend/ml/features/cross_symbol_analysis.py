"""
ml/features/cross_symbol_analysis.py

Finds what makes a price level universally strong across ALL symbols.

The core insight:
  A level characteristic that predicts reversals on EURUSD AND BTCUSD
  AND TSLA AND MES is a universal market truth, not a symbol quirk.
  That universal truth gets a much higher weight in the model.

What this module does:
  1. After fitting LevelHistoryTracker for each symbol, collect all
     level statistics across all symbols into one dataset
  2. Find which level characteristics (hold rate threshold, touch count,
     strength score, zone width) consistently predict reversals
     across all symbols independently
  3. Build a UniversalLevelProfile — the fingerprint of a strong level
  4. Score any new level against this profile → universal_strength_score
  5. Add this score as a feature to every symbol's training dataset

Additionally for correlated symbol pairs (ES/MES, NQ/MNQ, EURUSD/GBPUSD):
  6. Check if a level in one symbol aligns with a level in its correlated
     pair (same % distance from a key reference price)
  7. Levels that hold in BOTH correlated symbols get a bonus multiplier

Usage:
  # After fitting all trackers:
  analyzer = CrossSymbolAnalyzer()
  analyzer.fit(trackers_dict)        # trackers_dict = {symbol: tracker}
  analyzer.save("models/cross_symbol_profile.json")

  # During feature computation for one symbol:
  features = analyzer.get_features(
      symbol="MES",
      hold_rate=0.72,
      touch_count=12,
      strength=0.68,
      current_price=5200.0,
      all_trackers=trackers_dict,
  )
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("cross_symbol")


def _percentile(values, q: float) -> float:
    """Coerce Series/array inputs to float64 ndarray for np.percentile typing."""
    arr = np.asarray(values, dtype=np.float64)
    return float(np.percentile(arr, q))


# ─── Correlated symbol pairs ──────────────────────────────────────────────────
# These pairs track the same or highly correlated underlying.
# A level that holds in both has much higher conviction.

CORRELATED_PAIRS = [
    ("ES", "MES"),  # E-mini vs Micro E-mini S&P 500 — same underlying
    ("NQ", "MNQ"),  # E-mini vs Micro Nasdaq — same underlying
    ("RTY", "MES"),  # Russell / S&P correlation
    ("EURUSD", "GBPUSD"),  # Both USD pairs, correlated
    ("EURUSD", "AUDUSD"),  # Risk-on currency correlation
    ("BTCUSD", "ETHUSD"),  # Crypto correlation
    ("BTCUSD", "SOLUSD"),  # Crypto correlation
    ("TSLA", "NVDA"),  # High-beta tech correlation
    ("AAPL", "MSFT"),  # Large-cap tech correlation
    ("GC", "EURUSD"),  # Gold / EUR correlation (anti-dollar)
    ("CL", "BTCUSD"),  # Risk asset correlation
]

# Asset class groups — levels that hold across an entire asset class
# are stronger than those that hold in only one symbol
ASSET_CLASS_GROUPS = {
    "futures": ["MES", "ES", "MNQ", "NQ", "CL", "GC", "ZB", "RTY"],
    "forex": ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD"],
    "crypto": ["BTCUSD", "ETHUSD", "SOLUSD", "BNBUSD", "XRPUSD"],
    "equity": ["TSLA", "NVDA", "AAPL", "MSFT", "AMZN"],
}


# ─── Universal level profile ──────────────────────────────────────────────────


@dataclass
class UniversalLevelProfile:
    """
    The statistical fingerprint of what makes a level universally strong.
    Derived from analyzing level statistics across all symbols.

    Think of it as: "across all 23 symbols, what combination of
    touch_count + hold_rate + strength_score best predicts reversal?"
    """

    # Thresholds that consistently separate strong from weak levels
    strong_hold_rate_threshold: float = 0.65  # above this = strong
    weak_hold_rate_threshold: float = 0.45  # below this = weak
    strong_touch_threshold: int = 8  # above this = statistically reliable
    strong_strength_threshold: float = 0.60  # Wilson score above this = strong
    min_reliable_touches: int = 5  # minimum to trust the ratio

    # Distribution statistics across all symbols
    mean_hold_rate: float = 0.0
    std_hold_rate: float = 0.0
    mean_touch_count: float = 0.0
    mean_strength: float = 0.0
    percentile_75_hold_rate: float = 0.0
    percentile_90_hold_rate: float = 0.0
    percentile_75_touch_count: float = 0.0

    # Per asset class average hold rates
    asset_class_hold_rates: dict = field(default_factory=dict)

    # Number of symbols analyzed
    n_symbols: int = 0
    n_levels_analyzed: int = 0

    def universal_strength_score(
        self,
        hold_rate: float,
        touch_count: int,
        strength: float,
    ) -> float:
        """
        Score a level 0-1 based on how well it matches the
        universal profile of strong levels across all symbols.

        0.0 = weak level by any measure
        1.0 = matches all characteristics of universally strong levels
        """
        if touch_count < self.min_reliable_touches:
            return 0.0

        score = 0.0
        weight_total = 0.0

        # Hold rate component (weight: 40%)
        w = 0.40
        if self.std_hold_rate > 0:
            # How many std devs above the mean?
            z = (hold_rate - self.mean_hold_rate) / (self.std_hold_rate + 1e-10)
            hr_score = min(1.0, max(0.0, (z + 2) / 4))  # map [-2, +2] to [0, 1]
        else:
            hr_score = hold_rate
        score += w * hr_score
        weight_total += w

        # Wilson strength score component (weight: 35%)
        w = 0.35
        if self.mean_strength > 0:
            str_score = min(1.0, strength / (self.percentile_75_hold_rate + 1e-10))
        else:
            str_score = strength
        score += w * str_score
        weight_total += w

        # Touch count component (weight: 25%)
        w = 0.25
        tc_score = min(1.0, touch_count / (self.strong_touch_threshold * 1.5))
        score += w * tc_score
        weight_total += w

        return round(score / (weight_total + 1e-10), 4)

    def classify_level(self, hold_rate: float, touch_count: int) -> str:
        """Quick classification for logging/debugging."""
        if touch_count < self.min_reliable_touches:
            return "unproven"
        if (
            hold_rate >= self.strong_hold_rate_threshold
            and touch_count >= self.strong_touch_threshold
        ):
            return "strong"
        if hold_rate >= self.percentile_75_hold_rate:
            return "good"
        if hold_rate <= self.weak_hold_rate_threshold:
            return "weak"
        return "average"

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}

    @classmethod
    def from_dict(cls, d: dict) -> "UniversalLevelProfile":
        p = cls()
        for k, v in d.items():
            if hasattr(p, k):
                setattr(p, k, v)
        return p


# ─── Main analyzer ────────────────────────────────────────────────────────────


class CrossSymbolAnalyzer:
    """
    Analyzes level statistics across all symbols to find
    universal characteristics of strong reversal levels.
    """

    def __init__(self):
        self.profile: Optional[UniversalLevelProfile] = None
        self.per_symbol: dict = {}  # symbol → level stats summary
        self._is_fitted = False

    def fit(self, trackers: dict) -> "CrossSymbolAnalyzer":
        """
        Analyze level statistics across all fitted LevelHistoryTrackers.

        Args:
            trackers: dict of {symbol: LevelHistoryTracker}
        """
        logger.info(
            "CrossSymbolAnalyzer: analyzing %d symbols...", len(trackers)
        )

        all_levels = []
        per_symbol_stats = {}

        for symbol, tracker in trackers.items():
            if not tracker._is_fitted or not tracker.levels:
                logger.warning(
                    "%s: no levels — skipping from cross-symbol analysis", symbol
                )
                continue

            symbol_levels = []
            for lvl in tracker.levels:
                if lvl.touch_count < 3:
                    continue
                symbol_levels.append(
                    {
                        "symbol": symbol,
                        "asset_class": tracker.asset_class,
                        "price": lvl.price,
                        "touch_count": lvl.touch_count,
                        "hold_count": lvl.hold_count,
                        "break_count": lvl.break_count,
                        "hold_rate": lvl.hold_rate,
                        "strength": lvl.strength_score,
                    }
                )
                all_levels.append(symbol_levels[-1])

            if symbol_levels:
                hold_rates = [l["hold_rate"] for l in symbol_levels]
                per_symbol_stats[symbol] = {
                    "n_levels": len(symbol_levels),
                    "mean_hold_rate": float(np.mean(hold_rates)),
                    "median_hold_rate": float(np.median(hold_rates)),
                    "p75_hold_rate": _percentile(hold_rates, 75),
                    "p90_hold_rate": _percentile(hold_rates, 90),
                    "max_touch_count": max(l["touch_count"] for l in symbol_levels),
                    "best_level_price": tracker.levels[0].price,
                    "best_level_rate": tracker.levels[0].hold_rate,
                    "best_level_touches": tracker.levels[0].touch_count,
                }

        self.per_symbol = per_symbol_stats

        if not all_levels:
            logger.warning("CrossSymbolAnalyzer: no levels to analyze")
            self.profile = UniversalLevelProfile()
            self._is_fitted = True
            return self

        df = pd.DataFrame(all_levels)

        # ── Build universal profile ────────────────────────────────────────────
        hold_rate_s = df["hold_rate"]
        strength_s = df["strength"]
        touch_s = df["touch_count"]

        # Find thresholds that consistently separate strong from weak
        # A level is "objectively strong" if its hold_rate is in top quartile
        # within its own symbol (controls for symbol-specific base rates)
        strong_threshold = self._find_universal_threshold(df)
        weak_threshold = float(hold_rate_s.quantile(0.25))

        # Asset class average hold rates
        asset_class_rates = {}
        for ac in ["futures", "forex", "crypto", "equity"]:
            ac_df = df[df["asset_class"] == ac]
            if len(ac_df) > 0:
                asset_class_rates[ac] = round(float(ac_df["hold_rate"].mean()), 4)

        self.profile = UniversalLevelProfile(
            strong_hold_rate_threshold=round(strong_threshold, 4),
            weak_hold_rate_threshold=round(weak_threshold, 4),
            strong_touch_threshold=int(touch_s.quantile(0.75)),
            strong_strength_threshold=round(float(strength_s.quantile(0.75)), 4),
            min_reliable_touches=5,
            mean_hold_rate=round(float(hold_rate_s.mean()), 4),
            std_hold_rate=round(float(hold_rate_s.std()), 4),
            mean_touch_count=round(float(touch_s.mean()), 2),
            mean_strength=round(float(strength_s.mean()), 4),
            percentile_75_hold_rate=round(float(hold_rate_s.quantile(0.75)), 4),
            percentile_90_hold_rate=round(float(hold_rate_s.quantile(0.90)), 4),
            percentile_75_touch_count=round(float(touch_s.quantile(0.75)), 2),
            asset_class_hold_rates=asset_class_rates,
            n_symbols=len(trackers),
            n_levels_analyzed=len(all_levels),
        )

        self._is_fitted = True

        logger.info(
            "CrossSymbolAnalyzer: analyzed %d levels across %d symbols | "
            "mean hold rate=%.1f%% | strong threshold=%.1f%% | "
            "strong touch threshold=%d",
            len(all_levels),
            len(trackers),
            self.profile.mean_hold_rate * 100,
            self.profile.strong_hold_rate_threshold * 100,
            self.profile.strong_touch_threshold,
        )

        self._log_commonalities(df)
        return self

    def _find_universal_threshold(self, df: pd.DataFrame) -> float:
        """
        Find the hold_rate threshold that separates strong from average levels
        consistently across all symbols.

        Method: for each symbol find its 75th percentile hold_rate.
        The universal threshold is the median of those per-symbol thresholds.
        """
        per_symbol_p75 = []
        for symbol in df["symbol"].unique():
            sym_rates = df.loc[df["symbol"] == symbol, "hold_rate"]
            if len(sym_rates) >= 4:
                per_symbol_p75.append(float(sym_rates.quantile(0.75)))

        if not per_symbol_p75:
            return 0.65

        return float(np.median(per_symbol_p75))

    def _log_commonalities(self, df: pd.DataFrame) -> None:
        """Log the key findings — what's universally true about strong levels."""
        if self.profile is None:
            return

        logger.info("\n" + "=" * 65)
        logger.info("  CROSS-SYMBOL LEVEL ANALYSIS — Universal Findings")
        logger.info("=" * 65)

        # What % of symbols have levels above the strong threshold?
        strong_count = (
            df["hold_rate"] >= self.profile.strong_hold_rate_threshold
        ).sum()
        logger.info(
            "  Levels above strong threshold (%.0f%%): %d / %d (%.1f%%)",
            self.profile.strong_hold_rate_threshold * 100,
            strong_count,
            len(df),
            strong_count / len(df) * 100,
        )

        # Per asset class
        logger.info("  Hold rate by asset class:")
        for ac, rate in self.profile.asset_class_hold_rates.items():
            logger.info("    %-10s %.1f%%", ac, rate * 100)

        # Best performing symbols
        logger.info("  Best symbols (median level hold rate):")
        sym_medians = sorted(
            df.groupby("symbol")["hold_rate"].median().items(),
            key=lambda item: item[1],
            reverse=True,
        )
        for sym, rate in sym_medians[:5]:
            logger.info("    %-10s %.1f%%", sym, rate * 100)

        logger.info("=" * 65)

    def get_features(
        self,
        symbol: str,
        hold_rate: float,
        touch_count: int,
        strength: float,
        current_price: float | None = None,
        all_trackers: dict | None = None,
    ) -> dict:
        """
        Combined cross-symbol features for training or inference.

        Merges universal profile scoring with correlated-pair confirmation
        when current_price and all_trackers are provided.
        """
        features = self.get_cross_symbol_features(
            symbol, hold_rate, touch_count, strength
        )
        if current_price is not None and all_trackers:
            features.update(
                self.get_correlated_pair_feature(symbol, current_price, all_trackers)
            )
        else:
            for key in (
                "cx_correlated_confirmation",
                "cx_correlated_strength",
                "cx_has_confirmation",
            ):
                features.setdefault(key, 0 if key != "cx_correlated_strength" else 0.0)
        return features

    def get_cross_symbol_features(
        self,
        symbol: str,
        hold_rate: float,
        touch_count: int,
        strength: float,
    ) -> dict:
        """
        Get cross-symbol features for a level at the current price.

        These features answer: "how does this level compare to what
        we've seen across ALL symbols historically?"
        """
        if not self._is_fitted or self.profile is None:
            return self._empty_features()

        p = self.profile
        asset_class = self._get_asset_class(symbol)
        ac_mean_rate = p.asset_class_hold_rates.get(asset_class, p.mean_hold_rate)

        # Universal strength score — how strong is this level vs all levels
        # across all 23 symbols
        universal_score = p.universal_strength_score(hold_rate, touch_count, strength)

        # How does this level compare to the asset class average?
        vs_asset_class = hold_rate - ac_mean_rate

        # Is this level statistically in the top quartile universally?
        is_universally_strong = int(
            hold_rate >= p.strong_hold_rate_threshold
            and touch_count >= p.strong_touch_threshold
        )

        # Is this level statistically weak?
        is_universally_weak = int(
            touch_count >= p.min_reliable_touches
            and hold_rate <= p.weak_hold_rate_threshold
        )

        # Z-score of this level's hold rate vs the global distribution
        z_score = (hold_rate - p.mean_hold_rate) / (p.std_hold_rate + 1e-10)

        # Percentile rank
        percentile = self._hold_rate_percentile(hold_rate)

        return {
            "cx_universal_score": universal_score,
            "cx_vs_asset_class_mean": round(float(vs_asset_class), 4),
            "cx_is_universally_strong": is_universally_strong,
            "cx_is_universally_weak": is_universally_weak,
            "cx_hold_rate_zscore": round(float(np.clip(z_score, -3, 3)), 4),
            "cx_hold_rate_percentile": round(float(percentile), 4),
            "cx_above_strong_threshold": int(
                hold_rate >= p.strong_hold_rate_threshold
            ),
            "cx_touch_count_percentile": round(
                float(min(1.0, touch_count / (p.percentile_75_touch_count + 1))),
                4,
            ),
        }

    def get_correlated_pair_feature(
        self,
        symbol: str,
        current_price: float,
        all_trackers: dict,
    ) -> dict:
        """
        Check if the current price zone is also a significant level
        in correlated symbols.

        A level that holds in BOTH EURUSD and GBPUSD, or both ES and MES,
        has much higher conviction — two independent confirmations.
        """
        if not self._is_fitted:
            return {
                "cx_correlated_confirmation": 0,
                "cx_correlated_strength": 0.0,
                "cx_has_confirmation": 0,
            }

        correlated_symbols = self._get_correlated_symbols(symbol)
        confirmations = 0
        best_corr_strength = 0.0

        for corr_sym in correlated_symbols:
            tracker = all_trackers.get(corr_sym)
            if not tracker or not tracker._is_fitted or not tracker.levels:
                continue

            # For correlated pairs, compare percentage distance from
            # their respective reference prices rather than absolute price
            # (ES trades at ~5000, MES also at ~5000 — same price)
            # (EURUSD at 1.08, GBPUSD at 1.26 — different prices, look at % structure)

            # Find the nearest significant level in the correlated symbol
            tol_pct = 0.005  # 0.5% tolerance for cross-symbol level alignment
            corr_levels = [
                lvl
                for lvl in tracker.levels
                if (abs(lvl.price - current_price) / (current_price + 1e-10))
                <= tol_pct
                and lvl.touch_count >= 5
            ]

            if corr_levels:
                best = max(corr_levels, key=lambda x: x.strength_score)
                confirmations += 1
                best_corr_strength = max(best_corr_strength, best.strength_score)

        return {
            "cx_correlated_confirmation": confirmations,
            "cx_correlated_strength": round(best_corr_strength, 4),
            "cx_has_confirmation": int(confirmations > 0),
        }

    def _hold_rate_percentile(self, hold_rate: float) -> float:
        """Approximate percentile of this hold_rate in global distribution."""
        if self.profile is None:
            return 0.5
        p = self.profile
        # Linear interpolation between known percentiles
        if hold_rate <= p.weak_hold_rate_threshold:
            return 0.25 * hold_rate / (p.weak_hold_rate_threshold + 1e-10)
        if hold_rate <= p.mean_hold_rate:
            t = (hold_rate - p.weak_hold_rate_threshold) / (
                p.mean_hold_rate - p.weak_hold_rate_threshold + 1e-10
            )
            return 0.25 + 0.25 * t
        if hold_rate <= p.percentile_75_hold_rate:
            t = (hold_rate - p.mean_hold_rate) / (
                p.percentile_75_hold_rate - p.mean_hold_rate + 1e-10
            )
            return 0.50 + 0.25 * t
        if hold_rate <= p.percentile_90_hold_rate:
            t = (hold_rate - p.percentile_75_hold_rate) / (
                p.percentile_90_hold_rate - p.percentile_75_hold_rate + 1e-10
            )
            return 0.75 + 0.15 * t
        return min(
            1.0,
            0.90
            + 0.10
            * (
                (hold_rate - p.percentile_90_hold_rate)
                / (1.0 - p.percentile_90_hold_rate + 1e-10)
            ),
        )

    def _get_correlated_symbols(self, symbol: str) -> list:
        corr = []
        for a, b in CORRELATED_PAIRS:
            if symbol == a:
                corr.append(b)
            elif symbol == b:
                corr.append(a)
        return corr

    def _get_asset_class(self, symbol: str) -> str:
        for ac, syms in ASSET_CLASS_GROUPS.items():
            if symbol in syms:
                return ac
        return "equity"

    def _empty_features(self) -> dict:
        return {
            "cx_universal_score": 0.0,
            "cx_vs_asset_class_mean": 0.0,
            "cx_is_universally_strong": 0,
            "cx_is_universally_weak": 0,
            "cx_hold_rate_zscore": 0.0,
            "cx_hold_rate_percentile": 0.5,
            "cx_above_strong_threshold": 0,
            "cx_touch_count_percentile": 0.0,
            "cx_correlated_confirmation": 0,
            "cx_correlated_strength": 0.0,
            "cx_has_confirmation": 0,
        }

    def print_summary(self) -> None:
        """Print a human-readable summary of cross-symbol findings."""
        if not self._is_fitted or self.profile is None:
            print("Not fitted yet")
            return

        p = self.profile
        print(f"\n{'=' * 70}")
        print(
            f"  Cross-Symbol Level Analysis — {p.n_symbols} symbols, "
            f"{p.n_levels_analyzed:,} levels"
        )
        print(f"{'=' * 70}")
        print(f"  Global mean hold rate:      {p.mean_hold_rate * 100:.1f}%")
        print(f"  Global std hold rate:       {p.std_hold_rate * 100:.1f}%")
        print(f"  Strong level threshold:     {p.strong_hold_rate_threshold * 100:.1f}%")
        print(f"  Weak level threshold:       {p.weak_hold_rate_threshold * 100:.1f}%")
        print(f"  Strong touch threshold:     {p.strong_touch_threshold} touches")
        print(f"  75th pct hold rate:         {p.percentile_75_hold_rate * 100:.1f}%")
        print(f"  90th pct hold rate:         {p.percentile_90_hold_rate * 100:.1f}%")
        print("\n  Hold rate by asset class:")
        for ac, rate in p.asset_class_hold_rates.items():
            print(f"    {ac:<12} {rate * 100:.1f}%")
        print("\n  Per symbol summary:")
        for sym, stats in sorted(
            self.per_symbol.items(),
            key=lambda x: x[1]["median_hold_rate"],
            reverse=True,
        ):
            print(
                f"    {sym:<10} levels={stats['n_levels']:>4} | "
                f"median hold={stats['median_hold_rate'] * 100:.1f}% | "
                f"best: {stats['best_level_price']:.5f} "
                f"({stats['best_level_rate'] * 100:.1f}% hold, "
                f"{stats['best_level_touches']} touches)"
            )
        print(f"{'=' * 70}\n")

    def save(self, path: str) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "profile": self.profile.to_dict() if self.profile else {},
            "per_symbol": self.per_symbol,
        }
        p.write_text(json.dumps(data, indent=2))
        logger.info("CrossSymbolAnalyzer saved to %s", path)

    def load(self, path: str) -> "CrossSymbolAnalyzer":
        data = json.loads(Path(path).read_text())
        self.profile = UniversalLevelProfile.from_dict(data["profile"])
        self.per_symbol = data.get("per_symbol", {})
        self._is_fitted = True
        logger.info("CrossSymbolAnalyzer loaded from %s", path)
        return self
