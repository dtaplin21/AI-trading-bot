"""Paper trade executor — auto-wires learning loop on position close."""

from __future__ import annotations

import logging
from typing import Optional

from learning.runtime import get_learning_agent
from paper_trading.paper_ledger import PaperLedger
from paper_trading.position_book import OpenPosition, get_position_book
from risk.risk_runtime import get_risk_engine

logger = logging.getLogger(__name__)

_trader: Optional["PaperTrader"] = None


class PaperTrader:
    def __init__(self, learning_agent=None, ledger: PaperLedger | None = None) -> None:
        self._learning = learning_agent
        self._ledger = ledger or PaperLedger()

    @property
    def learning(self):
        return self._learning or get_learning_agent()

    def execute(self, signal: dict) -> dict:
        action = signal.get("action", "")
        direction = "long" if "long" in action else "short" if "short" in action else "long"
        entry = float(signal.get("entry") or 0)
        book = get_position_book()
        pos = book.open_position(
            symbol=signal.get("symbol", "MES"),
            direction=direction,
            entry_price=entry,
            stop_loss=float(signal.get("stop") or entry),
            take_profit=float(signal.get("target") or entry),
            quantity=max(1, int(signal.get("size") or 1)),
            broker="paper",
            signal_rank=int(signal.get("signal_rank") or 0),
            snapshot_id=str(signal.get("snapshot_id") or ""),
            timeframe=str(signal.get("timeframe") or "5m"),
        )
        get_risk_engine().open_position()
        logger.info(
            "PaperTrader: opened %s %s @ %.2f snapshot=%s",
            pos.symbol,
            pos.direction,
            pos.entry_price,
            pos.snapshot_id,
        )
        return {"status": "filled", "signal": signal, "position_id": pos.id}

    def on_bar(self, symbol: str, high: float, low: float, close: float) -> list[dict]:
        """Process bar for open positions; close on stop/target and notify learning agent."""
        book = get_position_book()
        closed = book.update_on_bar(symbol, high, low, close)
        results: list[dict] = []
        for _pid, pos, reason, exit_price in closed:
            results.append(self._finalize_close(pos, exit_price, reason))
        return results

    def close_position(self, position_id: str, exit_price: float, reason: str = "manual") -> dict:
        """Manually close a position at the given price."""
        book = get_position_book()
        pos = book.close_position(position_id)
        if not pos:
            return {"status": "not_found", "position_id": position_id}
        return self._finalize_close(pos, exit_price, reason)

    def _finalize_close(self, pos: OpenPosition, exit_price: float, reason: str) -> dict:
        pnl = pos.realized_pnl_dollars(exit_price)
        r_mult = pos.r_multiple(pnl)
        hit_target = reason == "target"
        hit_stop = reason == "stop"

        self._ledger.record(pnl)

        if pos.snapshot_id:
            self.learning.on_trade_closed(
                snapshot_id=pos.snapshot_id,
                pnl=pnl,
                r_multiple=r_mult,
                hit_target=hit_target,
                hit_stop=hit_stop,
                mfe_ticks=pos.mfe_ticks,
                mae_ticks=pos.mae_ticks,
                duration_bars=pos.duration_bars,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                symbol=pos.symbol,
                timeframe=pos.timeframe,
                signal_rank=pos.signal_rank,
            )
        else:
            logger.warning(
                "PaperTrader: closed %s without snapshot_id — learning loop skipped",
                pos.id,
            )

        get_risk_engine().close_position()

        result = {
            "status": "closed",
            "position_id": pos.id,
            "symbol": pos.symbol,
            "exit_reason": reason,
            "exit_price": exit_price,
            "pnl_dollars": pnl,
            "r_multiple": r_mult,
            "mfe_ticks": pos.mfe_ticks,
            "mae_ticks": pos.mae_ticks,
            "duration_bars": pos.duration_bars,
        }
        logger.info(
            "PaperTrader: closed %s %s | %s | P&L=$%.2f R=%.2f MFE=%.0f MAE=%.0f",
            pos.symbol,
            reason,
            pos.id,
            pnl,
            r_mult,
            pos.mfe_ticks,
            pos.mae_ticks,
        )
        return result


def get_paper_trader() -> PaperTrader:
    global _trader
    if _trader is None:
        _trader = PaperTrader()
    return _trader


def reset_paper_trader() -> None:
    global _trader
    _trader = None
