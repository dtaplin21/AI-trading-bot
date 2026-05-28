"""
llm/anthropic_client.py

Single Anthropic client for all LLM features (news, audit, learning summaries).
The API key is read from settings — never hardcoded or logged.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

from config.settings import Settings, get_settings

logger = logging.getLogger(__name__)

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

_client: Optional["AnthropicClient"] = None


class AnthropicClient:
    """Thin wrapper around Anthropic Messages API."""

    def __init__(self, settings: Settings | None = None) -> None:
        s = settings or get_settings()
        self.enabled = s.llm_enabled and bool(s.anthropic_api_key)
        self._api_key = s.anthropic_api_key
        self.model = s.anthropic_model
        self.default_max_tokens = s.anthropic_max_tokens

    @property
    def is_configured(self) -> bool:
        return self.enabled

    async def complete(
        self,
        user: str,
        system: str = "",
        max_tokens: int | None = None,
        temperature: float = 0.3,
    ) -> str:
        if not self.enabled:
            raise RuntimeError("Anthropic LLM disabled or ANTHROPIC_API_KEY not set")

        messages = [{"role": "user", "content": user}]
        payload: dict = {
            "model": self.model,
            "max_tokens": max_tokens or self.default_max_tokens,
            "messages": messages,
        }
        if system:
            payload["system"] = system
        if temperature != 1.0:
            payload["temperature"] = temperature

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                ANTHROPIC_MESSAGES_URL,
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
            return data["content"][0]["text"].strip()

    def complete_sync(
        self,
        user: str,
        system: str = "",
        max_tokens: int | None = None,
        temperature: float = 0.3,
    ) -> str:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(
                self.complete(user, system=system, max_tokens=max_tokens, temperature=temperature)
            )
        future = asyncio.run_coroutine_threadsafe(
            self.complete(user, system=system, max_tokens=max_tokens, temperature=temperature),
            loop,
        )
        return future.result(timeout=35)


def get_anthropic_client() -> AnthropicClient:
    global _client
    if _client is None:
        _client = AnthropicClient()
    return _client


def reset_anthropic_client() -> None:
    """Clear singleton — for tests."""
    global _client
    _client = None
