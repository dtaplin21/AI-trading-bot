"""
mcts/trade_planning_agent.py

TradePlanningAgent — the decision router.

Audit finding: planning was a 21-line rule stub in trading_supervisor.py.
This replaces it with a proper agent that:
  1. Runs Expectimax (fast pre-filter, always)
  2. Runs Beam Search (fast planner, most cases)
  3. Escalates to full MCTS when beam confidence is low
     or when the setup is genuinely ambiguous

Decision logic:
  Beam Search  → when confluence >= 0.55 and conflict <= 0.25
  MCTS         → when confluence <  0.55 or  conflict >  0.25
  Do nothing   → when Expectimax best action has negative EV

Env:
  MCTS_CONFIDENCE_THRESHOLD  (float, default 0.55)
  TICK_VALUE_{SYMBOL}        (float, e.g. TICK_VALUE_MES=1.25)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from mcts.beam_search_planner import BeamSearchPlanner
from mcts.expectimax_engine import ExpectimaxEngine
from mcts.mcts_planner import HierarchicalMCTSPlanner
from pipeline.confluence_report import ConfluenceReport
from pipeline.probability_gate import ProbabilityGate
from config.symbols import TICK_VALUES, get_symbol_or_none
from pipeline.schemas import TradeAction, TradePlan

logger = logging.getLogger(__name__)
MCTS_THRESHOLD = float(os.getenv("MCTS_CONFIDENCE_THRESHOLD", "0.55"))
BEAM_CONFLICT_MAX = 0.25


class TradePlanningAgent:
    """
    Routes each signal to the right planner and returns a TradePlan.
    Called by TradingPipelineSupervisor after prediction.
    """

    def __init__(self, symbol: str, timeframe: str) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.last_planner = "none"
        self.last_audit: dict | None = None
        sym = symbol.upper()
        spec = get_symbol_or_none(sym)
        default_tick = TICK_VALUES.get(sym, spec.tick_value if spec else 1.25)
        tick_value = float(os.getenv(f"TICK_VALUE_{sym}", str(default_tick)))
        self._gate = ProbabilityGate()
        self._expectimax = ExpectimaxEngine(tick_value=tick_value)
        self._beam = BeamSearchPlanner(tick_value=tick_value)
        self._mcts = HierarchicalMCTSPlanner(tick_value=tick_value, symbol=symbol)
        logger.info("TradePlanningAgent: %s %s | tick=$%.2f", symbol, timeframe, tick_value)

    def plan(
        self,
        confluence: ConfluenceReport,
        p_success: float,
        ev_dollars: float,
        sample_size: int,
        signal_rank: int,
        p_target: float,
        p_stop: float,
        reward_r: float = 2.0,
        risk_r: float = 1.0,
        entry_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        target_price: Optional[float] = None,
        level_intel: Optional[dict] = None,
    ) -> TradePlan:
        """Full planning pipeline for one signal. Returns a TradePlan for Risk Engine."""

        # ── Gate check first ──────────────────────────────────────────────────
        gate = self._gate.check(p_success, ev_dollars, sample_size, signal_rank)
        if not gate.passed:
            self.last_planner = "gate"
            self.last_audit = None
            logger.info("TradePlanning: gate failed — %s", "; ".join(gate.failures[:1]))
            return TradePlan(
                symbol=self.symbol,
                timeframe=self.timeframe,
                timestamp=datetime.now(tz=timezone.utc),
                action=TradeAction.DO_NOTHING,
                plan_notes=f"Gate failed: {gate.failures[0] if gate.failures else 'unknown'}",
                plan_confidence=0.0,
                plan_ev=0.0,
            )

        # ── Expectimax pre-filter ─────────────────────────────────────────────
        best_action, best_ev = self._expectimax.best_action(
            p_target=p_target,
            p_stop=p_stop,
            reward_r=reward_r,
            risk_r=risk_r,
        )
        if best_ev <= 0 or best_action == "do_nothing":
            self.last_planner = "expectimax"
            self.last_audit = None
            logger.info(
                "TradePlanning: Expectimax best=%s ev=$%.2f — do nothing",
                best_action,
                best_ev,
            )
            return TradePlan(
                symbol=self.symbol,
                timeframe=self.timeframe,
                timestamp=datetime.now(tz=timezone.utc),
                action=TradeAction.DO_NOTHING,
                plan_notes=f"Expectimax: best action={best_action} EV=${best_ev:.2f}",
                plan_confidence=p_success,
                plan_ev=best_ev,
            )

        # ── Route to Beam or MCTS ─────────────────────────────────────────────
        use_mcts = (
            confluence.confluence_score < MCTS_THRESHOLD
            or confluence.conflict_score > BEAM_CONFLICT_MAX
            or not confluence.news_aligned
        )

        if use_mcts:
            self.last_planner = "mcts"
            logger.info(
                "TradePlanning: routing to MCTS | conf=%.2f conflict=%.2f",
                confluence.confluence_score,
                confluence.conflict_score,
            )
            plan = self._mcts.plan(
                confluence=confluence,
                p_target=p_target,
                p_stop=p_stop,
                ev_dollars=ev_dollars,
                reward_r=reward_r,
                risk_r=risk_r,
                entry_price=entry_price,
                stop_price=stop_price,
                target_price=target_price,
                timeframe=self.timeframe,
                level_intel=level_intel,
            )
            self.last_audit = self._mcts.last_audit
            return plan

        self.last_planner = "beam"
        logger.info(
            "TradePlanning: routing to Beam | conf=%.2f",
            confluence.confluence_score,
        )
        plan = self._beam.plan(
            confluence=confluence,
            p_target=p_target,
            p_stop=p_stop,
            ev_dollars=ev_dollars,
            reward_r=reward_r,
            risk_r=risk_r,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            symbol=self.symbol,
            timeframe=self.timeframe,
            level_intel=level_intel,
        )
        self.last_audit = self._beam.last_audit
        return plan
