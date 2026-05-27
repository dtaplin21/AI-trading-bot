"""GET /dashboard — platforms, open positions, watched charts."""

from fastapi import APIRouter

from api.services.dashboard_service import build_dashboard

router = APIRouter()


@router.get("")
def dashboard_overview():
    return build_dashboard()
