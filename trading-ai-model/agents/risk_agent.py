"""Risk Agent — veto power; no approval = no trade."""

from agents.base import BaseAgent
from agents.pipeline_context import PipelineContext
from agents.schemas import RiskVerdict
from config.risk_params import get_risk_limits
from risk.risk_engine import RiskEngine


class RiskAgent(BaseAgent):
    name = "risk"

    MAX_TRADES_PER_DAY = 10
    MAX_RISK_OF_RUIN = 0.05

    def __init__(self):
        self.engine = RiskEngine()
        self.limits = get_risk_limits()

    def run(self, ctx: PipelineContext) -> PipelineContext:
        passed: list[str] = []
        failed: list[str] = []

        if ctx.metadata.get("data_stale"):
            failed.append("data_stale")
        else:
            passed.append("data_fresh")

        rank = ctx.fused.signal_rank if ctx.fused else 0
        decision = self.engine.evaluate(rank, ctx.portfolio, ctx.symbol)

        if decision.approved:
            passed.append("core_risk_checks")
        else:
            failed.append(decision.reason or "core_risk_rejected")

        ror = float(ctx.fused.features.get("risk_of_ruin", 0)) if ctx.fused else 0
        if ror <= self.MAX_RISK_OF_RUIN:
            passed.append("risk_of_ruin")
        else:
            failed.append("risk_of_ruin_exceeded")

        ev = ctx.prediction.expected_value if ctx.prediction else 0
        if ev > 0:
            passed.append("positive_ev")
        else:
            failed.append("non_positive_ev")

        sample = ctx.historical_sample_size
        if sample >= 300 or rank < 75:
            passed.append("sample_size")
        else:
            failed.append("insufficient_sample_size")

        if not ctx.metadata.get("all_methods_ran", False):
            failed.append("incomplete_method_review")
        else:
            passed.append("all_methods_reviewed")

        approved = len(failed) == 0 and decision.approved

        ctx.risk = RiskVerdict(
            approved=approved,
            reason=failed[0] if failed else None,
            max_position_size=decision.max_position_size if approved else 0.0,
            checks_passed=passed,
            checks_failed=failed,
        )
        return ctx
