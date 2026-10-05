"""One smoke test per script that calls `p_established_from_top_logprobs` / `score_decision`, run against a
recorded top_logprobs fixture (toy values, no model, no network). The two functions return `(p, clamp)` and
`(scores, missing)`; a script that still treats them as a float / dict fails here, so the next signature change
breaks CI instead of a sealed run. The clamp flag must reach each script's output; a bounded typed score fails.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import _p_scoring as ps  # type: ignore[import-not-found]  # noqa: E402
import t2_c3_ab_run as c3_ab  # type: ignore[import-not-found]  # noqa: E402
import t2_c3_score as c3_score  # type: ignore[import-not-found]  # noqa: E402
import t2_harness_assemble as assemble  # type: ignore[import-not-found]  # noqa: E402
import typed_layer_c3_baseline as baseline  # type: ignore[import-not-found]  # noqa: E402
import typed_layer_parity_c_prime as c_prime  # type: ignore[import-not-found]  # noqa: E402
import typed_layer_parity_e2e as e2e  # type: ignore[import-not-found]  # noqa: E402

from pravrudhi.models.openai_compat import CompletionResult  # noqa: E402

FREE = {" established": -0.1, " not": -3.0}
FREE_BOUNDED = {" established": -0.1}  # " not" absent: p is a lower bound
TYPED = FREE  # the typed bool field scores the same label tokens
TYPED_BOUNDED = FREE_BOUNDED


class _Res:
    def __init__(self, top: dict[str, float], text: str = "established F1:0:5") -> None:
        self.text = text
        self.top_logprobs = [top]
        self.model = "fake"


def _typed_res(top: dict[str, float]) -> CompletionResult:
    return CompletionResult(text="true", model="x", top_logprobs=[top], wall_s=0.0)


class _FakeHouse:
    def __init__(self, top: dict[str, float], calls: list[dict[str, Any]] | None = None, **_kw: Any) -> None:
        self.model = "fake-house"
        self._top = top
        self._calls = calls

    def _complete(self, prompt: str) -> _Res:
        if self._calls is not None:
            self._calls.append({"prompt_len": len(prompt)})
        return _Res(self._top)


class _FakeDecoder:
    def __init__(self, calls: list[dict[str, Any]] | None = None, **_kw: Any) -> None:
        self.model = "fake-typed"
        self._calls = calls

    def complete(self, prompt: str, **_kw: Any) -> _Res:
        if self._calls is not None:
            self._calls.append({"prompt_len": len(prompt)})
        return _Res(TYPED)


def _prompts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mod: Any, n: int = 3) -> Path:
    rows = [{"split": "calib_v1", "id": f"r{i}", "gold": "not_established", "prompt": f"toy prompt {i}"} for i in range(n)]
    path = tmp_path / "prompts.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    monkeypatch.setattr(mod, "EXPECTED_SHA", hashlib.sha256(path.read_bytes()).hexdigest())
    results = tmp_path / "results"
    monkeypatch.setenv("PRAVRUDHI_T1_PARITY_PROMPTS", str(path))
    monkeypatch.setenv("PRAVRUDHI_T2_RESULTS_DIR", str(results))
    return results


def test_typed_p_returns_the_score_and_refuses_a_bound() -> None:
    assert 0.9 < ps.typed_p(_typed_res(TYPED)) < 1.0
    with pytest.raises(ps.BoundedTypedScore):
        ps.typed_p(_typed_res(TYPED_BOUNDED))


def test_t2_c3_ab_run_scorers_unpack_and_record_the_clamp() -> None:
    p, fid, clamp = c3_ab._score_house(_Res(FREE))
    assert 0.9 < p < 1.0 and fid == "F1" and clamp == "none"
    assert c3_ab._score_house(_Res(FREE_BOUNDED))[2] == "lower_bound"
    p_t, fid_t = c3_ab._score_typed(_Res(TYPED))
    assert 0.9 < p_t < 1.0 and fid_t == "F1"
    with pytest.raises(ps.BoundedTypedScore):
        c3_ab._score_typed(_Res(TYPED_BOUNDED))


def test_t2_harness_assemble_loader_records_free_arm_bounds_and_refuses_typed_bounds() -> None:
    def row(item: str, free: dict[str, float], typed: dict[str, float]) -> dict[str, Any]:
        return {"item_id": item, "element_id": "e1",
                "free_text": {"text": "x", "top_logprobs": [free]}, "typed": {"text": "true", "top_logprobs": [typed]}}

    p_by_key, bounded = assemble.load_p_by_key([row("a", FREE, TYPED), row("b", FREE_BOUNDED, TYPED)])
    assert set(p_by_key) == {("a", "e1"), ("b", "e1")}
    assert all(0.5 <= p < 1.0 for pair in p_by_key.values() for p in pair)
    assert bounded == [["b", "e1", "lower_bound"]]
    with pytest.raises(ps.BoundedTypedScore):
        assemble.load_p_by_key([row("c", FREE, TYPED_BOUNDED)])


def test_typed_layer_c3_baseline_records_the_clamp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    results = _prompts(tmp_path, monkeypatch, baseline)
    tops = iter([FREE, FREE_BOUNDED, FREE])
    monkeypatch.setattr(baseline, "HouseJudge", lambda **kw: type("H", (), {
        "model": "fake", "_complete": lambda self, prompt: _Res(next(tops))})())
    assert baseline.main() == 0
    out = json.loads((results / "typed_layer_c3_baseline_result.json").read_text())
    assert out["splits"]["calib_v1"]["n"] == 3
    assert out["splits"]["calib_v1"]["n_bounded_p"] == 1


def test_typed_layer_parity_c_prime_records_the_clamp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    results = _prompts(tmp_path, monkeypatch, c_prime)
    # judge_1 sees a bound on every row, judge_2 a measured p: a delta and a clamp mismatch, both recorded.
    made = iter([_FakeHouse(FREE_BOUNDED), _FakeHouse(FREE)])
    monkeypatch.setattr(c_prime, "HouseJudge", lambda **kw: next(made))
    assert c_prime.main() == 0
    out = json.loads((results / "typed_layer_parity_c_prime_result.json").read_text())
    assert out["n_prompts"] == 3
    assert {d["clamp_1"] for d in out["large_deltas"]} <= {"lower_bound", "none"}


def test_typed_layer_parity_e2e_records_the_clamp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    results = _prompts(tmp_path, monkeypatch, e2e)
    calls = e2e._calls
    calls.clear()
    monkeypatch.setattr(e2e, "HouseJudge", lambda **kw: _FakeHouse(FREE_BOUNDED, calls))
    monkeypatch.setattr(e2e, "VLLMDecoder", lambda **kw: _FakeDecoder(calls))
    monkeypatch.setattr(e2e, "TypedHouseJudge", lambda **kw: type("T", (), {"decoder": kw["decoder"]})())
    # house p is a bound, typed p is measured: the parity gate must FAIL, and the flip rows carry the clamp.
    assert e2e.main() == 1
    data = json.loads(next(results.glob("*.json")).read_text())
    assert data["n_prompts"] == 3 and data["passed"] is False
    assert data["c_n_bounded"] == data["c_n_sampled"] > 0
    assert data["b_flips"] and all(f["clamp_house"] == "lower_bound" for f in data["b_flips"])


def test_typed_layer_parity_e2e_measured_p_passes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    results = _prompts(tmp_path, monkeypatch, e2e)
    calls = e2e._calls
    calls.clear()
    monkeypatch.setattr(e2e, "HouseJudge", lambda **kw: _FakeHouse(FREE, calls))
    monkeypatch.setattr(e2e, "VLLMDecoder", lambda **kw: _FakeDecoder(calls))
    monkeypatch.setattr(e2e, "TypedHouseJudge", lambda **kw: type("T", (), {"decoder": kw["decoder"]})())
    assert e2e.main() == 0
    data = json.loads(next(results.glob("*.json")).read_text())
    assert data["passed"] is True and data["c_n_bounded"] == 0


def test_t2_c3_score_records_the_clamp_and_keeps_the_numbers() -> None:
    def raw(free: dict[str, float], typed: dict[str, float]) -> dict[str, Any]:
        return {"id": "r", "gold": "established", "half": "even", "arm_run_first": "free_text",
                "free_text": {"text": "established F1:0:5", "top_logprobs": [free]},
                "typed": {"text": "true", "top_logprobs": [typed]}}

    ok = c3_score.score_row(raw(FREE, TYPED))
    assert ok["clamp_free"] == "none" and ok["typed_bounded"] is False and 0.9 < ok["p_free"] < 1.0
    bounded = c3_score.score_row(raw(FREE_BOUNDED, TYPED_BOUNDED))
    assert bounded["clamp_free"] == "lower_bound" and bounded["typed_bounded"] is True
    assert bounded["p_free"] is not None and bounded["p_typed"] is not None  # numbers as sealed, now flagged


# Scripts that call the two functions but are covered by a smoke test above, or are owned by another change.
COVERED = {
    "t2_c3_ab_run.py", "t2_c3_score.py", "t2_harness_assemble.py", "typed_layer_c3_baseline.py",
    "typed_layer_parity_c_prime.py", "typed_layer_parity_e2e.py",
}
OWNED_ELSEWHERE = {"typed_layer_parity.py": "pravrudhi#198 rewrites the T1 parity gate; remove this entry when it lands"}
GUARDED_NAMES = {"p_established_from_top_logprobs", "score_decision"}


def _scripts_calling_the_guarded_functions() -> set[str]:
    import ast

    found: set[str] = set()
    for path in Path(SCRIPTS_DIR).glob("*.py"):
        if path.name == "_p_scoring.py":
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Name) and node.id in GUARDED_NAMES) or (
                isinstance(node, ast.Attribute) and node.attr in GUARDED_NAMES
            ):
                found.add(path.name)
                break
    return found


def test_every_script_that_calls_the_two_functions_is_smoke_tested_or_explicitly_owned() -> None:
    found = _scripts_calling_the_guarded_functions()
    uncovered = found - COVERED - OWNED_ELSEWHERE.keys()
    assert not uncovered, f"script(s) call the (p, clamp)/(scores, missing) functions with no smoke test: {sorted(uncovered)}"
    stale = (COVERED | OWNED_ELSEWHERE.keys()) - found
    assert not stale, f"COVERED/OWNED_ELSEWHERE lists script(s) that no longer call them: {sorted(stale)}"
