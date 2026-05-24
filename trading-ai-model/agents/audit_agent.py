"""Audit / Explainability Agent — human-readable signal reasoning."""

from agents.base import BaseAgent
from agents.pipeline_context import PipelineContext
from agents.schemas import AuditReport


class AuditAgent(BaseAgent):
    name = "audit"

    def run(self, ctx: PipelineContext) -> PipelineContext:
        reasons: list[str] = []
        agreement: dict[str, bool] = {}
        disagreements: list[str] = []

        f = ctx.fused.features if ctx.fused else {}

        if f.get("near_666_level") or f.get("ancient_number_number_zone") == "66.6%":
            reasons.append("price reached the 66.6% 3-6-9 level")
        if f.get("near_618_fib"):
            reasons.append("price was within range of 61.8% Fibonacci")
        if f.get("bullish_rejection_candle"):
            reasons.append("bullish rejection candle formed")
        if f.get("fractal_down_confirmed"):
            reasons.append("fractal down confirmed")
        if float(f.get("acceleration_score", 0)) > 0.55:
            reasons.append("momentum acceleration turned positive")
        cont = float(f.get("markov_continuation_probability", 0))
        if cont > 0.5:
            reasons.append(f"Markov transition favored continuation at {cont:.0%}")
        sample = f.get("strategy_math_historical_sample_size", ctx.historical_sample_size)
        if sample:
            reasons.append(f"historical sample size was {sample:,}")
        ev = float(f.get("strategy_ev", 0))
        if ev > 0:
            reasons.append(f"EV was positive at +${ev:.2f}/trade")
        ror = float(f.get("risk_of_ruin", 0))
        if ror < 0.05:
            reasons.append("risk of ruin was below threshold")

        for o in ctx.method_outputs:
            agreement[o.method] = not o.skipped and o.confidence >= 0.5
            if o.skipped:
                disagreements.append(f"{o.method} skipped: {o.skip_reason}")
            elif o.confidence < 0.4:
                disagreements.append(f"{o.method} low confidence ({o.confidence:.2f})")

        if ctx.risk and not ctx.risk.approved:
            reasons.append(f"RISK VETO: {ctx.risk.reason}")
        elif ctx.risk and ctx.risk.approved:
            reasons.append("risk agent approved trade")

        direction = "long" if ctx.trade_plan and "long" in ctx.trade_plan.action.value else "short"
        if ctx.prediction and ctx.prediction.should_start:
            summary = f"The system marked a {direction} candidate because:"
        elif ctx.prediction and ctx.prediction.should_avoid:
            summary = "The system recommends avoiding this setup because:"
        else:
            summary = "The system recommends waiting because:"

        ctx.audit = AuditReport(
            summary=summary,
            reasons=reasons or ["insufficient confluence across methods"],
            method_agreement=agreement,
            disagreements=disagreements,
        )
        return ctx

    def explain(self, ctx: PipelineContext) -> str:
        if not ctx.audit:
            return "No audit available."
        lines = [ctx.audit.summary] + [f"- {r}" for r in ctx.audit.reasons]
        if ctx.audit.disagreements:
            lines.append("\nMethod concerns:")
            lines.extend(f"- {d}" for d in ctx.audit.disagreements)
        return "\n".join(lines)
