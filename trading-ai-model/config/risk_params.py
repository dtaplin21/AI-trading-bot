"""Risk limits, drawdown caps, and sizing rules."""

from dataclasses import dataclass

from config.settings import get_settings


@dataclass(frozen=True)
class RiskLimits:
    max_daily_loss_pct: float
    max_drawdown_pct: float
    max_positions: int
    default_risk_per_trade_pct: float
    max_correlation_exposure: float = 0.7


def get_risk_limits() -> RiskLimits:
    s = get_settings()
    return RiskLimits(
        max_daily_loss_pct=s.max_daily_loss_pct,
        max_drawdown_pct=s.max_drawdown_pct,
        max_positions=s.max_positions,
        default_risk_per_trade_pct=s.default_risk_per_trade_pct,
    )
