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
    "news_direction_alignment",
    "news_risk_penalty",
    "news_event_count_2h",
    "news_high_impact_count",
    "news_size_reduction",
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
