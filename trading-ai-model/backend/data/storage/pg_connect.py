"""
Shared psycopg2 connection helpers for Render / remote Postgres.

Fixes macOS "SSL error: certificate verify failed" when DATABASE_URL uses sslmode=require
by supplying sslrootcert from certifi (Mozilla CA bundle).
"""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

logger = logging.getLogger(__name__)


def _remote_ssl_host(database_url: str) -> bool:
    """True when connecting to a hosted Postgres (not localhost)."""
    try:
        host = (urlparse(database_url).hostname or "").lower()
    except Exception:
        return False
    if not host or host in ("localhost", "127.0.0.1", "::1"):
        return False
    return True


def _ssl_mode_from_env() -> str:
    """PGSSLMODE (libpq) or DATABASE_SSL_MODE override; default require for remote."""
    return (
        os.getenv("PGSSLMODE", "").strip()
        or os.getenv("DATABASE_SSL_MODE", "").strip()
        or "require"
    )


def normalize_database_url(database_url: str) -> str:
    """
    Ensure remote URLs include sslmode when missing (Render / external Postgres).

    Strips placeholder query junk; does not remove an existing valid sslmode.
    """
    if is_database_url_placeholder(database_url):
        return database_url
    if not _remote_ssl_host(database_url):
        return database_url

    parsed = urlparse(database_url)
    qs = parse_qs(parsed.query)
    if "sslmode" not in qs:
        mode = _ssl_mode_from_env()
        if os.getenv("DATABASE_SSL_DISABLE", "").lower() in ("true", "1", "yes"):
            mode = "disable"
        qs["sslmode"] = [mode]
        query = urlencode({k: v[0] for k, v in qs.items()})
        return urlunparse(parsed._replace(query=query))
    return database_url


def psycopg2_connect_kwargs(database_url: str) -> dict[str, Any]:
    """
    Extra kwargs for psycopg2.connect(dsn, **kwargs).

    Env:
      PGSSLMODE / DATABASE_SSL_MODE — sslmode (default require for remote)
      DATABASE_SSL_ROOTCERT       — path to CA bundle (default: certifi for remote)
      DATABASE_SSL_DISABLE        — if true, sslmode=disable (local docker / Render internal)
    """
    if os.getenv("DATABASE_SSL_DISABLE", "").lower() in ("true", "1", "yes"):
        return {"sslmode": "disable"}

    if not _remote_ssl_host(database_url):
        return {}

    sslmode = _ssl_mode_from_env()
    if sslmode.lower() == "disable":
        return {"sslmode": "disable"}

    rootcert = os.getenv("DATABASE_SSL_ROOTCERT", "").strip()
    if not rootcert:
        try:
            import certifi

            rootcert = certifi.where()
        except ImportError:
            logger.warning(
                "certifi not installed — remote Postgres SSL may fail; pip install certifi"
            )
            return {"sslmode": sslmode}

    return {"sslmode": sslmode, "sslrootcert": rootcert}


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
        if kwargs:
            return psycopg2.connect(url, **kwargs)
        return psycopg2.connect(url)
    except OperationalError as exc:
        err = str(exc).lower()
        if "ssl" not in err and "certificate" not in err:
            raise
        # Retry once with explicit certifi bundle (macOS / some Render external URLs)
        try:
            import certifi

            retry_kwargs = {"sslmode": kwargs.get("sslmode", "require"), "sslrootcert": certifi.where()}
            logger.warning("Postgres SSL retry with certifi CA bundle")
            return psycopg2.connect(url, **retry_kwargs)
        except Exception:
            raise exc from None
