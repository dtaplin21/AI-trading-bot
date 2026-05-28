"""Trade Planning Agent — beam search (fast) + hierarchical MCTS (deep)."""

from agents.base import BaseAgent
from agents.pipeline_context import PipelineContext
from agents.schemas import TradeAction, TradePlan
from config.agent_config import TRADING_PHILOSOPHY
from mcts.beam_search_planner import BeamSearchPlanner
from mcts.mcts_planner import HierarchicalMCTSPlanner
from pipeline.confluence_report import ConfluenceReport


class TradePlanningAgent(BaseAgent):
    name = "trade_planning"

    def __init__(self) -> None:
        loss_aversion = float(TRADING_PHILOSOPHY["loss_aversion_multiplier"])
        self._beam = BeamSearchPlanner(loss_aversion=loss_aversion)
        self._mcts = HierarchicalMCTSPlanner()

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

        # Fast planner — beam search + expectimax on every qualified signal
        beam_plan = self._beam.plan(
            confluence=ctx.confluence,
            p_target=p_target,
            p_stop=p_stop,
            ev_dollars=ev,
            entry_price=price,
            stop_price=stop,
            target_price=target,
            symbol=ctx.symbol,
            timeframe=ctx.timeframe,
        )
        ctx.metadata["planner"] = "beam"
        ctx.metadata["beam_plan_notes"] = beam_plan.plan_notes
        ctx.metadata["beam_paths"] = [
            {
                "action": p.action,
                "score": p.score,
                "p_success": p.p_success,
                "ev_dollars": p.ev_dollars,
                "notes": p.notes,
            }
            for p in self._beam.last_beam
        ]

        if beam_plan.action == TradeAction.DO_NOTHING and beam_plan.plan_ev <= 0:
            ctx.trade_plan = self._from_pipeline_plan(beam_plan, ctx.fused.signal_rank)
            return ctx

        beam_confidence = beam_plan.plan_confidence
        use_mcts = HierarchicalMCTSPlanner.should_use_mcts(
            beam_confidence=beam_confidence,
            confluence=ctx.confluence,
            signal_rank=ctx.fused.signal_rank,
        )

        if use_mcts:
            self._mcts.symbol = ctx.symbol
            pipeline_plan = self._mcts.plan(
                confluence=ctx.confluence,
                p_target=p_target,
                p_stop=p_stop,
                ev_dollars=ev,
                entry_price=price,
                stop_price=stop,
                target_price=target,
                timeframe=ctx.timeframe,
            )
            ctx.metadata["planner"] = "mcts"
            ctx.trade_plan = self._from_pipeline_plan(pipeline_plan, ctx.fused.signal_rank)
            return ctx

        ctx.trade_plan = self._from_pipeline_plan(beam_plan, ctx.fused.signal_rank)
        return ctx

    def _from_pipeline_plan(self, plan, signal_rank: int) -> TradePlan:
        action_map = {
            "enter_long": TradeAction.ENTER_LONG,
            "enter_short": TradeAction.ENTER_SHORT,
            "wait": TradeAction.WAIT,
            "do_nothing": TradeAction.DO_NOTHING,
        }
        action = action_map.get(plan.action.value, TradeAction.WAIT)
        return TradePlan(
            action=action,
            entry_price=plan.entry_price,
            stop_loss=plan.stop_loss,
            take_profit=plan.take_profit,
            stop_limit=plan.stop_loss,
            start_condition=f"signal_rank>={signal_rank}",
            stop_condition="stop_loss_hit",
            wait_condition="beam_wait" if action == TradeAction.WAIT else None,
            mcts_path=[plan.plan_notes] if plan.plan_notes else [],
        )
