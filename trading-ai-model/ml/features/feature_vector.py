"""Extract numeric feature vector from fused feature dict for ML."""

from __future__ import annotations

FEATURE_KEYS = [
    "signal_rank",
    "near_666_level",
    "near_618_fib",
    "bullish_rejection_candle",
    "fractal_down_confirmed",
    "gann_angle_support",
    "markov_continuation_probability",
    "volume_shift_score",
    "momentum_score",
    "acceleration_score",
    "strategy_ev",
    "risk_of_ruin",
    "candlestick_wick_rejection_score",
    "candlestick_body_to_range_ratio",
    "harmonic_pattern_completion_score",
    "elliott_wave_wave_3_probability",
    "level_369_reversal_zone_active",
    "monte_carlo_prob_positive_path",
    "news_sentiment_score",
    "news_impact_score",
    "news_urgency_score",
    "volatility_risk_score",
    "news_conflict_score",
    "minutes_since_last_news",
    "minutes_until_next_event",
    "high_impact_news_active",
    "breaking_news_active",
    "affected_symbol_match",
    "trading_blocked",
    "reduce_size_recommended",
]


def _to_float(value) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def extract_vector(features: dict) -> list[float]:
    return [_to_float(features.get(k, 0)) for k in FEATURE_KEYS]


def feature_names() -> list[str]:
    return list(FEATURE_KEYS)
