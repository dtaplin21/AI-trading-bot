"""Shared LearningAgent instance for paper trader and supervisor."""

from __future__ import annotations

from typing import Optional

from learning.learning_agent import LearningAgent
from pipeline.world_state_runtime import get_world_state_store
from risk.risk_runtime import get_risk_engine

_agent: Optional[LearningAgent] = None


def get_learning_agent() -> LearningAgent:
    global _agent
    if _agent is None:
        _agent = LearningAgent(
            world_store=get_world_state_store(),
            risk_engine=get_risk_engine(),
        )
    return _agent


def reset_learning_agent() -> None:
    """Clear singleton — for tests."""
    global _agent
    _agent = None
