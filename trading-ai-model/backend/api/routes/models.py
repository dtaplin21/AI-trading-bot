"""
api/routes/models.py

Model management API routes.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter(prefix="/models", tags=["models"])

_learning_agent = None
_model_registry = None
_promotion_policy = None


def set_agents(learning_agent, model_registry, promotion_policy) -> None:
    global _learning_agent, _model_registry, _promotion_policy
    _learning_agent = learning_agent
    _model_registry = model_registry
    _promotion_policy = promotion_policy


class ApproveRequest(BaseModel):
    approved_by: str
    notes: str = ""


class RollbackRequest(BaseModel):
    rolled_back_by: str
    reason: str = ""


class PolicyUpdateRequest(BaseModel):
    auto_promote: Optional[bool] = None
    min_samples: Optional[int] = None
    min_brier_improvement: Optional[float] = None
    min_holdout_auc: Optional[float] = None
    max_calibration_drift: Optional[float] = None


@router.post("/retrain")
async def trigger_retrain(
    symbol: Optional[str] = Query(None),
    timeframe: Optional[str] = Query(None),
    requested_by: str = Query("api"),
):
    """
    Manually trigger a model retrain.
    Uses the same unified RetrainPipeline as the auto-trigger.
    If MODEL_AUTO_PROMOTE=true and gates pass, model promotes automatically.
    """
    if not _learning_agent:
        raise HTTPException(status_code=503, detail="Learning agent not initialized")

    result = _learning_agent.force_retrain(
        requested_by=requested_by,
        symbol=symbol,
        timeframe=timeframe,
    )
    return {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "requested_by": requested_by,
        "symbol": symbol,
        "timeframe": timeframe,
        **result,
    }


@router.get("")
async def list_models(
    status: Optional[str] = Query(None, description="Filter by status"),
    limit: int = Query(20, ge=1, le=100),
):
    """List all model versions with their metrics."""
    if not _model_registry:
        raise HTTPException(status_code=503, detail="Model registry not initialized")
    records = _model_registry.list_models(status=status)[:limit]
    return [r.to_dict() for r in records]


@router.get("/production")
async def get_production_model():
    """Current production model info."""
    if not _model_registry:
        raise HTTPException(status_code=503, detail="Model registry not initialized")
    rec = _model_registry.get_production_record()
    if not rec:
        return {"production": None, "message": "No production model yet"}
    return {
        "production": rec.to_dict(),
        "auto_promote": os.getenv("MODEL_AUTO_PROMOTE", "false"),
        "registry_summary": _model_registry.status_summary(),
    }


@router.get("/promotions")
async def get_promotion_history(limit: int = Query(20, ge=1, le=100)):
    """
    Full promotion audit history — every approval, rejection, and rollback.
    Append-only. Cannot be deleted.
    """
    if not _promotion_policy:
        raise HTTPException(status_code=503, detail="Promotion policy not initialized")
    history = _promotion_policy.get_audit_history(last_n=limit)
    return {
        "count": len(history),
        "history": history,
    }


@router.get("/policy")
async def get_policy():
    """Current auto-promote policy settings."""
    if not _promotion_policy:
        raise HTTPException(status_code=503, detail="Promotion policy not initialized")
    return {
        "auto_promote": _promotion_policy.auto_promote,
        "min_samples": _promotion_policy.min_samples,
        "min_brier_improvement": _promotion_policy.min_brier_improvement,
        "min_holdout_auc": _promotion_policy.min_holdout_auc,
        "max_calibration_drift": _promotion_policy.max_calibration_drift,
        "min_positive_rate": _promotion_policy.min_positive_rate,
        "max_positive_rate": _promotion_policy.max_positive_rate,
        "note": (
            "To change permanently, update .env and restart. "
            "PUT /models/policy updates in-memory only (resets on restart)."
        ),
    }


@router.put("/policy")
async def update_policy(req: PolicyUpdateRequest):
    """
    Update auto-promote policy thresholds at runtime.
    Changes are in-memory only — add to .env for persistence.
    """
    if not _promotion_policy:
        raise HTTPException(status_code=503, detail="Promotion policy not initialized")

    updated: dict = {}
    if req.auto_promote is not None:
        _promotion_policy.auto_promote = req.auto_promote
        os.environ["MODEL_AUTO_PROMOTE"] = str(req.auto_promote).lower()
        updated["auto_promote"] = req.auto_promote
    if req.min_samples is not None:
        _promotion_policy.min_samples = req.min_samples
        updated["min_samples"] = req.min_samples
    if req.min_brier_improvement is not None:
        _promotion_policy.min_brier_improvement = req.min_brier_improvement
        updated["min_brier_improvement"] = req.min_brier_improvement
    if req.min_holdout_auc is not None:
        _promotion_policy.min_holdout_auc = req.min_holdout_auc
        updated["min_holdout_auc"] = req.min_holdout_auc
    if req.max_calibration_drift is not None:
        _promotion_policy.max_calibration_drift = req.max_calibration_drift
        updated["max_calibration_drift"] = req.max_calibration_drift

    return {
        "updated": updated,
        "policy_now": {
            "auto_promote": _promotion_policy.auto_promote,
            "min_samples": _promotion_policy.min_samples,
            "min_brier_improvement": _promotion_policy.min_brier_improvement,
            "min_holdout_auc": _promotion_policy.min_holdout_auc,
            "max_calibration_drift": _promotion_policy.max_calibration_drift,
        },
        "warning": "Changes are in-memory only. Add to .env for persistence.",
    }


@router.post("/{model_id}/approve")
async def approve_model(model_id: str, req: ApproveRequest):
    """
    Manually approve and promote a staged model.
    Use when MODEL_AUTO_PROMOTE=false.
    """
    if not _learning_agent:
        raise HTTPException(status_code=503, detail="Learning agent not initialized")
    if not req.approved_by or req.approved_by == "auto":
        raise HTTPException(
            status_code=400,
            detail="approved_by must be a real person's name, not 'auto'",
        )
    result = _learning_agent.manual_approve_model(model_id, req.approved_by)
    return {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "model_id": model_id,
        "approved_by": req.approved_by,
        "notes": req.notes,
        **result,
    }


@router.post("/{model_id}/rollback")
async def rollback_model(model_id: str, req: RollbackRequest):
    """
    Rollback production to a specific prior model.
    Takes effect on next prediction (reload runs immediately).
    """
    if not _learning_agent:
        raise HTTPException(status_code=503, detail="Learning agent not initialized")
    result = _learning_agent.rollback_model(model_id, req.rolled_back_by)
    return {
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "rolled_back_by": req.rolled_back_by,
        "reason": req.reason,
        **result,
    }
