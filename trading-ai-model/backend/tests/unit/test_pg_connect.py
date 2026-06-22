"""Tests for remote Postgres SSL connect kwargs."""

from pathlib import Path

import pytest

from data.storage.pg_connect import (
    _is_render_internal_host,
    _validate_database_url,
    is_database_url_placeholder,
    normalize_database_url,
    psycopg2_connect_kwargs,
)


def test_localhost_no_ssl_kwargs():
    url = "postgresql://u:p@localhost:5432/db"
    assert psycopg2_connect_kwargs(url) == {"connect_timeout": 30}


def test_connect_timeout_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_CONNECT_TIMEOUT", "45")
    url = "postgresql://u:p@localhost:5432/db"
    assert psycopg2_connect_kwargs(url)["connect_timeout"] == 45


def test_render_host_gets_sslrootcert():
    url = "postgresql://u:p@dpg-abc.oregon-postgres.render.com:5432/db?sslmode=require"
    kwargs = psycopg2_connect_kwargs(url)
    assert kwargs.get("sslmode") == "require"
    rootcert = kwargs.get("sslrootcert")
    assert rootcert and Path(rootcert).is_file()


def test_ssl_disable_ignored_for_remote_render():
    url = "postgresql://u:p@dpg-abc.oregon-postgres.render.com:5432/db"
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("DATABASE_SSL_DISABLE", "true")
    try:
        kwargs = psycopg2_connect_kwargs(url)
        assert kwargs.get("sslmode") == "require"
        assert kwargs.get("sslrootcert")
    finally:
        monkeypatch.undo()


def test_ssl_disable_applies_to_localhost(monkeypatch):
    monkeypatch.setenv("DATABASE_SSL_DISABLE", "true")
    url = "postgresql://u:p@localhost:5432/db"
    kwargs = psycopg2_connect_kwargs(url)
    assert kwargs.get("sslmode") == "disable"


def test_rejects_render_placeholder_url():
    with pytest.raises(ValueError, match="placeholder"):
        _validate_database_url("postgresql://trading:<paste External URL>@host/db")


def test_detects_render_hint_without_scheme():
    assert is_database_url_placeholder("<paste External URL with ?sslmode=require>")


def test_normalize_adds_sslmode_for_remote():
    url = "postgresql://u:p@dpg-abc.oregon-postgres.render.com:5432/db"
    normalized = normalize_database_url(url)
    assert "sslmode=require" in normalized


def test_render_internal_host_detection():
    assert _is_render_internal_host("dpg-d8ed4h4m0tmc73eof7a0-a")
    assert not _is_render_internal_host("dpg-d8ed4h4m0tmc73eof7a0-a.oregon-postgres.render.com")
    assert not _is_render_internal_host("localhost")


def test_render_internal_url_no_sslrootcert():
    url = "postgresql://u:p@dpg-d8ed4h4m0tmc73eof7a0-a/trading_ai"
    kwargs = psycopg2_connect_kwargs(url)
    assert kwargs.get("sslmode") == "prefer"
    assert "sslrootcert" not in kwargs


def test_normalize_internal_render_adds_prefer():
    url = "postgresql://u:p@dpg-d8ed4h4m0tmc73eof7a0-a/trading_ai"
    normalized = normalize_database_url(url)
    assert "sslmode=prefer" in normalized
