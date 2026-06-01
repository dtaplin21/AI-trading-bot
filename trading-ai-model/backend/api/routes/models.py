"""Model retraining and promotion routes — gates in PromotionPolicy."""

from fastapi import APIRouter, HTTPException, Query

from agents.learning.retrain_pipeline import RetrainPipeline
from ml.promotion.promotion_policy import PromotionPolicy
from ml.registry.model_registry import ModelRegistry

router = APIRouter()
_registry = ModelRegistry()
_pipeline = RetrainPipeline()
_policy = PromotionPolicy()


@router.get("")
def list_models():
    return {
        "production_id": _registry.production_model_id(),
        "summary": _registry.status_summary(),
        "models": _registry.list_models_dict(),
    }


@router.get("/promotions/audit")
def promotion_audit(last_n: int = Query(20, ge=1, le=100)):
    """Last N promotion decisions (auto, manual, rejected)."""
    return {"records": _policy.get_audit_history(last_n=last_n)}


@router.post("/retrain")
def trigger_retrain(force: bool = Query(False)):
    """Daily retrain — evaluates PromotionPolicy; auto-promotes if MODEL_AUTO_PROMOTE=true."""
    return _pipeline.run_scheduled_retrain(force=force)


@router.get("/retrain/status")
def retrain_status():
    state_file = _pipeline.state_file
    state = _pipeline._load_state() if state_file.exists() else {}
    return {
        "due_for_retrain": _pipeline.due_for_retrain(),
        "schedule_days": _pipeline.settings.retrain_schedule_days,
        "state": state,
        "registry": _registry.status_summary(),
    }


@router.post("/{model_id}/paper-test")
def approve_paper_test(model_id: str):
    try:
        entry = _pipeline.approve_for_paper_test(model_id)
        return {"model": entry}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{model_id}/approve")
def approve_model(model_id: str):
    """Manual approval gate — required before production promotion."""
    try:
        entry = _pipeline.approve_model(model_id)
        return {"model": entry}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{model_id}/promote")
def promote_model(model_id: str, approved_by: str = Query(...)):
    """Manual production deploy — logged via PromotionPolicy.manual_approve()."""
    try:
        entry = _pipeline.promote_model(model_id, approved_by=approved_by)
        return {
            "model": entry,
            "message": "Promoted to production. Restart API to reload model.",
        }
    except (KeyError, ValueError, FileNotFoundError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{model_id}/rollback")
def rollback_model(model_id: str, rolled_back_by: str = Query(...)):
    """Rollback production to a prior archived model."""
    try:
        entry = _pipeline.rollback_model(model_id, rolled_back_by=rolled_back_by)
        return {"model": entry, "message": "Rolled back to prior model."}
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
