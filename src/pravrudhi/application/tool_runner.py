"""A bounded tool-call runner (#300 slice A): a registry of typed callables, a hard call budget, retries only
for errors a tool declares transient, and a log of every call. It never raises for a tool failure -- the caller
gets a typed result, so an infrastructure fault cannot read as an answer. Nothing here is wired into the judge
path; a judge verdict stays tool-free until a measured change says otherwise."""

from __future__ import annotations

import hashlib
import inspect
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

MAX_ATTEMPTS_CEILING = 5


class TransientToolError(Exception):
    """Raised by a tool for a failure worth retrying (timeout, 5xx, busy). Anything else is final."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    fn: Callable[..., Any]
    max_attempts: int = 1
    backoff_s: float = 0.0

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= MAX_ATTEMPTS_CEILING:
            raise ValueError(f"max_attempts must be in 1..{MAX_ATTEMPTS_CEILING}, got {self.max_attempts}")


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    value: Any = None
    error: str | None = None
    attempts: int = 0


@dataclass(frozen=True)
class CallRecord:
    """One tool call. `args` holds the caller's own values (an expression, a party name, a quoted passage) and stays
    IN MEMORY for the run only: it is `repr=False`, and anything written to an audit trail or log must use
    `audit_row()`, which carries the argument NAMES and a digest, never the values."""

    name: str
    args: dict[str, Any] = field(repr=False)
    ok: bool
    attempts: int
    error: str | None

    def audit_row(self) -> dict[str, Any]:
        """The audit-safe view: no argument values. The digest lets a reader confirm two rows had the same input."""
        canonical = json.dumps(self.args, sort_keys=True, default=str, ensure_ascii=False)
        return {
            "name": self.name, "arg_names": sorted(self.args), "args_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
            "ok": self.ok, "attempts": self.attempts, "error": self.error,
        }


@dataclass
class ToolRunner:
    max_calls: int = 20
    sleep: Callable[[float], None] = time.sleep
    log: list[CallRecord] = field(default_factory=list)
    _tools: dict[str, ToolSpec] = field(default_factory=dict)
    _calls: int = 0

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"tool {spec.name!r} already registered")
        self._tools[spec.name] = spec

    def call(self, name: str, args: dict[str, Any]) -> ToolResult:
        res = self._run(name, args)
        self.log.append(CallRecord(name, dict(args), res.ok, res.attempts, res.error))
        return res

    def _run(self, name: str, args: dict[str, Any]) -> ToolResult:
        if self._calls >= self.max_calls:
            return ToolResult(False, error="budget_exhausted")
        spec = self._tools.get(name)
        if spec is None:
            return ToolResult(False, error="unknown_tool")
        self._calls += 1
        try:
            inspect.signature(spec.fn).bind(**args)
        except TypeError:
            return ToolResult(False, error="bad_arguments")
        for attempt in range(1, spec.max_attempts + 1):
            try:
                return ToolResult(True, value=spec.fn(**args), attempts=attempt)
            except TransientToolError:
                if attempt == spec.max_attempts:
                    return ToolResult(False, error="transient_exhausted", attempts=attempt)
                if spec.backoff_s:
                    self.sleep(spec.backoff_s * 2 ** (attempt - 1))
            except Exception as e:  # noqa: BLE001 -- a tool fault is a typed result, never a raise
                return ToolResult(False, error=f"tool_error:{type(e).__name__}", attempts=attempt)
        return ToolResult(False, error="transient_exhausted", attempts=spec.max_attempts)
