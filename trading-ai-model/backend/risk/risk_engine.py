"""Master risk approval/rejection engine."""

from dataclasses import dataclass
from enum import Enum

from config.risk_params import RiskLimits, get_risk_limits
from risk.risk_approval_schema import RiskDecision


class RejectionReason(str, Enum):
    DAILY_LOSS_LIMIT = "daily_loss_limit"
    DRAWDOWN_LIMIT = "drawdown_limit"
    MAX_POSITIONS = "max_positions"
    CORRELATION = "correlation_limit"
    LOW_SIGNAL_RANK = "low_signal_rank"


@dataclass
class PortfolioState:
    daily_pnl_pct: float = 0.0
    current_drawdown_pct: float = 0.0
    open_positions: int = 0
    correlated_exposure: float = 0.0


class RiskEngine:
    """Non-negotiable risk gate before paper or live execution."""

    MIN_SIGNAL_RANK = 50

    def __init__(self, limits: RiskLimits | None = None):
        self.limits = limits or get_risk_limits()

    def evaluate(
        self,
        signal_rank: int,
        portfolio: PortfolioState,
        symbol: str | None = None,
    ) -> RiskDecision:
        if portfolio.daily_pnl_pct <= -self.limits.max_daily_loss_pct:
            return RiskDecision(
                approved=False,
                reason=RejectionReason.DAILY_LOSS_LIMIT.value,
                max_position_size=0.0,
            )
        if portfolio.current_drawdown_pct >= self.limits.max_drawdown_pct:
            return RiskDecision(
                approved=False,
                reason=RejectionReason.DRAWDOWN_LIMIT.value,
                max_position_size=0.0,
            )
        if portfolio.open_positions >= self.limits.max_positions:
            return RiskDecision(
                approved=False,
                reason=RejectionReason.MAX_POSITIONS.value,
                max_position_size=0.0,
            )
        if portfolio.correlated_exposure >= self.limits.max_correlation_exposure:
            return RiskDecision(
                approved=False,
                reason=RejectionReason.CORRELATION.value,
                max_position_size=0.0,
            )
        if signal_rank < self.MIN_SIGNAL_RANK:
            return RiskDecision(
                approved=False,
                reason=RejectionReason.LOW_SIGNAL_RANK.value,
                max_position_size=0.0,
            )

        size_pct = self.limits.default_risk_per_trade_pct
        if signal_rank >= 85:
            size_pct *= 1.25
        elif signal_rank < 65:
            size_pct *= 0.75

        return RiskDecision(
            approved=True,
            reason=None,
            max_position_size=size_pct,
            symbol=symbol,
        )
