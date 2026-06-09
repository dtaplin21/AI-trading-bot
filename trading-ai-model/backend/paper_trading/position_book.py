"""In-memory open position tracker for paper (and future live) execution."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

from config.symbols import SYMBOLS

BROKER_DISPLAY_NAMES: dict[str, str] = {
    "paper": "Paper Trading",
    "coinbase": "Coinbase",
    "oanda": "OANDA",
    "robinhood": "Robinhood",
    "webull": "Webull",
    "alpaca": "Alpaca",
    "schwab": "Charles Schwab",
    "tastytrade": "tastytrade",
    "ibkr": "Interactive Brokers",
    "tradovate": "Tradovate",
    "ninjatrader": "NinjaTrader",
}

TICK_SIZES = {sym: spec.tick_size for sym, spec in SYMBOLS.items()}
TICK_VALUES = {sym: spec.tick_value for sym, spec in SYMBOLS.items()}


@dataclass
class OpenPosition:
    id: str
    symbol: str
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    quantity: int
    opened_at: datetime
    broker: str = "paper"
    platform_id: str = "paper"
    signal_rank: int = 0
    snapshot_id: str = ""
    timeframe: str = "5m"
    current_price: Optional[float] = None
    mfe_ticks: float = 0.0
    mae_ticks: float = 0.0
    duration_bars: int = 0

    def unrealized_ticks(self) -> float:
        price = self.current_price if self.current_price is not None else self.entry_price
        tick = TICK_SIZES.get(self.symbol, 0.25)
        diff = price - self.entry_price
        if self.direction == "short":
            diff = -diff
        return round(diff / tick, 1)

    def unrealized_pnl_dollars(self) -> float:
        ticks = self.unrealized_ticks()
        tick_val = TICK_VALUES.get(self.symbol, 1.25)
        return round(ticks * tick_val * self.quantity, 2)

    def update_excursion(self, high: float, low: float) -> None:
        """Track MFE/MAE from bar high/low."""
        tick = TICK_SIZES.get(self.symbol, 0.25)
        if self.direction == "long":
            fav = (high - self.entry_price) / tick
            adv = (self.entry_price - low) / tick
        else:
            fav = (self.entry_price - low) / tick
            adv = (high - self.entry_price) / tick
        self.mfe_ticks = max(self.mfe_ticks, round(fav, 1))
        self.mae_ticks = max(self.mae_ticks, round(adv, 1))

    def check_exit(self, high: float, low: float) -> tuple[Optional[str], Optional[float]]:
        """Return (exit_reason, exit_price) if stop or target hit on this bar."""
        if self.direction == "long":
            if low <= self.stop_loss:
                return "stop", self.stop_loss
            if high >= self.take_profit:
                return "target", self.take_profit
        else:
            if high >= self.stop_loss:
                return "stop", self.stop_loss
            if low <= self.take_profit:
                return "target", self.take_profit
        return None, None

    def realized_pnl_dollars(self, exit_price: float) -> float:
        tick = TICK_SIZES.get(self.symbol, 0.25)
        tick_val = TICK_VALUES.get(self.symbol, 1.25)
        diff = exit_price - self.entry_price
        if self.direction == "short":
            diff = -diff
        ticks = diff / tick
        return round(ticks * tick_val * self.quantity, 2)

    def r_multiple(self, pnl_dollars: float) -> float:
        tick = TICK_SIZES.get(self.symbol, 0.25)
        tick_val = TICK_VALUES.get(self.symbol, 1.25)
        risk_ticks = abs(self.entry_price - self.stop_loss) / tick
        risk_dollars = risk_ticks * tick_val * self.quantity
        if risk_dollars <= 0:
            return 0.0
        return round(pnl_dollars / risk_dollars, 2)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "current_price": self.current_price if self.current_price is not None else self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "quantity": self.quantity,
            "unrealized_pnl_dollars": self.unrealized_pnl_dollars(),
            "unrealized_pnl_ticks": self.unrealized_ticks(),
            "opened_at": self.opened_at.isoformat(),
            "broker": self.broker,
            "platform_id": self.platform_id,
            "platform_name": BROKER_DISPLAY_NAMES.get(self.platform_id, self.broker),
            "signal_rank": self.signal_rank,
            "snapshot_id": self.snapshot_id,
            "timeframe": self.timeframe,
            "mfe_ticks": self.mfe_ticks,
            "mae_ticks": self.mae_ticks,
            "duration_bars": self.duration_bars,
            "status": "open",
        }


@dataclass
class PositionBook:
    """Thread-safe store of open positions."""

    _positions: dict[str, OpenPosition] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def open_position(
        self,
        *,
        symbol: str,
        direction: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        quantity: int = 1,
        broker: str = "paper",
        platform_id: str | None = None,
        signal_rank: int = 0,
        snapshot_id: str = "",
        timeframe: str = "5m",
    ) -> OpenPosition:
        pid = platform_id or broker
        pos = OpenPosition(
            id=f"pos-{uuid.uuid4().hex[:8]}",
            symbol=symbol.upper(),
            direction=direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            quantity=quantity,
            opened_at=datetime.now(timezone.utc),
            broker=broker,
            platform_id=pid,
            signal_rank=signal_rank,
            snapshot_id=snapshot_id,
            timeframe=timeframe,
            current_price=entry_price,
        )
        with self._lock:
            self._positions[pos.id] = pos
        return pos

    def update_on_bar(
        self, symbol: str, high: float, low: float, close: float
    ) -> list[tuple[str, OpenPosition, str, float]]:
        """
        Update positions for symbol; return list of
        (position_id, position, exit_reason, exit_price) for closed trades.
        """
        sym = symbol.upper()
        closed: list[tuple[str, OpenPosition, str, float]] = []
        with self._lock:
            for pid, pos in list(self._positions.items()):
                if pos.symbol != sym:
                    continue
                pos.duration_bars += 1
                pos.update_excursion(high, low)
                pos.current_price = close
                reason, exit_price = pos.check_exit(high, low)
                if reason and exit_price is not None:
                    closed.append((pid, pos, reason, exit_price))
                    self._positions.pop(pid, None)
        return closed

    def close_position(self, position_id: str) -> Optional[OpenPosition]:
        with self._lock:
            return self._positions.pop(position_id, None)

    def update_price(self, symbol: str, price: float) -> None:
        sym = symbol.upper()
        with self._lock:
            for pos in self._positions.values():
                if pos.symbol == sym:
                    pos.current_price = price

    def list_open(self) -> list[dict]:
        with self._lock:
            return [p.to_dict() for p in sorted(self._positions.values(), key=lambda p: p.opened_at, reverse=True)]

    def count(self) -> int:
        with self._lock:
            return len(self._positions)

    def open_symbols(self) -> list[str]:
        with self._lock:
            return sorted({p.symbol for p in self._positions.values()})


_book: PositionBook | None = None


def get_position_book() -> PositionBook:
    global _book
    if _book is None:
        _book = PositionBook()
        _seed_demo_positions(_book)
    return _book


def reset_position_book(*, seed_demo: bool = False) -> PositionBook:
    """Reset global book (tests). Optionally seed dashboard demo positions."""
    global _book
    _book = PositionBook()
    if seed_demo:
        _seed_demo_positions(_book)
    return _book


def _seed_demo_positions(book: PositionBook) -> None:
    """Seed paper positions when book is empty (development dashboard)."""
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    book.open_position(
        symbol="MES",
        direction="long",
        entry_price=5420.25,
        stop_loss=5410.0,
        take_profit=5442.0,
        quantity=2,
        signal_rank=84,
        platform_id="tradovate",
        broker="tradovate",
    )
    book.open_position(
        symbol="NQ",
        direction="short",
        entry_price=19355.0,
        stop_loss=19390.0,
        take_profit=19290.0,
        quantity=1,
        signal_rank=78,
        platform_id="paper",
        broker="paper",
    )
    with book._lock:
        for pos in book._positions.values():
            if pos.symbol == "MES":
                pos.current_price = 5426.50
                pos.opened_at = now - timedelta(hours=1, minutes=12)
            elif pos.symbol == "NQ":
                pos.current_price = 19338.0
                pos.opened_at = now - timedelta(minutes=38)
