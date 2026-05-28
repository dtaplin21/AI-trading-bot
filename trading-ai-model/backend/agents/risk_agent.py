"""Risk Agent — final veto gate via full RiskEngine.approve()."""

from agents.base import BaseAgent
from agents.pipeline_context import PipelineContext
from agents.schemas import RiskVerdict, TradeAction as AgentTradeAction
from pipeline.confluence_report import ConfluenceReport
from pipeline.schemas import FusedFeatureSet, TradeAction, TradePlan
from risk.risk_engine import RiskEngine


class RiskAgent(BaseAgent):
    name = "risk"

    def __init__(self, news_agent=None, engine: RiskEngine | None = None):
        self.engine = engine or RiskEngine()
        self._news = news_agent

    def run(self, ctx: PipelineContext) -> PipelineContext:
        passed: list[str] = []
        failed: list[str] = []

        if ctx.metadata.get("data_stale"):
            failed.append("data_stale")
        else:
            passed.append("data_fresh")

        if not ctx.metadata.get("all_methods_ran", False):
            failed.append("incomplete_method_review")
        else:
            passed.append("all_methods_reviewed")

        if not ctx.trade_plan or not ctx.fused:
            ctx.risk = RiskVerdict(
                approved=False,
                reason="missing_plan_or_features",
                checks_passed=passed,
                checks_failed=failed + ["missing_plan_or_features"],
            )
            return ctx

        self.engine.sync_portfolio(ctx.portfolio)

        confluence = ctx.confluence or ConfluenceReport(
            symbol=ctx.symbol,
            timeframe=ctx.timeframe,
            timestamp=ctx.timestamp,
            regime="chop",
        )
        fused = FusedFeatureSet.from_fused_features(ctx.fused)
        plan = self._to_pipeline_plan(ctx)

        p_success = float(ctx.prediction.target_before_stop_probability or 0.0) if ctx.prediction else 0.0
        ev = float(ctx.prediction.expected_value or 0.0) if ctx.prediction else 0.0
        sample = int(
            ctx.fused.features.get("strategy_math_sample_size", ctx.historical_sample_size) or 0
        )

        pre_failed = list(failed)

        decision = self.engine.approve(
            plan=plan,
            fused=fused,
            confluence=confluence,
            p_success=p_success,
            ev_dollars=ev,
            sample_size=sample,
            signal_rank=ctx.fused.signal_rank,
        )

        failed = pre_failed + decision.rejection_reasons
        approved = decision.approved and not pre_failed

        if decision.approved:
            passed.append("core_risk_checks")

        ctx.metadata["risk_decision"] = decision.model_dump()

        ctx.risk = RiskVerdict(
            approved=approved,
            reason=failed[0] if failed else None,
            max_position_size=float(decision.position_size_contracts),
            checks_passed=passed,
            checks_failed=failed,
        )
        return ctx

    def _to_pipeline_plan(self, ctx: PipelineContext) -> TradePlan:
        tp = ctx.trade_plan
        action_map = {
            AgentTradeAction.ENTER_LONG: TradeAction.ENTER_LONG,
            AgentTradeAction.ENTER_SHORT: TradeAction.ENTER_SHORT,
            AgentTradeAction.WAIT: TradeAction.WAIT,
            AgentTradeAction.DO_NOTHING: TradeAction.DO_NOTHING,
            AgentTradeAction.SCALE_IN: TradeAction.SCALE_IN,
            AgentTradeAction.PARTIAL_PROFIT: TradeAction.PARTIAL_EXIT,
            AgentTradeAction.TRAIL_STOP: TradeAction.TRAIL_STOP,
            AgentTradeAction.EXIT: TradeAction.EXIT,
        }
        return TradePlan(
            symbol=ctx.symbol,
            timeframe=ctx.timeframe,
            timestamp=ctx.timestamp,
            action=action_map.get(tp.action, TradeAction.WAIT),
            entry_price=tp.entry_price,
            stop_loss=tp.stop_loss,
            take_profit=tp.take_profit,
            plan_notes=tp.mcts_path[0] if tp.mcts_path else "",
        )
