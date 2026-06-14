"""
risk/risk_engine.py

Full Risk Engine — replaces partial implementation.

Audit findings fixed:
  - Kill switch was schema-only, not enforced → now enforced
  - DrawdownMonitor/DailyLossLimiter existed but not wired → wired
  - All 4 probability gate values now unified via ProbabilityGate
  - All thresholds read from env vars with TRADING_PHILOSOPHY fallback

Env vars (all optional — fall back to agent_config.py):
  RISK_ACCOUNT_CAP_USD        (float, optional — sets account size, e.g. 500 for Coinbase)
  RISK_MAX_DAILY_LOSS_USD       (float, optional — absolute daily stop, e.g. 30)
  RISK_MAX_DAILY_LOSS_PCT     (float, default 0.02)
  RISK_MAX_DRAWDOWN_PCT       (float, default 0.06)
  RISK_MAX_ORDER_NOTIONAL_USD (float, optional — max single order size in USD)
  RISK_MAX_CONTRACTS          (int,   default 5)
  RISK_MAX_OPEN_POSITIONS     (int,   default 3)
  RISK_MIN_RR_RATIO           (float, default 1.5)
  RISK_MAX_CONSECUTIVE_LOSSES (int,   default 4)
  RISK_KILL_SWITCH            (bool,  default false)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from config.agent_config import TRADING_PHILOSOPHY
from pipeline.confluence_report import ConfluenceReport
from pipeline.probability_gate import ProbabilityGate
from pipeline.schemas import FusedFeatureSet, RiskDecision, TradeAction, TradePlan
from pipeline.level_setup import LevelSetup
from risk.correlation_checker import get_correlation_checker
from risk.kill_switch_runtime import is_kill_switch_active

logger = logging.getLogger(__name__)


def _open_position_symbols() -> list[str]:
    try:
        from paper_trading.position_book import get_position_book

        return get_position_book().open_symbols()
    except Exception:
        return []


def _env_float(key: str, default: float) -> float:
    return float(os.getenv(key, str(default)))


def _env_int(key: str, default: int) -> int:
    return int(os.getenv(key, str(default)))


def _env_bool(key: str, default: bool) -> bool:
    return os.getenv(key, str(default)).lower() in ("true", "1", "yes")


@dataclass
class PortfolioState:
    """Portfolio snapshot passed through the pipeline supervisor."""

    daily_pnl_pct: float = 0.0
    current_drawdown_pct: float = 0.0
    open_positions: int = 0
    correlated_exposure: float = 0.0
    account_size: float = 10_000.0


def _env_optional_float(key: str) -> Optional[float]:
    raw = os.getenv(key, "").strip()
    if not raw:
        return None
    return float(raw)


def default_account_size() -> float:
    cap = _env_optional_float("RISK_ACCOUNT_CAP_USD")
    if cap is not None:
        return cap
    return _env_float("RISK_ACCOUNT_SIZE", 10_000.0)


class DailyLossLimiter:
    """Tracks realised P&L and enforces the daily loss cap (USD or % of account)."""

    def __init__(
        self,
        max_loss_pct: float,
        account_size: float = 10_000.0,
        max_loss_usd: Optional[float] = None,
    ) -> None:
        self._max_loss_pct = max_loss_pct
        self._max_loss_usd = max_loss_usd
        self._account_size = account_size
        self._daily_pnl: float = 0.0
        self._trade_date: Optional[str] = None

    def sync_from_portfolio(self, daily_pnl_pct: float, account_size: float) -> None:
        """Hydrate from supervisor portfolio state (percent points, e.g. -3.0 = -3%)."""
        self._account_size = account_size
        self._daily_pnl = (daily_pnl_pct / 100.0) * account_size
        self._trade_date = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    def record_trade(self, pnl: float) -> None:
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        if self._trade_date != today:
            self._daily_pnl = 0.0
            self._trade_date = today
        self._daily_pnl += pnl

    def daily_loss_limit_usd(self) -> float:
        if self._max_loss_usd is not None:
            return self._max_loss_usd
        return self._account_size * self._max_loss_pct

    def is_limit_hit(self) -> tuple[bool, float]:
        limit = self.daily_loss_limit_usd()
        hit = self._daily_pnl <= -limit
        remaining_pct = max(0.0, (limit + self._daily_pnl) / self._account_size)
        return hit, remaining_pct


class DrawdownMonitor:
    """Tracks peak equity and current drawdown."""

    def __init__(self, max_drawdown_pct: float, account_size: float = 10_000.0) -> None:
        self._max_dd_pct = max_drawdown_pct
        self._peak_equity = account_size
        self._current = account_size

    def sync_from_portfolio(
        self, current_drawdown_pct: float, account_size: float
    ) -> None:
        """Hydrate from supervisor portfolio state (percent points)."""
        dd_frac = current_drawdown_pct / 100.0
        self._peak_equity = account_size
        self._current = account_size * (1.0 - dd_frac)

    def update(self, pnl: float) -> None:
        self._current += pnl
        self._peak_equity = max(self._peak_equity, self._current)

    def current_drawdown_pct(self) -> float:
        if self._peak_equity <= 0:
            return 0.0
        return (self._peak_equity - self._current) / self._peak_equity

    def is_limit_hit(self) -> bool:
        return self.current_drawdown_pct() >= self._max_dd_pct


class RiskEngine:
    """
    Final veto gate. No trade passes without approval here.
    Called after TradePlanningAgent. Cannot be overridden.
    """

    def __init__(self, account_size: float | None = None) -> None:
        self._account_size = account_size if account_size is not None else default_account_size()
        self._account_cap_usd = _env_optional_float("RISK_ACCOUNT_CAP_USD") or self._account_size
        self._max_order_notional = _env_optional_float("RISK_MAX_ORDER_NOTIONAL_USD")
        self._max_daily_loss_usd = _env_optional_float("RISK_MAX_DAILY_LOSS_USD")

        self._max_daily_loss = _env_float(
            "RISK_MAX_DAILY_LOSS_PCT", float(TRADING_PHILOSOPHY["daily_loss_stop_pct"])
        )
        if self._max_daily_loss_usd is not None and self._account_size > 0:
            self._max_daily_loss = self._max_daily_loss_usd / self._account_size
        self._max_drawdown = _env_float(
            "RISK_MAX_DRAWDOWN_PCT", float(TRADING_PHILOSOPHY["max_drawdown_pct"])
        )
        self._max_contracts = _env_int(
            "RISK_MAX_CONTRACTS", int(TRADING_PHILOSOPHY["max_contracts_per_trade"])
        )
        self._max_open = _env_int(
            "RISK_MAX_OPEN_POSITIONS", int(TRADING_PHILOSOPHY["max_open_positions"])
        )
        self._min_rr = _env_float("RISK_MIN_RR_RATIO", float(TRADING_PHILOSOPHY["min_rr_ratio"]))
        self._max_consec_losses = _env_int(
            "RISK_MAX_CONSECUTIVE_LOSSES", int(TRADING_PHILOSOPHY["consecutive_loss_limit"])
        )

        self._daily_limiter = DailyLossLimiter(
            self._max_daily_loss,
            self._account_size,
            max_loss_usd=self._max_daily_loss_usd,
        )
        self._drawdown_monitor = DrawdownMonitor(self._max_drawdown, self._account_size)
        self._consecutive_losses = 0
        self._open_positions = 0
        self._gate = ProbabilityGate()
        self._correlation = get_correlation_checker()

        daily_desc = (
            f"${self._max_daily_loss_usd:.0f}/day"
            if self._max_daily_loss_usd is not None
            else f"{self._max_daily_loss * 100:.1f}%"
        )
        logger.info(
            "RiskEngine: account=$%.0f cap=$%.0f daily_loss=%s drawdown=%.1f%% "
            "max_contracts=%d min_rr=%.1f max_consec=%d",
            self._account_size,
            self._account_cap_usd,
            daily_desc,
            self._max_drawdown * 100,
            self._max_contracts,
            self._min_rr,
            self._max_consec_losses,
        )

    def sync_portfolio(self, portfolio: PortfolioState) -> None:
        """Apply external portfolio state before checks."""
        self._account_size = portfolio.account_size
        self._open_positions = portfolio.open_positions
        self._daily_limiter.sync_from_portfolio(portfolio.daily_pnl_pct, portfolio.account_size)
        self._drawdown_monitor.sync_from_portfolio(
            portfolio.current_drawdown_pct, portfolio.account_size
        )

    def approve(
        self,
        plan: TradePlan,
        fused: FusedFeatureSet,
        confluence: ConfluenceReport,
        p_success: float,
        ev_dollars: float,
        sample_size: int,
        signal_rank: int,
    ) -> RiskDecision:
        """Run all risk checks. Returns RiskDecision with approved=True only if all pass."""
        rejections: list[str] = []

        kill_active = is_kill_switch_active()
        if kill_active:
            rejections.append(
                "KILL SWITCH ACTIVE — disable via dashboard or set RISK_KILL_SWITCH=false"
            )

        news_blocked = getattr(fused, "news_trading_blocked", False) or confluence.news_trading_blocked
        if news_blocked:
            reason = confluence.news_risk_reason or getattr(fused, "news_risk_reason", "news risk active")
            rejections.append(f"News block: {reason}")

        gate_result = self._gate.check(p_success, ev_dollars, sample_size, signal_rank)
        if not gate_result.passed:
            rejections.extend(gate_result.failures)

        daily_hit, remaining_pct = self._daily_limiter.is_limit_hit()
        if daily_hit:
            limit_usd = self._daily_limiter.daily_loss_limit_usd()
            rejections.append(
                f"Daily loss limit hit (${limit_usd:.0f}). Trading suspended for today."
            )

        current_dd = self._drawdown_monitor.current_drawdown_pct()
        if self._drawdown_monitor.is_limit_hit():
            rejections.append(
                f"Max drawdown {current_dd:.1%} >= limit {self._max_drawdown:.0%}."
            )

        if self._consecutive_losses >= self._max_consec_losses:
            rejections.append(
                f"{self._consecutive_losses} consecutive losses — forced pause."
            )

        if self._open_positions >= self._max_open:
            rejections.append(f"Max open positions ({self._max_open}) reached.")

        rr_ratio = 0.0
        if plan.stop_loss and plan.take_profit and plan.entry_price:
            risk = abs(plan.entry_price - plan.stop_loss)
            reward = abs(plan.take_profit - plan.entry_price)
            rr_ratio = reward / risk if risk > 0 else 0.0
            if rr_ratio < self._min_rr:
                rejections.append(
                    f"R:R ratio {rr_ratio:.2f} < minimum {self._min_rr:.1f}."
                )

        if confluence.conflict_score > float(TRADING_PHILOSOPHY["max_conflict_score"]):
            rejections.append(
                f"Confluence conflict {confluence.conflict_score:.2f} > "
                f"max {TRADING_PHILOSOPHY['max_conflict_score']:.2f}."
            )

        if plan.action in (TradeAction.DO_NOTHING, TradeAction.WAIT):
            rejections.append("Plan action is DO_NOTHING or WAIT — no trade.")

        correlation_factor = 1.0
        corr_result = self._correlation.check(plan.symbol, _open_position_symbols())
        if not corr_result["allowed"]:
            rejections.append(corr_result["reason"])
        else:
            correlation_factor = float(corr_result.get("size_factor", 1.0))
            if correlation_factor < 1.0:
                logger.info(
                    "RiskEngine: correlation sizing [%s] factor=%.2f (%s)",
                    plan.symbol,
                    correlation_factor,
                    corr_result.get("reason", ""),
                )

        approved = len(rejections) == 0
        contracts = (
            self._size_position(
                p_success,
                ev_dollars,
                current_dd,
                remaining_pct,
                correlation_factor=correlation_factor,
            )
            if approved
            else 0
        )
        max_notional = 0.0
        if _env_optional_float("RISK_ACCOUNT_CAP_USD") is not None or self._max_order_notional:
            max_order = self._max_order_notional or (self._account_cap_usd * 0.1)
            notional_usd = self._estimate_order_notional(plan, contracts)
            if approved and notional_usd > max_order:
                rejections.append(
                    f"Order notional ${notional_usd:.2f} exceeds max ${max_order:.2f}."
                )
                approved = False
            if approved and notional_usd > self._account_cap_usd:
                rejections.append(
                    f"Order notional ${notional_usd:.2f} exceeds account cap "
                    f"${self._account_cap_usd:.0f}."
                )
                approved = False
            max_notional = min(max_order, self._account_cap_usd) if approved else 0.0

        result = RiskDecision(
            approved=approved,
            symbol=plan.symbol,
            timestamp=datetime.now(tz=timezone.utc),
            position_size_contracts=contracts,
            max_notional_usd=round(max_notional, 2),
            risk_reward_ratio=round(rr_ratio, 2),
            rejection_reasons=rejections,
            daily_loss_remaining_pct=remaining_pct,
            drawdown_current_pct=round(current_dd, 4),
            consecutive_losses=self._consecutive_losses,
            kill_switch_active=kill_active,
        )

        if approved:
            logger.info(
                "RiskEngine: APPROVED [%s] | contracts=%d RR=%.2f",
                plan.symbol,
                contracts,
                rr_ratio,
            )
        else:
            logger.info(
                "RiskEngine: REJECTED [%s] | %d reasons | %s",
                plan.symbol,
                len(rejections),
                rejections[0] if rejections else "",
            )

        return result

    def approve_level_fast_lane(
        self,
        plan: TradePlan,
        level_setup: LevelSetup,
    ) -> RiskDecision:
        """
        Safety-only risk for actionable watchlist fast lane.
        Skips probability gate, news block, confluence conflict, and soft R:R
        (R:R already validated on the watchlist row).
        """
        rejections: list[str] = []

        kill_active = is_kill_switch_active()
        if kill_active:
            rejections.append(
                "KILL SWITCH ACTIVE — disable via dashboard or set RISK_KILL_SWITCH=false"
            )

        daily_hit, remaining_pct = self._daily_limiter.is_limit_hit()
        if daily_hit:
            limit_usd = self._daily_limiter.daily_loss_limit_usd()
            rejections.append(
                f"Daily loss limit hit (${limit_usd:.0f}). Trading suspended for today."
            )

        current_dd = self._drawdown_monitor.current_drawdown_pct()
        if self._drawdown_monitor.is_limit_hit():
            rejections.append(
                f"Max drawdown {current_dd:.1%} >= limit {self._max_drawdown:.0%}."
            )

        if self._consecutive_losses >= self._max_consec_losses:
            rejections.append(
                f"{self._consecutive_losses} consecutive losses — forced pause."
            )

        if self._open_positions >= self._max_open:
            rejections.append(f"Max open positions ({self._max_open}) reached.")

        if plan.action in (TradeAction.DO_NOTHING, TradeAction.WAIT):
            rejections.append("Plan action is DO_NOTHING or WAIT — no trade.")

        rr_ratio = level_setup.optimal_rr
        if rr_ratio <= 0 and plan.stop_loss and plan.take_profit and plan.entry_price:
            risk = abs(plan.entry_price - plan.stop_loss)
            reward = abs(plan.take_profit - plan.entry_price)
            rr_ratio = reward / risk if risk > 0 else 0.0

        correlation_factor = 1.0
        corr_result = self._correlation.check(plan.symbol, _open_position_symbols())
        if not corr_result["allowed"]:
            rejections.append(corr_result["reason"])
        else:
            correlation_factor = float(corr_result.get("size_factor", 1.0))

        approved = len(rejections) == 0
        p_success = level_setup.exit_win_rate if level_setup.exit_win_rate > 0 else level_setup.hold_rate
        ev_dollars = level_setup.expected_value_pct
        contracts = (
            self._size_position(
                p_success,
                ev_dollars,
                current_dd,
                remaining_pct,
                correlation_factor=correlation_factor,
            )
            if approved
            else 0
        )
        max_notional = 0.0
        if _env_optional_float("RISK_ACCOUNT_CAP_USD") is not None or self._max_order_notional:
            max_order = self._max_order_notional or (self._account_cap_usd * 0.1)
            notional_usd = self._estimate_order_notional(plan, contracts)
            if approved and notional_usd > max_order:
                rejections.append(
                    f"Order notional ${notional_usd:.2f} exceeds max ${max_order:.2f}."
                )
                approved = False
            if approved and notional_usd > self._account_cap_usd:
                rejections.append(
                    f"Order notional ${notional_usd:.2f} exceeds account cap "
                    f"${self._account_cap_usd:.0f}."
                )
                approved = False
            max_notional = min(max_order, self._account_cap_usd) if approved else 0.0

        result = RiskDecision(
            approved=approved,
            symbol=plan.symbol,
            timestamp=datetime.now(tz=timezone.utc),
            position_size_contracts=contracts,
            max_notional_usd=round(max_notional, 2),
            risk_reward_ratio=round(rr_ratio, 2),
            rejection_reasons=rejections,
            daily_loss_remaining_pct=remaining_pct,
            drawdown_current_pct=round(current_dd, 4),
            consecutive_losses=self._consecutive_losses,
            kill_switch_active=kill_active,
        )

        if approved:
            logger.info(
                "RiskEngine: FAST LANE APPROVED [%s] | contracts=%d RR=%.2f EV=%.3f%%",
                plan.symbol,
                contracts,
                rr_ratio,
                level_setup.expected_value_pct,
            )
        else:
            logger.info(
                "RiskEngine: FAST LANE REJECTED [%s] | %s",
                plan.symbol,
                rejections[0] if rejections else "",
            )

        return result

    def evaluate(
        self,
        signal_rank: int,
        portfolio: PortfolioState,
        symbol: str | None = None,
    ):
        """
        Legacy API for tests and signal_engine integration.
        Returns risk.risk_approval_schema.RiskDecision.
        """
        from risk.risk_approval_schema import RiskDecision as LegacyRiskDecision

        self.sync_portfolio(portfolio)

        if self._daily_limiter.is_limit_hit()[0]:
            return LegacyRiskDecision(
                approved=False, reason="daily_loss_limit", max_position_size=0.0, symbol=symbol
            )
        if self._drawdown_monitor.is_limit_hit():
            return LegacyRiskDecision(
                approved=False, reason="drawdown_limit", max_position_size=0.0, symbol=symbol
            )
        if portfolio.open_positions >= self._max_open:
            return LegacyRiskDecision(
                approved=False, reason="max_positions", max_position_size=0.0, symbol=symbol
            )
        if signal_rank < int(TRADING_PHILOSOPHY["signal_rank_minimum"]):
            return LegacyRiskDecision(
                approved=False, reason="low_signal_rank", max_position_size=0.0, symbol=symbol
            )

        size = float(self._max_contracts)
        if signal_rank >= 85:
            size *= 1.25
        elif signal_rank < 65:
            size *= 0.75

        return LegacyRiskDecision(
            approved=True, reason=None, max_position_size=size, symbol=symbol
        )

    def record_outcome(self, pnl: float) -> None:
        self._daily_limiter.record_trade(pnl)
        self._drawdown_monitor.update(pnl)

        if pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

        if pnl != 0:
            self._open_positions = max(0, self._open_positions - 1)

        logger.info(
            "RiskEngine.record_outcome: pnl=$%.2f consec_losses=%d drawdown=%.1f%%",
            pnl,
            self._consecutive_losses,
            self._drawdown_monitor.current_drawdown_pct() * 100,
        )

    def open_position(self) -> None:
        self._open_positions += 1

    def close_position(self) -> None:
        self._open_positions = max(0, self._open_positions - 1)

    def _size_position(
        self,
        p_success: float,
        ev_dollars: float,
        current_dd: float,
        remaining_daily: float,
        correlation_factor: float = 1.0,
    ) -> int:
        base = self._max_contracts

        if current_dd > 0.03:
            base = max(1, base - 1)
        if current_dd > 0.05:
            base = 1

        if remaining_daily < 0.005:
            base = 1

        if p_success < 0.65:
            base = max(1, base - 1)

        if self._consecutive_losses >= 2:
            base = max(1, base - 1)

        factor = max(0.25, min(1.0, correlation_factor))
        base = max(1, int(base * factor)) if factor < 1.0 else base

        return min(base, self._max_contracts)

    def _estimate_order_notional(self, plan: TradePlan, contracts: int) -> float:
        from config.coinbase_symbols import is_coinbase_tradable

        max_order = self._max_order_notional or (self._account_cap_usd * 0.1)
        if is_coinbase_tradable(plan.symbol):
            return max_order
        if not plan.entry_price:
            return 0.0
        return abs(float(plan.entry_price)) * max(1, contracts)

    def risk_summary(self) -> dict:
        """Dashboard / status payload."""
        daily_limit = self._daily_limiter.daily_loss_limit_usd()
        return {
            "account_cap_usd": self._account_cap_usd,
            "max_daily_loss_usd": daily_limit,
            "max_order_notional_usd": self._max_order_notional
            or (self._account_cap_usd * 0.1),
            "daily_pnl_usd": round(self._daily_limiter._daily_pnl, 2),
            "daily_loss_remaining_usd": round(
                daily_limit + self._daily_limiter._daily_pnl, 2
            ),
        }
