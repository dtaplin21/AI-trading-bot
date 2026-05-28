"""Tests for AnthropicClient."""

import pytest

from config.settings import get_settings
from llm.anthropic_client import AnthropicClient, reset_anthropic_client


@pytest.fixture(autouse=True)
def _clear_settings():
    get_settings.cache_clear()
    reset_anthropic_client()
    yield
    get_settings.cache_clear()
    reset_anthropic_client()


def test_not_configured_without_key(monkeypatch):
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    client = AnthropicClient()
    assert client.is_configured is False


def test_configured_with_key(monkeypatch):
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    client = AnthropicClient()
    assert client.is_configured is True


def test_complete_raises_when_disabled(monkeypatch):
    monkeypatch.setenv("LLM_ENABLED", "false")
    client = AnthropicClient()
    with pytest.raises(RuntimeError, match="disabled"):
        import asyncio

        asyncio.run(client.complete(user="hello"))
