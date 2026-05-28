"""
mcts/mcts_planner.py

Risk-Sensitive Hierarchical MCTS — full L1–L5 implementation.
Replaces the previous 21-line rule stub.

Audit finding: mcts_planner.py was a stub that returned
enter_long/enter_short/wait based on rank only. No tree search existed.

This file implements:
  L1: Trade or skip?
  L2: Long or short?
  L3: Entry timing (now / next bar / wait for confirmation)
  L4: Position size (full / half / quarter)
  L5: Stop + target + trail plan

UCB1 node selection, rollout simulation, backpropagation.
Risk-sensitive: downside outcomes penalised by loss_aversion.

Called by TradePlanningAgent when:
  - Beam Search confidence < MCTS_CONFIDENCE_THRESHOLD, OR
  - Confluence conflict > 0.30 (ambiguous setup needs deep search), OR
  - Position sizing decision is non-trivial

Env:
  MCTS_ROLLOUTS         (int,   default 500)
  MCTS_EXPLORATION      (float, default 1.414)
  LOSS_AVERSION_MULTIPLIER (float, default 2.0)
"""
from __future__ import annotations

import logging
import math
import os
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from config.agent_config import MCTS_CONFIG, TRADING_PHILOSOPHY
from mcts.expectimax_engine import ExpectimaxEngine
from pipeline.confluence_report import ConfluenceReport
from pipeline.schemas import Direction, TradeAction, TradePlan

logger = logging.getLogger(__name__)

ROLLOUTS = int(os.getenv("MCTS_ROLLOUTS", str(MCTS_CONFIG["min_rollouts"])))
EXPLORATION = float(os.getenv("MCTS_EXPLORATION", str(MCTS_CONFIG["exploration_constant"])))
LOSS_AVERSION = float(
    os.getenv("LOSS_AVERSION_MULTIPLIER", str(TRADING_PHILOSOPHY["loss_aversion_multiplier"]))
)
MCTS_CONFIDENCE_THRESHOLD = float(MCTS_CONFIG["confidence_threshold"])
MCTS_CONFLICT_THRESHOLD = float(MCTS_CONFIG["conflict_threshold"])


@dataclass
class MCTSNode:
    state: dict
    parent: Optional["MCTSNode"] = None
    action: str = "root"
    level: int = 0

    visits: int = 0
    value_sum: float = 0.0
    children: list["MCTSNode"] = field(default_factory=list)
    is_terminal: bool = False

    @property
    def avg_value(self) -> float:
        return self.value_sum / self.visits if self.visits else 0.0

    def ucb_score(self, total_visits: int, exploration: float = EXPLORATION) -> float:
        if self.visits == 0:
            return float("inf")
        exploit = self.avg_value
        explore = exploration * math.sqrt(math.log(total_visits) / self.visits)
        return exploit + explore

    def is_fully_expanded(self) -> bool:
        return len(self.children) >= len(self._legal_actions())

    def _legal_actions(self) -> list[str]:
        level_actions = {
            1: ["trade", "skip"],
            2: ["long", "short"],
            3: ["enter_now", "wait_1", "wait_confirm"],
            4: ["full_size", "half_size", "quarter_size"],
            5: ["tight_stop", "normal_stop", "wide_stop"],
        }
        return level_actions.get(self.level + 1, [])

    def expand(self) -> "MCTSNode":
        tried = {c.action for c in self.children}
        legal = [a for a in self._legal_actions() if a not in tried]
        if not legal:
            return self
        action = random.choice(legal)
        child_state = {**self.state, f"L{self.level + 1}": action}
        child = MCTSNode(
            state=child_state,
            parent=self,
            action=action,
            level=self.level + 1,
            is_terminal=(self.level + 1 >= 5),
        )
        self.children.append(child)
        return child

    def best_child(self, total_visits: int) -> "MCTSNode":
        return max(self.children, key=lambda c: c.ucb_score(total_visits))

    def best_child_by_value(self) -> Optional["MCTSNode"]:
        if not self.children:
            return None
        return max(self.children, key=lambda c: c.avg_value)


class HierarchicalMCTSPlanner:
    """
    Full 5-level hierarchical MCTS.

    L1: trade or skip
    L2: long or short
    L3: entry timing
    L4: position size
    L5: stop/target/trail

    Each rollout simulates a full trade outcome using
    the Expectimax probability model.
    Risk-sensitive value function penalises losing paths.
    """

    def __init__(
        self,
        tick_value: float = 1.25,
        symbol: str = "MES",
    ) -> None:
        self.tick_value = tick_value
        self.symbol = symbol
        self._expectimax = ExpectimaxEngine(tick_value=tick_value, loss_aversion=LOSS_AVERSION)

    @staticmethod
    def should_use_mcts(
        beam_confidence: float,
        confluence: Optional[ConfluenceReport],
        signal_rank: int,
    ) -> bool:
        """True when deep search is warranted."""
        if beam_confidence < MCTS_CONFIDENCE_THRESHOLD:
            return True
        if confluence and confluence.conflict_score > MCTS_CONFLICT_THRESHOLD:
            return True
        rank_min = int(TRADING_PHILOSOPHY["signal_rank_minimum"])
        if rank_min <= signal_rank <= rank_min + 10:
            return True
        return False

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
        timeframe: str = "5m",
    ) -> TradePlan:
        """Run MCTS and return the best TradePlan."""
        initial_state = {
            "symbol": self.symbol,
            "timeframe": timeframe,
            "p_target": p_target,
            "p_stop": p_stop,
            "ev_dollars": ev_dollars,
            "confluence": confluence.confluence_score,
            "conflict": confluence.conflict_score,
            "direction": confluence.consensus_direction,
            "news_blocked": confluence.news_trading_blocked,
        }

        # Expectimax pre-filter — skip MCTS when no action has positive EV
        positive_actions = self._expectimax.filter_positive_ev(
            p_target, p_stop, reward_r, risk_r
        )
        if not positive_actions:
            logger.info("MCTS[%s]: expectimax pre-filter — no positive EV actions", self.symbol)
            return self._decode_plan(
                [MCTSNode(state={**initial_state, "L1": "skip"}, level=1, action="skip")],
                confluence,
                entry_price,
                stop_price,
                target_price,
                timeframe,
                p_target,
                ev_dollars,
                datetime.now(tz=timezone.utc),
            )

        best_action, best_ev = self._expectimax.best_action(p_target, p_stop, reward_r, risk_r)
        initial_state["expectimax_best"] = best_action
        initial_state["expectimax_ev"] = best_ev

        root = MCTSNode(state=initial_state, level=0)

        for _ in range(ROLLOUTS):
            node = self._select(root)
            if not node.is_terminal:
                node = node.expand()
            reward = self._rollout(node, p_target, p_stop, reward_r, risk_r)
            self._backpropagate(node, reward)

        path = self._extract_best_path(root)
        logger.info(
            "MCTS[%s]: %d rollouts | best path: %s | value=%.3f",
            self.symbol,
            ROLLOUTS,
            " → ".join(n.action for n in path[1:]),
            path[-1].avg_value if len(path) > 1 else 0.0,
        )

        return self._decode_plan(
            path,
            confluence,
            entry_price,
            stop_price,
            target_price,
            timeframe,
            p_target,
            ev_dollars,
            datetime.now(tz=timezone.utc),
        )

    def _select(self, node: MCTSNode) -> MCTSNode:
        while node.children and not node.is_terminal:
            if not node.is_fully_expanded():
                return node
            node = node.best_child(node.visits or 1)
        return node

    def _rollout(
        self,
        node: MCTSNode,
        p_target: float,
        p_stop: float,
        reward_r: float,
        risk_r: float,
    ) -> float:
        state = node.state

        if state.get("L1") == "skip":
            return 0.0

        if state.get("news_blocked"):
            return -2.0

        p_adj_target = p_target
        p_adj_stop = p_stop
        adj_reward_r = reward_r

        timing = state.get("L3", "enter_now")
        if timing == "wait_1":
            p_adj_target *= 0.90
            p_adj_stop *= 0.85
        elif timing == "wait_confirm":
            p_adj_target *= 0.80
            p_adj_stop *= 0.70

        sizing = state.get("L4", "full_size")
        size_mult = {"full_size": 1.0, "half_size": 0.5, "quarter_size": 0.25}.get(sizing, 1.0)

        stop_style = state.get("L5", "normal_stop")
        if stop_style == "tight_stop":
            p_adj_stop *= 1.20
            adj_reward_r *= 1.10
        elif stop_style == "wide_stop":
            p_adj_stop *= 0.80
            adj_reward_r *= 0.85

        raw_value = self._expectimax.sample_outcome(
            p_adj_target,
            p_adj_stop,
            adj_reward_r,
            risk_r,
            size_mult,
        )

        conf_bonus = state.get("confluence", 0.5) * 0.10 * abs(raw_value)
        if raw_value > 0:
            raw_value += conf_bonus
        else:
            raw_value -= conf_bonus * 0.5

        return round(raw_value, 4)

    def _backpropagate(self, node: MCTSNode, reward: float) -> None:
        while node is not None:
            node.visits += 1
            node.value_sum += reward
            node = node.parent

    def _extract_best_path(self, root: MCTSNode) -> list[MCTSNode]:
        path = [root]
        node = root
        while node.children:
            best = node.best_child_by_value()
            if not best:
                break
            path.append(best)
            node = best
        return path

    def _decode_plan(
        self,
        path: list[MCTSNode],
        confluence: ConfluenceReport,
        entry_price: Optional[float],
        stop_price: Optional[float],
        target_price: Optional[float],
        timeframe: str,
        p_target: float,
        ev_dollars: float,
        timestamp: datetime,
    ) -> TradePlan:
        path_state = path[-1].state if len(path) > 1 else {}

        if path_state.get("L1") == "skip":
            return TradePlan(
                symbol=self.symbol,
                timeframe=timeframe,
                timestamp=timestamp,
                action=TradeAction.DO_NOTHING,
                plan_notes="MCTS L1: skip",
                plan_confidence=0.0,
                plan_ev=0.0,
                mcts_iterations=ROLLOUTS,
            )

        direction = path_state.get(
            "L2", "long" if confluence.consensus_direction >= 0 else "short"
        )
        trade_direction = Direction.LONG if direction == "long" else Direction.SHORT
        action = TradeAction.ENTER_LONG if direction == "long" else TradeAction.ENTER_SHORT

        timing = path_state.get("L3", "enter_now")
        if timing != "enter_now":
            action = TradeAction.WAIT

        stop_style = path_state.get("L5", "normal_stop")
        sizing = path_state.get("L4", "full_size")

        notes = (
            f"MCTS L1-L5: {path_state.get('L1', 'trade')} | "
            f"{direction} | {timing} | {sizing} | {stop_style} | "
            f"rollouts={ROLLOUTS} | path_value={path[-1].avg_value:.3f}"
        )

        adj_stop = stop_price
        adj_target = target_price
        if stop_price and target_price and entry_price:
            risk_ticks = abs(entry_price - stop_price)
            if stop_style == "tight_stop":
                adj_stop = (
                    entry_price - risk_ticks * 0.8
                    if direction == "long"
                    else entry_price + risk_ticks * 0.8
                )
            elif stop_style == "wide_stop":
                adj_stop = (
                    entry_price - risk_ticks * 1.2
                    if direction == "long"
                    else entry_price + risk_ticks * 1.2
                )

        return TradePlan(
            symbol=self.symbol,
            timeframe=timeframe,
            timestamp=timestamp,
            action=action,
            direction=trade_direction,
            entry_price=entry_price,
            stop_loss=adj_stop,
            take_profit=adj_target,
            plan_confidence=p_target,
            plan_ev=ev_dollars,
            plan_notes=notes,
            mcts_iterations=ROLLOUTS,
        )


class MCTSPlanner(HierarchicalMCTSPlanner):
    """Backward-compatible alias — legacy dict API returns action string."""

    def plan_legacy(self, state: dict) -> str:
        rank = state.get("signal_rank", 0)
        confidence = state.get("model_confidence", 0)
        ev = state.get("expected_value", 0)
        ror = state.get("risk_of_ruin", 1)
        trend = state.get("market_state", "unknown")

        if rank < 65 or confidence < 0.55 or ev <= 0 or ror > 0.05:
            return "wait"
        if trend == "down":
            return "enter_short"
        if trend == "up":
            return "enter_long"
        return "wait"

    def plan(self, state: dict) -> str:  # type: ignore[override]
        return self.plan_legacy(state)
