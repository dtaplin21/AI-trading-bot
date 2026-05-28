"""
mcts/beam_search_planner.py

Beam Search planner — fully wired, replaces the previous stub.

Audit finding: BeamSearchScorer existed in pipeline/reward_function.py
but was never called. This file is the actual planner that:
  1. Generates candidate action paths
  2. Scores each path via Expectimax + reward function
  3. Keeps top beam_width paths
  4. Returns the best plan

Used as the fast planner (called every candle).
MCTS is the deep planner (called when beam confidence < threshold).

Env:
  BEAM_WIDTH          (int,   default 4)
  BEAM_MIN_EV         (float, default 0.0)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from config.agent_config import MCTS_CONFIG, TRADING_PHILOSOPHY
from mcts.expectimax_engine import ActionNode, ExpectimaxEngine
from pipeline.confluence_report import ConfluenceReport
from pipeline.reward_function import RewardFunction
from pipeline.schemas import Direction, TradeAction, TradePlan

logger = logging.getLogger(__name__)


@dataclass
class BeamPath:
    """One scored action path from Beam Search."""

    action: str
    direction: int  # +1 long / -1 short / 0 flat
    score: float
    p_success: float
    ev_dollars: float
    entry_condition: str = ""
    stop_condition: str = ""
    target_condition: str = ""
    notes: str = ""


class BeamSearchPlanner:
    """
    Fast action planner using Beam Search + Expectimax.

    Generates 4-6 candidate paths, scores them all,
    keeps top beam_width, returns the best as a TradePlan.

    Called by TradePlanningAgent on every qualified signal.
    """

    def __init__(
        self,
        tick_value: float = 1.25,
        loss_aversion: float | None = None,
    ) -> None:
        la = loss_aversion if loss_aversion is not None else float(
            TRADING_PHILOSOPHY["loss_aversion_multiplier"]
        )
        self.beam_width = int(os.getenv("BEAM_WIDTH", str(MCTS_CONFIG["beam_width"])))
        self.min_ev = float(os.getenv("BEAM_MIN_EV", "0.0"))
        self._expectimax = ExpectimaxEngine(tick_value=tick_value, loss_aversion=la)
        self._reward = RewardFunction(loss_aversion=la)
        self._last_beam: list[BeamPath] = []
        logger.info("BeamSearchPlanner: width=%d min_ev=%.2f", self.beam_width, self.min_ev)

    @property
    def last_beam(self) -> list[BeamPath]:
        return list(self._last_beam)

    def plan(
        self,
        confluence: ConfluenceReport,
        p_target: float,
        p_stop: float,
        ev_dollars: float,
        reward_r: float = 2.0,
        risk_r: float = 1.0,
        entry_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        target_price: Optional[float] = None,
        symbol: str = "",
        timeframe: str = "",
    ) -> TradePlan:
        """Run beam search and return the best TradePlan."""
        from datetime import datetime, timezone

        direction = confluence.consensus_direction

        # ── Step 1: Expectimax pre-filter ─────────────────────────────────────
        expectimax_actions = self._expectimax.score_actions(
            p_target=p_target,
            p_stop=p_stop,
            reward_r=reward_r,
            risk_r=risk_r,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
        )

        # ── Step 2: Generate beam paths ───────────────────────────────────────
        candidate_paths = self._generate_paths(
            direction, confluence, p_target, p_stop, ev_dollars, expectimax_actions
        )

        # ── Step 3: Score and sort ─────────────────────────────────────────────
        scored = self._score_paths(candidate_paths, confluence)
        scored.sort(key=lambda p: p.score, reverse=True)

        # ── Step 4: Keep top beam_width ───────────────────────────────────────
        beam = scored[: self.beam_width]
        self._last_beam = beam

        for i, path in enumerate(beam):
            logger.debug(
                "Beam[%d]: %s score=%.3f p=%.1f%% ev=$%.2f",
                i,
                path.action,
                path.score,
                path.p_success * 100,
                path.ev_dollars,
            )

        # ── Step 5: Best path → TradePlan ────────────────────────────────────
        if not beam or beam[0].ev_dollars < self.min_ev:
            return TradePlan(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=datetime.now(tz=timezone.utc),
                action=TradeAction.DO_NOTHING,
                plan_notes="Beam search: no path with positive EV",
                plan_confidence=0.0,
                plan_ev=0.0,
            )

        best = beam[0]
        trade_action = self._map_action(best.action, best.direction)

        logger.info(
            "BeamSearch best: %s | score=%.3f p=%.1f%% ev=$%.2f",
            best.action,
            best.score,
            best.p_success * 100,
            best.ev_dollars,
        )

        return TradePlan(
            symbol=symbol,
            timeframe=timeframe,
            timestamp=datetime.now(tz=timezone.utc),
            action=trade_action,
            direction=(
                Direction.LONG
                if best.direction == 1
                else Direction.SHORT
                if best.direction == -1
                else None
            ),
            entry_price=entry_price,
            stop_loss=stop_price,
            take_profit=target_price,
            plan_confidence=best.p_success,
            plan_ev=best.ev_dollars,
            plan_notes=best.notes,
        )

    # ─── Path generation ──────────────────────────────────────────────────────

    def _generate_paths(
        self,
        direction: int,
        confluence: ConfluenceReport,
        p_target: float,
        p_stop: float,
        ev_dollars: float,
        expectimax_actions: list[ActionNode],
    ) -> list[BeamPath]:
        paths: list[BeamPath] = []
        p_chop = max(0.0, 1.0 - p_target - p_stop)

        # Path 1: Enter full size now
        if direction != 0:
            paths.append(
                BeamPath(
                    action="enter_full",
                    direction=direction,
                    score=0.0,
                    p_success=p_target,
                    ev_dollars=ev_dollars,
                    entry_condition="immediate entry at current price",
                    notes=f"Full size | confluence={confluence.confluence_score:.2f}",
                )
            )

        # Path 2: Enter half size (lower risk, lower reward)
        if direction != 0 and p_stop > 0.25:
            paths.append(
                BeamPath(
                    action="enter_half",
                    direction=direction,
                    score=0.0,
                    p_success=p_target,
                    ev_dollars=ev_dollars * 0.5,
                    entry_condition="half-size entry — elevated stop risk",
                    notes=f"Half size | p_stop={p_stop:.1%} elevated",
                )
            )

        # Path 3: Wait — if conflict score is borderline
        if confluence.conflict_score > 0.25:
            paths.append(
                BeamPath(
                    action="wait",
                    direction=direction,
                    score=0.0,
                    p_success=p_target * 0.7,
                    ev_dollars=ev_dollars * 0.6,
                    entry_condition="wait for conflict to resolve",
                    notes=f"Wait | conflict={confluence.conflict_score:.2f}",
                )
            )

        # Path 4: Wait for news alignment
        if not confluence.news_aligned:
            paths.append(
                BeamPath(
                    action="wait",
                    direction=direction,
                    score=0.0,
                    p_success=p_target * 0.8,
                    ev_dollars=ev_dollars * 0.7,
                    entry_condition="wait for news alignment",
                    notes=f"News conflict={confluence.news_conflict_score:.2f}",
                )
            )

        # Path 5: Do nothing — always available
        paths.append(
            BeamPath(
                action="do_nothing",
                direction=0,
                score=0.0,
                p_success=0.0,
                ev_dollars=0.0,
                notes="Skip this setup",
            )
        )

        # Merge Expectimax scores into paths
        expectimax_map = {a.action: a.risk_adjusted_ev for a in expectimax_actions}
        for p in paths:
            ea_key = (
                "enter_full"
                if p.action == "enter_full"
                else "enter_half"
                if p.action == "enter_half"
                else "wait"
                if p.action == "wait"
                else "do_nothing"
            )
            p.ev_dollars = max(p.ev_dollars, expectimax_map.get(ea_key, p.ev_dollars))

        return paths

    def _score_paths(
        self,
        paths: list[BeamPath],
        confluence: ConfluenceReport,
    ) -> list[BeamPath]:
        """Score each path combining EV, probability, confluence, and reward function."""
        for path in paths:
            if path.action == "do_nothing":
                path.score = 0.0
                continue

            reward_component = min(
                1.0, max(0.0, self._reward.score(path.ev_dollars / 100.0))
            )
            score = (
                path.p_success * 0.40
                + reward_component * 0.35
                + confluence.confluence_score * 0.25
            )

            # Bonus: direction matches strongest cluster
            if (
                confluence.strongest_cluster
                and confluence.strongest_cluster.direction == path.direction
            ):
                score += 0.05

            # Penalty: news conflict
            score -= confluence.news_conflict_score * 0.10
            path.score = round(score, 4)

        return paths

    def _map_action(self, action: str, direction: int) -> TradeAction:
        if action in ("enter_full", "enter_half"):
            return TradeAction.ENTER_LONG if direction == 1 else TradeAction.ENTER_SHORT
        if action == "wait":
            return TradeAction.WAIT
        return TradeAction.DO_NOTHING
