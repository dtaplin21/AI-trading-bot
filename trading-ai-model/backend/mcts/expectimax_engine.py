"""
mcts/expectimax_engine.py

Expectimax — probability-weighted outcome tree.

Chosen over minimax because markets are NOT adversarial.
The market does not try to beat you. It is random with structure.
Expectimax models that correctly:

  For each action:
    Expected value = sum(P(outcome_i) * value(outcome_i))

Three outcomes per trade path:
  - Target hit     (prob from LightGBM + Monte Carlo)
  - Stop hit       (prob from LightGBM + Monte Carlo)
  - Chop/timeout   (prob = 1 - target - stop)

This is called BEFORE MCTS for a fast pre-filter.
MCTS does deep search. Expectimax does wide shallow scoring.

Env:
  ANTHROPIC_API_KEY — used if LLM explanation of action is requested
  LOSS_AVERSION_MULTIPLIER (default 2.0, from TRADING_PHILOSOPHY)
"""
from __future__ import annotations

import logging
import os
import random
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class OutcomeNode:
    """One possible outcome of one action."""

    label: str  # "target" | "stop" | "chop"
    probability: float  # P(this outcome)
    value: float  # Reward if this outcome occurs
    ev: float  # probability * value


@dataclass
class ActionNode:
    """
    One possible action with all its outcome branches.
    The expected value is the probability-weighted sum of outcomes.
    """

    action: str
    outcomes: list[OutcomeNode] = field(default_factory=list)
    expected_value: float = 0.0
    risk_adjusted_ev: float = 0.0  # EV penalised for downside risk

    def compute_ev(self, loss_aversion: float = 2.0) -> None:
        self.expected_value = sum(o.ev for o in self.outcomes)
        raw = 0.0
        for o in self.outcomes:
            if o.value < 0:
                raw += o.probability * o.value * loss_aversion
            else:
                raw += o.probability * o.value
        self.risk_adjusted_ev = round(raw, 4)
        self.expected_value = round(self.expected_value, 4)


class ExpectimaxEngine:
    """
    Scores every possible action by expected value under uncertainty.

    Called by TradePlanningAgent before MCTS to quickly eliminate
    actions with negative expected value, narrowing the MCTS search.

    Inputs:
      p_target   — P(price hits take-profit before stop)
      p_stop     — P(price hits stop-loss first)
      reward_r   — R-multiple if target hit  (e.g. 2.0)
      risk_r     — R-multiple if stop hit    (e.g. -1.0)
      tick_value — Dollar value per tick for this symbol
    """

    def __init__(
        self,
        tick_value: float = 1.25,
        loss_aversion: float = 2.0,
    ) -> None:
        self.tick_value = tick_value
        self.loss_aversion = float(os.getenv("LOSS_AVERSION_MULTIPLIER", str(loss_aversion)))

    def score_actions(
        self,
        p_target: float,
        p_stop: float,
        reward_r: float = 2.0,
        risk_r: float = 1.0,
        entry_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        target_price: Optional[float] = None,
    ) -> list[ActionNode]:
        """
        Scores all possible actions using expectimax.
        Returns list sorted by risk_adjusted_ev descending.
        """
        p_target = max(0.0, min(1.0, p_target))
        p_stop = max(0.0, min(1.0, p_stop))
        p_chop = max(0.0, 1.0 - p_target - p_stop)

        target_dollars = reward_r * 100 * self.tick_value
        stop_dollars = -(risk_r * 100 * self.tick_value)
        chop_dollars = -(0.20 * 100 * self.tick_value)

        actions: list[ActionNode] = []

        enter = ActionNode(
            action="enter_full",
            outcomes=[
                OutcomeNode("target", p_target, target_dollars, p_target * target_dollars),
                OutcomeNode("stop", p_stop, stop_dollars, p_stop * stop_dollars),
                OutcomeNode("chop", p_chop, chop_dollars, p_chop * chop_dollars),
            ],
        )
        enter.compute_ev(self.loss_aversion)
        actions.append(enter)

        half = ActionNode(
            action="enter_half",
            outcomes=[
                OutcomeNode(
                    "target",
                    p_target,
                    target_dollars * 0.5,
                    p_target * target_dollars * 0.5,
                ),
                OutcomeNode(
                    "stop", p_stop, stop_dollars * 0.5, p_stop * stop_dollars * 0.5
                ),
                OutcomeNode(
                    "chop", p_chop, chop_dollars * 0.5, p_chop * chop_dollars * 0.5
                ),
            ],
        )
        half.compute_ev(self.loss_aversion)
        actions.append(half)

        wait = ActionNode(
            action="wait",
            outcomes=[
                OutcomeNode(
                    "missed_move",
                    p_target * 0.4,
                    target_dollars * 0.5,
                    p_target * 0.4 * target_dollars * 0.5,
                ),
                OutcomeNode(
                    "better_entry",
                    p_target * 0.3,
                    target_dollars * 1.1,
                    p_target * 0.3 * target_dollars * 1.1,
                ),
                OutcomeNode(
                    "no_setup",
                    1.0 - p_target * 0.7,
                    -5.0,
                    (1.0 - p_target * 0.7) * -5.0,
                ),
            ],
        )
        wait.compute_ev(self.loss_aversion)
        actions.append(wait)

        nothing = ActionNode(
            action="do_nothing",
            outcomes=[OutcomeNode("no_trade", 1.0, 0.0, 0.0)],
        )
        nothing.compute_ev(self.loss_aversion)
        actions.append(nothing)

        actions.sort(key=lambda a: a.risk_adjusted_ev, reverse=True)

        for a in actions:
            logger.debug(
                "Expectimax: %s → EV=$%.2f risk_adj=$%.2f",
                a.action,
                a.expected_value,
                a.risk_adjusted_ev,
            )

        return actions

    def best_action(
        self,
        p_target: float,
        p_stop: float,
        reward_r: float = 2.0,
        risk_r: float = 1.0,
    ) -> tuple[str, float]:
        """Returns (best_action_name, risk_adjusted_ev)."""
        actions = self.score_actions(p_target, p_stop, reward_r, risk_r)
        best = actions[0]
        return best.action, best.risk_adjusted_ev

    def filter_positive_ev(
        self,
        p_target: float,
        p_stop: float,
        reward_r: float = 2.0,
        risk_r: float = 1.0,
    ) -> list[ActionNode]:
        """Returns only actions with positive risk-adjusted EV."""
        all_actions = self.score_actions(p_target, p_stop, reward_r, risk_r)
        return [a for a in all_actions if a.risk_adjusted_ev > 0]

    def expected_value(
        self,
        p_target: float,
        p_stop: float,
        reward_r: float = 2.0,
        risk_r: float = 1.0,
        size_mult: float = 1.0,
    ) -> float:
        """Risk-adjusted EV for full-size entry — used by MCTS rollouts."""
        actions = self.score_actions(p_target, p_stop, reward_r, risk_r)
        enter = next((a for a in actions if a.action == "enter_full"), actions[0])
        if size_mult != 1.0:
            return round(enter.risk_adjusted_ev * size_mult, 4)
        return enter.risk_adjusted_ev

    def sample_outcome(
        self,
        p_target: float,
        p_stop: float,
        reward_r: float = 2.0,
        risk_r: float = 1.0,
        size_mult: float = 1.0,
        roll: float | None = None,
    ) -> float:
        """Single stochastic outcome for MCTS rollout simulation."""
        p_target = max(0.0, min(1.0, p_target))
        p_stop = max(0.0, min(1.0, p_stop))
        p_chop = max(0.0, 1.0 - p_target - p_stop)

        target_dollars = reward_r * 100 * self.tick_value * size_mult
        stop_dollars = -(risk_r * 100 * self.tick_value * size_mult) * self.loss_aversion
        chop_dollars = -(0.20 * 100 * self.tick_value * size_mult)

        r = random.random() if roll is None else roll
        if r < p_target:
            return target_dollars
        if r < p_target + p_stop:
            return stop_dollars
        return chop_dollars
