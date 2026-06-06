"""Tests for futures contract roll windows."""

from data.providers.futures_contracts import (
    contract_to_polygon_ticker,
    get_contract_windows,
    get_contract_windows_for_job,
    infer_backfill_year,
    uses_futures_contract_roll,
    uses_futures_contract_roll_for_job,
)
from data.providers.polygon_backfill import parse_date


def test_mes_has_four_quarterly_contracts_2025():
    start = parse_date("2025-01-01")
    end = parse_date("2025-12-31")
    windows = get_contract_windows("MES", 2025, start, end)
    codes = [w.contract_code for w in windows]
    assert codes == ["MESH25", "MESM25", "MESU25", "MESZ25"]
    assert all(w.polygon_ticker == w.contract_code for w in windows)


def test_cl_has_twelve_monthly_contracts_2025():
    start = parse_date("2025-01-01")
    end = parse_date("2025-12-31")
    windows = get_contract_windows("CL", 2025, start, end)
    assert len(windows) == 12
    assert windows[0].contract_code == "CLF25"
    assert windows[-1].contract_code == "CLZ25"


def test_uses_roll_for_futures_not_equity():
    assert uses_futures_contract_roll("MES", 2025) is True
    assert uses_futures_contract_roll("TSLA", 2025) is False


def test_job_clipped_to_year():
    start = parse_date("2025-07-01")
    end = parse_date("2025-12-31")
    windows = get_contract_windows("ES", 2025, start, end)
    assert windows[0].contract_code == "ESU25"
    assert windows[0].start.month == 7


def test_infer_backfill_year():
    assert infer_backfill_year("2025-01-01", "2025-12-31") == 2025


def test_contract_to_polygon_ticker():
    assert contract_to_polygon_ticker("mesh25") == "MESH25"


def test_mes_two_year_job_has_eight_contracts():
    start = parse_date("2024-01-01")
    end = parse_date("2025-12-31")
    windows = get_contract_windows_for_job("MES", start, end)
    codes = [w.contract_code for w in windows]
    assert codes == [
        "MESH24",
        "MESM24",
        "MESU24",
        "MESZ24",
        "MESH25",
        "MESM25",
        "MESU25",
        "MESZ25",
    ]
    assert uses_futures_contract_roll_for_job("MES", start, end) is True


def test_mes_2024_quarterly():
    start = parse_date("2024-01-01")
    end = parse_date("2024-12-31")
    windows = get_contract_windows("MES", 2024, start, end)
    assert [w.contract_code for w in windows] == ["MESH24", "MESM24", "MESU24", "MESZ24"]
