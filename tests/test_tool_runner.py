"""#300 slice A: the tool-call runner. Constructed toy tools only; no network, nothing wired into the judge path."""

from __future__ import annotations

import pytest

from pravrudhi.application.tool_runner import ToolRunner, ToolSpec, TransientToolError


def _runner(**kw) -> ToolRunner:
    return ToolRunner(sleep=lambda _s: None, **kw)


def test_a_registered_tool_runs_and_is_logged() -> None:
    r = _runner()
    r.register(ToolSpec("add", lambda a, b: a + b))
    res = r.call("add", {"a": 1, "b": 2})
    assert (res.ok, res.value, res.attempts, res.error) == (True, 3, 1, None)
    assert [(c.name, c.ok, c.attempts) for c in r.log] == [("add", True, 1)]


def test_unknown_tool_is_a_typed_error_not_a_raise() -> None:
    res = _runner().call("nope", {})
    assert (res.ok, res.error) == (False, "unknown_tool")


def test_bad_arguments_are_a_typed_error_and_not_retried() -> None:
    r = _runner()
    r.register(ToolSpec("add", lambda a, b: a + b, max_attempts=3))
    res = r.call("add", {"a": 1})
    assert (res.ok, res.error, res.attempts) == (False, "bad_arguments", 0)


def test_transient_failures_retry_up_to_the_cap_then_succeed() -> None:
    n = {"i": 0}

    def flaky() -> str:
        n["i"] += 1
        if n["i"] < 3:
            raise TransientToolError("blip")
        return "ok"

    r = _runner()
    r.register(ToolSpec("flaky", flaky, max_attempts=3))
    res = r.call("flaky", {})
    assert (res.ok, res.value, res.attempts) == (True, "ok", 3)


def test_transient_failure_beyond_the_cap_is_an_error_with_the_attempt_count() -> None:
    def down() -> None:
        raise TransientToolError("down")

    r = _runner()
    r.register(ToolSpec("down", down, max_attempts=2))
    res = r.call("down", {})
    assert (res.ok, res.error, res.attempts) == (False, "transient_exhausted", 2)


def test_a_non_transient_exception_is_never_retried_and_never_raised() -> None:
    n = {"i": 0}

    def boom() -> None:
        n["i"] += 1
        raise ValueError("secret detail")

    r = _runner()
    r.register(ToolSpec("boom", boom, max_attempts=5))
    res = r.call("boom", {})
    assert (res.ok, res.error, res.attempts, n["i"]) == (False, "tool_error:ValueError", 1, 1)
    assert "secret detail" not in (res.error or "")


def test_the_call_budget_is_hard() -> None:
    r = _runner(max_calls=2)
    r.register(ToolSpec("one", lambda: 1))
    assert r.call("one", {}).ok and r.call("one", {}).ok
    res = r.call("one", {})
    assert (res.ok, res.error, res.attempts) == (False, "budget_exhausted", 0)
    assert len(r.log) == 3


def test_max_attempts_has_a_global_ceiling() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        ToolSpec("x", lambda: 1, max_attempts=99)
    with pytest.raises(ValueError, match="max_attempts"):
        ToolSpec("x", lambda: 1, max_attempts=0)


def test_duplicate_registration_is_refused() -> None:
    r = _runner()
    r.register(ToolSpec("t", lambda: 1))
    with pytest.raises(ValueError, match="already registered"):
        r.register(ToolSpec("t", lambda: 2))


def test_backoff_sleeps_between_attempts_only() -> None:
    slept: list[float] = []

    def down() -> None:
        raise TransientToolError("x")

    r = ToolRunner(sleep=slept.append)
    r.register(ToolSpec("down", down, max_attempts=3, backoff_s=0.5))
    r.call("down", {})
    assert slept == [0.5, 1.0]
