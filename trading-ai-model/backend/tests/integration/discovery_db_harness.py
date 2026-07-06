"""In-memory Postgres stub for rolling level-discovery integration tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PriceLevelRow:
    symbol: str
    level_price: float
    price_min: float
    price_max: float
    touch_count: int = 0
    hold_count: int = 0
    break_count: int = 0
    support_count: int = 0
    resistance_count: int = 0
    hold_rate: float = 0.0
    strength_score: float = 0.0
    role: str = "UNKNOWN"
    is_active: bool = True
    discovery_source: str = "seed"
    first_seen: str | None = None
    last_touched: str | None = None


@dataclass
class WatchlistRow:
    symbol: str
    level_price: float
    hold_rate: float
    touch_count: int
    strength_score: float
    role: str
    entry_side: str = "EITHER"
    is_active: bool = True


@dataclass
class FakeDiscoveryStore:
    """Minimal state store backing discover_symbol() SQL in tests."""

    levels: dict[tuple[str, float], PriceLevelRow] = field(default_factory=dict)
    archive: list[dict[str, Any]] = field(default_factory=list)
    watchlist: dict[tuple[str, float], WatchlistRow] = field(default_factory=dict)
    audit_runs: list[dict[str, Any]] = field(default_factory=list)
    last_rowcount: int = 0

    def connect(self):
        return _FakeConn(self)

    def seed_level(
        self,
        symbol: str,
        level_price: float,
        *,
        touch_count: int = 10,
        hold_rate: float = 0.70,
        strength_score: float = 0.60,
        is_active: bool = True,
        price_min: float | None = None,
        price_max: float | None = None,
        role: str = "SUPPORT",
    ) -> None:
        sym = symbol.upper()
        lp = float(level_price)
        row = PriceLevelRow(
            symbol=sym,
            level_price=lp,
            price_min=price_min if price_min is not None else lp * 0.999,
            price_max=price_max if price_max is not None else lp * 1.001,
            touch_count=touch_count,
            hold_count=max(1, int(touch_count * hold_rate)),
            break_count=max(0, touch_count - max(1, int(touch_count * hold_rate))),
            hold_rate=hold_rate,
            strength_score=strength_score,
            role=role,
            is_active=is_active,
        )
        self.levels[(sym, lp)] = row

    def active_levels(self, symbol: str) -> list[PriceLevelRow]:
        sym = symbol.upper()
        return [
            row
            for (s, _), row in self.levels.items()
            if s == sym and row.is_active
        ]

    def archived_levels(self, symbol: str) -> list[dict[str, Any]]:
        sym = symbol.upper()
        return [row for row in self.archive if row["symbol"] == sym]

    def watchlist_active_count(self, symbol: str) -> int:
        sym = symbol.upper()
        return sum(
            1
            for (s, _), row in self.watchlist.items()
            if s == sym and row.is_active
        )

    def qualifying_active_levels(self, symbol: str) -> list[PriceLevelRow]:
        from ml.features.rolling_level_discovery import (
            WATCHLIST_MIN_HOLD,
            WATCHLIST_MIN_STRENGTH,
            WATCHLIST_MIN_TOUCHES,
        )

        return [
            row
            for row in self.active_levels(symbol)
            if row.touch_count >= WATCHLIST_MIN_TOUCHES
            and row.hold_rate >= WATCHLIST_MIN_HOLD
            and row.strength_score >= WATCHLIST_MIN_STRENGTH
        ]

    def handle_execute(self, sql: str, params: tuple[Any, ...]) -> list[tuple[Any, ...]]:
        from ml.features.rolling_level_discovery import (
            WATCHLIST_MIN_HOLD,
            WATCHLIST_MIN_STRENGTH,
            WATCHLIST_MIN_TOUCHES,
        )

        self.last_rowcount = 0
        if "SELECT MIN(level_price), MAX(level_price)" in sql:
            rows = self.active_levels(str(params[0]))
            if not rows:
                return [(None, None)]
            prices = [row.level_price for row in rows]
            return [(min(prices), max(prices))]

        if "SELECT level_price, COALESCE(is_active, TRUE)" in sql:
            sym = str(params[0]).upper()
            rows = sorted(
                ((lp, row.is_active) for (s, lp), row in self.levels.items() if s == sym),
                key=lambda item: item[0],
            )
            return rows

        if "UPDATE price_levels SET price_min = LEAST" in sql:
            (
                price_min,
                price_max,
                touch_count,
                hold_count,
                break_count,
                hold_rate,
                strength,
                sym,
                level_price,
            ) = params
            key = (str(sym).upper(), float(level_price))
            row = self.levels[key]
            row.price_min = min(row.price_min, float(price_min))
            row.price_max = max(row.price_max, float(price_max))
            row.touch_count = int(touch_count)
            row.hold_count = int(hold_count)
            row.break_count = int(break_count)
            row.hold_rate = float(hold_rate)
            row.strength_score = float(strength)
            row.is_active = True
            row.discovery_source = "rolling"
            return []

        if sql.startswith("INSERT INTO price_levels ("):
            (
                sym,
                level_price,
                price_min,
                price_max,
                touch_count,
                hold_count,
                break_count,
                hold_rate,
                strength,
                role,
            ) = params
            key = (str(sym).upper(), float(level_price))
            self.levels[key] = PriceLevelRow(
                symbol=key[0],
                level_price=key[1],
                price_min=float(price_min),
                price_max=float(price_max),
                touch_count=int(touch_count),
                hold_count=int(hold_count),
                break_count=int(break_count),
                hold_rate=float(hold_rate),
                strength_score=float(strength),
                role=str(role),
                is_active=True,
                discovery_source="rolling",
            )
            return []

        if "DELETE FROM price_levels_archive pa USING price_levels pl" in sql:
            sym = str(params[0]).upper()
            active_keys = {
                (row.symbol, row.level_price) for row in self.active_levels(sym)
            }
            self.archive = [
                entry
                for entry in self.archive
                if not (
                    entry["symbol"] == sym
                    and (sym, entry["level_price"]) in active_keys
                )
            ]
            return []

        if "UPDATE price_levels pl SET is_active = FALSE FROM price_levels_archive pa" in sql:
            sym = str(params[0]).upper()
            archived_keys = {
                (entry["symbol"], entry["level_price"])
                for entry in self.archive
                if entry["symbol"] == sym
            }
            for key in archived_keys:
                if key in self.levels:
                    self.levels[key].is_active = False
            return []

        if "DELETE FROM price_levels_archive" in sql and len(params) == 2:
            sym, level_price = params
            key = (str(sym).upper(), float(level_price))
            self.archive = [
                entry
                for entry in self.archive
                if not (
                    entry["symbol"] == key[0]
                    and entry["level_price"] == key[1]
                )
            ]
            return []

        if "UPDATE price_levels_archive pa" in sql:
            reason, sym, level_price = params
            key = (str(sym).upper(), float(level_price))
            row = self.levels.get(key)
            if row is None:
                return []
            archive_entry = {
                "symbol": row.symbol,
                "level_price": row.level_price,
                "archive_reason": reason,
            }
            self.archive = [
                entry
                for entry in self.archive
                if not (
                    entry["symbol"] == archive_entry["symbol"]
                    and entry["level_price"] == archive_entry["level_price"]
                )
            ]
            self.archive.append(archive_entry)
            row.is_active = False
            wl_key = (row.symbol, row.level_price)
            if wl_key in self.watchlist:
                self.watchlist[wl_key].is_active = False
            self.last_rowcount = 1
            return []

        if "SELECT level_price FROM price_levels WHERE symbol = %s AND COALESCE(is_active, TRUE) = TRUE" in sql:
            sym = str(params[0]).upper()
            return [(row.level_price,) for row in self.active_levels(sym)]

        if "INSERT INTO price_levels_archive" in sql:
            reason, sym, level_price = params
            key = (str(sym).upper(), float(level_price))
            row = self.levels[key]
            archive_entry = {
                "symbol": row.symbol,
                "level_price": row.level_price,
                "archive_reason": reason,
            }
            self.archive = [
                entry
                for entry in self.archive
                if not (
                    entry["symbol"] == archive_entry["symbol"]
                    and entry["level_price"] == archive_entry["level_price"]
                )
            ]
            self.archive.append(archive_entry)
            row.is_active = False
            wl_key = (row.symbol, row.level_price)
            if wl_key in self.watchlist:
                self.watchlist[wl_key].is_active = False
            self.last_rowcount = 1
            return []

        if "UPDATE price_levels SET is_active = FALSE" in sql:
            sym, level_price = params
            key = (str(sym).upper(), float(level_price))
            self.levels[key].is_active = False
            return []

        if "UPDATE level_watchlist SET is_active = FALSE WHERE symbol = %s AND strength_score" in sql:
            sym, min_strength = params
            for key, row in self.watchlist.items():
                if key[0] == str(sym).upper() and row.strength_score < float(min_strength):
                    row.is_active = False
            return []

        if "UPDATE level_watchlist SET is_active = FALSE WHERE symbol = %s" in sql:
            sym = str(params[0]).upper()
            for key, row in self.watchlist.items():
                if key[0] == sym:
                    row.is_active = False
            return []

        if sql.startswith("INSERT INTO level_watchlist"):
            sym = str(params[0]).upper()
            for row in self.active_levels(sym):
                if row.touch_count < WATCHLIST_MIN_TOUCHES or row.hold_rate < WATCHLIST_MIN_HOLD:
                    continue
                strength = row.strength_score
                entry_side = (
                    "BUY"
                    if row.role == "SUPPORT"
                    else "SELL"
                    if row.role == "RESISTANCE"
                    else "EITHER"
                )
                self.watchlist[(sym, row.level_price)] = WatchlistRow(
                    symbol=sym,
                    level_price=row.level_price,
                    hold_rate=row.hold_rate,
                    touch_count=row.touch_count,
                    strength_score=strength,
                    role=row.role,
                    entry_side=entry_side,
                    is_active=strength >= WATCHLIST_MIN_STRENGTH,
                )
            return []

        if "SELECT COUNT(*) FROM level_watchlist" in sql:
            sym = str(params[0]).upper()
            return [(self.watchlist_active_count(sym),)]

        if sql.startswith("INSERT INTO level_discovery_runs"):
            self.audit_runs.append({"params": params})
            return []

        return []


class _FakeCursor:
    def __init__(self, store: FakeDiscoveryStore) -> None:
        self._store = store
        self._rows: list[tuple[Any, ...]] = []

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> None:
        self._rows = self._store.handle_execute(" ".join(sql.split()), params or ())
        self.rowcount = self._store.last_rowcount

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows

    def close(self) -> None:
        return None


class _FakeConn:
    def __init__(self, store: FakeDiscoveryStore) -> None:
        self._store = store

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._store)

    def commit(self) -> None:
        return None

    def close(self) -> None:
        return None
