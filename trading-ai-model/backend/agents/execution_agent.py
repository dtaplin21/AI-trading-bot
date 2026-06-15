"""Execution Agent — paper (default), Coinbase crypto, or OANDA forex."""

from __future__ import annotations

import uuid

from agents.base import BaseAgent
from agents.pipeline_context import PipelineContext
from agents.schemas import ExecutionResult, TradeAction
from config.coinbase_symbols import is_coinbase_tradable
from config.execution_config import (
    coinbase_live_allowed,
    oanda_live_allowed,
    resolve_execution_mode,
)
from config.oanda_symbols import is_oanda_tradable
from config.settings import get_settings
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

        settings = get_settings()
        symbol = (ctx.symbol or "").upper()

        if settings.paper_trading_enabled:
            return self._execute_paper(ctx)

        if oanda_live_allowed(settings) and is_oanda_tradable(symbol):
            return self._execute_oanda(ctx)

        if coinbase_live_allowed(settings) and is_coinbase_tradable(symbol):
            return self._execute_coinbase(ctx)

        ctx.execution = ExecutionResult(
            executed=False,
            mode=self.mode,
            message="execution_disabled_enable_oanda_or_coinbase_live",
        )
        return ctx

    def _execute_paper(self, ctx: PipelineContext) -> PipelineContext:
        trade_plan = ctx.trade_plan
        risk = ctx.risk
        if trade_plan is None or risk is None:
            raise ValueError("trade_plan and risk required for execution")

        order = {
            "symbol": ctx.symbol,
            "action": trade_plan.action.value,
            "entry": trade_plan.entry_price,
            "stop": trade_plan.stop_loss,
            "target": trade_plan.take_profit,
            "size": max(1, int(risk.max_position_size)),
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

        trade_plan = ctx.trade_plan
        risk = ctx.risk
        if trade_plan is None or risk is None:
            raise ValueError("trade_plan and risk required for execution")

        risk_meta = ctx.metadata.get("risk_decision") or {}
        quote_usd = float(risk_meta.get("max_notional_usd") or risk.max_position_size or 0)

        order = {
            "symbol": ctx.symbol,
            "action": trade_plan.action.value,
            "entry": trade_plan.entry_price,
            "quote_size_usd": quote_usd,
            "size": max(1, int(risk.max_position_size)),
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

    def _execute_oanda(self, ctx: PipelineContext) -> PipelineContext:
        from live.oanda_executor import get_oanda_executor

        trade_plan = ctx.trade_plan
        risk = ctx.risk
        if trade_plan is None or risk is None:
            raise ValueError("trade_plan and risk required for execution")

        risk_meta = ctx.metadata.get("risk_decision") or {}

        order = {
            "symbol": ctx.symbol,
            "action": trade_plan.action.value,
            "entry": trade_plan.entry_price,
            "order_usd": float(
                risk_meta.get("oanda_order_usd") or risk_meta.get("max_notional_usd") or 0
            ),
            "units": int(risk_meta.get("oanda_units") or 0) or None,
            "snapshot_id": ctx.metadata.get("world_state_snapshot_id", ""),
            "timeframe": ctx.timeframe,
        }
        result = get_oanda_executor().execute(order)
        ctx.execution = ExecutionResult(
            executed=result.get("status") == "filled",
            mode="oanda",
            order_id=result.get("order_id"),
            message=result.get("status") or result.get("message"),
        )
        return ctx
