"""Tracks which method agents have validated edge for a symbol/regime."""

from __future__ import annotations

import logging
from typing import Optional

from config.agent_config import DEFAULT_PROVEN_METHODS, METHOD_NAME_ALIASES

logger = logging.getLogger(__name__)


class MethodEdgeRegistry:
    """
    Knows which methods are proven vs research-only.
    In production this loads from backtest / isolation validator results.
    """

    def __init__(self, approved: Optional[set[str]] = None) -> None:
        self._approved = approved if approved is not None else set(DEFAULT_PROVEN_METHODS)
        logger.debug("MethodEdgeRegistry: %d approved methods", len(self._approved))

    def normalize_name(self, method_name: str) -> str:
        return METHOD_NAME_ALIASES.get(method_name, method_name)

    def is_approved(self, method_name: str, symbol: str, timeframe: str, regime: str) -> bool:
        name = self.normalize_name(method_name)
        if name == "gann":
            return False
        return name in self._approved

    def approve(self, method_name: str) -> None:
        self._approved.add(self.normalize_name(method_name))

    def revoke(self, method_name: str) -> None:
        self._approved.discard(self.normalize_name(method_name))
