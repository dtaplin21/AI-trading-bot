"""
pipeline/trading_supervisor.py

The Trading Supervisor Agent — updated with ConfluenceAgent wired in.

Pipeline per candle:
  1. Chart Reading
  2. All 13 Method Agents (concurrent)
  3. News Features
  4. Confluence Agent → ConfluenceReport
  5. Feature Fusion → FusedFeatureSet
  6. Signal Rank
  7. Prediction Agent (LightGBM)
  8. Beam Search / MCTS Planner
  9. Risk Agent
  10. Execution
  11. Learning → WorldStateStore
  12. Audit
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from agents.news.market_news_agent import MarketNewsAgent
from agents.news_runtime import bootstrap_news_sync, get_news_agent
from agents.supervisor import TradingSupervisor
from config.agent_config import TRADING_PHILOSOPHY
from pipeline.confluence_report import ConfluenceReport
from pipeline.reward_function import BeamSearchScorer, RewardFunction
from pipeline.schemas import (
    AuditReport,
    FusedFeatureSet,
    OHLCV,
    PredictionOutput,
    RiskDecision,
    TradeAction,
    TradePlan,
    decision_to_fused,
)
from pipeline.session_probability_manager import SessionProbabilityManager, SessionSetup
from pipeline.world_state_runtime import get_world_state_store
from pipeline.world_state_store import WorldStateStore
from risk.risk_engine import PortfolioState

logger = logging.getLogger(__name__)


class TradingPipelineResult:
    """Full output of one bar's pipeline run."""

    def __init__(self) -> None:
        self.snapshot_id: Optional[str] = None
        self.confluence: Optional[ConfluenceReport] = None
        self.fused: Optional[FusedFeatureSet] = None
        self.prediction: Optional[PredictionOutput] = None
        self.plan: Optional[TradePlan] = None
        self.risk: Optional[RiskDecision] = None
        self.audit: Optional[AuditReport] = None
        self.executed: bool = False
        self.errors: list[str] = []


class TradingPipelineSupervisor:
    """
    Owns all agents for one symbol/timeframe pair.
    Called on every new completed bar.

    Async façade over agents.supervisor.TradingSupervisor — all real agents,
    no method stubs. Confluence + WorldStateStore wired explicitly.
    """

    def __init__(
        self,
        symbol: str,
        timeframe: str,
        news_agent: Optional[MarketNewsAgent] = None,
        world_store: Optional[WorldStateStore] = None,
        session_mgr: Optional[SessionProbabilityManager] = None,
        paper_mode: bool = True,
        historical_sample_size: int = 500,
    ) -> None:
        self.symbol = symbol.upper()
        self.timeframe = timeframe
        self.paper = paper_mode
        self._sample_size = historical_sample_size

        self._news = news_agent or get_news_agent()
        bootstrap_news_sync()

        self._world = world_store or get_world_state_store()
        self._session = session_mgr
        self._reward = RewardFunction(
            loss_aversion=float(TRADING_PHILOSOPHY["loss_aversion_multiplier"]),
            time_penalty_bars=20,
            min_r_for_full_reward=1.5,
            target_r=2.0,
        )
        self._beam = BeamSearchScorer(self._reward, beam_width=4)

        self._supervisor = TradingSupervisor(
            execution_mode="paper" if paper_mode else "live",
            news_agent=self._news,
        )

        logger.info(
            "Supervisor initialized | %s %s | paper=%s",
            symbol,
            timeframe,
            paper_mode,
        )

    async def on_new_bar(
        self,
        bar: OHLCV,
        ohlcv: pd.DataFrame | None = None,
        portfolio: PortfolioState | None = None,
        historical_sample_size: int | None = None,
        execute: bool | None = None,
    ) -> TradingPipelineResult:
        """Process one completed bar through the full pipeline."""
        result = TradingPipelineResult()
        result.snapshot_id = str(uuid.uuid4())

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
                historical_sample_size or self._sample_size,
                should_execute,
                load_from_db,
            )

            result = self._map_decision(decision, result)
            result.snapshot_id = result.snapshot_id or str(uuid.uuid4())

            if result.confluence and not result.confluence.ready_for_prediction:
                logger.debug("Confluence not ready: %s", result.confluence.summary())
                return result

            if result.fused and result.confluence:
                result.fused.signal_rank = self._compute_signal_rank(
                    result.fused, result.confluence
                )

            hist_p, hist_n = 0.0, 0
            if result.confluence:
                hist_p, hist_n = self._world.compute_historical_p_success(result.confluence)

            if result.prediction and hist_p > 0:
                blended = (result.prediction.trade_start_probability + hist_p) / 2
                result.prediction = result.prediction.model_copy(
                    update={
                        "trade_start_probability": round(blended, 4),
                        "model_confidence": round(
                            (result.prediction.model_confidence + hist_p) / 2, 4
                        ),
                    }
                )

            if result.confluence and result.fused and result.prediction:
                self._world.store_snapshot(
                    snapshot_id=result.snapshot_id,
                    confluence=result.confluence,
                    signal_rank=result.fused.signal_rank,
                    predicted_p=result.prediction.trade_start_probability,
                    predicted_ev=result.prediction.expected_value,
                )

            prob_min = float(TRADING_PHILOSOPHY["probability_minimum"])
            if (
                self._session
                and result.prediction
                and result.confluence
                and result.prediction.trade_start_probability >= prob_min
            ):
                direction = (
                    "long"
                    if result.confluence.consensus_direction == 1
                    else "short"
                    if result.confluence.consensus_direction == -1
                    else "flat"
                )
                self._session.add_setup(
                    SessionSetup(
                        setup_id=result.snapshot_id,
                        symbol=self.symbol,
                        timeframe=self.timeframe,
                        direction=direction,
                        timestamp_scored=datetime.now(tz=timezone.utc),
                        p_success=result.prediction.trade_start_probability,
                        ev_dollars=result.prediction.expected_value,
                        signal_rank=result.fused.signal_rank if result.fused else 0,
                        sample_size=hist_n,
                        methods_agreed=(
                            result.confluence.strongest_cluster.methods
                            if result.confluence.strongest_cluster
                            else []
                        ),
                        methods_disagreed=(
                            result.confluence.opposing_cluster.methods
                            if result.confluence.opposing_cluster
                            else []
                        ),
                        methods_excluded=result.confluence.excluded_methods,
                        confluence_score=result.confluence.confluence_score,
                        regime=result.confluence.regime,
                        news_aligned=result.confluence.news_aligned,
                        conflict_score=result.confluence.conflict_score,
                    )
                )

            if result.fused and result.risk and result.plan and result.confluence:
                result.risk = self._run_risk(result.fused, result.plan, result.confluence)

            if result.audit is None:
                result.audit = self._build_audit(result)

            asyncio.create_task(self._log_to_learning(result))

        except Exception as e:
            msg = f"Pipeline error {self.symbol} {self.timeframe}: {e}"
            result.errors.append(msg)
            logger.error(msg, exc_info=True)

        return result

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
        result.confluence = decision.confluence
        if result.confluence is not None and not isinstance(result.confluence, ConfluenceReport):
            result.confluence = ConfluenceReport.model_validate(result.confluence)
        elif not result.confluence:
            raw = getattr(decision, "metadata", None)
            if isinstance(raw, dict) and "confluence_report" in raw:
                result.confluence = ConfluenceReport.model_validate(raw["confluence_report"])

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

    def _run_risk(
        self,
        fused: FusedFeatureSet,
        plan: TradePlan,
        confluence: ConfluenceReport,
    ) -> RiskDecision:
        rejections: list[str] = []
        rank_min = int(TRADING_PHILOSOPHY["signal_rank_minimum"])
        sample_min = int(TRADING_PHILOSOPHY["sample_size_minimum"])
        max_conflict = float(TRADING_PHILOSOPHY["max_conflict_score"])

        if fused.news_trading_blocked:
            rejections.append(f"News block: {fused.news_risk_reason or 'active'}")
        if fused.signal_rank < rank_min:
            rejections.append(f"Signal rank {fused.signal_rank} < {rank_min}")
        if confluence.conflict_score > max_conflict:
            rejections.append(
                f"Conflict score {confluence.conflict_score:.2f} too high"
            )
        if fused.risk_of_ruin > 0.05:
            rejections.append(f"Risk of ruin {fused.risk_of_ruin:.3f} too high")
        if 0 < fused.sample_size < sample_min:
            rejections.append(f"Sample size {fused.sample_size} < {sample_min}")

        approved = (
            len(rejections) == 0
            and plan.action not in {TradeAction.DO_NOTHING, TradeAction.WAIT}
        )
        return RiskDecision(
            approved=approved,
            symbol=self.symbol,
            timestamp=datetime.now(tz=timezone.utc),
            rejection_reasons=rejections,
        )

    def _build_audit(self, result: TradingPipelineResult) -> AuditReport:
        c = result.confluence
        fused = result.fused
        risk = result.risk
        approved = risk.approved if risk else False
        reasons = list(c.top_signals) if c else []
        if c and c.news_trading_blocked:
            reasons = [f"NEWS BLOCK: {c.news_risk_reason}"] + reasons
        explanation = (
            f"{c.probability_statement if c else 'No confluence.'} "
            f"Signal rank: {fused.signal_rank if fused else 0}. "
            f"{'APPROVED' if approved else 'REJECTED'}."
        )
        return AuditReport(
            symbol=self.symbol,
            timeframe=self.timeframe,
            timestamp=datetime.now(tz=timezone.utc),
            signal_rank=fused.signal_rank if fused else 0,
            action=result.plan.action.value if result.plan else "none",
            approved=approved,
            explanation=explanation,
            key_reasons=reasons,
            disagreements=risk.rejection_reasons if risk else [],
            confidence=c.confluence_score if c else 0.0,
            ev=fused.strategy_ev if fused else 0.0,
            sample_size=fused.sample_size if fused else 0,
        )

    async def _log_to_learning(self, result: TradingPipelineResult) -> None:
        """Background hook — LearningAgent already logs via TradingSupervisor."""
        if result.confluence:
            logger.debug(
                "Learning snapshot %s | %s",
                result.snapshot_id,
                result.confluence.summary(),
            )

    def compute_signal_rank(
        self,
        fused: FusedFeatureSet,
        confluence: Optional[ConfluenceReport] = None,
    ) -> int:
        """Public API — recomputes rank with optional confluence boost."""
        if confluence is None:
            confluence = ConfluenceReport(
                symbol=fused.symbol,
                timeframe=fused.timeframe,
                timestamp=fused.timestamp,
                regime=getattr(fused, "regime", "chop"),
            )
        return self._compute_signal_rank(fused, confluence)

    def _compute_signal_rank(
        self,
        fused: FusedFeatureSet,
        confluence: ConfluenceReport,
    ) -> int:
        score = 0.0
        score += fused.wick_rejection_score * 8.0
        score += fused.candle_reversal_prob * 15.0
        score += fused.harmonic_completion_score * 12.0
        score += (1.0 if fused.fib_reversal_zone else 0.0) * 10.0
        score += (1.0 if fused.near_369_level else 0.0) * 10.0
        score += fused.elliott_confidence * 5.0
        score += fused.fractal_strength * 3.0
        score += fused.markov_continuation_probability * 10.0
        score += min(1.0, fused.strategy_ev / 20.0) * 7.0
        score += fused.momentum_score * 4.0
        score += fused.volume_shift_score * 3.0
        score += confluence.confluence_score * 8.0

        if fused.news_trading_blocked:
            score -= 40.0
        elif fused.news_conflict_score > 0.40:
            score -= fused.news_conflict_score * 20.0

        score += min(3.0, fused.gann_confluence_score * 60.0)
        return max(0, min(100, int(score)))
