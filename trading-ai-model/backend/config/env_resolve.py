"""Resolve env vars when the shell exports dashboard placeholders over backend/.env."""

from __future__ import annotations

import os
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent


def is_env_placeholder(value: str) -> bool:
    """True for Render/docs placeholders, not real secrets."""
    lower = (value or "").strip().lower()
    if not lower:
        return False
    markers = (
        "<your",
        "<paste",
        "your key",
        "paste your",
        "paste external",
        "changeme",
        "replace_me",
        "xxx",
    )
    return any(m in lower for m in markers)


def env_var_from_file(name: str, backend_dir: Path | None = None) -> str:
    env_path = (backend_dir or _BACKEND_DIR) / ".env"
    if not env_path.is_file():
        return ""
    prefix = f"{name}="
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or not stripped.startswith(prefix):
            continue
        value = stripped.split("=", 1)[1].strip().strip('"').strip("'")
        if value and not is_env_placeholder(value):
            return value
    return ""


def resolve_env(name: str, backend_dir: Path | None = None) -> str:
    """Prefer a real value from backend/.env when the environment has a placeholder."""
    raw = os.getenv(name, "").strip()
    if raw and not is_env_placeholder(raw):
        return raw
    return env_var_from_file(name, backend_dir)
