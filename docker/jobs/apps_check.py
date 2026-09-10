"""Running a candidate `solve(stdin) -> str` against APPS stdin/stdout pairs, one disposable interpreter per pair.

MBPP+ never needed this: EvalPlus ships the tests and its own guarded executor, so the scorer could hand it a
solution and a task id. APPS ships raw input/output strings and nothing runnable, so the obvious implementation
is to `exec` the candidate in the scorer process — and the first `while True:` a model writes then hangs the
scorer until the kernel's job timeout kills it, which discards the scores of every other item in the rotation
and admits a whole paired evaluation of zeros for a reason no ledger row would show. Each pair therefore runs in
its own interpreter with its own timeout, so a hang costs exactly one test.

Pure stdlib and no torch: importable and testable outside the container.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

MAX_FAILURES = 8
FAILURE_CHARS = 200

# Reads {code, stdin, fn_name} on its own stdin, then re-points stdin at the test input so a solution that also
# calls input() behaves; the candidate's own prints go to a buffer, so what this writes to stdout is the value
# solve returned and nothing else -- the same thing the visible `assert solve(...) == ...` compares.
#
# Accepting a solution's PRINTED output as a fallback was tried and reverted. These items look like
# standard-input problems, but every question in the pool states `def solve(stdin: str) -> str` and carries a
# visible `assert solve(...) == ...` -- so the return contract is what the model is told, what the harness's
# own retry feedback tests, and what the sealer sealed. A hidden scorer more lenient than the visible test the
# model is given is worse than a strict one: the loop would prune candidates the scorer would have passed.
RUNNER = """\
import contextlib, io, json, sys

payload = json.loads(sys.stdin.read())
sys.stdin = io.StringIO(payload["stdin"])
ns = {}
with contextlib.redirect_stdout(io.StringIO()):
    exec(payload["code"], ns)
    fn = ns.get(payload["fn_name"])
    if not callable(fn):
        raise NameError(payload["fn_name"] + " is not defined")
    returned = fn(payload["stdin"])
sys.stdout.write("" if returned is None else str(returned))
"""


def normalise(text: str) -> str:
    """Trailing whitespace is not part of an APPS answer: line-end padding and a missing final newline are
    formatting, not a wrong result, and penalising them would score presentation instead of correctness."""
    return "\n".join(line.rstrip() for line in text.rstrip().splitlines())


def run_solve(
    code: str, inputs: list[str], outputs: list[str], timeout_s: float, fn_name: str = "solve"
) -> dict[str, Any]:
    """Run `code`'s `solve` against every pair. Returns {"passed", "total", "failures"}; never raises for a
    candidate's fault -- a crash, a hang and a wrong answer are all just failures of that one test."""
    if len(inputs) != len(outputs):
        raise ValueError(f"{len(inputs)} inputs against {len(outputs)} outputs")
    failures: list[str] = []
    passed = 0

    def note(msg: str) -> None:
        if len(failures) < MAX_FAILURES:
            failures.append(msg)

    for i, (stdin, expected) in enumerate(zip(inputs, outputs, strict=True)):
        payload = json.dumps({"code": code, "stdin": stdin, "fn_name": fn_name})
        try:
            proc = subprocess.run(  # noqa: S603 - fixed argv, candidate code arrives on stdin, no shell
                [sys.executable, "-I", "-c", RUNNER],
                input=payload,
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            note(f"test {i}: timeout after {timeout_s:g}s")
            continue
        if proc.returncode != 0:
            note(f"test {i}: {proc.stderr.strip()[-FAILURE_CHARS:]}")
            continue
        got, want = normalise(proc.stdout), normalise(expected)
        if got != want:
            note(f"test {i}: expected {want[:FAILURE_CHARS]!r}, got {got[:FAILURE_CHARS]!r}")
            continue
        passed += 1
    return {"passed": passed, "total": len(inputs), "failures": failures}
