"""T1 typed-layer parity gate (pravrudhi #196): typed `TypedHouseJudge` vs untyped `HouseJudge` on the SAME
recorded completion per prompt, so the comparison isolates the scoring/decision path from backend noise
(two separate live calls differ by ~0.06 |dp| on vLLM, see scripts/typed_layer_parity_c_prime.py).

Pure logic only: `run_parity` takes a `fetch(prompt) -> CompletionResult` callable and has no I/O of its own.
The CLI (scripts/typed_layer_parity.py) supplies the live fetch, the sha-checked prompt set and the report file.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from pravrudhi.application.nyaya_judges import ElementJudgment, HouseJudge, JudgeRequest
from pravrudhi.application.typed.decoder import VLLMDecoder
from pravrudhi.application.typed.house_judge import TypedHouseJudge
from pravrudhi.models.openai_compat import CompletionResult

PROMPTS_SHA256 = "d56c449f9332f22a85176b1008974c1f147a7b8d45e4f5cea0e5ed08a01935cf"
MAX_ABS_DP = 1e-6
TAU = 0.74
STATUTE_CHARS = 600
MAX_TOKENS = 30
TOP_LOGPROBS = 20
PLACEHOLDER = "{prompt}"

# The judges' transports ignore the prompt text (they return the recorded completion), so the request is only
# a vehicle for judge(); fact_id comes from the completion text, not from the facts.
_DUMMY_REQUEST = JudgeRequest(contract_id="parity", element="parity", is_denial=False, statute="", narrative="", facts=())


class ParityError(RuntimeError):
    """A refusal: the run cannot produce a trustworthy verdict (bad sha, bad template). CLI exit code 2."""


@dataclass(frozen=True)
class Outcome:
    status: str
    p: float | None
    clamp: str | None
    bound_undetermined: bool | None
    fact_id: str | None
    error: str | None = None


def apply_template(template: str | None, prompt: str) -> str:
    """No template: the prompt as stored. A template must contain `{prompt}` exactly once."""
    if template is None:
        return prompt
    if template.count(PLACEHOLDER) != 1:
        raise ParityError(f"prompt template must contain {PLACEHOLDER!r} exactly once, found {template.count(PLACEHOLDER)}")
    return template.replace(PLACEHOLDER, prompt)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _outcome(j: ElementJudgment) -> Outcome:
    return Outcome(j.status, j.p_established, j.clamp, j.bound_undetermined, j.fact_id)


def judge_both(
    res: CompletionResult, *, tau: float = TAU, judges: tuple[Any, Any] | None = None
) -> tuple[Outcome, Outcome]:
    """Feed one recorded completion to the real `HouseJudge.judge` and the real `TypedHouseJudge.judge`.
    `judges=(untyped, typed)` substitutes any objects with `.judge(request)` (tests of the gate's own logic)."""

    def untyped_complete(prompt: str) -> CompletionResult:
        return res

    def typed_complete(prompt: str, *, max_tokens: int, temperature: float, logprobs: int | None) -> CompletionResult:
        return res

    if judges is not None:
        untyped, typed = judges
    else:
        untyped = HouseJudge(tau=tau, statute_chars=STATUTE_CHARS, model=res.model, complete=untyped_complete)
        typed = TypedHouseJudge(
            tau=tau, statute_chars=STATUTE_CHARS, decoder=VLLMDecoder(model=res.model, complete=typed_complete)
        )
    out: list[Outcome] = []
    for judge in (untyped, typed):
        try:
            out.append(_outcome(judge.judge(_DUMMY_REQUEST)))
        except Exception as e:  # noqa: BLE001 -- the error class itself is the compared outcome
            out.append(Outcome("error", None, None, None, None, error=type(e).__name__))
    return out[0], out[1]


def differs(a: Outcome, b: Outcome) -> bool:
    return (a.status, a.clamp, a.bound_undetermined, a.fact_id, a.error) != (
        b.status, b.clamp, b.bound_undetermined, b.fact_id, b.error
    )


def run_parity(
    rows: Sequence[dict[str, Any]],
    fetch: Callable[[str], CompletionResult],
    *,
    template: str | None = None,
    tau: float = TAU,
    judges: tuple[Any, Any] | None = None,
) -> dict[str, Any]:
    """Per row: build the (templated) prompt, fetch ONE completion, score it through both judges. Returns a
    report dict whose `gate` block is the verdict. A fetch failure is recorded and fails the gate; no row is
    skipped silently."""
    flips: list[dict[str, Any]] = []
    transport_errors: list[dict[str, Any]] = []
    both_error = 0
    n_compared = 0
    max_dp = 0.0
    top_deltas: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        prompt = apply_template(template, row["prompt"])
        try:
            res = fetch(prompt)
        except Exception as e:  # noqa: BLE001
            transport_errors.append({"row": i, "id": row.get("id"), "error": f"{type(e).__name__}: {e}"})
            continue
        untyped, typed = judge_both(res, tau=tau, judges=judges)
        if differs(untyped, typed):
            flips.append({"row": i, "id": row.get("id"), "untyped": asdict(untyped), "typed": asdict(typed)})
            continue
        if untyped.error is not None:
            both_error += 1
            continue
        n_compared += 1
        assert untyped.p is not None and typed.p is not None
        delta = abs(untyped.p - typed.p)
        max_dp = max(max_dp, delta)
        if delta > 0.0:
            top_deltas.append({"row": i, "id": row.get("id"), "delta": delta})
    top_deltas.sort(key=lambda d: -d["delta"])
    failures = []
    if flips:
        failures.append(f"{len(flips)} flip(s)")
    if max_dp > MAX_ABS_DP:
        failures.append(f"max|dp| {max_dp:.3e} > {MAX_ABS_DP:.0e}")
    if transport_errors:
        failures.append(f"{len(transport_errors)} transport error(s)")
    if n_compared == 0:
        failures.append("no prompt was compared")
    return {
        "n_prompts": len(rows),
        "n_compared": n_compared,
        "n_both_error": both_error,
        "flips": flips,
        "transport_errors": transport_errors,
        "max_abs_dp": max_dp,
        "top_deltas": top_deltas[:10],
        "gate": {
            "status": "fail" if failures else "pass",
            "failures": failures,
            "max_flips": 0,
            "max_abs_dp": MAX_ABS_DP,
        },
    }
