"""
pipeline/trading_supervisor.py  — FULLY WIRED VERSION

All audit gaps fixed:
  ✅ TradePlanningAgent replaces 21-line rule stub
  ✅ Beam Search + Expectimax + MCTS all callable
  ✅ Unified ProbabilityGate (one check, correct thresholds)
  ✅ RiskEngine with kill switch + full drawdown wiring
  ✅ LearningAgent auto-called on trade close (MFE/MAE included)
  ✅ SessionProbabilityManager default-on
  ✅ Method agents run concurrently (asyncio.gather)
  ✅ All env vars read via os.getenv with config fallbacks
"""
from __future__ import annotations

import asyncio
import copy
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional, cast

import numpy as np
import pandas as pd

from agents.chart_reading_agent import ChartReadingAgent
from agents.method_agents import ALL_METHOD_AGENTS
from agents.news.market_news_agent import MarketNewsAgent
from agents.news_runtime import bootstrap_news_sync, get_news_agent
from agents.pipeline_context import PipelineContext
from learning.learning_agent import LearningAgent
from mcts.trade_planning_agent import TradePlanningAgent
from paper_trading.paper_trader import get_paper_trader
from pipeline.confluence_adapter import prepare_confluence_inputs
from pipeline.confluence_agent import ConfluenceAgent
from pipeline.confluence_report import ConfluenceReport
from pipeline.feature_fusion_news_patch import (
    FeatureFusionAgent,
    NewsAgentProtocol,
    fetch_news_features,
)
from pipeline.planner_audit_service import persist_planner_audit
from pipeline.probability_gate import ProbabilityGate
from pipeline.schemas import (
    AuditReport,
    FusedFeatureSet,
    OHLCV,
    PredictionOutput,
    RiskDecision,
    TradeAction,
    TradePlan,
)
from pipeline.session_probability_manager import SessionProbabilityManager, SessionSetup
from data.storage.feature_store import get_feature_store
from ml.features.feature_pipeline import FeaturePipeline
from pipeline.world_state_runtime import get_world_state_store
from risk.risk_engine import PortfolioState, RiskEngine
from validation.method_isolation.method_isolation_validator import MethodEdgeRegistry

logger = logging.getLogger(__name__)

_UNSET = object()


class TradingPipelineResult:
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
    def __init__(
        self,
        symbol: str,
        timeframe: str,
        news_agent: Optional[NewsAgentProtocol] | object = _UNSET,
        world_store=None,
        session_mgr: Optional[SessionProbabilityManager] = None,
        paper_mode: bool = True,
        historical_sample_size: int = 500,
    ) -> None:
        self.symbol = symbol.upper()
        self.timeframe = timeframe
        self.paper = paper_mode
        self._sample_size = historical_sample_size

        self._registry = MethodEdgeRegistry()
        self._world = world_store or get_world_state_store()
        self._risk_eng = RiskEngine(
            account_size=float(os.getenv("ACCOUNT_SIZE", "10000"))
        )
        if news_agent is _UNSET:
            self._news: NewsAgentProtocol | None = get_news_agent()
            bootstrap_news_sync()
        else:
            self._news = cast(NewsAgentProtocol | None, news_agent)

        self._chart = ChartReadingAgent()
        self._confluence = ConfluenceAgent(method_registry=self._registry)
        self._fusion = FeatureFusionAgent(news_agent=self._news)
        self._planner = TradePlanningAgent(symbol=self.symbol, timeframe=self.timeframe)
        self._gate = ProbabilityGate()
        self._learning = LearningAgent(world_store=self._world, risk_engine=self._risk_eng)
        self._session = session_mgr or SessionProbabilityManager(
            watched_symbols=[self.symbol],
            watched_timeframes=[self.timeframe],
        )
        self._feature_pipeline = FeaturePipeline.for_symbol(self.symbol)
        self._feature_store = get_feature_store()

        logger.info(
            "Supervisor WIRED | %s %s | paper=%s | news=%s | kill=%s",
            symbol,
            timeframe,
            paper_mode,
            self._news is not None,
            os.getenv("RISK_KILL_SWITCH", "false"),
        )

    async def on_new_bar(
        self,
        bar: OHLCV,
        ohlcv: pd.DataFrame | None = None,
        portfolio: PortfolioState | None = None,
        historical_sample_size: int | None = None,
        execute: bool | None = None,
    ) -> TradingPipelineResult:
        result = TradingPipelineResult()
        result.snapshot_id = str(uuid.uuid4())
        should_execute = self.paper if execute is None else execute

        try:
            merged = self._merge_bar(ohlcv, bar)
            if merged is None or len(merged) < 20:
                result.errors.append("insufficient_ohlcv")
                result.audit = self._build_audit(result)
                return result

            ctx = PipelineContext(
                symbol=self.symbol,
                timeframe=bar.timeframe or self.timeframe,
                ohlcv=merged,
                timestamp=bar.timestamp if bar.timestamp.tzinfo else bar.timestamp.replace(tzinfo=timezone.utc),
                portfolio=portfolio or PortfolioState(),
                historical_sample_size=historical_sample_size or self._sample_size,
            )

            if portfolio:
                self._risk_eng.sync_portfolio(portfolio)

            tf = bar.timeframe or self.timeframe
            shared = self._feature_pipeline.build(
                {
                    "symbol": self.symbol,
                    "timeframe": tf,
                    "timestamp": ctx.timestamp,
                    "ohlcv": merged,
                }
            )
            ctx.metadata["shared_features"] = shared

            await asyncio.to_thread(self._chart.run, ctx)
            await self._run_methods_concurrent(ctx)

            news = fetch_news_features(self._news, self.symbol, 0, ctx.timestamp)
            ctx.metadata["news_features"] = news.model_dump()
            confluence_inputs = prepare_confluence_inputs(ctx, news)
            result.confluence = self._confluence.analyze(**confluence_inputs)

            ctx.confluence = result.confluence
            fused = self._fusion.build_from_context(ctx)
            result.fused = fused
            fused.signal_rank = self._compute_signal_rank(fused, result.confluence)

            fused_payload = fused.model_dump() if hasattr(fused, "model_dump") else dict(fused)
            self._feature_store.set_signal(self.symbol, tf, ctx.timestamp, fused_payload)

            if not result.confluence.ready_for_prediction:
                result.audit = self._build_audit(result)
                return result

            hist_p, hist_n = self._world.compute_historical_p_success(result.confluence)
            if hist_n <= 0:
                hist_n = int(fused.sample_size or ctx.historical_sample_size or 0)

            prediction = await self._run_prediction(
                fused,
                hist_p,
                hist_n,
                ohlcv=merged,
                shared_features=ctx.metadata.get("shared_features"),
            )
            result.prediction = prediction

            gate = self._gate.check(
                prediction.trade_start_probability,
                prediction.expected_value,
                hist_n,
                fused.signal_rank,
                f"{self.symbol} {self.timeframe}",
                chop_probability=prediction.chop_probability,
                continuation_probability=prediction.continuation_probability,
            )
            if not gate.passed:
                result.audit = self._build_audit(result)
                return result

            self._world.store_snapshot(
                snapshot_id=result.snapshot_id,
                confluence=result.confluence,
                signal_rank=fused.signal_rank,
                predicted_p=prediction.trade_start_probability,
                predicted_ev=prediction.expected_value,
            )

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
                    p_success=prediction.trade_start_probability,
                    ev_dollars=prediction.expected_value,
                    signal_rank=fused.signal_rank,
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

            result.plan = self._planner.plan(
                confluence=result.confluence,
                p_success=prediction.trade_start_probability,
                ev_dollars=prediction.expected_value,
                sample_size=hist_n,
                signal_rank=fused.signal_rank,
                p_target=prediction.target_hit_probability,
                p_stop=prediction.continuation_probability,
            )

            persist_planner_audit(
                self._planner.last_audit,
                snapshot_id=result.snapshot_id,
                symbol=self.symbol,
                timeframe=bar.timeframe or self.timeframe,
                confluence=result.confluence,
                plan=result.plan,
                p_success=prediction.trade_start_probability,
                ev_dollars=prediction.expected_value,
                signal_rank=fused.signal_rank,
            )

            result.risk = self._risk_eng.approve(
                plan=result.plan,
                fused=fused,
                confluence=result.confluence,
                p_success=prediction.trade_start_probability,
                ev_dollars=prediction.expected_value,
                sample_size=hist_n,
                signal_rank=fused.signal_rank,
            )

            if result.risk.approved and should_execute:
                result.executed = await self._execute(result.plan, result.risk, result.snapshot_id)

            result.audit = self._build_audit(result)

            if self.paper:
                get_paper_trader().on_bar(self.symbol, bar.high, bar.low, bar.close)

            asyncio.create_task(self._background_logging(result))

        except Exception as e:
            result.errors.append(f"Pipeline error [{self.symbol}]: {e}")
            logger.error(result.errors[-1], exc_info=True)
            if result.audit is None:
                result.audit = self._build_audit(result)

        return result

    async def on_trade_closed(
        self,
        snapshot_id: str,
        pnl: float,
        r_multiple: float,
        hit_target: bool,
        hit_stop: bool,
        mfe_ticks: float,
        mae_ticks: float,
        duration_bars: int,
        entry_price: float,
        exit_price: float,
        signal_rank: int,
    ) -> None:
        self._learning.on_trade_closed(
            snapshot_id=snapshot_id,
            pnl=pnl,
            r_multiple=r_multiple,
            hit_target=hit_target,
            hit_stop=hit_stop,
            mfe_ticks=mfe_ticks,
            mae_ticks=mae_ticks,
            duration_bars=duration_bars,
            entry_price=entry_price,
            exit_price=exit_price,
            symbol=self.symbol,
            timeframe=self.timeframe,
            signal_rank=signal_rank,
        )
        self._session.record_outcome(snapshot_id, pnl, r_multiple)
        self._risk_eng.close_position()

    async def _run_methods_concurrent(self, ctx: PipelineContext) -> None:
        async def run_one(agent):
            def _run():
                local = copy.copy(ctx)
                local.method_outputs = []
                return agent.run(local).method_outputs

            return await asyncio.to_thread(_run)

        batches = await asyncio.gather(*(run_one(agent) for agent in ALL_METHOD_AGENTS))
        ctx.method_outputs = [output for batch in batches for output in batch]
        ctx.metadata["methods_ran"] = len({o.method for o in ctx.method_outputs if not o.skipped})
        ctx.metadata["all_methods_ran"] = len(ctx.method_outputs) >= len(ALL_METHOD_AGENTS)

    async def _run_prediction(
        self,
        fused: FusedFeatureSet,
        hist_p: float,
        hist_n: int,
        ohlcv: pd.DataFrame | None = None,
        shared_features: dict | None = None,
    ) -> PredictionOutput:
        from ml.models.chop_detector import detect_chop
        from ml.models.continuation_predictor import predict_continuation
        from ml.models.reversal_predictor import predict_reversal

        if ohlcv is not None and len(ohlcv) >= 20:
            chop_p = detect_chop(ohlcv)
            cont_p = predict_continuation(ohlcv)
        else:
            chop_p = 0.5
            cont_p = 0.5

        p = predict_reversal(
            self.symbol,
            features={},
            ohlcv=ohlcv,
            fused=fused,
            shared_features=shared_features,
        )
        if hist_p > 0:
            p = (p + hist_p) / 2

        # Dampen reversal confidence in choppy or strongly trending markets
        p = p * (1.0 - chop_p * 0.5)
        p = p * (1.0 - cont_p * 0.4)

        return PredictionOutput(
            symbol=self.symbol,
            timeframe=self.timeframe,
            timestamp=datetime.now(tz=timezone.utc),
            trade_start_probability=round(p, 4),
            target_hit_probability=round(p * 0.85, 4),
            continuation_probability=round(cont_p, 4),
            model_confidence=round(p, 4),
            expected_value=fused.strategy_ev,
            chop_probability=round(chop_p, 4),
        )

    async def _execute(
        self,
        plan: TradePlan,
        risk: RiskDecision,
        snapshot_id: str,
    ) -> bool:
        if plan.action in (TradeAction.DO_NOTHING, TradeAction.WAIT):
            return False

        if not self.paper:
            logger.info(
                "EXECUTE [%s %s]: %s | contracts=%d | live disabled",
                self.symbol,
                self.timeframe,
                plan.action.value,
                risk.position_size_contracts,
            )
            return False

        order = {
            "symbol": self.symbol,
            "action": plan.action.value,
            "entry": plan.entry_price,
            "stop": plan.stop_loss,
            "target": plan.take_profit,
            "size": risk.position_size_contracts,
            "snapshot_id": snapshot_id,
            "timeframe": self.timeframe,
            "signal_rank": 0,
        }
        result = get_paper_trader().execute(order)
        logger.info(
            "EXECUTE [%s %s]: %s | contracts=%d | paper=%s",
            self.symbol,
            self.timeframe,
            plan.action.value,
            risk.position_size_contracts,
            self.paper,
        )
        return result.get("status") == "filled"

    async def _background_logging(self, result: TradingPipelineResult) -> None:
        if result.confluence:
            logger.debug(
                "Learning snapshot %s | %s",
                result.snapshot_id,
                result.confluence.summary(),
            )

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

    def _build_audit(self, result: TradingPipelineResult) -> AuditReport:
        c = result.confluence
        fused = result.fused
        risk = result.risk
        approved = risk.approved if risk else False
        reasons = list(c.top_signals) if c else []
        if c and c.news_trading_blocked:
            reasons = [f"NEWS BLOCK: {c.news_risk_reason}"] + reasons
        reject = ""
        if risk and risk.rejection_reasons:
            reject = risk.rejection_reasons[0]
        return AuditReport(
            symbol=self.symbol,
            timeframe=self.timeframe,
            timestamp=datetime.now(tz=timezone.utc),
            signal_rank=fused.signal_rank if fused else 0,
            action=result.plan.action.value if result.plan else "none",
            approved=approved,
            explanation=(
                f"{c.probability_statement if c else 'No confluence.'} "
                f"Rank:{fused.signal_rank if fused else 0}. "
                f"{'APPROVED' if approved else 'REJECTED: ' + reject}."
            ),
            key_reasons=reasons,
            disagreements=risk.rejection_reasons if risk else [],
            confidence=c.confluence_score if c else 0.0,
            ev=fused.strategy_ev if fused else 0.0,
            sample_size=fused.sample_size if fused else 0,
        )

    def compute_signal_rank(
        self,
        fused: FusedFeatureSet,
        confluence: Optional[ConfluenceReport] = None,
    ) -> int:
        if confluence is None:
            confluence = ConfluenceReport(
                symbol=fused.symbol,
                timeframe=fused.timeframe,
                timestamp=fused.timestamp,
                regime=getattr(fused, "regime", "chop"),
            )
        return self._compute_signal_rank(fused, confluence)

    def _compute_signal_rank(self, fused: FusedFeatureSet, confluence: ConfluenceReport) -> int:
        s = 0.0
        s += fused.wick_rejection_score * 8.0
        s += fused.candle_reversal_prob * 15.0
        s += fused.harmonic_completion_score * 12.0
        s += (1.0 if fused.fib_reversal_zone else 0.0) * 10.0
        s += (1.0 if fused.near_369_level else 0.0) * 10.0
        s += fused.elliott_confidence * 5.0
        s += fused.fractal_strength * 3.0
        s += fused.markov_continuation_probability * 10.0
        s += min(1.0, fused.strategy_ev / 20.0) * 7.0
        s += fused.momentum_score * 4.0
        s += fused.volume_shift_score * 3.0
        s += confluence.confluence_score * 8.0
        if getattr(fused, "news_trading_blocked", False):
            s -= 40.0
        elif getattr(fused, "news_conflict_score", 0.0) > 0.40:
            s -= getattr(fused, "news_conflict_score", 0.0) * 20.0
        s += min(3.0, fused.gann_confluence_score * 60.0)
        return max(0, min(100, int(s)))
