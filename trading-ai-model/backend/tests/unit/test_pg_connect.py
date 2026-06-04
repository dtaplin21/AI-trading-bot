"""Tests for remote Postgres SSL connect kwargs."""

from pathlib import Path

import pytest

from data.storage.pg_connect import (
    _validate_database_url,
    is_database_url_placeholder,
    normalize_database_url,
    psycopg2_connect_kwargs,
)


def test_localhost_no_ssl_kwargs():
    url = "postgresql://u:p@localhost:5432/db"
    assert psycopg2_connect_kwargs(url) == {}


def test_render_host_gets_sslrootcert():
    url = "postgresql://u:p@dpg-abc.oregon-postgres.render.com:5432/db?sslmode=require"
    kwargs = psycopg2_connect_kwargs(url)
    assert kwargs.get("sslmode") == "require"
    rootcert = kwargs.get("sslrootcert")
    assert rootcert and Path(rootcert).is_file()


def test_rejects_render_placeholder_url():
    with pytest.raises(ValueError, match="placeholder"):
        _validate_database_url("postgresql://trading:<paste External URL>@host/db")


def test_detects_render_hint_without_scheme():
    assert is_database_url_placeholder("<paste External URL with ?sslmode=require>")


def test_normalize_adds_sslmode_for_remote():
    url = "postgresql://u:p@dpg-abc.oregon-postgres.render.com:5432/db"
    normalized = normalize_database_url(url)
    assert "sslmode=require" in normalized
