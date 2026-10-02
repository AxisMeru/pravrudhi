"""Calculator and date tools for `tool_runner.ToolRunner` (#300 slice C). Pure and deterministic: Decimal arithmetic
(40 significant digits, so 1/3 is rounded, not exact) over a closed grammar (no eval), calendar-day arithmetic over
ISO dates, no clock and no network, so a result is reproducible and an unparseable input is an error, never a
guessed answer. Month arithmetic clamps to the last day of a shorter month (31 Jan + 1 month = 28/29 Feb): state
that convention wherever a limitation period is computed from it."""

from __future__ import annotations

import ast
import calendar
import datetime as dt
import operator
import re
from collections.abc import Callable
from decimal import Decimal, InvalidOperation, localcontext

from pravrudhi.application.tool_runner import ToolRunner, ToolSpec

MAX_EXPR_CHARS = 200
MAX_EXPONENT = 1000
_PREC = 40

_BIN: dict[type[ast.operator], Callable[[Decimal, Decimal], Decimal]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}


def _eval(node: ast.expr) -> Decimal:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return Decimal(str(node.value))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _eval(node.operand)
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and (right != right.to_integral_value() or abs(right) > MAX_EXPONENT):
            raise ValueError("exponent must be an integer no larger than 1000")
        return _BIN[type(node.op)](left, right)
    raise ValueError("only numbers and + - * / % ** ( ) are allowed")


def calculate(expression: str) -> str:
    if not expression.strip() or len(expression) > MAX_EXPR_CHARS:
        raise ValueError(f"expression must be 1..{MAX_EXPR_CHARS} characters")
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as e:
        raise ValueError("not an arithmetic expression") from e
    try:
        with localcontext() as ctx:
            ctx.prec = _PREC
            result = _eval(tree.body)
            if not result.is_finite():
                raise ValueError("result is not a finite number")
            text = format(result.normalize(), "f")
    except (InvalidOperation, ZeroDivisionError, OverflowError) as e:
        raise ValueError(f"arithmetic error: {type(e).__name__}") from e
    return text


_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")


def _parse_date(s: str) -> dt.date:
    if not _ISO.fullmatch(s):
        raise ValueError(f"not an ISO date (YYYY-MM-DD): {s!r}")
    return dt.date.fromisoformat(s)


def days_between(start: str, end: str) -> int:
    return (_parse_date(end) - _parse_date(start)).days


def add_days(date: str, days: int) -> str:
    try:
        return (_parse_date(date) + dt.timedelta(days=days)).isoformat()
    except OverflowError as e:
        raise ValueError("result is out of the supported date range") from e


def add_months(date: str, months: int) -> str:
    d = _parse_date(date)
    index = d.year * 12 + (d.month - 1) + months
    year, month = divmod(index, 12)
    if not 1 <= year <= 9999:
        raise ValueError("result is out of the supported date range")
    month += 1
    return dt.date(year, month, min(d.day, calendar.monthrange(year, month)[1])).isoformat()


def register_deterministic_tools(runner: ToolRunner) -> None:
    runner.register(ToolSpec("calculate", calculate))
    runner.register(ToolSpec("days_between", days_between))
    runner.register(ToolSpec("add_days", add_days))
    runner.register(ToolSpec("add_months", add_months))
