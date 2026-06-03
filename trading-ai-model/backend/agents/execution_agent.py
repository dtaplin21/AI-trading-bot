"""Execution Agent — paper (default) or Coinbase live crypto."""

from __future__ import annotations

import uuid

from agents.base import BaseAgent
from agents.pipeline_context import PipelineContext
from agents.schemas import ExecutionResult, TradeAction
from config.execution_config import resolve_execution_mode
from paper_trading.paper_trader import PaperTrader


class ExecutionAgent(BaseAgent):
    name = "execution"

    def __init__(self, mode: str | None = None):
        self.mode = mode or resolve_execution_mode()
        self.paper = PaperTrader()

    def run(self, ctx: PipelineContext) -> PipelineContext:
        if not ctx.risk or not ctx.risk.approved:
            ctx.execution = ExecutionResult(
                executed=False, mode=self.mode, message="risk_not_approved"
            )
            return ctx

        if not ctx.trade_plan or ctx.trade_plan.action in (TradeAction.WAIT, TradeAction.DO_NOTHING):
            ctx.execution = ExecutionResult(executed=False, mode=self.mode, message="no_action")
            return ctx

        if self.mode == "paper":
            return self._execute_paper(ctx)

        if self.mode == "coinbase":
            return self._execute_coinbase(ctx)

        ctx.execution = ExecutionResult(
            executed=False,
            mode=self.mode,
            message="execution_disabled_enable_coinbase_live",
        )
        return ctx

    def _execute_paper(self, ctx: PipelineContext) -> PipelineContext:
        order = {
            "symbol": ctx.symbol,
            "action": ctx.trade_plan.action.value,
            "entry": ctx.trade_plan.entry_price,
            "stop": ctx.trade_plan.stop_loss,
            "target": ctx.trade_plan.take_profit,
            "size": max(1, int(ctx.risk.max_position_size)),
            "snapshot_id": ctx.metadata.get("world_state_snapshot_id", ""),
            "timeframe": ctx.timeframe,
            "signal_rank": ctx.fused.signal_rank if ctx.fused else 0,
        }
        result = self.paper.execute(order)
        ctx.execution = ExecutionResult(
            executed=result.get("status") == "filled",
            mode="paper",
            order_id=str(uuid.uuid4())[:8],
            message=result.get("status"),
        )
        return ctx

    def _execute_coinbase(self, ctx: PipelineContext) -> PipelineContext:
        from live.coinbase_executor import get_coinbase_executor

        risk_meta = ctx.metadata.get("risk_decision") or {}
        quote_usd = float(risk_meta.get("max_notional_usd") or ctx.risk.max_position_size or 0)

        order = {
            "symbol": ctx.symbol,
            "action": ctx.trade_plan.action.value,
            "entry": ctx.trade_plan.entry_price,
            "quote_size_usd": quote_usd,
            "size": max(1, int(ctx.risk.max_position_size)),
            "snapshot_id": ctx.metadata.get("world_state_snapshot_id", ""),
            "timeframe": ctx.timeframe,
        }
        result = get_coinbase_executor().execute(order)
        ctx.execution = ExecutionResult(
            executed=result.get("status") == "filled",
            mode="coinbase",
            order_id=result.get("order_id"),
            message=result.get("status") or result.get("message"),
        )
        return ctx
