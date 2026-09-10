"""The second internal code pool: an APPS slice sealed as `solve(stdin) -> str` problems (ADR-0029).

MBPP+ was the harness track's only internal pool and 378 problems at exposure cap 8 is spent, so a night now
dies at `draw_rotation` before it evaluates anything. HumanEval+ cannot take over: it is the external check the
track reports against. These tests pin the contract that lets a second pool arrive without changing the agent
job or the harness grammar -- one visible `assert` in the question, every other test hidden in the answer, a
scorer chosen by the pool's own bench name, and a candidate executed out of process so a hang costs one test
rather than the whole rotation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "docker" / "jobs"))

from agent_code import extract_code  # noqa: E402
from apps_check import run_solve  # noqa: E402
from checks import visible_tests  # noqa: E402

from pravrudhi.application.harness_track import (  # noqa: E402
    SCORERS,
    _scorer,
    baseline_recipe,
    promoted_path,
)
from pravrudhi.application.pool_admin import seal_apps  # noqa: E402
from pravrudhi_kernel.metrics.pool import load_manifest, read_item  # noqa: E402

SUM_SOLVE = "def solve(stdin):\n    return str(sum(int(x) for x in stdin.split())) + '\\n'\n"


def _problem(pid: int, difficulty: str, io: dict[str, Any]) -> dict[str, Any]:
    return {
        "problem_id": pid,
        "question": f"Problem {pid}: read the numbers and print their sum.",
        "input_output": json.dumps(io),
        "difficulty": difficulty,
        "solutions": json.dumps([SUM_SOLVE]),
    }


def _stdin_io(n: int) -> dict[str, Any]:
    return {"inputs": [f"{i} {i}\n" for i in range(n)], "outputs": [f"{2 * i}\n" for i in range(n)]}


def _fixture(tmp_path: Path) -> Path:
    """Six APPS rows in the codeparrot/apps column shape: four sealable, one out-of-scope difficulty, one
    call-based problem the `solve(stdin)` contract cannot express."""
    rows = [
        _problem(10, "introductory", _stdin_io(4)),
        _problem(11, "interview", _stdin_io(3)),
        _problem(12, "introductory", _stdin_io(5)),
        _problem(13, "interview", _stdin_io(2)),
        _problem(14, "competition", _stdin_io(4)),
        _problem(15, "introductory", {"inputs": [[1, 2], [3, 4]], "outputs": [3, 7], "fn_name": "add"}),
    ]
    p = tmp_path / "apps_test.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return p


def test_seal_apps_keeps_one_visible_assert_and_hides_the_rest(tmp_path: Path) -> None:
    root = tmp_path / "root"
    manifest = seal_apps(root, _fixture(tmp_path), count=3, seed=7)
    assert manifest["bench"] == "apps"
    assert manifest["n_items"] == 3
    assert manifest["source"]["draw"] == {
        "seed": 7,
        "count": 3,
        "difficulties": ["introductory", "interview"],
        "min_tests": 2,
        "max_hidden_tests": 12,
        "n_eligible": 4,
    }
    pool = root / ".pravrudhi" / "kernel" / "pools" / "apps"
    for item_id in load_manifest(pool)["item_hashes"]:
        item = read_item(pool, item_id)
        answer = json.loads(item["answer"])
        assert answer["fn_name"] == "solve"
        assert answer["task_id"] in {"10", "11", "12", "13"}
        # exactly one visible check, and it is the first pair rendered as an assert the executor already reads
        checks = visible_tests(item["question"])
        assert checks == ["assert solve('0 0\\n') == '0\\n'"]
        assert item["question"].count("assert solve(") == 1
        # the hidden pairs are the remaining ones and appear nowhere in the question
        assert answer["inputs"] == [f"{i} {i}\n" for i in range(1, len(answer["inputs"]) + 1)]
        assert answer["outputs"] == [f"{2 * i}\n" for i in range(1, len(answer["outputs"]) + 1)]
        assert "1 1" not in item["question"]


def test_seal_apps_draw_is_deterministic_in_the_seed(tmp_path: Path) -> None:
    src = _fixture(tmp_path)

    def drawn(root: Path, seed: int) -> list[str]:
        seal_apps(root, src, count=3, seed=seed)
        pool = root / ".pravrudhi" / "kernel" / "pools" / "apps"
        ids = sorted(load_manifest(pool)["item_hashes"])
        return [json.loads(read_item(pool, i)["answer"])["task_id"] for i in ids]

    assert drawn(tmp_path / "a", 7) == drawn(tmp_path / "b", 7)


def test_seal_apps_refuses_a_count_the_slice_cannot_fill(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="eligible APPS problems"):
        seal_apps(tmp_path / "root", _fixture(tmp_path), count=5, seed=7)


def test_run_solve_passes_a_correct_candidate() -> None:
    res = run_solve(SUM_SOLVE, ["1 2\n", "3 4\n"], ["3\n", "7 \n"], timeout_s=10.0)
    assert res == {"passed": 2, "total": 2, "failures": []}


def test_run_solve_fails_a_wrong_candidate() -> None:
    res = run_solve("def solve(stdin):\n    return '0\\n'\n", ["1 2\n"], ["3\n"], timeout_s=10.0)
    assert res["passed"] == 0
    assert res["total"] == 1
    assert "expected '3', got '0'" in res["failures"][0]


def test_run_solve_reports_a_hang_as_one_failed_test() -> None:
    res = run_solve("def solve(stdin):\n    while True:\n        pass\n", ["1\n"], ["1\n"], timeout_s=1.0)
    assert res["passed"] == 0
    assert res["failures"] == ["test 0: timeout after 1s"]


def test_run_solve_reports_a_candidate_that_never_defines_solve() -> None:
    res = run_solve("x = 1\n", ["1\n"], ["1\n"], timeout_s=10.0)
    assert res["passed"] == 0
    assert "solve is not defined" in res["failures"][0]


def test_scorer_is_selected_by_the_pools_bench_name(tmp_path: Path) -> None:
    root = tmp_path / "root"
    seal_apps(root, _fixture(tmp_path), count=3, seed=7)
    scorer, needs_pool = _scorer(root / ".pravrudhi" / "kernel" / "pools" / "apps")
    assert scorer.name == "score_apps.py"
    assert scorer.exists()
    assert needs_pool is True
    assert SCORERS["mbppplus"] == ("score_code.py", False)


def test_the_jsonl_export_keys_its_id_as_id_not_problem_id(tmp_path: Path) -> None:
    """The parquet conversion names it `problem_id`; the JSONL the dataset ships names it `id`, and sealing read
    only the first, so the real download failed with a KeyError."""
    from pravrudhi.application.pool_admin import _apps_task_id

    assert _apps_task_id({"problem_id": 7}) == "7"
    assert _apps_task_id({"id": 9}) == "9"
    assert _apps_task_id({"problem_id": 1, "id": 2}) == "1", "the parquet name wins when both are present"
    assert _apps_task_id({"question": "no id at all"}) is None


def test_an_unterminated_fence_yields_the_code_and_not_the_fence() -> None:
    """`max_new_tokens` truncates long APPS solutions mid-code, so the closing fence never arrives. The
    extractor returned the raw text WITH the opening fence on it, and every such item failed with
    `SyntaxError: invalid syntax` at line 1 pointing at the fence -- our error, read as a model that cannot
    code."""
    truncated = "Here it is:\n```python\ndef solve(stdin):\n    return '4\\n'\n    # cut off mid-th"
    got = extract_code(truncated)
    assert not got.startswith("```")
    assert got.startswith("def solve(stdin):")
    # A closed fence still wins, and the LAST one, so a worked example before the answer does not.
    closed = "```python\ndef solve(s):\n    return 'no'\n```\nand better:\n```python\ndef solve(s):\n    return 'yes'\n```"
    assert extract_code(closed) == "def solve(s):\n    return 'yes'"
    # Text with no fence at all is unchanged.
    assert extract_code("def solve(s):\n    return ''") == "def solve(s):\n    return ''"


def test_the_runner_compares_what_solve_returned_and_not_what_it_printed() -> None:
    """Deliberate and reverted once. These items look like standard-input problems, but every question in the
    pool states `def solve(stdin: str) -> str` and carries a visible `assert solve(...) == ...`, so the return
    contract is what the model is told and what the harness's own retry feedback tests. A hidden scorer more
    lenient than the visible test would prune candidates it would itself have passed."""
    printer = "def solve(stdin):\n    print('4')"
    assert run_solve(printer, ["x\n"], ["4\n"], timeout_s=6.0)["passed"] == 0
    returner = "def solve(stdin):\n    return '4\\n'"
    assert run_solve(returner, ["x\n"], ["4\n"], timeout_s=6.0)["passed"] == 1


def test_a_baseline_recipe_is_per_bench_and_absent_means_the_grammar_default(tmp_path: Path) -> None:
    """The grammar default is a CODE recipe. It was the starting incumbent on a law multiple-choice bench,
    which is most of why the harness baseline scored 0.1042 there against the model track's 0.3993."""
    root = Path(__file__).resolve().parents[1]
    default = baseline_recipe(root, {"bench": "mbppplus"})
    assert "Python programmer" in default.system_prompt

    law = baseline_recipe(root, {"bench": "mmlu-law-val", "baseline_recipe": "harness/agent/baselines/mmlu-law-val.json"})
    assert "Python" not in law.system_prompt
    assert "letter of the correct option" in law.system_prompt

    apps = baseline_recipe(root, {"bench": "apps", "baseline_recipe": "harness/agent/baselines/apps.json"})
    assert apps.max_new_tokens == 1024, "512 truncated long solutions mid-block"

    with pytest.raises(ValueError, match="does not exist"):
        baseline_recipe(tmp_path, {"bench": "apps", "baseline_recipe": "nope.json"})

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"strategy": "prompt_only", "execution_family": "system_prompt", "retries": 9}))
    # `parse_harness` returns the grammar's complaint as a string rather than raising, because the proposer's
    # candidates are expected to be invalid sometimes. A baseline is not.
    with pytest.raises(ValueError, match="not a valid harness recipe"):
        baseline_recipe(tmp_path, {"bench": "apps", "baseline_recipe": "bad.json"})


def test_a_promotion_writes_one_file_per_bench() -> None:
    """There was one file for every bench, and `scripts/ext_humaneval.sh` reads it, so a law night's
    promotion silently became what the external HumanEval+ proof ran."""
    assert promoted_path(Path("/r"), "apps") == Path("/r/harness/agent/apps/harness.json")
    assert promoted_path(Path("/r"), "mmlu-law-val") != promoted_path(Path("/r"), "apps")
