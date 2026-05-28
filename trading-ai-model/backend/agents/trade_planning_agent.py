"""Trade Planning Agent — hierarchical MCTS action proposals, no execution."""

from agents.base import BaseAgent
from agents.pipeline_context import PipelineContext
from agents.schemas import TradeAction, TradePlan
from config.agent_config import TRADING_PHILOSOPHY
from mcts.expectimax_engine import ExpectimaxEngine
from mcts.mcts_planner import HierarchicalMCTSPlanner
from pipeline.confluence_report import ConfluenceReport
from pipeline.reward_function import BeamSearchScorer, RewardFunction


class TradePlanningAgent(BaseAgent):
    name = "trade_planning"

    def __init__(self) -> None:
        self.planner = HierarchicalMCTSPlanner()
        self._expectimax = ExpectimaxEngine()
        self._beam = BeamSearchScorer(
            RewardFunction(
                loss_aversion=float(TRADING_PHILOSOPHY["loss_aversion_multiplier"])
            ),
            beam_width=4,
        )

    def run(self, ctx: PipelineContext) -> PipelineContext:
        if not ctx.prediction or not ctx.fused:
            ctx.trade_plan = TradePlan(action=TradeAction.WAIT)
            return ctx

        if ctx.prediction.should_avoid or ctx.prediction.should_wait:
            ctx.trade_plan = TradePlan(
                action=TradeAction.WAIT if ctx.prediction.should_wait else TradeAction.DO_NOTHING,
                wait_condition="model_wait" if ctx.prediction.should_wait else "model_avoid",
            )
            return ctx

        if not ctx.confluence:
            ctx.confluence = ConfluenceReport(
                symbol=ctx.symbol,
                timeframe=ctx.timeframe,
                timestamp=ctx.timestamp,
                regime="chop",
            )

        price = float(ctx.ohlcv["close"].iloc[-1])
        atr = float((ctx.ohlcv["high"] - ctx.ohlcv["low"]).tail(14).mean())
        is_long_bias = not ctx.chart or ctx.chart.trend_direction != "down"
        stop = price - atr * 2 if is_long_bias else price + atr * 2
        target = price + atr * 4 if is_long_bias else price - atr * 4

        p_target = float(ctx.prediction.target_before_stop_probability or 0.5)
        p_stop = max(0.0, 1.0 - p_target - 0.15)
        ev = float(ctx.prediction.expected_value or 0.0)

        # Expectimax pre-filter — wide shallow scoring before deep MCTS
        expectimax_actions = self._expectimax.score_actions(
            p_target, p_stop, entry_price=price, stop_price=stop, target_price=target
        )
        best_action, best_ev = self._expectimax.best_action(p_target, p_stop)
        ctx.metadata["expectimax_best_action"] = best_action
        ctx.metadata["expectimax_best_ev"] = best_ev
        ctx.metadata["expectimax_actions"] = [
            {"action": a.action, "ev": a.expected_value, "risk_adj_ev": a.risk_adjusted_ev}
            for a in expectimax_actions
        ]

        positive = self._expectimax.filter_positive_ev(p_target, p_stop)
        if not positive:
            ctx.trade_plan = TradePlan(
                action=TradeAction.DO_NOTHING,
                wait_condition="expectimax_no_positive_ev",
                entry_price=price,
                mcts_path=[f"expectimax:best={best_action}"],
            )
            return ctx

        beam_confidence = self._beam.score_path(
            cumulative_r=ev / max(1.0, atr * 10),
            bars_held=0,
        )
        use_mcts = HierarchicalMCTSPlanner.should_use_mcts(
            beam_confidence=beam_confidence,
            confluence=ctx.confluence,
            signal_rank=ctx.fused.signal_rank,
        )

        if use_mcts or ctx.prediction.should_start:
            self.planner.symbol = ctx.symbol
            pipeline_plan = self.planner.plan(
                confluence=ctx.confluence,
                p_target=p_target,
                p_stop=p_stop,
                ev_dollars=ev,
                entry_price=price,
                stop_price=stop,
                target_price=target,
                timeframe=ctx.timeframe,
            )
            ctx.trade_plan = self._from_pipeline_plan(pipeline_plan, ctx.fused.signal_rank)
            return ctx

        ctx.trade_plan = TradePlan(
            action=TradeAction.WAIT,
            wait_condition="mcts_skip",
            entry_price=price,
            mcts_path=["skip"],
        )
        return ctx

    def _from_pipeline_plan(self, plan, signal_rank: int) -> TradePlan:
        action_map = {
            "enter_long": TradeAction.ENTER_LONG,
            "enter_short": TradeAction.ENTER_SHORT,
            "wait": TradeAction.WAIT,
            "do_nothing": TradeAction.DO_NOTHING,
        }
        action = action_map.get(plan.action.value, TradeAction.WAIT)
        path = plan.plan_notes.split("|") if plan.plan_notes else []
        return TradePlan(
            action=action,
            entry_price=plan.entry_price,
            stop_loss=plan.stop_loss,
            take_profit=plan.take_profit,
            stop_limit=plan.stop_loss,
            start_condition=f"signal_rank>={signal_rank}",
            stop_condition="stop_loss_hit",
            wait_condition="mcts_timing" if action == TradeAction.WAIT else None,
            mcts_path=[plan.plan_notes] if plan.plan_notes else [],
        )
