"""Paper trade executor."""

from paper_trading.position_book import get_position_book


class PaperTrader:
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
            quantity=int(signal.get("size") or 1),
            broker="paper",
        )
        return {"status": "filled", "signal": signal, "position_id": pos.id}

