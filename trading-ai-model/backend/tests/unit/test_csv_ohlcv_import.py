"""Tests for OHLCV CSV import helpers."""

from pathlib import Path

import pandas as pd
import pytest

from data.providers.csv_ohlcv_import import (
    discover_ohlcv_csvs,
    load_ohlcv_csv,
    parse_ohlcv_csv_stem,
)


def test_parse_ohlcv_csv_stem():
    assert parse_ohlcv_csv_stem("MES_1m") == ("MES", "1m")
    assert parse_ohlcv_csv_stem("btcusd_1m") == ("BTCUSD", "1m")
    assert parse_ohlcv_csv_stem("not_a_bar_file") is None


def test_discover_ohlcv_csvs_filters(tmp_path: Path):
    (tmp_path / "MES_1m.csv").write_text("timestamp,open,high,low,close,volume\n")
    (tmp_path / "ES_5m.csv").write_text("timestamp,open,high,low,close,volume\n")
    (tmp_path / "notes.txt").write_text("ignore\n")

    all_1m = discover_ohlcv_csvs(tmp_path, timeframe="1m")
    assert [item[1] for item in all_1m] == ["MES"]

    mes_only = discover_ohlcv_csvs(tmp_path, symbols={"MES"}, timeframe="1m")
    assert len(mes_only) == 1
    assert mes_only[0][1] == "MES"


def test_load_ohlcv_csv(tmp_path: Path):
    path = tmp_path / "MES_1m.csv"
    path.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2025-01-06T14:30:00Z,1,2,0.5,1.5,100\n"
        "2025-01-06T14:31:00Z,1.5,2.5,1,2,110\n"
    )
    df = load_ohlcv_csv(path)
    assert len(df) == 2
    assert df.iloc[0]["close"] == 1.5
    assert df.index.tz is not None


def test_load_ohlcv_csv_missing_columns(tmp_path: Path):
    path = tmp_path / "MES_1m.csv"
    path.write_text("timestamp,open,high,low,close\n2025-01-06T14:30:00Z,1,2,0.5,1.5\n")
    with pytest.raises(ValueError, match="missing columns"):
        load_ohlcv_csv(path)
