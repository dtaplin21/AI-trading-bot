"""CORS origin parsing for api.main."""

import os

from api.main import _cors_origins, _DEFAULT_CORS_ORIGINS


def test_cors_origins_default(monkeypatch):
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    assert _cors_origins() == [
        o.strip() for o in _DEFAULT_CORS_ORIGINS.split(",") if o.strip()
    ]


def test_cors_origins_custom(monkeypatch):
    monkeypatch.setenv(
        "CORS_ORIGINS",
        "https://app.vercel.app, https://preview.vercel.app ",
    )
    assert _cors_origins() == [
        "https://app.vercel.app",
        "https://preview.vercel.app",
    ]
