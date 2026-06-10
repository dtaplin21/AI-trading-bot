"""mcts/tree_node.py"""
from __future__ import annotations

import math
from typing import Optional


class TreeNode:
    """
    Node in the MCTS search tree for trade planning.
    Each node represents one possible trade decision state.
    """

    __slots__ = (
        "state",
        "action",
        "parent",
        "children",
        "visits",
        "value",
        "prior",
        "is_terminal",
    )

    def __init__(
        self,
        state,
        action=None,
        parent: Optional["TreeNode"] = None,
        prior: float = 1.0,
    ):
        self.state = state
        self.action = action
        self.parent = parent
        self.children: list[TreeNode] = []
        self.visits: int = 0
        self.value: float = 0.0
        self.prior: float = prior
        self.is_terminal: bool = False

    @property
    def q_value(self) -> float:
        """Mean value estimate."""
        return self.value / self.visits if self.visits > 0 else 0.0

    def ucb_score(self, c_puct: float = 1.41) -> float:
        """Upper Confidence Bound score for node selection."""
        if self.visits == 0:
            return float("inf")
        parent_visits = self.parent.visits if self.parent else 1
        exploration = c_puct * self.prior * math.sqrt(parent_visits) / (1 + self.visits)
        return self.q_value + exploration

    def is_leaf(self) -> bool:
        return len(self.children) == 0

    def best_child(self, c_puct: float = 1.41) -> Optional["TreeNode"]:
        if not self.children:
            return None
        return max(self.children, key=lambda n: n.ucb_score(c_puct))

    def best_action(self) -> Optional[object]:
        """Return action of most-visited child (exploitation)."""
        if not self.children:
            return None
        return max(self.children, key=lambda n: n.visits).action

    def update(self, value: float) -> None:
        self.visits += 1
        self.value += value

    def __repr__(self) -> str:
        return (
            f"TreeNode(action={self.action}, visits={self.visits}, "
            f"Q={self.q_value:.3f}, children={len(self.children)})"
        )
