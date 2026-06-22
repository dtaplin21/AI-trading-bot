"""
Shared psycopg2 connection helpers for Render / remote Postgres.

SSL behavior:
  - localhost: optional disable via DATABASE_SSL_DISABLE
  - Render internal (dpg-*-a): sslmode=prefer, no CA bundle (private network cert)
  - Render external / other remote: sslmode=require + certifi CA bundle
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

logger = logging.getLogger(__name__)

_RENDER_INTERNAL_HOST = re.compile(r"^dpg-[a-z0-9]+-a$", re.IGNORECASE)


def _hostname(database_url: str) -> str:
    try:
        return (urlparse(database_url).hostname or "").lower()
    except Exception:
        return ""


def _is_render_internal_host(host: str) -> bool:
    """
    Render Internal Database URL host (private network), e.g. dpg-d8ed4h4m0tmc73eof7a0-a.

    External URLs use the full domain (*.oregon-postgres.render.com) and need certifi.
    """
    h = (host or "").lower()
    if not h or ".render.com" in h:
        return False
    return bool(_RENDER_INTERNAL_HOST.match(h))


def _remote_ssl_host(database_url: str) -> bool:
    """True when connecting to a hosted Postgres (not localhost)."""
    host = _hostname(database_url)
    if not host or host in ("localhost", "127.0.0.1", "::1"):
        return False
    return True


def _ssl_disable_requested() -> bool:
    """True when env asks to skip SSL — only honored for local Postgres."""
    return os.getenv("DATABASE_SSL_DISABLE", "").lower() in ("true", "1", "yes")


def _ssl_mode_from_env(*, internal: bool = False) -> str:
    """PGSSLMODE / DATABASE_SSL_MODE override; sensible default per host type."""
    override = (
        os.getenv("PGSSLMODE", "").strip()
        or os.getenv("DATABASE_SSL_MODE", "").strip()
    )
    if override:
        return override
    return "prefer" if internal else "require"


def _connect_timeout_seconds() -> int:
    """Seconds before connect() fails; avoids hung backfill writes to remote Postgres."""
    raw = (
        os.getenv("PGCONNECT_TIMEOUT", "").strip()
        or os.getenv("DATABASE_CONNECT_TIMEOUT", "").strip()
        or "30"
    )
    try:
        return max(1, int(raw))
    except ValueError:
        return 30


def normalize_database_url(database_url: str) -> str:
    """
    Ensure remote URLs include sslmode when missing (Render / external Postgres).

    Strips placeholder query junk; does not remove an existing valid sslmode.
    """
    if is_database_url_placeholder(database_url):
        return database_url
    if not _remote_ssl_host(database_url):
        return database_url

    host = _hostname(database_url)
    parsed = urlparse(database_url)
    qs = parse_qs(parsed.query)
    if "sslmode" not in qs:
        mode = _ssl_mode_from_env(internal=_is_render_internal_host(host))
        qs["sslmode"] = [mode]
        query = urlencode({k: v[0] for k, v in qs.items()})
        return urlunparse(parsed._replace(query=query))
    return database_url


def psycopg2_connect_kwargs(database_url: str) -> dict[str, Any]:
    """
    Extra kwargs for psycopg2.connect(dsn, **kwargs).

    Env:
      PGCONNECT_TIMEOUT / DATABASE_CONNECT_TIMEOUT — connect_timeout seconds (default 30)
      PGSSLMODE / DATABASE_SSL_MODE — sslmode override
      DATABASE_SSL_ROOTCERT       — path to CA bundle (external remote only)
      DATABASE_SSL_DISABLE        — if true, sslmode=disable for localhost only
    """
    kwargs: dict[str, Any] = {"connect_timeout": _connect_timeout_seconds()}

    if not _remote_ssl_host(database_url):
        if _ssl_disable_requested():
            kwargs["sslmode"] = "disable"
        return kwargs

    host = _hostname(database_url)
    internal = _is_render_internal_host(host)
    sslmode = _ssl_mode_from_env(internal=internal)
    if sslmode.lower() == "disable":
        kwargs["sslmode"] = "disable"
        return kwargs

    if internal:
        # Render private-network Postgres — do not verify against public CA bundle.
        kwargs["sslmode"] = sslmode
        return kwargs

    rootcert = os.getenv("DATABASE_SSL_ROOTCERT", "").strip()
    if not rootcert:
        try:
            import certifi

            rootcert = certifi.where()
        except ImportError:
            logger.warning(
                "certifi not installed — remote Postgres SSL may fail; pip install certifi"
            )
            kwargs["sslmode"] = sslmode
            return kwargs

    kwargs["sslmode"] = sslmode
    kwargs["sslrootcert"] = rootcert
    return kwargs


def is_database_url_placeholder(database_url: str) -> bool:
    """True when URL is a dashboard hint, not a real connection string."""
    lower = (database_url or "").strip().lower()
    if not lower:
        return False
    if not lower.startswith("postgresql://") and not lower.startswith("postgres://"):
        if any(m in lower for m in ("<paste", "paste external", "paste your")):
            return True
    return any(m in lower for m in ("<paste", "<your", "paste your", "paste external"))


def _validate_database_url(database_url: str) -> None:
    """Reject placeholder URLs before libpq returns an opaque DSN error."""
    if is_database_url_placeholder(database_url):
        raise ValueError(
            "DATABASE_URL contains a Render/dashboard placeholder (<paste...>). "
            "Unset the shell variable (unset DATABASE_URL) and set the real URL in "
            "trading-ai-model/backend/.env, or export the full External Database URL."
        )


def connect_psycopg2(database_url: str):
    """Open a psycopg2 connection with Render-friendly SSL defaults."""
    import psycopg2
    from psycopg2 import OperationalError

    _validate_database_url(database_url)
    url = normalize_database_url(database_url)
    kwargs = psycopg2_connect_kwargs(url)
    try:
        return psycopg2.connect(url, **kwargs)
    except OperationalError as exc:
        err = str(exc).lower()
        if "ssl" not in err and "certificate" not in err:
            raise

        host = _hostname(url)
        if _is_render_internal_host(host):
            # Internal URL + cert verify failure: retry without sslrootcert, then disable.
            for mode in ("prefer", "disable"):
                try:
                    retry_kwargs = {
                        "connect_timeout": kwargs.get("connect_timeout", 30),
                        "sslmode": mode,
                    }
                    logger.warning("Postgres internal Render retry sslmode=%s", mode)
                    return psycopg2.connect(url, **retry_kwargs)
                except OperationalError:
                    continue
            raise exc from None

        # External / macOS: retry once with explicit certifi bundle
        try:
            import certifi

            retry_kwargs = dict(kwargs)
            if "ssl/tls required" in err or kwargs.get("sslmode") == "disable":
                retry_kwargs["sslmode"] = "require"
            else:
                retry_kwargs["sslmode"] = kwargs.get("sslmode", "require")
            retry_kwargs["sslrootcert"] = certifi.where()
            logger.warning("Postgres SSL retry with certifi CA bundle")
            return psycopg2.connect(url, **retry_kwargs)
        except Exception:
            raise exc from None
