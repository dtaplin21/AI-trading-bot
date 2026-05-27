"""Virtual P&L tracker."""

class PaperLedger:
    def __init__(self):
        self.balance = 0.0

    def record(self, pnl: float) -> None:
        self.balance += pnl

