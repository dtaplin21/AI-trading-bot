"""Load Coinbase / retail risk caps from environment."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_float(key: str, default: float | None = None) -> float | None:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    return float(raw)


@dataclass(frozen=True)
class CoinbaseRiskProfile:
    """Defaults for primary-account Coinbase production guardrails."""

    account_cap_usd: float = 500.0
    max_daily_loss_usd: float = 30.0
    max_order_notional_usd: float = 50.0
    max_open_positions: int = 2

    @property
    def max_daily_loss_pct(self) -> float:
        if self.account_cap_usd <= 0:
            return 0.06
        return self.max_daily_loss_usd / self.account_cap_usd


def load_coinbase_risk_profile() -> CoinbaseRiskProfile:
    cap = _env_float("RISK_ACCOUNT_CAP_USD", 500.0) or 500.0
    daily = _env_float("RISK_MAX_DAILY_LOSS_USD", 30.0) or 30.0
    order = _env_float("RISK_MAX_ORDER_NOTIONAL_USD", min(50.0, cap * 0.1)) or min(50.0, cap * 0.1)
    max_open = int(os.getenv("RISK_MAX_OPEN_POSITIONS", "2"))
    return CoinbaseRiskProfile(
        account_cap_usd=cap,
        max_daily_loss_usd=daily,
        max_order_notional_usd=order,
        max_open_positions=max_open,
    )
