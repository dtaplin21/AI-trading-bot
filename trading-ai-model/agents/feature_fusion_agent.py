"""Feature Fusion Agent — combines method outputs + news features."""

from datetime import datetime, timezone

from agents.base import BaseAgent
from agents.news_runtime import get_news_agent
from agents.pipeline_context import PipelineContext
from agents.schemas import FusedFeatures
from pipeline.feature_fusion_news_patch import (
    apply_news_signal_rank_penalty,
    fetch_news_features,
    inject_news_into_features_dict,
    resolve_technical_direction,
)
from signal_engine.layer_scores import LayerScores
from signal_engine.signal_rank_service import SignalRankService


class FeatureFusionAgent(BaseAgent):
    """
    Merges all method agent outputs + news features into FusedFeatures.
    News agent is injected at construction — no global state required.
    """

    name = "feature_fusion"

    def __init__(self, news_agent=None) -> None:
        self.rank_service = SignalRankService()
        self._news = news_agent

    @property
    def news(self):
        return self._news or get_news_agent()

    def run(self, ctx: PipelineContext) -> PipelineContext:
        features: dict = {
            "symbol": ctx.symbol,
            "timeframe": ctx.timeframe,
        }

        scores = LayerScores()
        for output in ctx.method_outputs:
            if output.skipped:
                continue
            features.update({f"{output.method}_{k}": v for k, v in output.features.items()})
            self._apply_to_scores(output, scores)

        if ctx.chart:
            features["trend_direction"] = ctx.chart.trend_direction
            features["vwap_relation"] = ctx.chart.vwap_relation
            features["higher_highs"] = ctx.chart.higher_highs
            features["higher_lows"] = ctx.chart.higher_lows

        tech_dir = resolve_technical_direction(ctx)
        news_features = fetch_news_features(self.news, ctx.symbol, tech_dir, ctx.timestamp)
        ctx.metadata["news_features"] = news_features.model_dump()
        inject_news_into_features_dict(features, news_features)

        features["near_666_level"] = features.get("ancient_number_number_zone") == "66.6%"
        features["near_618_fib"] = features.get("fibonacci_spiral_near_618_fib", False)
        features["bullish_rejection_candle"] = features.get("candlestick_bullish_rejection_candle", False)
        features["fractal_down_confirmed"] = features.get("fractal_fractal_down_confirmed", False)
        features["gann_angle_support"] = features.get("gann_gann_angle_support", False)
        features["markov_continuation_probability"] = features.get(
            "markov_state_markov_continuation_probability", 0.0
        )
        features["volume_shift_score"] = features.get("momentum_volume_shift_score", 0.0)
        features["momentum_score"] = features.get("momentum_momentum_score", 0.0)
        features["acceleration_score"] = features.get("momentum_acceleration_score", 0.0)
        features["strategy_ev"] = features.get("strategy_math_strategy_ev", 0.0)
        features["risk_of_ruin"] = features.get("strategy_math_risk_of_ruin", 0.0)

        signal_rank = self.rank_service.compute_rank(scores)
        signal_rank = apply_news_signal_rank_penalty(signal_rank, news_features)
        features["signal_rank"] = signal_rank

        skipped = sum(1 for o in ctx.method_outputs if o.skipped)
        ctx.fused = FusedFeatures.with_news(
            news_features,
            symbol=ctx.symbol,
            timeframe=ctx.timeframe,
            timestamp=ctx.timestamp or datetime.now(timezone.utc),
            method_outputs=ctx.method_outputs,
            features=features,
            signal_rank=signal_rank,
            methods_run=len(ctx.method_outputs) - skipped,
            methods_skipped=skipped,
        )
        return ctx

    def _apply_to_scores(self, output, scores: LayerScores) -> None:
        f = output.features
        m = output.method
        if m == "candlestick":
            scores.candlestick = output.confidence
        elif m == "harmonic":
            scores.harmonic = output.confidence
        elif m == "elliott_wave":
            if f.get("can_influence_signal_rank"):
                scores.elliott = output.confidence
        elif m == "fibonacci_spiral":
            scores.fibonacci = output.confidence
        elif m in ("level_369", "ancient_number"):
            scores.number_zone = max(scores.number_zone, output.confidence)
        elif m == "fractal":
            scores.fractal = output.confidence
        elif m == "markov_state":
            scores.markov = output.confidence
        elif m == "strategy_math":
            scores.ev = min(1.0, max(0.0, f.get("strategy_ev", 0) / 20))
        elif m == "gann":
            scores.gann_modifier = f.get("signal_rank_modifier", 0.0)
