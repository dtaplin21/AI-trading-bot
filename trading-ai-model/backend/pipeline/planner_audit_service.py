"""Persist compact MCTS / beam audits to DB and optional JSONL archive."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from data.storage.timescale_store import TimescaleStore
from mcts.planner_audit import envelope_audit
from pipeline.confluence_report import ConfluenceReport
from pipeline.schemas import TradePlan

logger = logging.getLogger(__name__)

PLANNER_AUDIT_LOG = Path(os.getenv("PLANNER_AUDIT_LOG", "logs/planner_audits.jsonl"))
DEEP_PLANNERS = frozenset({"mcts", "beam"})


def append_planner_audit_jsonl(record: dict) -> None:
    PLANNER_AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with PLANNER_AUDIT_LOG.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def persist_planner_audit(
    audit: Optional[dict],
    *,
    snapshot_id: Optional[str],
    symbol: str,
    timeframe: str,
    confluence: ConfluenceReport,
    plan: TradePlan,
    p_success: float,
    ev_dollars: float,
    signal_rank: int,
    store: TimescaleStore | None = None,
) -> None:
    """Save compact search audit for deep planners (mcts, beam)."""
    if not audit or audit.get("planner") not in DEEP_PLANNERS:
        return

    record = envelope_audit(
        audit,
        snapshot_id=snapshot_id,
        symbol=symbol,
        timeframe=timeframe,
        confluence=confluence,
        plan=plan,
        p_success=p_success,
        ev_dollars=ev_dollars,
        signal_rank=signal_rank,
    )

    try:
        append_planner_audit_jsonl(record)
    except Exception as exc:
        logger.warning("planner_audit: jsonl write failed: %s", exc)

    ts = store or TimescaleStore()
    if ts.available:
        ts.insert_planner_audit(record)
    else:
        logger.debug("planner_audit: DB unavailable — JSONL only")
