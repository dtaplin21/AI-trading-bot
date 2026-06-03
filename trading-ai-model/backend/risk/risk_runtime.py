"""Shared RiskEngine instance for risk agent and learning loop."""

from __future__ import annotations

from typing import Optional

from risk.risk_engine import RiskEngine, default_account_size

_engine: Optional[RiskEngine] = None


def get_risk_engine(account_size: float | None = None) -> RiskEngine:
    global _engine
    if _engine is None:
        size = account_size if account_size is not None else default_account_size()
        _engine = RiskEngine(account_size=size)
    return _engine


def reset_risk_engine() -> None:
    """Clear singleton — for tests."""
    global _engine
    _engine = None
