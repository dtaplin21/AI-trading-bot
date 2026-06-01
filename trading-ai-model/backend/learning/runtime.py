"""Shared LearningAgent instance for paper trader and supervisor."""

from __future__ import annotations

from typing import Optional

from learning.learning_agent import LearningAgent
from pipeline.world_state_runtime import get_world_state_store
from risk.risk_runtime import get_risk_engine

_agent: Optional[LearningAgent] = None


def reload_production_model(model_id: str) -> None:
    """Default callback after promotion — refresh in-process classifiers."""
    from ml.models.lightgbm_classifier import LightGBMSignalClassifier

    LightGBMSignalClassifier.reload_singleton()
    logger = __import__("logging").getLogger(__name__)
    logger.info("LearningAgent: reloaded production model (%s)", model_id)


def get_learning_agent() -> LearningAgent:
    global _agent
    if _agent is None:
        _agent = LearningAgent(
            world_store=get_world_state_store(),
            risk_engine=get_risk_engine(),
            on_model_reload=reload_production_model,
        )
    return _agent


def reset_learning_agent() -> None:
    """Clear singleton — for tests."""
    global _agent
    _agent = None
