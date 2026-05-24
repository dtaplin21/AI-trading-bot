"""Trade Planning Agent — MCTS action proposals, no execution."""

from agents.base import BaseAgent
from agents.pipeline_context import PipelineContext
from agents.schemas import TradeAction, TradePlan
from mcts.mcts_planner import MCTSPlanner


class TradePlanningAgent(BaseAgent):
    name = "trade_planning"

    def __init__(self):
        self.planner = MCTSPlanner()

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

        compressed_state = {
            "market_state": ctx.chart.trend_direction if ctx.chart else "unknown",
            "model_confidence": ctx.prediction.model_confidence,
            "expected_value": ctx.prediction.expected_value,
            "risk_of_ruin": ctx.fused.features.get("risk_of_ruin", 0),
            "number_confluence_score": ctx.fused.features.get("level_369_reversal_zone_active", 0),
            "trend_strength": ctx.fused.features.get("momentum_momentum_score", 0),
            "volatility_state": "normal",
            "signal_rank": ctx.fused.signal_rank,
        }

        action = self.planner.plan(compressed_state)
        price = float(ctx.ohlcv["close"].iloc[-1])
        atr = float((ctx.ohlcv["high"] - ctx.ohlcv["low"]).tail(14).mean())

        if action == "wait" and ctx.prediction.should_start:
            action = "enter_long" if ctx.chart and ctx.chart.trend_direction != "down" else "enter_short"

        trade_action = self._map_action(action)
        is_long = trade_action == TradeAction.ENTER_LONG

        ctx.trade_plan = TradePlan(
            action=trade_action,
            entry_price=price,
            stop_loss=price - atr * 2 if is_long else price + atr * 2,
            take_profit=price + atr * 4 if is_long else price - atr * 4,
            stop_limit=price - atr if is_long else price + atr,
            start_condition=f"signal_rank>={ctx.fused.signal_rank}",
            stop_condition="stop_loss_hit",
            wait_condition=None,
            mcts_path=[action],
        )
        return ctx

    def _map_action(self, action: str) -> TradeAction:
        mapping = {
            "wait": TradeAction.WAIT,
            "enter_long": TradeAction.ENTER_LONG,
            "enter_short": TradeAction.ENTER_SHORT,
            "scale_in": TradeAction.SCALE_IN,
            "partial_profit": TradeAction.PARTIAL_PROFIT,
            "trail_stop": TradeAction.TRAIL_STOP,
            "exit": TradeAction.EXIT,
            "do_nothing": TradeAction.DO_NOTHING,
        }
        return mapping.get(action, TradeAction.WAIT)
