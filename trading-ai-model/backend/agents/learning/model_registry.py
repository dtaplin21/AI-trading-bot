"""Re-exports — canonical registry lives in ml.registry.model_registry."""

from enum import Enum

from ml.registry.model_registry import ModelRecord, ModelRegistry

__all__ = ["ModelRecord", "ModelRegistry", "ModelStage", "PROMOTION_FLOW"]


class ModelStage(str, Enum):
    CANDIDATE = "candidate"
    VALIDATED = "validated"
    PAPER_TEST = "paper_test"
    APPROVED = "approved"
    PRODUCTION = "production"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"
    ROLLED_BACK = "rolled_back"


PROMOTION_FLOW = [
    ModelStage.CANDIDATE,
    ModelStage.VALIDATED,
    ModelStage.PAPER_TEST,
    ModelStage.APPROVED,
    ModelStage.PRODUCTION,
]
