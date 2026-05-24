"""Execution Agent — paper broker only in v1."""

import uuid

from agents.base import BaseAgent
from agents.pipeline_context import PipelineContext
from agents.schemas import ExecutionResult, TradeAction
from paper_trading.paper_trader import PaperTrader


class ExecutionAgent(BaseAgent):
    name = "execution"

    def __init__(self, mode: str = "paper"):
        self.mode = mode
        self.paper = PaperTrader()

    def run(self, ctx: PipelineContext) -> PipelineContext:
        if not ctx.risk or not ctx.risk.approved:
            ctx.execution = ExecutionResult(executed=False, mode=self.mode, message="risk_not_approved")
            return ctx

        if not ctx.trade_plan or ctx.trade_plan.action in (TradeAction.WAIT, TradeAction.DO_NOTHING):
            ctx.execution = ExecutionResult(executed=False, mode=self.mode, message="no_action")
            return ctx

        if self.mode != "paper":
            ctx.execution = ExecutionResult(
                executed=False,
                mode=self.mode,
                message="live_execution_disabled",
            )
            return ctx

        order = {
            "symbol": ctx.symbol,
            "action": ctx.trade_plan.action.value,
            "entry": ctx.trade_plan.entry_price,
            "stop": ctx.trade_plan.stop_loss,
            "target": ctx.trade_plan.take_profit,
            "size": ctx.risk.max_position_size,
        }
        result = self.paper.execute(order)
        ctx.execution = ExecutionResult(
            executed=result.get("status") == "filled",
            mode="paper",
            order_id=str(uuid.uuid4())[:8],
            message=result.get("status"),
        )
        return ctx
