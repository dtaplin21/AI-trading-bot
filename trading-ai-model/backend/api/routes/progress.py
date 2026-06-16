"""
api/routes/progress.py

GET /progress — fast-lane level progress for all watched symbols.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get("")
def get_progress():
    try:
        from api.services.progress_service import build_progress_payload

        return build_progress_payload()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
