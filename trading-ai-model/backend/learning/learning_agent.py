"""
learning/learning_agent.py

Closed Learning Loop — full implementation.

Audit findings fixed:
  - record_outcome() existed in WorldStateStore API but was never
    auto-wired from paper trader close → now auto-wired
  - MFE/MAE not logged → now logged
  - LightGBM trained on observations.jsonl with proxy labels
    → now trains directly from WorldStateStore training rows
  - Retrain pipeline stages defined but not end-to-end → now automated
    with safe promotion gate (manual approval still required)

Safe learning loop (Mark Douglas: the edge proves itself over a series):
  Collect (every trade close)
       ↓
  Accumulate (min 100 new samples)
       ↓
  Retrain LightGBM on WorldState rows
       ↓
  Backtest new model vs current model
       ↓
  If new model Brier score improves by >= 1% → promote to staging
       ↓
  Paper trade staging model for 5 sessions
       ↓
  MANUAL APPROVAL → deploy to production

Env:
  LLM_ENABLED          — master switch (default false)
  ANTHROPIC_API_KEY    — API key (never hardcoded; set in .env only)
  ANTHROPIC_MODEL      — model id (default claude-sonnet-4-20250514)
  LEARNING_RETRAIN_SCHEDULED  (bool,  default true) — gate retrains by RETRAIN_SCHEDULE_DAYS
  LEARNING_PAPER_DAYS        (int,   default 5)
  LEARNING_BRIER_IMPROVEMENT (float, default 0.01)
  LEARNING_OUTCOMES_LOG      (path,  default logs/outcomes.jsonl)
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from pipeline.world_state_store import WorldStateStore
from risk.risk_engine import RiskEngine

logger = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────
MIN_NEW_SAMPLES = int(os.getenv("LEARNING_MIN_SAMPLES", "100"))
BRIER_IMPROVEMENT = float(os.getenv("LEARNING_BRIER_IMPROVEMENT", "0.01"))
PAPER_DAYS_BEFORE_LIVE = int(os.getenv("LEARNING_PAPER_DAYS", "5"))
OUTCOMES_LOG_PATH = Path(os.getenv("LEARNING_OUTCOMES_LOG", "logs/outcomes.jsonl"))
RETRAIN_SCHEDULED = os.getenv(
    "LEARNING_RETRAIN_SCHEDULED",
    os.getenv("LEARNING_RETRAIN_WEEKLY", "true"),
).lower() == "true"
RETRAIN_STATE_PATH = Path(os.getenv("LEARNING_RETRAIN_STATE", "logs/learning_retrain_state.json"))

META_COLS = frozenset(
    {
        "snapshot_id",
        "label",
        "_symbol",
        "_timeframe",
        "_regime",
        "_timestamp",
        "actual_pnl",
        "actual_r",
        "hit_target",
        "hit_stop",
    }
)


class LearningAgent:
    """
    Closes the learning loop after every trade.

    Wired into TradingPipelineSupervisor — called automatically
    when paper_trader closes a position. No manual trigger needed.
    """

    def __init__(
        self,
        world_store: WorldStateStore,
        risk_engine: RiskEngine,
        model_path: str = "ml/models/lightgbm_production.pkl",
    ) -> None:
        self._world = world_store
        self._risk = risk_engine
        self._model_path = model_path
        self._new_samples: int = 0
        self._session_outcomes: list[dict] = []
        OUTCOMES_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        logger.info("LearningAgent initialized | log=%s", OUTCOMES_LOG_PATH)

    # ─── Primary: called on every trade close ─────────────────────────────────

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
        """
        Auto-called by paper trader / execution agent on every close.
        Records outcome to WorldStateStore and risk engine.
        Triggers retrain check.
        """
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
            "LearningAgent: trade closed [%s] P&L=$%.2f R=%.2f "
            "MFE=%.0f MAE=%.0f bars=%d | new_samples=%d",
            symbol,
            pnl,
            r_multiple,
            mfe_ticks,
            mae_ticks,
            duration_bars,
            self._new_samples,
        )

        if self._new_samples >= MIN_NEW_SAMPLES:
            logger.info(
                "LearningAgent: %d new samples — triggering retrain check",
                self._new_samples,
            )
            self._try_retrain()

    # ─── Retrain pipeline ─────────────────────────────────────────────────────

    def _try_retrain(self) -> None:
        """
        Safe retrain pipeline:
          1. Get training rows from WorldStateStore
          2. Retrain LightGBM
          3. Backtest new model vs current production model
          4. If improved → save as staging model
          5. Manual promotion required before production use
        """
        try:
            if not self._retrain_allowed():
                logger.info(
                    "LearningAgent: retrain schedule gate — skipping until next window (%dd)",
                    self._retrain_interval_days(),
                )
                return

            rows = self._world.get_training_rows(last_n_days=90)
            if len(rows) < 200:
                logger.info(
                    "LearningAgent: only %d training rows — need 200 to retrain",
                    len(rows),
                )
                return

            train_result = self._train_lightgbm(rows)
            if train_result is None:
                return

            new_model = train_result["model"]
            new_brier = train_result["brier"]
            X_val = train_result["X_val"]
            y_val = train_result["y_val"]
            feature_cols = train_result["feature_cols"]

            backtest = self._backtest_compare(new_model, X_val, y_val, feature_cols)
            current_brier = backtest["current_brier"]
            improvement = current_brier - new_brier

            logger.info(
                "LearningAgent: retrain done | current_brier=%.4f "
                "new_brier=%.4f improvement=%.4f | production_model=%s",
                current_brier,
                new_brier,
                improvement,
                backtest["production_loaded"],
            )

            if improvement >= BRIER_IMPROVEMENT:
                staging_path = self._model_path.replace(
                    "production",
                    f"staging_{datetime.now(tz=timezone.utc).strftime('%Y%m%d_%H%M')}",
                )
                self._save_model(new_model, staging_path, feature_cols=feature_cols)
                logger.info(
                    "LearningAgent: NEW MODEL STAGED at %s | "
                    "improvement=%.4f | MANUAL APPROVAL REQUIRED before production",
                    staging_path,
                    improvement,
                )
                self._write_promotion_request(
                    staging_path,
                    new_brier,
                    improvement,
                    len(rows),
                    backtest=backtest,
                )
            else:
                logger.info(
                    "LearningAgent: new model did not improve enough "
                    "(%.4f < %.4f required) — keeping current model",
                    improvement,
                    BRIER_IMPROVEMENT,
                )

            self._new_samples = 0
            self._mark_retrain_complete()

        except Exception as e:
            logger.error("LearningAgent: retrain failed: %s", e, exc_info=True)

    def _retrain_interval_days(self) -> int:
        from config.settings import get_settings

        return max(1, int(get_settings().retrain_schedule_days))

    def _retrain_allowed(self) -> bool:
        if not RETRAIN_SCHEDULED:
            return True
        state = self._load_retrain_state()
        last = state.get("last_retrain")
        if not last:
            return True
        last_dt = datetime.fromisoformat(last)
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        return datetime.now(tz=timezone.utc) - last_dt >= timedelta(days=self._retrain_interval_days())

    def _load_retrain_state(self) -> dict:
        if not RETRAIN_STATE_PATH.exists():
            return {}
        try:
            return json.loads(RETRAIN_STATE_PATH.read_text())
        except Exception:
            return {}

    def _mark_retrain_complete(self) -> None:
        RETRAIN_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        state = self._load_retrain_state()
        state["last_retrain"] = datetime.now(tz=timezone.utc).isoformat()
        RETRAIN_STATE_PATH.write_text(json.dumps(state, indent=2))

    def _build_feature_matrix(
        self, rows: list[dict]
    ) -> tuple[Any, Any, list[str]]:
        import numpy as np

        feature_cols = [k for k in rows[0].keys() if k not in META_COLS]
        X = np.array([[row.get(col, 0.0) or 0.0 for col in feature_cols] for row in rows])
        y = np.array([row["label"] for row in rows])
        return X, y, feature_cols

    def _brier_score(self, model, X, y) -> float:
        import numpy as np

        preds = model.predict(X)
        return float(np.mean((preds - y) ** 2))

    def _load_production_model(self) -> tuple[Any | None, list[str]]:
        path = Path(self._model_path)
        if not path.exists():
            return None, []
        try:
            import pickle

            with open(path, "rb") as f:
                payload = pickle.load(f)
            if isinstance(payload, dict):
                return payload.get("model"), list(payload.get("feature_cols") or [])
            return payload, []
        except Exception as e:
            logger.warning("LearningAgent: could not load production model: %s", e)
            return None, []

    def _backtest_compare(
        self,
        new_model,
        X_val,
        y_val,
        feature_cols: list[str],
    ) -> dict:
        """
        Backtest new model vs current production model on the same validation holdout.
        Falls back to WorldStateStore calibration Brier when no production model exists.
        """
        production_model, prod_cols = self._load_production_model()
        world_brier = float(self._world.stats().get("brier_score", 1.0))

        if production_model is None:
            return {
                "current_brier": world_brier,
                "new_brier": self._brier_score(new_model, X_val, y_val),
                "production_loaded": False,
                "comparison": "world_state_calibration",
            }

        try:
            if prod_cols and prod_cols != feature_cols:
                import numpy as np

                # Align columns when feature set evolved between retrains
                idx = {col: i for i, col in enumerate(feature_cols)}
                aligned = np.zeros((len(X_val), len(prod_cols)))
                for j, col in enumerate(prod_cols):
                    if col in idx:
                        aligned[:, j] = X_val[:, idx[col]]
                current_brier = self._brier_score(production_model, aligned, y_val)
            else:
                current_brier = self._brier_score(production_model, X_val, y_val)
        except Exception as e:
            logger.warning(
                "LearningAgent: production backtest failed (%s) — using world brier",
                e,
            )
            current_brier = world_brier

        return {
            "current_brier": current_brier,
            "new_brier": self._brier_score(new_model, X_val, y_val),
            "production_loaded": True,
            "comparison": "holdout_backtest",
        }

    def _train_lightgbm(self, rows: list[dict]) -> Optional[dict]:
        """Train a LightGBM model on WorldState training rows."""
        try:
            import lightgbm as lgb

            X, y, feature_cols = self._build_feature_matrix(rows)

            split = int(len(X) * 0.80)
            X_train, X_val = X[:split], X[split:]
            y_train, y_val = y[:split], y[split:]

            train_data = lgb.Dataset(X_train, label=y_train)
            val_data = lgb.Dataset(X_val, label=y_val, reference=train_data)

            params = {
                "objective": "binary",
                "metric": "binary_logloss",
                "learning_rate": 0.05,
                "num_leaves": 31,
                "min_data_in_leaf": 20,
                "feature_fraction": 0.8,
                "bagging_fraction": 0.8,
                "bagging_freq": 5,
                "verbose": -1,
            }

            model = lgb.train(
                params,
                train_data,
                num_boost_round=200,
                valid_sets=[val_data],
                callbacks=[lgb.early_stopping(20), lgb.log_evaluation(50)],
            )

            brier = self._brier_score(model, X_val, y_val)

            logger.info(
                "LightGBM trained: n_train=%d n_val=%d brier=%.4f",
                len(X_train),
                len(X_val),
                brier,
            )
            return {
                "model": model,
                "brier": brier,
                "X_val": X_val,
                "y_val": y_val,
                "feature_cols": feature_cols,
            }

        except ImportError:
            logger.warning("LightGBM not installed — skipping retrain")
            return None
        except Exception as e:
            logger.error("LightGBM training failed: %s", e)
            return None

    def _save_model(self, model, path: str, feature_cols: list[str] | None = None) -> None:
        try:
            import pickle

            Path(path).parent.mkdir(parents=True, exist_ok=True)
            payload = {"model": model, "feature_cols": feature_cols or []}
            with open(path, "wb") as f:
                pickle.dump(payload, f)
            logger.info("LearningAgent: model saved to %s", path)
        except Exception as e:
            logger.error("LearningAgent: save failed: %s", e)

    def _write_promotion_request(
        self,
        staging_path: str,
        brier: float,
        improvement: float,
        n_samples: int,
        backtest: dict | None = None,
    ) -> None:
        """Write a promotion request file for manual review."""
        req = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "staging_model": staging_path,
            "new_brier": brier,
            "improvement": improvement,
            "n_samples": n_samples,
            "paper_days_required": PAPER_DAYS_BEFORE_LIVE,
            "status": "AWAITING_MANUAL_APPROVAL",
            "backtest": backtest or {},
            "instructions": (
                "1. Paper trade this model for 5 sessions. "
                "2. Review brier score and win rate. "
                "3. If satisfied, copy to production path and restart. "
                "4. NEVER auto-promote to live trading."
            ),
        }
        req_path = (
            Path("logs/promotion_requests")
            / f"request_{datetime.now(tz=timezone.utc).strftime('%Y%m%d_%H%M')}.json"
        )
        req_path.parent.mkdir(parents=True, exist_ok=True)
        with open(req_path, "w") as f:
            json.dump(req, f, indent=2)
        logger.info("LearningAgent: promotion request written to %s", req_path)

    def _log_outcome(self, row: dict) -> None:
        try:
            with open(OUTCOMES_LOG_PATH, "a") as f:
                f.write(json.dumps(row) + "\n")
        except Exception as e:
            logger.error("LearningAgent: log write failed: %s", e)

    # ─── Session summary ──────────────────────────────────────────────────────

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
            "win_rate": len(wins) / len(outcomes),
            "total_pnl": sum(o["pnl"] for o in outcomes),
            "avg_mfe": sum(o.get("mfe_ticks", 0) for o in outcomes) / len(outcomes),
            "avg_mae": sum(o.get("mae_ticks", 0) for o in outcomes) / len(outcomes),
            "avg_r": sum(o["r_multiple"] for o in outcomes) / len(outcomes),
            "world_stats": self._world.stats(),
        }
