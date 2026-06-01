"""Prediction Agent — LightGBM/XGBoost path with rule-based fallback."""

from agents.base import BaseAgent
from agents.pipeline_context import PipelineContext
from agents.schemas import PredictionOutput
from ml.models.signal_classifier import SignalClassifier


class PredictionAgent(BaseAgent):
    name = "prediction"

    def __init__(self):
        from ml.models.lightgbm_classifier import LightGBMSignalClassifier

        self.classifier = SignalClassifier()
        LightGBMSignalClassifier.get_singleton()

    def reload_classifier(self) -> None:
        """Reload production model after RetrainPipeline promotion."""
        self.classifier.reload()
        self.min_confidence = 0.55
        self.min_signal_rank = 65

    def run(self, ctx: PipelineContext) -> PipelineContext:
        if not ctx.fused:
            ctx.prediction = PredictionOutput(should_wait=True, should_avoid=True)
            return ctx

        if ctx.confluence and not ctx.confluence.ready_for_prediction:
            ctx.prediction = PredictionOutput(
                should_start=False,
                should_stop=False,
                should_wait=True,
                should_avoid=ctx.confluence.news_trading_blocked,
                model_confidence=ctx.confluence.confluence_score,
                model_version="confluence_gate",
            )
            return ctx

        f = ctx.fused.features
        rank = ctx.fused.signal_rank
        ev = float(f.get("strategy_ev", 0))
        ror = float(f.get("risk_of_ruin", 1))
        cont_prob = float(f.get("markov_continuation_probability", 0.5))

        ml_out = self.classifier.predict(f)
        ml_conf = float(ml_out.get("signal_probability", 0.5))
        model_version = ml_out.get("model_version", "rule_fallback")

        bullish_signals = sum(
            [
                bool(f.get("bullish_rejection_candle")),
                bool(f.get("near_618_fib")),
                bool(f.get("fractal_down_confirmed")),
                cont_prob > 0.55,
            ]
        )

        model_confidence = (ml_conf + rank / 100 + bullish_signals / 4) / 3
        should_start = rank >= self.min_signal_rank and ev > 0 and ror < 0.05 and model_confidence >= self.min_confidence
        should_avoid = (
            rank < 50
            or ror > 0.1
            or f.get("momentum_momentum_score", 0.5) < 0.2
            or bool(f.get("news_trading_blocked") or f.get("trading_blocked"))
            or (ctx.confluence and ctx.confluence.conflict_score > 0.45)
        )

        ctx.prediction = PredictionOutput(
            should_start=should_start,
            should_stop=False,
            should_wait=not should_start and not should_avoid,
            should_avoid=should_avoid,
            target_before_stop_probability=min(1.0, cont_prob + rank / 200),
            reversal_probability=float(f.get("candlestick_wick_rejection_score", 0.3)),
            continuation_probability=cont_prob,
            expected_value=ev,
            expected_drawdown=ror * 100,
            model_confidence=model_confidence,
            model_version=model_version,
        )
        return ctx
