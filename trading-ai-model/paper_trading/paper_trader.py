"""Paper trade executor."""

class PaperTrader:
    def execute(self, signal: dict) -> dict:
        return {"status": "filled", "signal": signal}

