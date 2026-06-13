"""Run async broker calls from sync legacy callers (executors, order router)."""

from __future__ import annotations

import asyncio
from typing import Coroutine, TypeVar

T = TypeVar("T")


def run_broker(coro: Coroutine[None, None, T]) -> T:
    """Execute an async broker coroutine from synchronous code."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    if loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return loop.run_until_complete(coro)


def action_to_side(action: str) -> str:
    """Map legacy signal action strings to BUY/SELL."""
    act = str(action).lower()
    if "long" in act or act in ("buy", "enter_long"):
        return "BUY"
    if "short" in act or act in ("sell", "enter_short"):
        return "SELL"
    raise ValueError(f"unsupported action {action!r}")


def broker_order_to_result(order, *, broker: str, **extra) -> dict:
    """Normalize BrokerOrder to legacy executor dict shape."""
    status = (order.status or "").upper()
    if status in ("FILLED", "PENDING"):
        return {
            "status": "filled",
            "broker": broker,
            "order_id": order.broker_order_id,
            **extra,
        }
    return {
        "status": "rejected",
        "message": order.error_message or "order_rejected",
    }
