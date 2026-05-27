"""Log all signals for review."""

import json
from pathlib import Path


class SignalLogger:
    def __init__(self, log_dir: str = "./logs/signals"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def log(self, signal: dict) -> None:
        path = self.log_dir / "signals.jsonl"
        with path.open("a") as f:
            f.write(json.dumps(signal) + "\n")

