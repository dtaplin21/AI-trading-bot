"""mcts/policy_network.py"""
from __future__ import annotations

from mcts.action_space import ACTIONS


class PolicyNetwork:
    """
    Returns prior probabilities over available actions.
    Used by MCTS to guide search toward promising moves.
    """

    def get_priors(self, state: dict) -> dict[str, float]:
        """
        Returns dict of {action: prior_probability}.
        Probabilities sum to 1.0.
        """
        rev_prob = state.get("reversal_prob", 0.5)
        r_mult = state.get("unrealized_pnl", 0) / (state.get("risk_amount", 1) + 1e-10)
        time_elapsed = state.get("bars_elapsed", 0) / (state.get("max_bars", 20) + 1)

        priors: dict[str, float] = {}
        for action in ACTIONS:
            if action == "wait":
                priors[action] = 0.4 * (1 - time_elapsed) * max(0.5, rev_prob)
            elif action == "exit":
                loss_signal = max(0, -r_mult * 0.2)
                priors[action] = 0.3 * time_elapsed + loss_signal
            elif action == "partial_profit":
                priors[action] = 0.15 * max(0, r_mult * 0.1)
            elif action == "trail_stop":
                priors[action] = 0.1 * max(0, r_mult - 1) * 0.5
            elif action == "scale_in":
                priors[action] = 0.05 * max(0, rev_prob - 0.7)
            elif action == "enter_now":
                priors[action] = 0.05 * max(0, rev_prob - 0.6)
            elif action == "do_nothing":
                priors[action] = 0.02
            else:
                priors[action] = 0.01

        total = sum(priors.values()) + 1e-10
        return {a: round(p / total, 4) for a, p in priors.items()}
