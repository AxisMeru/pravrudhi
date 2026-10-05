"""#300 slice C: calculator and date tools on the runner. Constructed inputs only; no clock, no network."""

from __future__ import annotations

import pytest

from pravrudhi.application.deterministic_tools import (
    MAX_EXPR_CHARS,
    add_days,
    add_months,
    calculate,
    days_between,
    register_deterministic_tools,
)
from pravrudhi.application.tool_runner import ToolRunner


def _runner() -> ToolRunner:
    r = ToolRunner(sleep=lambda _s: None)
    register_deterministic_tools(r)
    return r


@pytest.mark.parametrize(
    "expr,out",
    [
        ("1+2*3", "7"),
        ("(1+2)*3", "9"),
        ("10/4", "2.5"),
        ("2**10", "1024"),
        ("-5 + 3", "-2"),
        ("7 % 3", "1"),
        ("0.1+0.2", "0.3"),
        ("1/3", "0.3333333333333333333333333333333333333333"),
        ("250000*18/100", "45000"),
    ],
)
def test_calculate_is_exact_decimal_arithmetic(expr: str, out: str) -> None:
    assert calculate(expr) == out


@pytest.mark.parametrize(
    "expr",
    [
        "", "1/0", "__import__('os')", "a+1", "1+", "2**100000", "9**9**9", "(1)(2)", "1 if 1 else 2",
        "x" * (MAX_EXPR_CHARS + 1), "1; 2",
    ],
)
def test_calculate_refuses_anything_but_arithmetic(expr: str) -> None:
    with pytest.raises(ValueError):
        calculate(expr)


def test_days_between_is_signed_and_counts_calendar_days() -> None:
    assert days_between("2024-02-28", "2024-03-01") == 2  # leap year
    assert days_between("2023-02-28", "2023-03-01") == 1
    assert days_between("2024-03-01", "2024-02-28") == -2
    assert days_between("2024-01-01", "2024-01-01") == 0


def test_add_days_crosses_month_and_year() -> None:
    assert add_days("2023-12-31", 1) == "2024-01-01"
    assert add_days("2024-03-01", -1) == "2024-02-29"


def test_add_months_clamps_to_the_end_of_a_shorter_month() -> None:
    assert add_months("2024-01-31", 1) == "2024-02-29"
    assert add_months("2023-01-31", 1) == "2023-02-28"
    assert add_months("2024-11-15", 3) == "2025-02-15"
    assert add_months("2024-03-31", -1) == "2024-02-29"
    assert add_months("2024-02-29", 12) == "2025-02-28"


@pytest.mark.parametrize("bad", ["", "2024-13-01", "2024-02-30", "01/02/2024", "2024-1-1", "not a date"])
def test_bad_dates_are_refused_not_guessed(bad: str) -> None:
    with pytest.raises(ValueError):
        days_between(bad, "2024-01-01")


def test_out_of_range_results_are_refused() -> None:
    with pytest.raises(ValueError):
        add_days("9999-12-31", 1)
    with pytest.raises(ValueError):
        add_months("2024-01-01", 10**9)


def test_through_the_runner_values_and_typed_errors() -> None:
    r = _runner()
    assert r.call("calculate", {"expression": "2+2"}).value == "4"
    assert r.call("days_between", {"start": "2024-01-01", "end": "2024-01-31"}).value == 30
    assert r.call("add_months", {"date": "2024-01-31", "months": 1}).value == "2024-02-29"
    bad = r.call("calculate", {"expression": "1/0"})
    assert (bad.ok, bad.error) == (False, "tool_error:ValueError")
    assert r.call("calculate", {"expr": "1"}).error == "bad_arguments"
    assert [c.name for c in r.log] == ["calculate", "days_between", "add_months", "calculate", "calculate"]


# -- R1 review fixes (#190) -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expr,out",
    [
        ("1234567890123456.78+0", "1234567890123456.78"),  # was 1234567890123456.8: a float round trip changed the amount
        ("0.1+0.2", "0.3"),
        ("9999999999999999.99*1", "9999999999999999.99"),
        ("1_000.50+0", "1000.5"),
        (".5+.5", "1"),
        ("1e3+0", "1000"),
        ("12345678901234567890123+1", "12345678901234567890124"),
    ],
)
def test_a_numeric_literal_is_read_from_its_source_text_not_a_binary_float(expr: str, out: str) -> None:
    assert calculate(expr) == out


def test_the_modulo_sign_rule_is_decimals_and_is_stated() -> None:
    """`%` takes the sign of the dividend (Decimal's rule), NOT Python's: documented in the module docstring."""
    assert calculate("-7 % 3") == "-1"
    assert calculate("7 % -3") == "1"
    assert calculate("7 % 3") == "1"
    from pravrudhi.application import deterministic_tools

    assert "sign of the DIVIDEND" in (deterministic_tools.__doc__ or "")


@pytest.mark.parametrize("expr", ["(10**1000)**1000", "9**9**9", "(10**1000)**1000*10"])
def test_decimal_overflow_is_a_clean_value_error_not_an_escaping_exception(expr: str) -> None:
    with pytest.raises(ValueError):
        calculate(expr)


def test_a_failed_calculation_through_the_runner_is_an_error_result_not_a_raise() -> None:
    res = _runner().call("calculate", {"expression": "(10**1000)**1000"})
    assert not res.ok and res.error


def test_the_audit_row_carries_names_and_a_digest_never_the_argument_values() -> None:
    r = _runner()
    r.call("calculate", {"expression": "1234567.89*12 /* ACME Rs 9,87,654 */"})
    r.call("add_days", {"date": "2026-01-31", "days": 30})
    record = r.log[0]
    row = record.audit_row()
    assert row["arg_names"] == ["expression"] and len(row["args_sha256"]) == 64
    flat = repr(row)
    assert "1234567" not in flat and "ACME" not in flat
    assert "ACME" not in repr(record)  # args are repr=False: an accidental log of the record carries no values
    assert record.args["expression"].startswith("1234567")  # still available in memory for the run
    assert r.log[0].audit_row()["args_sha256"] != r.log[1].audit_row()["args_sha256"]
