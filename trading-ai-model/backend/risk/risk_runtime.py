"""Shared RiskEngine instance for risk agent and learning loop."""

from __future__ import annotations

from typing import Optional

from risk.risk_engine import RiskEngine

_engine: Optional[RiskEngine] = None


def get_risk_engine(account_size: float = 10_000.0) -> RiskEngine:
    global _engine
    if _engine is None:
        _engine = RiskEngine(account_size=account_size)
    return _engine


def reset_risk_engine() -> None:
    """Clear singleton — for tests."""
    global _engine
    _engine = None
