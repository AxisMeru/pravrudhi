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
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any

MAX_FAILURES = 8
FAILURE_CHARS = 200
#: How many of one item's hidden pairs run at once. Each pair is its own interpreter, so these are processes
#: and threads only wait on them. Serial, one item of 12 pairs cost up to 72 seconds of wall clock and a
#: 100-item rotation took the better part of an hour -- nine of those is a floor study nobody can run in a
#: day. Held well below the core count on purpose: the 6-second budget is wall clock, so oversubscribing the
#: machine turns correct-but-slow solutions into timeouts and makes the score depend on the load.
WORKERS = max(1, min(4, (os.cpu_count() or 2) // 2))

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

    def one(index: int, stdin: str, expected: str) -> tuple[int, bool, bool, str]:
        """(index, passed, timed_out, message) for a single pair."""
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
            return index, False, True, f"test {index}: timeout after {timeout_s:g}s"
        if proc.returncode != 0:
            return index, False, False, f"test {index}: {proc.stderr.strip()[-FAILURE_CHARS:]}"
        got, want = normalise(proc.stdout), normalise(expected)
        if got != want:
            return index, False, False, f"test {index}: expected {want[:FAILURE_CHARS]!r}, got {got[:FAILURE_CHARS]!r}"
        return index, True, False, ""

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = sorted(
            pool.map(lambda a: one(*a), [(i, s, e) for i, (s, e) in enumerate(zip(inputs, outputs, strict=True))]),
        )
    passed = sum(1 for _, ok, _, _ in results if ok)
    timed_out = sum(1 for _, _, late, _ in results if late)
    failures = [msg for _, ok, _, msg in results if not ok][:MAX_FAILURES]
    # `timed_out` is reported separately because a timeout is not the same evidence as a wrong answer. Under
    # load a correct solution can exceed the budget, which makes the score depend on what else the machine
    # was doing -- and a rotation of timeouts reads identically to a rotation of wrong answers unless the
    # count travels with it.
    return {"passed": passed, "total": len(inputs), "timed_out": timed_out, "failures": failures}
