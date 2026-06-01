"""
learning/learning_agent.py

Wired to unified RetrainPipeline + ModelRegistry + PromotionPolicy.
Reloads prediction model after successful auto-promotion via on_model_reload.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from ml.promotion.promotion_policy import PromotionPolicy
from ml.registry.model_registry import ModelRegistry
from ml.training.retrain_pipeline import RetrainPipeline
from pipeline.world_state_store import WorldStateStore
from risk.risk_engine import RiskEngine

logger = logging.getLogger(__name__)

MIN_NEW_SAMPLES = int(os.getenv("LEARNING_MIN_SAMPLES", "100"))
OUTCOMES_LOG_PATH = Path(os.getenv("LEARNING_OUTCOMES_LOG", "logs/outcomes.jsonl"))


class LearningAgent:
    """Closes the learning loop on every trade close; triggers unified retrain."""

    def __init__(
        self,
        world_store: WorldStateStore,
        risk_engine: RiskEngine,
        model_path: str = "ml/models/lightgbm_production.pkl",
        on_model_reload: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._world = world_store
        self._risk = risk_engine
        self._model_path = model_path
        self._on_reload = on_model_reload
        self._registry = ModelRegistry()
        self._policy = PromotionPolicy()
        self._retrain = RetrainPipeline(world_store, self._registry, self._policy)
        self._new_samples: int = 0
        self._session_outcomes: list[dict] = []
        OUTCOMES_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        logger.info(
            "LearningAgent: auto_promote=%s min_samples=%d brier_improve=%.3f | log=%s",
            self._policy.auto_promote,
            self._policy.min_samples,
            self._policy.min_brier_improvement,
            OUTCOMES_LOG_PATH,
        )

    def on_trade_closed(
        self,
        snapshot_id: str,
        pnl: float,
        r_multiple: float,
        hit_target: bool,
        hit_stop: bool,
        mfe_ticks: float,
        mae_ticks: float,
        duration_bars: int,
        entry_price: float,
        exit_price: float,
        symbol: str,
        timeframe: str,
        signal_rank: int,
    ) -> None:
        snap = self._world.record_outcome(
            snapshot_id=snapshot_id,
            pnl=pnl,
            r_multiple=r_multiple,
            hit_target=hit_target,
            hit_stop=hit_stop,
        )
        self._risk.record_outcome(pnl)

        row = {
            "snapshot_id": snapshot_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "pnl": pnl,
            "r_multiple": r_multiple,
            "hit_target": hit_target,
            "hit_stop": hit_stop,
            "mfe_ticks": mfe_ticks,
            "mae_ticks": mae_ticks,
            "duration_bars": duration_bars,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "signal_rank": signal_rank,
            "outcome_label": 1 if pnl > 0 else 0,
        }
        if snap and snap.confluence:
            row["confluence_score"] = snap.confluence.confluence_score
            row["conflict_score"] = snap.confluence.conflict_score
            row["methods_agreed"] = snap.methods_that_were_right
            row["methods_wrong"] = snap.methods_that_were_wrong

        self._log_outcome(row)
        self._session_outcomes.append(row)
        self._new_samples += 1

        logger.info(
            "LearningAgent: trade closed [%s] P&L=$%.2f R=%.2f MFE=%.0f MAE=%.0f | new=%d",
            symbol,
            pnl,
            r_multiple,
            mfe_ticks,
            mae_ticks,
            self._new_samples,
        )

        if self._new_samples >= MIN_NEW_SAMPLES:
            self._trigger_retrain(symbol, timeframe)

    def _trigger_retrain(self, symbol: str, timeframe: str) -> None:
        logger.info(
            "LearningAgent: triggering retrain new_samples=%d", self._new_samples
        )
        try:
            result = self._retrain.run(
                symbol=symbol,
                timeframe=timeframe,
                requested_by="learning_agent_auto",
            )
            if result.promoted:
                logger.info(
                    "LearningAgent: PROMOTED %s by=%s brier%s%.4f",
                    result.model_id,
                    result.promoted_by,
                    "+" if result.brier_improvement >= 0 else "",
                    result.brier_improvement,
                )
                self._call_reload(result.model_id)
            elif result.skipped:
                logger.debug("LearningAgent: retrain skipped: %s", result.skip_reason)
            elif result.error:
                logger.error("LearningAgent: retrain error: %s", result.error)
        except Exception as e:
            logger.error("LearningAgent: retrain failed: %s", e, exc_info=True)
        finally:
            self._new_samples = 0

    def force_retrain(
        self,
        requested_by: str = "manual",
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
    ) -> dict:
        result = self._retrain.run(
            force=True,
            symbol=symbol,
            timeframe=timeframe,
            requested_by=requested_by,
        )
        if result.promoted:
            self._call_reload(result.model_id)
        return {
            "model_id": result.model_id,
            "promoted": result.promoted,
            "promoted_by": result.promoted_by,
            "holdout_brier": result.holdout_brier,
            "brier_improvement": result.brier_improvement,
            "n_train": result.n_train,
            "n_holdout": result.n_holdout,
            "skipped": result.skipped,
            "skip_reason": result.skip_reason,
            "error": result.error,
        }

    def manual_approve_model(self, model_id: str, approved_by: str) -> dict:
        decision = self._policy.manual_approve(model_id, approved_by)
        promoted = self._registry.promote_to_production(
            model_id=model_id,
            promoted_by=decision.promoted_by,
            notes=decision.notes,
        )
        if promoted:
            self._call_reload(model_id)
            return {"promoted": True, "model_id": model_id, "by": approved_by}
        return {"promoted": False, "error": "manual promotion failed"}

    def rollback_model(self, target_model_id: str, rolled_back_by: str) -> dict:
        success = self._registry.rollback(target_model_id, rolled_back_by)
        if success:
            self._call_reload(target_model_id)
        return {"rolled_back": success, "target": target_model_id}

    def session_summary(self) -> dict:
        outcomes = self._session_outcomes
        if not outcomes:
            return {"trades": 0}
        wins = [o for o in outcomes if o["pnl"] > 0]
        losses = [o for o in outcomes if o["pnl"] < 0]
        return {
            "trades": len(outcomes),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(outcomes) if outcomes else 0,
            "total_pnl": sum(o["pnl"] for o in outcomes),
            "avg_mfe": sum(o.get("mfe_ticks", 0) for o in outcomes) / len(outcomes),
            "avg_mae": sum(o.get("mae_ticks", 0) for o in outcomes) / len(outcomes),
            "avg_r": sum(o["r_multiple"] for o in outcomes) / len(outcomes),
            "world_stats": self._world.stats(),
            "registry": self._registry.status_summary(),
        }

    def _call_reload(self, model_id: Optional[str]) -> None:
        if not self._on_reload or not model_id:
            return
        try:
            self._on_reload(model_id)
        except Exception as e:
            logger.error("LearningAgent: reload callback failed: %s", e)

    def _log_outcome(self, row: dict) -> None:
        try:
            with open(OUTCOMES_LOG_PATH, "a") as f:
                f.write(json.dumps(row) + "\n")
        except Exception as e:
            logger.error("LearningAgent: log failed: %s", e)
