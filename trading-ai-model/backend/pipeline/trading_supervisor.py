"""
pipeline/trading_supervisor.py

The Trading Supervisor Agent — orchestrates the full pipeline for every new bar.
Delegates to agents.supervisor.TradingSupervisor (all real agents, no stubs).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from agents.news.market_news_agent import MarketNewsAgent
from agents.news_runtime import bootstrap_news_sync, get_news_agent
from agents.supervisor import TradingSupervisor
from pipeline.schemas import (
    AuditReport,
    FusedFeatureSet,
    OHLCV,
    PredictionOutput,
    RiskDecision,
    TradePlan,
    decision_to_fused,
)
from risk.risk_engine import PortfolioState

logger = logging.getLogger(__name__)


class TradingPipelineResult:
    """Full output of one bar's pipeline run."""

    def __init__(self) -> None:
        self.fused: Optional[FusedFeatureSet] = None
        self.prediction: Optional[PredictionOutput] = None
        self.plan: Optional[TradePlan] = None
        self.risk: Optional[RiskDecision] = None
        self.audit: Optional[AuditReport] = None
        self.executed: bool = False
        self.errors: list[str] = []


class TradingPipelineSupervisor:
    """
    Central supervisor for one symbol/timeframe pair.
    Async façade over the synchronous multi-agent TradingSupervisor.
    """

    MIN_SIGNAL_RANK = 70

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        news_agent: Optional[MarketNewsAgent] = None,
        paper_mode: bool = True,
        historical_sample_size: int = 500,
    ) -> None:
        self.symbol = symbol.upper()
        self.timeframe = timeframe
        self.paper = paper_mode
        self._sample_size = historical_sample_size
        self._news = news_agent or get_news_agent()
        bootstrap_news_sync()

        self._supervisor = TradingSupervisor(
            execution_mode="paper" if paper_mode else "live",
            news_agent=self._news,
        )

        logger.info(
            "TradingPipelineSupervisor initialized | %s %s | paper=%s | news=%s",
            symbol,
            timeframe,
            paper_mode,
            self._news is not None,
        )

    async def on_new_bar(
        self,
        bar: OHLCV,
        ohlcv: pd.DataFrame | None = None,
        portfolio: PortfolioState | None = None,
        historical_sample_size: int | None = None,
        execute: bool | None = None,
    ) -> TradingPipelineResult:
        """
        Called on every new completed bar. Runs the full 11-step pipeline.

        Steps (via TradingSupervisor):
          1. MarketDataAgent
          2. ChartReadingAgent
          3. All method agents (13)
          4. MarketNewsAgent (inside FeatureFusion)
          5. FeatureFusionAgent
          6. PredictionAgent
          7. TradePlanningAgent (MCTS)
          8. RiskAgent
          9. ExecutionAgent
          10. LearningAgent
          11. AuditAgent
        """
        result = TradingPipelineResult()
        logger.debug(
            "Supervisor: new bar %s %s @ %s",
            self.symbol,
            self.timeframe,
            bar.timestamp,
        )

        try:
            merged = self._merge_bar(ohlcv, bar)
            load_from_db = merged is None or len(merged) < 20
            should_execute = self.paper if execute is None else execute

            decision = await asyncio.to_thread(
                self._supervisor.process_candle,
                self.symbol,
                merged,
                self.timeframe,
                portfolio or PortfolioState(),
                historical_sample_size or self._default_sample_size(),
                should_execute,
                load_from_db,
            )

            result = self._map_decision(decision, result)

            if result.fused and result.risk:
                result.risk = self._apply_pipeline_risk_gates(result.fused, result.risk, result.plan)

        except Exception as e:
            msg = f"Pipeline error on {self.symbol} {self.timeframe}: {e}"
            result.errors.append(msg)
            logger.error(msg, exc_info=True)

        return result

    def _default_sample_size(self) -> int:
        return self._sample_size

    def _merge_bar(self, ohlcv: pd.DataFrame | None, bar: OHLCV) -> pd.DataFrame | None:
        if ohlcv is None or ohlcv.empty:
            return None
        ts = bar.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        row = {
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
        }
        df = ohlcv.copy()
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.date_range(end=ts, periods=len(df), freq="5min", tz=timezone.utc)
        df.loc[pd.Timestamp(ts)] = row
        return df.sort_index()

    def _map_decision(
        self,
        decision,
        result: TradingPipelineResult,
    ) -> TradingPipelineResult:
        ts = decision.timestamp
        fused = decision_to_fused(decision)
        result.fused = fused

        if decision.prediction and fused:
            result.prediction = PredictionOutput.from_agent(
                decision.prediction, self.symbol, self.timeframe, ts
            )

        if decision.trade_plan:
            result.plan = TradePlan.from_agent(
                decision.trade_plan, self.symbol, self.timeframe, ts
            )

        if decision.risk:
            result.risk = RiskDecision.from_verdict(decision.risk, self.symbol, ts)

        if decision.execution:
            result.executed = bool(decision.execution.executed)

        news_explanation = ""
        if self._news and fused:
            news_explanation = self._news.get_latest_explanation(fused.symbol)

        if decision.audit:
            result.audit = AuditReport.from_agent(
                decision.audit,
                self.symbol,
                self.timeframe,
                ts,
                fused.signal_rank if fused else 0,
                result.plan.action.value if result.plan else "none",
                result.risk.approved if result.risk else False,
                fused,
                news_explanation,
            )

        return result

    def _apply_pipeline_risk_gates(
        self,
        fused: FusedFeatureSet,
        risk: RiskDecision,
        plan: Optional[TradePlan],
    ) -> RiskDecision:
        """
        Additional pipeline-level gates documented in the supervisor spec.
        Merges with RiskAgent verdict (does not override hard news calendar blocks).
        """
        rejections = list(risk.rejection_reasons)
        approved = risk.approved

        if fused.news_trading_blocked:
            reason = f"News risk block: {fused.news_risk_reason or 'active'}"
            if reason not in rejections:
                rejections.append(reason)
            approved = False

        if fused.signal_rank < self.MIN_SIGNAL_RANK:
            rejections.append(f"SignalRank {fused.signal_rank} below minimum {self.MIN_SIGNAL_RANK}")
            approved = False

        if fused.risk_of_ruin > 0.05:
            rejections.append(f"Risk of ruin {fused.risk_of_ruin:.3f} above threshold")
            approved = False

        if 0 < fused.sample_size < 100:
            rejections.append(f"Sample size {fused.sample_size} below minimum 100")
            approved = False

        from pipeline.schemas import TradeAction

        action = plan.action if plan else TradeAction.DO_NOTHING
        if action in (TradeAction.DO_NOTHING, TradeAction.WAIT):
            approved = False

        return RiskDecision(
            approved=approved,
            symbol=risk.symbol,
            timestamp=risk.timestamp,
            rejection_reasons=rejections,
            position_size_contracts=risk.position_size_contracts,
        )

    def compute_signal_rank(self, fused: FusedFeatureSet) -> int:
        """
        Weighted signal rank 0–100 with news capped influence.
        Used when recomputing rank outside FeatureFusionAgent.
        """
        score = 0.0
        score += fused.wick_rejection_score * 8.0
        score += fused.candle_reversal_prob * 15.0
        score += fused.candle_exhaustion_score * 4.0
        score += fused.body_to_range_ratio * 3.0
        score += fused.harmonic_completion_score * 12.0
        score += (1.0 if fused.fib_reversal_zone else 0.0) * 10.0
        score += (1.0 if fused.near_369_level else 0.0) * 10.0
        score += fused.elliott_confidence * 5.0
        score += fused.fractal_strength * 3.0
        score += fused.markov_continuation_probability * 10.0
        score += min(1.0, fused.strategy_ev / 20.0) * 7.0
        score += (1.0 - fused.risk_of_ruin) * 3.0
        score += fused.momentum_score * 4.0
        score += fused.volume_shift_score * 3.0

        if fused.news_trading_blocked:
            score -= 40.0
        elif fused.news_conflict_score > 0.40:
            score -= fused.news_conflict_score * 20.0
        elif fused.affected_symbol_match:
            score += min(3.0, fused.news_impact_score * 3.0)

        score += min(3.0, fused.gann_confluence_score * 60.0)
        return max(0, min(100, int(score)))
