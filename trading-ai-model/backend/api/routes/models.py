"""Model retraining and promotion routes — manual approval required."""

from fastapi import APIRouter, HTTPException, Query

from agents.learning.model_registry import ModelRegistry
from agents.learning.retrain_pipeline import RetrainPipeline

router = APIRouter()
_registry = ModelRegistry()
_pipeline = RetrainPipeline()


@router.get("")
def list_models():
    return {
        "production_id": _registry.production_model_id(),
        "models": _registry.list_models(),
    }


@router.post("/retrain")
def trigger_retrain(force: bool = Query(False)):
    """Weekly retrain — creates candidate model only, never auto-promotes."""
    return _pipeline.run_scheduled_retrain(force=force)


@router.get("/retrain/status")
def retrain_status():
    state_file = _pipeline.state_file
    state = _pipeline._load_state() if state_file.exists() else {}
    return {
        "due_for_retrain": _pipeline.due_for_retrain(),
        "schedule_days": _pipeline.settings.retrain_schedule_days,
        "state": state,
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
    """Deploy approved model to production — copies artifact to lightgbm_production.txt."""
    try:
        entry = _pipeline.promote_model(model_id, approved_by=approved_by)
        return {"model": entry, "message": "Promoted to production. Restart API to reload model."}
    except (KeyError, ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
