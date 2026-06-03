"""Tests for env placeholder detection and .env fallback."""

from config.env_resolve import env_var_from_file, is_env_placeholder, resolve_env


def test_detects_polygon_placeholder():
    assert is_env_placeholder("<your key>")
    assert not is_env_placeholder("pk_real_key_abc123")


def test_resolve_env_ignores_placeholder(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("POLYGON_API_KEY=from_dotenv_key\n", encoding="utf-8")
    monkeypatch.setenv("POLYGON_API_KEY", "<your key>")
    assert resolve_env("POLYGON_API_KEY", tmp_path) == "from_dotenv_key"


def test_env_var_from_file_reads_value(tmp_path):
    (tmp_path / ".env").write_text(
        "# comment\nPOLYGON_API_KEY=abc\n",
        encoding="utf-8",
    )
    assert env_var_from_file("POLYGON_API_KEY", tmp_path) == "abc"
