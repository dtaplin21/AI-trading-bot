"""Pydantic schema for risk decisions."""

from typing import Optional

from pydantic import BaseModel


class RiskDecision(BaseModel):
    approved: bool
    reason: Optional[str] = None
    max_position_size: float = 0.0
    symbol: Optional[str] = None
