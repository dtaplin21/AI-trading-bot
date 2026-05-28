"""
pipeline/world_state_store.py

The WorldStateStore is the system's memory.

It stores every ConfluenceReport alongside its eventual trade outcome.
Over time this builds the training dataset that LightGBM learns from.

Key jobs:
  1. Store confluence snapshots at the moment of every signal
  2. Update snapshots with actual trade outcomes after close
  3. Query historical patterns similar to the current setup
  4. Compute rolling accuracy — is our probability model calibrated?
  5. Feed the Learning Agent with labeled training rows

Mark Douglas principle:
  We log every outcome — wins and losses equally.
  We do not cherry-pick. The edge proves itself over the full series.
  We need the losses as much as the wins to train an honest model.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from pipeline.confluence_report import ConfluenceReport

logger = logging.getLogger(__name__)


class WorldStateSnapshot:
    """
    One stored state: the confluence report + eventual outcome.
    Written at signal time. Updated when trade closes.
    """

    __slots__ = [
        "snapshot_id",
        "confluence",
        "signal_rank",
        "predicted_p_success",
        "predicted_ev",
        "timestamp",
        "regime",
        "symbol",
        "timeframe",
        "actual_pnl",
        "actual_r_multiple",
        "hit_target",
        "hit_stop",
        "outcome_label",
        "timestamp_closed",
        "methods_that_were_right",
        "methods_that_were_wrong",
    ]

    def __init__(
        self,
        snapshot_id: str,
        confluence: ConfluenceReport,
        signal_rank: int,
        predicted_p: float,
        predicted_ev: float,
    ) -> None:
        self.snapshot_id = snapshot_id
        self.confluence = confluence
        self.signal_rank = signal_rank
        self.predicted_p_success = predicted_p
        self.predicted_ev = predicted_ev
        self.timestamp = confluence.timestamp
        self.regime = confluence.regime
        self.symbol = confluence.symbol
        self.timeframe = confluence.timeframe

        self.actual_pnl = None
        self.actual_r_multiple = None
        self.hit_target = None
        self.hit_stop = None
        self.outcome_label = None
        self.timestamp_closed = None
        self.methods_that_were_right: list[str] = []
        self.methods_that_were_wrong: list[str] = []

    def record_outcome(
        self,
        pnl: float,
        r_multiple: float,
        hit_target: bool,
        hit_stop: bool,
    ) -> None:
        self.actual_pnl = pnl
        self.actual_r_multiple = r_multiple
        self.hit_target = hit_target
        self.hit_stop = hit_stop
        self.outcome_label = 1 if pnl > 0 else 0
        self.timestamp_closed = datetime.now(tz=timezone.utc)

        actual_direction = 1 if pnl > 0 and not hit_stop else -1
        for vote in self.confluence.votes:
            if vote.direction == actual_direction:
                self.methods_that_were_right.append(vote.method_name)
            elif vote.direction != 0:
                self.methods_that_were_wrong.append(vote.method_name)

    def to_training_row(self) -> Optional[dict]:
        """
        Convert to a flat dict for LightGBM training.
        Returns None if outcome has not been recorded yet.
        """
        if self.outcome_label is None:
            return None

        c = self.confluence
        return {
            "label": self.outcome_label,
            "_symbol": self.symbol,
            "_timeframe": self.timeframe,
            "_regime": self.regime,
            "_timestamp": self.timestamp.isoformat(),
            "bullish_count": c.bullish_count,
            "bearish_count": c.bearish_count,
            "neutral_count": c.neutral_count,
            "total_voting": c.total_voting,
            "weighted_consensus": c.weighted_consensus,
            "conflict_score": c.conflict_score,
            "confluence_score": c.confluence_score,
            "news_sentiment_score": c.news_sentiment_score,
            "news_conflict_score": c.news_conflict_score,
            "news_trading_blocked": int(c.news_trading_blocked),
            **{f"vote_{v.method_name}": v.direction * v.confidence for v in c.votes},
            **{f"weight_{v.method_name}": v.weighted_score for v in c.votes},
            "cluster_direction": c.strongest_cluster.direction if c.strongest_cluster else 0,
            "cluster_score": c.strongest_cluster.cluster_score if c.strongest_cluster else 0.0,
            "cluster_size": len(c.strongest_cluster.methods) if c.strongest_cluster else 0,
            "signal_rank": self.signal_rank,
            "predicted_p_success": self.predicted_p_success,
            "predicted_ev": self.predicted_ev,
            "actual_pnl": self.actual_pnl,
            "actual_r": self.actual_r_multiple,
            "hit_target": int(self.hit_target or False),
            "hit_stop": int(self.hit_stop or False),
        }


class WorldStateStore:
    """
    In-memory store for confluence snapshots and their outcomes.
    Optionally persists to disk / database via the injected writer.

    This is the Learning Agent's data source.
    Every signal that fires adds a snapshot.
    Every trade that closes updates it with an outcome.
    The result is a growing, labeled training dataset.
    """

    def __init__(self, db_writer=None) -> None:
        self._snapshots: dict[str, WorldStateSnapshot] = {}
        self._db = db_writer
        self._method_correct: defaultdict[str, int] = defaultdict(int)
        self._method_incorrect: defaultdict[str, int] = defaultdict(int)
        logger.info("WorldStateStore initialized")

    def store_snapshot(
        self,
        snapshot_id: str,
        confluence: ConfluenceReport,
        signal_rank: int,
        predicted_p: float,
        predicted_ev: float,
    ) -> WorldStateSnapshot:
        snap = WorldStateSnapshot(
            snapshot_id=snapshot_id,
            confluence=confluence,
            signal_rank=signal_rank,
            predicted_p=predicted_p,
            predicted_ev=predicted_ev,
        )
        self._snapshots[snapshot_id] = snap
        logger.debug(
            "WorldState: stored snapshot %s | %s",
            snapshot_id,
            confluence.summary(),
        )
        return snap

    def record_outcome(
        self,
        snapshot_id: str,
        pnl: float,
        r_multiple: float,
        hit_target: bool,
        hit_stop: bool,
    ) -> Optional[WorldStateSnapshot]:
        snap = self._snapshots.get(snapshot_id)
        if not snap:
            logger.warning("WorldState: snapshot %s not found", snapshot_id)
            return None

        snap.record_outcome(pnl, r_multiple, hit_target, hit_stop)

        for m in snap.methods_that_were_right:
            self._method_correct[m] += 1
        for m in snap.methods_that_were_wrong:
            self._method_incorrect[m] += 1

        logger.info(
            "WorldState: outcome recorded %s | P&L=$%.2f R=%.2f | "
            "methods_right=%s methods_wrong=%s",
            snapshot_id,
            pnl,
            r_multiple,
            snap.methods_that_were_right,
            snap.methods_that_were_wrong,
        )

        if self._db:
            row = snap.to_training_row()
            if row:
                self._persist_row(row)

        return snap

    def _persist_row(self, row: dict) -> None:
        try:
            import asyncio

            loop = asyncio.get_running_loop()
            loop.create_task(self._db.save_snapshot(row))
        except RuntimeError:
            save_sync = getattr(self._db, "save_snapshot_sync", None)
            if callable(save_sync):
                save_sync(row)
            else:
                logger.debug("WorldState: no event loop for async db write")

    def get_training_rows(
        self,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
        regime: Optional[str] = None,
        min_rank: int = 0,
        last_n_days: int = 90,
    ) -> list[dict]:
        """
        Returns labeled training rows for LightGBM.
        Only includes closed trades (outcome recorded).
        Filters by symbol/timeframe/regime/rank as needed.
        """
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=last_n_days)
        rows: list[dict] = []
        for snap in self._snapshots.values():
            if snap.outcome_label is None:
                continue
            ts = (
                snap.timestamp
                if snap.timestamp.tzinfo
                else snap.timestamp.replace(tzinfo=timezone.utc)
            )
            if ts < cutoff:
                continue
            if symbol and snap.symbol != symbol:
                continue
            if timeframe and snap.timeframe != timeframe:
                continue
            if regime and snap.regime != regime:
                continue
            if snap.signal_rank < min_rank:
                continue
            row = snap.to_training_row()
            if row:
                rows.append(row)
        return rows

    def find_similar_setups(
        self,
        confluence: ConfluenceReport,
        top_n: int = 20,
        min_samples: int = 5,
    ) -> list[WorldStateSnapshot]:
        """
        Find historical snapshots similar to the current setup.
        Similarity = same symbol + regime + consensus direction +
                     confluence_score within 0.10.
        """
        candidates: list[WorldStateSnapshot] = []
        for snap in self._snapshots.values():
            if snap.outcome_label is None:
                continue
            c = snap.confluence
            if (
                c.symbol == confluence.symbol
                and c.regime == confluence.regime
                and c.consensus_direction == confluence.consensus_direction
                and abs(c.confluence_score - confluence.confluence_score) <= 0.10
            ):
                candidates.append(snap)

        if len(candidates) < min_samples:
            candidates = [
                s
                for s in self._snapshots.values()
                if s.outcome_label is not None
                and s.confluence.symbol == confluence.symbol
                and s.confluence.consensus_direction == confluence.consensus_direction
            ]

        candidates.sort(
            key=lambda s: abs(s.confluence.confluence_score - confluence.confluence_score)
        )
        return candidates[:top_n]

    def compute_historical_p_success(
        self,
        confluence: ConfluenceReport,
        min_samples: int = 30,
    ) -> tuple[float, int]:
        """
        Computes P(success) from historical similar setups.
        Returns (probability, sample_size).
        """
        similar = self.find_similar_setups(confluence, top_n=200)
        if len(similar) < min_samples:
            return 0.0, len(similar)
        wins = sum(1 for s in similar if s.outcome_label == 1)
        return round(wins / len(similar), 4), len(similar)

    def get_method_accuracy(self) -> dict[str, dict]:
        """Returns each method's accuracy from recorded outcomes."""
        result: dict[str, dict] = {}
        all_methods = set(list(self._method_correct.keys()) + list(self._method_incorrect.keys()))
        for m in all_methods:
            correct = self._method_correct[m]
            incorrect = self._method_incorrect[m]
            total = correct + incorrect
            result[m] = {
                "correct": correct,
                "incorrect": incorrect,
                "total": total,
                "accuracy": round(correct / total, 4) if total else 0.0,
            }
        return dict(sorted(result.items(), key=lambda x: -x[1]["accuracy"]))

    def stats(self) -> dict:
        total = len(self._snapshots)
        closed = sum(1 for s in self._snapshots.values() if s.outcome_label is not None)
        wins = sum(1 for s in self._snapshots.values() if s.outcome_label == 1)
        win_rate = wins / closed if closed else 0.0

        closed_snaps = [s for s in self._snapshots.values() if s.outcome_label is not None]
        brier = 0.0
        if closed_snaps:
            brier = sum(
                (s.predicted_p_success - s.outcome_label) ** 2 for s in closed_snaps
            ) / len(closed_snaps)

        return {
            "total_snapshots": total,
            "closed_trades": closed,
            "open_trades": total - closed,
            "wins": wins,
            "losses": closed - wins,
            "win_rate": round(win_rate, 4),
            "brier_score": round(brier, 4),
            "method_accuracy": self.get_method_accuracy(),
        }
