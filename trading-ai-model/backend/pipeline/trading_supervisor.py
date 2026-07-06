"""
pipeline/trading_supervisor.py  (Live Trading — Phase 1)

Changes from paper version:
  • _execute() calls LiveExecutionAgent.execute_level() — real broker orders
  • LivePositionMonitor.on_bar() called every bar to check TP/SL hits
  • PaperTrader removed entirely
  • LevelEntryGate still the first gate — no level, no trade

Phase 2 — Level-first confirm path:
  • Actionable level → confirm methods only (7 agents from agents.yaml)
  • method_agreement + regime veto → DB entry/TP/SL (no MCTS)
  • LEVEL_FAST_LANE=true skips confirm for top watchlist rows (default off — use confirm path)
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
from agents.method_agents import ALL_METHOD_AGENTS, get_confirm_method_agents_from_registry, get_all_method_agents_from_registry
from agents.news.market_news_agent import MarketNewsAgent
from agents.news_runtime import bootstrap_news_sync, get_news_agent
from agents.pipeline_context import PipelineContext
from learning.learning_agent import LearningAgent
from mcts.trade_planning_agent import TradePlanningAgent
from pipeline.confluence_adapter import prepare_confluence_inputs
from pipeline.confluence_agent import ConfluenceAgent
from pipeline.confluence_report import ConfluenceReport
from pipeline.feature_fusion_news_patch import (
    FeatureFusionAgent,
    NewsAgentProtocol,
    fetch_news_features,
)
from pipeline.level_confirmation import (
    evaluate_level_confirmation,
    filter_confirm_method_outputs,
)
from pipeline.bar_validators import is_valid_bar_close
from pipeline.level_entry_gate import LevelEntryGate, _gate_disabled, level_fast_lane_enabled
from pipeline.level_method_fusion import (
    apply_level_to_plan,
    fused_level_probability,
    method_agreement_score,
    min_method_agreement,
    plan_from_level_setup,
)
from pipeline.level_setup import LevelSetup
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
from risk.kill_switch_runtime import is_kill_switch_active
from validation.method_isolation.method_isolation_validator import MethodEdgeRegistry

logger = logging.getLogger(__name__)

_UNSET = object()


class TradingPipelineResult:
    def __init__(self) -> None:
        self.snapshot_id: str = str(uuid.uuid4())
        self.level_setup: Optional[LevelSetup] = None
        self.confluence: Optional[ConfluenceReport] = None
        self.fused: Optional[FusedFeatureSet] = None
        self.prediction: Optional[PredictionOutput] = None
        self.plan: Optional[TradePlan] = None
        self.risk: Optional[RiskDecision] = None
        self.audit: Optional[AuditReport] = None
        self.executed: bool = False
        self.skipped: bool = False
        self.fast_lane: bool = False
        self.level_confirm: bool = False
        self.closed_positions: list = []
        self.errors: list[str] = []


class TradingPipelineSupervisor:
    """
    One instance per symbol/timeframe.
    Phase 1 live: gate → pipeline → live order → position monitor.
    """

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
        self._level_gate = LevelEntryGate(symbol=self.symbol)

        from live.live_position_monitor import get_position_monitor

        self._pos_monitor = get_position_monitor()
        self._pos_monitor.configure(paper_mode=paper_mode)

        self._live_agent = None
        if not paper_mode:
            from live.live_execution_agent import get_live_execution_agent

            self._live_agent = get_live_execution_agent()

        logger.info(
            "Supervisor WIRED | %s %s | mode=%s | news=%s | kill=%s",
            symbol,
            timeframe,
            "paper" if paper_mode else "LIVE",
            self._news is not None,
            is_kill_switch_active(),
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
        # Default execute in both paper and live; self.paper selects sim vs broker in _execute.
        should_execute = True if execute is None else execute

        try:
            if self._pos_monitor:
                closed = await self._pos_monitor.on_bar(
                    symbol=self.symbol,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                )
                result.closed_positions = closed
                if closed:
                    asyncio.create_task(self._log_closes_to_learning(closed))

            if is_kill_switch_active():
                result.skipped = True
                result.audit = self._build_audit(result)
                return result

            if not is_valid_bar_close(bar.close):
                result.skipped = True
                result.audit = self._build_audit(result)
                return result

            prev_close: float | None = None
            if ohlcv is not None and len(ohlcv) >= 2:
                prev_close = float(ohlcv["close"].iloc[-2])

            level_setup = self._level_gate.check(
                current_price=bar.close,
                bar_high=bar.high,
                bar_low=bar.low,
                prev_close=prev_close,
            )
            if level_setup is None and not _gate_disabled():
                result.skipped = True
                result.audit = self._build_audit(result)
                return result

            result.level_setup = level_setup
            if level_setup is not None:
                logger.info("%s: level gate PASSED — %s", self.symbol, level_setup)
                if level_fast_lane_enabled():
                    return await self._run_level_fast_lane(
                        bar, level_setup, result, should_execute, portfolio
                    )

            merged = self._merge_bar(ohlcv, bar)
            if merged is None or len(merged) < 20:
                result.errors.append("insufficient_ohlcv")
                result.audit = self._build_audit(result)
                return result

            if level_setup is not None:
                return await self._run_level_confirm_path(
                    bar=bar,
                    level_setup=level_setup,
                    result=result,
                    should_execute=should_execute,
                    portfolio=portfolio,
                    merged=merged,
                    historical_sample_size=historical_sample_size,
                )

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

            p_for_gate = prediction.trade_start_probability

            gate = self._gate.check(
                p_for_gate,
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

            from ml.features.level_intelligence import get_system

            level_intel = get_system(self.symbol).get_probability(
                float(merged["close"].iloc[-1])
            )
            watchlist = get_system(self.symbol).get_watchlist()
            ctx.metadata["level_intel"] = level_intel
            ctx.metadata["level_watchlist"] = (
                watchlist.to_dict("records") if not watchlist.empty else []
            )
            if level_setup is not None:
                ctx.metadata["level_setup"] = level_setup

            result.plan = self._planner.plan(
                confluence=result.confluence,
                p_success=p_for_gate,
                ev_dollars=prediction.expected_value,
                sample_size=hist_n,
                signal_rank=fused.signal_rank,
                p_target=prediction.target_hit_probability,
                p_stop=prediction.continuation_probability,
                entry_price=level_setup.entry_price if level_setup else None,
                stop_price=level_setup.stop_price if level_setup else None,
                target_price=level_setup.target_price if level_setup else None,
                level_intel=level_intel,
            )
            if level_setup is not None and result.plan is not None:
                result.plan = apply_level_to_plan(result.plan, level_setup)

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
                result.executed = await self._execute(
                    result.plan,
                    result.risk,
                    result.snapshot_id,
                    level_setup=level_setup,
                )

            result.audit = self._build_audit(result)

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

    async def _run_methods_concurrent(self, ctx: PipelineContext, agents=None) -> None:
        method_agents = agents if agents is not None else get_all_method_agents_from_registry(self.symbol)

        async def run_one(agent):
            def _run():
                local = copy.copy(ctx)
                local.method_outputs = []
                return agent.run(local).method_outputs

            return await asyncio.to_thread(_run)

        batches = await asyncio.gather(*(run_one(agent) for agent in method_agents))
        ctx.method_outputs = [output for batch in batches for output in batch]
        ctx.metadata["methods_ran"] = len({o.method for o in ctx.method_outputs if not o.skipped})
        ctx.metadata["all_methods_ran"] = len(ctx.method_outputs) >= len(method_agents)
        ctx.metadata["confirm_methods_only"] = agents is not None

    async def _run_level_confirm_path(
        self,
        bar: OHLCV,
        level_setup: LevelSetup,
        result: TradingPipelineResult,
        should_execute: bool,
        portfolio: PortfolioState | None,
        merged: pd.DataFrame,
        historical_sample_size: int | None,
    ) -> TradingPipelineResult:
        """Level-first path: confirm methods → agreement → DB prices → risk → execute."""
        result.level_confirm = True
        tf = bar.timeframe or self.timeframe

        ctx = PipelineContext(
            symbol=self.symbol,
            timeframe=tf,
            ohlcv=merged,
            timestamp=bar.timestamp if bar.timestamp.tzinfo else bar.timestamp.replace(tzinfo=timezone.utc),
            portfolio=portfolio or PortfolioState(),
            historical_sample_size=historical_sample_size or self._sample_size,
        )
        ctx.metadata["level_setup"] = level_setup

        if portfolio:
            self._risk_eng.sync_portfolio(portfolio)

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
        confirm_agents = get_confirm_method_agents_from_registry(symbol=self.symbol)
        await self._run_methods_concurrent(ctx, agents=confirm_agents)
        ctx.method_outputs = filter_confirm_method_outputs(
            ctx.method_outputs, level_setup, float(bar.close)
        )

        news = fetch_news_features(self._news, self.symbol, 0, ctx.timestamp)
        ctx.metadata["news_features"] = news.model_dump()
        confluence_inputs = prepare_confluence_inputs(ctx, news)
        result.confluence = self._confluence.analyze(**confluence_inputs)
        ctx.confluence = result.confluence

        confirm = evaluate_level_confirmation(result.confluence, level_setup)
        level_setup.method_agreement = confirm.agreement
        if not confirm.passed:
            result.skipped = True
            logger.info(
                "%s: level confirm failed — %s (agreement=%.2f)",
                self.symbol,
                confirm.reason,
                confirm.agreement,
            )
            result.audit = self._build_audit(result)
            return result

        if getattr(news, "trading_blocked", False):
            result.skipped = True
            logger.info("%s: level confirm skipped — news block", self.symbol)
            result.audit = self._build_audit(result)
            return result

        fused = self._fusion.build_from_context(ctx)
        result.fused = fused
        fused.signal_rank = self._compute_signal_rank(fused, result.confluence)

        use_ml = os.getenv("LEVEL_CONFIRM_USE_ML", "false").lower() in ("true", "1", "yes")
        p_for_gate = level_setup.hold_rate
        if use_ml:
            hist_p, hist_n = self._world.compute_historical_p_success(result.confluence)
            if hist_n <= 0:
                hist_n = int(fused.sample_size or ctx.historical_sample_size or 0)
            prediction = await self._run_prediction(
                fused,
                hist_p,
                hist_n,
                ohlcv=merged,
                shared_features=shared,
            )
            result.prediction = prediction
            level_setup.fused_probability = fused_level_probability(
                level_setup, prediction.trade_start_probability
            )
            p_for_gate = level_setup.fused_probability

        result.plan = plan_from_level_setup(level_setup, tf, path_label="confirm")
        result.risk = self._risk_eng.approve_level_fast_lane(result.plan, level_setup)

        if result.risk.approved and should_execute:
            result.executed = await self._execute(
                result.plan,
                result.risk,
                result.snapshot_id,
                level_setup=level_setup,
            )

        logger.info(
            "%s: level confirm | agreement=%.2f p=%.3f approved=%s executed=%s",
            self.symbol,
            confirm.agreement,
            p_for_gate,
            result.risk.approved,
            result.executed,
        )
        result.audit = self._build_audit(result)
        return result

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

    async def _run_level_fast_lane(
        self,
        bar: OHLCV,
        level_setup: LevelSetup,
        result: TradingPipelineResult,
        should_execute: bool,
        portfolio: PortfolioState | None,
    ) -> TradingPipelineResult:
        """Actionable watchlist only — skip methods, ML, confluence, soft risk."""
        result.fast_lane = True
        if portfolio:
            self._risk_eng.sync_portfolio(portfolio)

        tf = bar.timeframe or self.timeframe
        result.plan = plan_from_level_setup(level_setup, tf)
        result.risk = self._risk_eng.approve_level_fast_lane(result.plan, level_setup)

        if result.risk.approved and should_execute:
            result.executed = await self._execute(
                result.plan,
                result.risk,
                result.snapshot_id,
                level_setup=level_setup,
            )

        result.audit = self._build_audit(result)
        logger.info(
            "%s: level fast lane | approved=%s executed=%s ev=%.3f%% rr=%.1f",
            self.symbol,
            result.risk.approved,
            result.executed,
            level_setup.expected_value_pct,
            level_setup.optimal_rr,
        )
        return result

    async def _execute(
        self,
        plan: TradePlan,
        risk: RiskDecision,
        snapshot_id: str,
        level_setup: LevelSetup | None = None,
    ) -> bool:
        if plan.action in (TradeAction.DO_NOTHING, TradeAction.WAIT):
            return False

        if level_setup is None:
            logger.debug("%s: no level_setup — execution skipped", self.symbol)
            return False

        if self.paper:
            logger.info(
                "PAPER (not submitted) | %s %s entry=%.5f tp=%.5f sl=%.5f",
                level_setup.symbol,
                level_setup.entry_side,
                level_setup.entry_price,
                level_setup.target_price,
                level_setup.stop_price,
            )
            if self._pos_monitor is not None:
                from live.live_position_monitor import LivePosition

                qty = float(max(risk.position_size_contracts, 1))
                self._pos_monitor.register(
                    LivePosition(
                        trade_id=f"paper-{snapshot_id[:12]}",
                        symbol=level_setup.symbol,
                        side="LONG" if level_setup.entry_side == "BUY" else "SHORT",
                        entry_price=level_setup.entry_price,
                        target_price=level_setup.target_price,
                        stop_price=level_setup.stop_price,
                        quantity=qty,
                        broker_order_id="paper",
                        tp_pct=level_setup.optimal_tp_pct,
                        sl_pct=level_setup.optimal_sl_pct,
                        ev_pct=level_setup.expected_value_pct,
                        touch_count=level_setup.touch_count,
                        hold_rate=level_setup.hold_rate,
                    )
                )
            return True

        if self._live_agent is not None:
            return await self._live_agent.execute_level(level_setup)

        logger.info(
            "EXECUTE [%s %s]: %s | contracts=%d | live agent unavailable",
            self.symbol,
            self.timeframe,
            plan.action.value,
            risk.position_size_contracts,
        )
        return False

    async def _log_closes_to_learning(self, closes) -> None:
        for close in closes:
            try:
                hit_target = close.reason == "TP"
                hit_stop = close.reason == "SL"
                self._learning.on_trade_closed(
                    snapshot_id=close.trade_id,
                    pnl=close.pnl_pct,
                    r_multiple=close.pnl_pct / 100.0,
                    hit_target=hit_target,
                    hit_stop=hit_stop,
                    mfe_ticks=0.0,
                    mae_ticks=0.0,
                    duration_bars=close.bars_held,
                    entry_price=0.0,
                    exit_price=close.exit_price,
                    symbol=close.symbol,
                    timeframe=self.timeframe,
                    signal_rank=0,
                )
            except Exception as exc:
                logger.debug("Learning close log failed for %s: %s", close.trade_id, exc)

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
