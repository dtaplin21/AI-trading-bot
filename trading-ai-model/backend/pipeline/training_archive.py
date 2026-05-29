"""Append-only local archive for ML training rows (survives DB loss)."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

TRAINING_ROWS_PATH = Path(os.getenv("LEARNING_TRAINING_ROWS", "logs/training_rows.jsonl"))


def append_training_row(row: dict) -> None:
    """Append one labeled row — source of truth for retrain when Postgres is empty."""
    TRAINING_ROWS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TRAINING_ROWS_PATH.open("a") as f:
        f.write(json.dumps(row, default=str) + "\n")


def load_training_rows(path: Path | None = None) -> list[dict]:
    """Load all rows from the JSONL archive."""
    p = path or TRAINING_ROWS_PATH
    if not p.exists():
        return []
    rows: list[dict] = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            logger.debug("training_archive: skip bad line: %s", exc)
    return rows


def merge_training_rows(*sources: list[dict]) -> list[dict]:
    """Dedupe by snapshot_id; later sources win."""
    merged: dict[str, dict] = {}
    for source in sources:
        for row in source:
            sid = row.get("snapshot_id")
            if sid:
                merged[sid] = row
            else:
                merged[f"anon_{len(merged)}"] = row
    return list(merged.values())
