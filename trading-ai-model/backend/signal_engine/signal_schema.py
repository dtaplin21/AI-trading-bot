"""Pydantic schema for signal output."""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class SignalStatus(str, Enum):
    REJECTED = "rejected"
    WATCH = "watch"
    PAPER_TRADE_CANDIDATE = "paper_trade_candidate"
    LIVE_CANDIDATE = "live_candidate"


class TradingSignal(BaseModel):
    symbol: str
    setup: str
    candlestick_confirmation: Optional[str] = None
    body_to_range_ratio: Optional[float] = None
    lower_wick_ratio: Optional[float] = None
    close_location: Optional[float] = None
    harmonic_pattern: Optional[str] = None
    xab_ratio: Optional[float] = None
    abc_ratio: Optional[float] = None
    bcd_ratio: Optional[float] = None
    harmonic_completion_score: Optional[float] = None
    elliott_state: Optional[str] = None
    wave_3_probability: Optional[float] = None
    wave_5_probability: Optional[float] = None
    abc_correction_probability: Optional[float] = None
    gann_confluence: Optional[bool] = None
    gann_angle_distance: Optional[float] = None
    fib_level: Optional[str] = None
    number_zone: Optional[str] = None
    near_369_level: Optional[bool] = None
    fractal_down: Optional[bool] = None
    markov_next_state: Optional[str] = None
    markov_next_state_probability: Optional[float] = None
    price_action_confluence_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    reversal_confluence_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    historical_sample_size: Optional[int] = None
    expected_value: Optional[float] = None
    signal_rank: int = Field(..., ge=0, le=100)
    risk_approved: bool = False
    status: SignalStatus = SignalStatus.WATCH
