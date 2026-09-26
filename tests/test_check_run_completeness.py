"""Tests for `scripts/check_run_completeness.py` -- the run-completeness CI guard.

Two styles on purpose, matching what each rule actually needs:

* the RULE tests build a real selection artefact, a real counted `.jsonl` and a real result artefact in
  `tmp_path` and run `check_artefact` over them. The sha256 pins are computed from the files as written, so
  a test cannot accidentally pass because a pin was stale -- and the one test that wants a mismatched pin
  corrupts it deliberately and says so.
* the DISCOVERY and BASELINE tests run the guard over a throwaway git checkout, so `git ls-files` -- the
  same tracked-file walk `check_no_private_data.py` and `check_fail_open_defaults.py` use -- is exercised
  rather than stubbed.

`tests/run_completeness_fixtures/` holds the two committed fixtures: a 6-row selection manifest, and
`broken_scored_result.json`, which is deliberately wrong AND deliberately named so the guard's own discovery
would match it. `test_the_real_repo_run_is_green` and
`test_the_fixture_directory_exclusion_is_what_keeps_the_broken_fixture_out` are the pair that keeps that
arrangement honest: the first asserts the real build stays green with the broken fixture tracked, the second
asserts the fixture really would fail if the exclusion were removed. Without the second, the first would be
passing for an unknown reason.

NOT COVERED HERE, stated so its absence is not read as coverage: the abort-path fall-through in
`scripts/t2_harness_inference.py` and `scripts/t2_c3_ab_run.py` (a truncated run writing the canonical
filename with a valid seal and exit 0). That fix is Track-C's, in their own PR; this PR is the reader-side
guard only and changes neither harness. In particular, nothing here tests that a complete run's sealed
output is byte-identical to today's -- there is no change to a harness in this PR for such a test to be
about.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "run_completeness_fixtures"
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from check_run_completeness import (  # noqa: E402 -- path set just above, repo idiom (test_check_fail_open_defaults.py:26)
    BLOCK_KEY,
    REQUIRED_BLOCK_FIELDS,
    SCHEMA,
    Counts,
    baseline_key,
    check,
    check_artefact,
    count_usable,
    load_baseline,
    main,
    usable_score,
)

MANIFEST_ROWS = 6


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def _scored_rows(n: int, *, retried_indices: tuple[int, ...] = ()) -> list[dict[str, Any]]:
    """`n` rows that all carry a usable score, with retries recorded on the named indices."""
    rows: list[dict[str, Any]] = []
    for i in range(n):
        row: dict[str, Any] = {"item_id": f"fixture-item-{i + 1}", "p_established": round(0.1 + i * 0.1, 2)}
        if i in retried_indices:
            row["retries"] = 1
        rows.append(row)
    return rows


def _manifest(tmp_path: Path) -> Path:
    """The committed 6-row selection fixture, copied in so paths resolve beside the artefact."""
    dest = tmp_path / "selection_manifest.jsonl"
    dest.write_bytes((FIXTURES / "selection_manifest.jsonl").read_bytes())
    return dest


def _artefact(
    tmp_path: Path,
    *,
    rows: list[dict[str, Any]] | None = None,
    overrides: dict[str, Any] | None = None,
    drop: tuple[str, ...] = (),
    extra_top_level: dict[str, Any] | None = None,
    name: str = "t2_scored_result.json",
) -> Path:
    """A result artefact that is CORRECT unless `overrides`/`drop`/`rows` make it wrong.

    Every sha256 and every count is computed from the files as actually written, so the default really is
    the passing case rather than a hand-maintained guess at one.
    """
    manifest = _manifest(tmp_path)
    rows = _scored_rows(MANIFEST_ROWS) if rows is None else rows
    counted = _write_jsonl(tmp_path / "t2_raw_outputs.jsonl", rows)
    counts = count_usable(rows, "p_established")
    block: dict[str, Any] = {
        "schema": SCHEMA,
        "planned_from": {"path": manifest.name, "sha256": _sha(manifest)},
        "n_planned": MANIFEST_ROWS,
        "counted_file": {"path": counted.name, "sha256": _sha(counted)},
        "score_field": "p_established",
        "n_scored": counts.n_scored,
        "n_error_terminal": counts.n_error_terminal,
        "n_error_retried": counts.n_error_retried,
        "truncated": False,
    }
    if overrides:
        block.update(overrides)
    for key in drop:
        block.pop(key, None)
    payload: dict[str, Any] = {BLOCK_KEY: block}
    if extra_top_level:
        payload.update(extra_top_level)
    path = tmp_path / name
    path.write_text(json.dumps(payload, indent=2))
    return path


def _codes(path: Path, root: Path) -> list[str]:
    return [v.code for v in check_artefact(path, root=root)]


# -- the rule the team adopted ----------------------------------------------------------------------------


class TestTheAdoptedRule:
    def test_a_complete_run_passes(self, tmp_path: Path) -> None:
        """6 usable scores against a 6-row selection artefact, no truncation. The baseline case every other
        test in this class perturbs by exactly one thing."""
        assert check_artefact(_artefact(tmp_path), root=tmp_path) == []

    def test_a_truncated_run_fails(self, tmp_path: Path) -> None:
        """3 of 6 scored, not marked truncated -- the whole reason the guard exists."""
        art = _artefact(tmp_path, rows=_scored_rows(3), overrides={"n_planned": MANIFEST_ROWS})
        codes = _codes(art, tmp_path)
        assert "truncated-unmarked" in codes
        assert any("3 < n_planned 6" in v.message for v in check_artefact(art, root=tmp_path))

    def test_a_truncated_run_marked_truncated_with_a_walk_order_passes(self, tmp_path: Path) -> None:
        art = _artefact(
            tmp_path,
            rows=_scored_rows(3),
            overrides={"truncated": True, "walk_order": "corpus order (selection artefact's own file order)"},
        )
        assert check_artefact(art, root=tmp_path) == []

    @pytest.mark.parametrize("walk_order", [None, "", "   "], ids=["absent", "empty", "whitespace"])
    def test_marked_truncated_without_a_walk_order_fails(self, tmp_path: Path, walk_order: str | None) -> None:
        overrides: dict[str, Any] = {"truncated": True}
        if walk_order is not None:
            overrides["walk_order"] = walk_order
        art = _artefact(tmp_path, rows=_scored_rows(3), overrides=overrides)
        assert "walk-order-missing" in _codes(art, tmp_path)

    def test_a_complete_run_marked_truncated_also_fails(self, tmp_path: Path) -> None:
        """So that `truncated: true` on everything cannot become the way past the guard: a label that does
        not match the counts is a reporting error in its own right."""
        art = _artefact(tmp_path, overrides={"truncated": True, "walk_order": "corpus order"})
        assert "complete-marked-truncated" in _codes(art, tmp_path)

    def test_a_truncated_result_may_not_publish_a_headline_number(self, tmp_path: Path) -> None:
        art = _artefact(
            tmp_path,
            rows=_scored_rows(3),
            overrides={"truncated": True, "walk_order": "corpus order"},
            extra_top_level={"summary": {"false_prove_rate": 0.02, "n": 3}},
        )
        found = check_artefact(art, root=tmp_path)
        assert "headline-on-truncated" in [v.code for v in found]
        assert any("summary.false_prove_rate" in v.message for v in found)

    def test_a_truncated_result_may_still_state_its_counts(self, tmp_path: Path) -> None:
        """`n`, `count`, `total`, `rows` are bookkeeping, not headlines -- a truncated record SHOULD say how
        far it got, so the headline rule must not fire on those."""
        art = _artefact(
            tmp_path,
            rows=_scored_rows(3),
            overrides={"truncated": True, "walk_order": "corpus order"},
            extra_top_level={"rows_written": 3, "n": 3, "total_calls": 6},
        )
        assert check_artefact(art, root=tmp_path) == []

    def test_n_scored_greater_than_n_planned_fails(self, tmp_path: Path) -> None:
        """A 4-row selection artefact against 6 scored rows: the two are not describing the same run."""
        small = _write_jsonl(tmp_path / "small_manifest.jsonl", [{"item_id": f"x{i}"} for i in range(4)])
        art = _artefact(
            tmp_path,
            overrides={"planned_from": {"path": small.name, "sha256": _sha(small)}, "n_planned": 4},
        )
        assert "scored-exceeds-planned" in _codes(art, tmp_path)


# -- design point 1: n_planned comes from the manifest, never from the result's own claim ------------------


class TestPlannedComesFromTheManifest:
    def test_a_self_declared_n_planned_that_disagrees_with_the_manifest_fails(self, tmp_path: Path) -> None:
        """The exact escape this rule closes: a run that scored 3 of 6 declaring `n_planned: 3` so that its
        own arithmetic works out."""
        art = _artefact(tmp_path, rows=_scored_rows(3), overrides={"n_planned": 3})
        found = check_artefact(art, root=tmp_path)
        assert "planned-disagrees" in [v.code for v in found]
        assert any("says 3 but the selection artefact it names holds 6" in v.message for v in found)
        # and it is NOT let off as merely truncated-unmarked: the denominator claim itself is the finding
        assert any("cannot declare its own denominator" in v.message for v in found)

    def test_pointing_planned_from_at_the_output_file_is_refused(self, tmp_path: Path) -> None:
        """The hole the adversarial pass found in this guard's own first draft, which every other rule here
        would have let through: aim `planned_from` at the OUTPUT and the denominator becomes the numerator,
        so a 3-row prefix "plans" 3 rows and reads as a complete run."""
        rows = _scored_rows(3)
        art = _artefact(tmp_path, rows=rows)
        counted = tmp_path / "t2_raw_outputs.jsonl"
        payload = json.loads(art.read_text())
        payload[BLOCK_KEY]["planned_from"] = {"path": counted.name, "sha256": _sha(counted)}
        payload[BLOCK_KEY]["n_planned"] = 3
        art.write_text(json.dumps(payload, indent=2))
        found = check_artefact(art, root=tmp_path)
        assert "planned-is-the-output" in [v.code for v in found]
        assert any("makes n_planned self-declared again" in v.message for v in found)

    def test_a_missing_manifest_file_is_a_violation_not_a_pass(self, tmp_path: Path) -> None:
        art = _artefact(tmp_path)
        (tmp_path / "selection_manifest.jsonl").unlink()
        assert "file-missing" in _codes(art, tmp_path)

    def test_a_manifest_whose_sha256_does_not_match_the_pin_is_a_violation(self, tmp_path: Path) -> None:
        art = _artefact(tmp_path)
        # Deliberate corruption: one extra planned row appended AFTER the pin was computed.
        with (tmp_path / "selection_manifest.jsonl").open("a") as f:
            f.write(json.dumps({"item_id": "smuggled-in-after-the-pin"}) + "\n")
        found = check_artefact(art, root=tmp_path)
        assert "sha-mismatch" in [v.code for v in found]
        assert any("!= pinned" in v.message for v in found)

    def test_an_object_shaped_manifest_without_a_count_field_is_refused_not_guessed(self, tmp_path: Path) -> None:
        manifest = tmp_path / "selection_manifest.json"
        manifest.write_text(json.dumps({"planned": 6, "note": "an object, so which key is the denominator?"}))
        art = _artefact(tmp_path, overrides={"planned_from": {"path": manifest.name, "sha256": _sha(manifest)}})
        found = check_artefact(art, root=tmp_path)
        assert "planned-underivable" in [v.code for v in found]
        assert any("Refusing rather than guessing a denominator" in v.message for v in found)

    def test_an_object_shaped_manifest_with_a_count_field_is_accepted(self, tmp_path: Path) -> None:
        manifest = tmp_path / "selection_manifest.json"
        manifest.write_text(json.dumps({"selection": {"n_planned": MANIFEST_ROWS}}))
        art = _artefact(
            tmp_path,
            overrides={
                "planned_from": {
                    "path": manifest.name,
                    "sha256": _sha(manifest),
                    "count_field": "selection.n_planned",
                }
            },
        )
        assert check_artefact(art, root=tmp_path) == []


# -- design point 2: fail closed on a missing or mistyped field --------------------------------------------


class TestFailsClosed:
    def test_a_missing_n_planned_fails(self, tmp_path: Path) -> None:
        """THE fail-closed case. If this ever passes, every artefact in existence passes and the guard is
        decorative -- which is the bug class this repo spent 2026-09-26 auditing."""
        art = _artefact(tmp_path, drop=("n_planned",))
        found = check_artefact(art, root=tmp_path)
        assert "field-missing" in [v.code for v in found]
        assert any("n_planned" in v.message for v in found)

    @pytest.mark.parametrize("field", REQUIRED_BLOCK_FIELDS)
    def test_every_required_field_missing_fails(self, tmp_path: Path, field: str) -> None:
        assert check_artefact(_artefact(tmp_path, drop=(field,)), root=tmp_path) != []

    def test_an_artefact_with_no_block_at_all_fails(self, tmp_path: Path) -> None:
        path = tmp_path / "t2_scored_result.json"
        path.write_text(json.dumps({"accuracy": 0.99, "n": 3}))
        assert _codes(path, tmp_path) == ["no-block"]

    def test_an_unparseable_artefact_is_reported_not_skipped(self, tmp_path: Path) -> None:
        """A file that does not parse is a file that was NOT CHECKED. One guard in this repo shipped
        skipping these silently and printing OK; this asserts the opposite."""
        path = tmp_path / "t2_scored_result.json"
        path.write_text('{"run_completeness": {')
        found = check_artefact(path, root=tmp_path)
        assert [v.code for v in found] == ["unreadable"]
        assert "NOTHING in it was checked" in found[0].message

    def test_an_unknown_schema_fails(self, tmp_path: Path) -> None:
        assert "schema" in _codes(_artefact(tmp_path, overrides={"schema": "run-completeness/v2"}), tmp_path)

    @pytest.mark.parametrize("value", [None, "1519", 1519.0, True], ids=["null", "string", "float", "bool"])
    def test_a_non_int_count_is_rejected(self, tmp_path: Path, value: Any) -> None:
        """`True` is in here on purpose: `isinstance(True, int)` is True in Python, so a bool count would
        read as the number 1 in any check that did not say so."""
        assert "field-type" in _codes(_artefact(tmp_path, overrides={"n_planned": value}), tmp_path)

    def test_a_negative_count_is_rejected(self, tmp_path: Path) -> None:
        assert "field-range" in _codes(_artefact(tmp_path, overrides={"n_scored": -1}), tmp_path)

    @pytest.mark.parametrize("truncated", [None, "false", 0], ids=["absent", "string", "int"])
    def test_truncated_must_be_an_explicit_bool(self, tmp_path: Path, truncated: Any) -> None:
        """Stated on every result, complete or not, so that "complete" is a claim someone made rather than
        what an absent key happened to mean."""
        art = (
            _artefact(tmp_path, drop=("truncated",))
            if truncated is None
            else _artefact(tmp_path, overrides={"truncated": truncated})
        )
        assert check_artefact(art, root=tmp_path) != []


# -- design point 3: n_scored counts usable scores, not rows present ---------------------------------------


class TestUsableScores:
    @pytest.mark.parametrize(
        "value",
        [None, "", "   ", "not-a-number", True, False, float("nan"), float("inf"), [], {}, [0.5]],
        ids=["null", "empty", "whitespace", "unparseable", "true", "false", "nan", "inf", "list", "dict", "listnum"],
    )
    def test_these_are_not_scores(self, value: Any) -> None:
        assert usable_score(value) is False

    @pytest.mark.parametrize("value", [0, 0.0, 1, 0.74, -0.5, "0.74", "0"], ids=[str(i) for i in range(7)])
    def test_these_are_scores(self, value: Any) -> None:
        """`0` and `0.0` ARE usable scores when they are the value a row actually carries. The fail-open
        hazard is a zero SUBSTITUTED for an absent value, which is a different thing and is the case above."""
        assert usable_score(value) is True

    def test_an_absent_field_is_not_a_score(self) -> None:
        assert count_usable([{"item_id": "a"}], "p_established") == Counts(1, 0, 1, 0)

    def test_a_dotted_score_field_is_followed(self) -> None:
        rows = [{"free_text": {"p": 0.8}}, {"free_text": {"p": None}}, {"free_text": {}}]
        assert count_usable(rows, "free_text.p") == Counts(3, 1, 2, 0)

    def test_a_null_score_does_not_count_toward_n_scored(self, tmp_path: Path) -> None:
        """6 rows present, 3 of them unscorable: n_scored is 3, not 6, and the artefact claiming 6 fails."""
        rows = _scored_rows(MANIFEST_ROWS)
        rows[1]["p_established"] = None
        rows[3]["p_established"] = ""
        rows[5]["p_established"] = "n/a"
        art = _artefact(tmp_path, rows=rows, overrides={"n_scored": MANIFEST_ROWS, "n_error_terminal": 0})
        found = check_artefact(art, root=tmp_path)
        assert "scored-disagrees" in [v.code for v in found]
        assert any("3 of the 6 rows in the counted file carry a usable score" in v.message for v in found)

    def test_rows_present_is_not_n_scored_even_when_the_run_finished(self, tmp_path: Path) -> None:
        """All 6 planned rows attempted, 1 unscorable. Truthfully reported it passes -- 5 scored, 1 terminal
        error, marked truncated with its walk order, because 5 < 6."""
        rows = _scored_rows(MANIFEST_ROWS)
        rows[2]["p_established"] = None
        art = _artefact(
            tmp_path, rows=rows, overrides={"truncated": True, "walk_order": "corpus order"}
        )
        assert check_artefact(art, root=tmp_path) == []

    def test_retried_then_succeeded_is_reported_separately_from_terminal(self, tmp_path: Path) -> None:
        """The 32B Config C case: two timeouts retried to success. `n_error_terminal` is 0 and
        `n_error_retried` is 2 -- not one `n_error: 0` that hides both."""
        rows = _scored_rows(MANIFEST_ROWS, retried_indices=(0, 4))
        counts = count_usable(rows, "p_established")
        assert counts == Counts(6, 6, 0, 2)
        assert check_artefact(_artefact(tmp_path, rows=rows), root=tmp_path) == []
        # claiming the old shape -- zero errors of either kind -- is a violation
        art = _artefact(tmp_path, rows=rows, overrides={"n_error_retried": 0})
        found = check_artefact(art, root=tmp_path)
        assert "retried-disagrees" in [v.code for v in found]
        assert any("record a retry and then a usable score" in v.message for v in found)

    def test_a_row_retried_and_still_unscorable_is_terminal_not_retried(self, tmp_path: Path) -> None:
        rows = _scored_rows(MANIFEST_ROWS, retried_indices=(0, 1))
        rows[1]["p_established"] = None
        assert count_usable(rows, "p_established") == Counts(6, 5, 1, 1)

    @pytest.mark.parametrize(
        "marker", [{"retries": 2}, {"n_retries": 1}, {"attempts": 3}, {"n_attempts": 2}]
    )
    def test_every_retry_marker_shape_is_counted(self, marker: dict[str, Any]) -> None:
        assert count_usable([{"p": 0.5, **marker}], "p").n_error_retried == 1

    @pytest.mark.parametrize("marker", [{"attempts": 1}, {"retries": 0}, {"retries": True}])
    def test_a_single_attempt_is_not_a_retry(self, marker: dict[str, Any]) -> None:
        """`{"retries": True}` is in here for the `isinstance(True, int)` trap again: a bool must not read
        as the count 1."""
        assert count_usable([{"p": 0.5, **marker}], "p").n_error_retried == 0

    def test_a_terminal_error_count_that_disagrees_fails(self, tmp_path: Path) -> None:
        rows = _scored_rows(MANIFEST_ROWS)
        rows[0]["p_established"] = None
        art = _artefact(
            tmp_path,
            rows=rows,
            overrides={"n_error_terminal": 0, "truncated": True, "walk_order": "corpus order"},
        )
        assert "terminal-disagrees" in _codes(art, tmp_path)


# -- design point 4: the number and the file it came from travel together ----------------------------------


class TestTheCountedFileShaTravelsWithTheNumber:
    def test_the_counted_file_sha256_is_required(self, tmp_path: Path) -> None:
        art = _artefact(tmp_path, overrides={"counted_file": {"path": "t2_raw_outputs.jsonl"}})
        found = check_artefact(art, root=tmp_path)
        assert "field-missing" in [v.code for v in found]
        assert any("counted_file.sha256" in v.message for v in found)

    @pytest.mark.parametrize(
        "sha", ["", "deadbeef", "DEADBEEF" * 8, None, 1234], ids=["empty", "short", "uppercase", "null", "int"]
    )
    def test_a_counted_file_sha256_that_is_not_64_lowercase_hex_is_rejected(self, tmp_path: Path, sha: Any) -> None:
        art = _artefact(tmp_path, overrides={"counted_file": {"path": "t2_raw_outputs.jsonl", "sha256": sha}})
        assert "field-missing" in _codes(art, tmp_path)

    def test_a_counted_file_whose_bytes_changed_after_the_pin_is_a_violation(self, tmp_path: Path) -> None:
        """n_scored must not be readable as describing a file it was not computed over. The row appended
        here would even make a wrong n_scored look right, which is exactly why the pin is checked first."""
        art = _artefact(tmp_path, rows=_scored_rows(3), overrides={"truncated": True, "walk_order": "corpus order"})
        with (tmp_path / "t2_raw_outputs.jsonl").open("a") as f:
            for row in _scored_rows(MANIFEST_ROWS)[3:]:
                f.write(json.dumps(row) + "\n")
        found = check_artefact(art, root=tmp_path)
        assert "sha-mismatch" in [v.code for v in found]
        assert any("counted_file" in v.message for v in found)

    def test_a_missing_counted_file_is_a_violation_not_a_trusted_number(self, tmp_path: Path) -> None:
        art = _artefact(tmp_path)
        (tmp_path / "t2_raw_outputs.jsonl").unlink()
        found = check_artefact(art, root=tmp_path)
        assert "file-missing" in [v.code for v in found]
        assert any("an unchecked count is not a checked one" in v.message for v in found)

    def test_an_unparseable_counted_file_is_reported_not_skipped(self, tmp_path: Path) -> None:
        """Pinned to the broken bytes on purpose, so the finding is the PARSE FAILURE and not the sha -- a
        sha-only failure would leave "what if the pin matched?" untested."""
        art = _artefact(tmp_path)
        counted = tmp_path / "t2_raw_outputs.jsonl"
        counted.write_text('{"item_id": "a", "p_established": 0.5}\n{"item_id": broken\n')
        payload = json.loads(art.read_text())
        payload[BLOCK_KEY]["counted_file"]["sha256"] = _sha(counted)
        art.write_text(json.dumps(payload, indent=2))
        found = check_artefact(art, root=tmp_path)
        assert "counted-unreadable" in [v.code for v in found]
        assert any("row count is not trustworthy" in v.message for v in found)

    def test_a_json_array_counted_file_is_accepted(self, tmp_path: Path) -> None:
        rows = _scored_rows(MANIFEST_ROWS)
        counted = tmp_path / "counted_rows.json"
        counted.write_text(json.dumps(rows))
        art = _artefact(
            tmp_path, rows=rows, overrides={"counted_file": {"path": counted.name, "sha256": _sha(counted)}}
        )
        assert check_artefact(art, root=tmp_path) == []


# -- discovery and the baseline, over a real throwaway git checkout ----------------------------------------


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "repo"
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    return root


class TestDiscoveryAndBaseline:
    def test_a_name_matched_file_with_no_block_is_a_violation(self, tmp_path: Path) -> None:
        """The second prong: without it, the one way to escape this guard forever is to not opt in."""
        root = _repo(tmp_path, {"gates/gate_t2.json": json.dumps({"false_prove_rate": 0.02, "n": 55})})
        report = check(root, baseline_path=root / "missing_baseline.txt")
        assert not report.ok
        assert report.n_name_matched == 1
        assert any("no-block" in v for v in report.violations)

    @pytest.mark.parametrize(
        "name",
        [
            "gate_t2.json",
            "t2_scored_result.json",
            "t2_c3_scored_result.json",
            "4b_alone_tau_sweep_results.json",
            "baseline_results.example.json",
            "t2_harness_raw_outputs.jsonl",
            "t2_harness_raw_outputs.partial.jsonl",
        ],
    )
    def test_the_name_patterns_match_the_names_these_projects_actually_produce(
        self, tmp_path: Path, name: str
    ) -> None:
        root = _repo(tmp_path, {f"out/{name}": "{}\n"})
        report = check(root, baseline_path=root / "missing_baseline.txt")
        assert report.n_name_matched == 1, f"{name} was not recognised as a result artefact"

    def test_a_baseline_entry_waives_it(self, tmp_path: Path) -> None:
        content = json.dumps({"_note": "FABRICATED EXAMPLE", "accuracy": 0.9})
        root = _repo(tmp_path, {"proposals/x/baseline_results.json": content})
        target = root / "proposals/x/baseline_results.json"
        bl = root / "bl.txt"
        bl.write_text(f"{baseline_key(target, root)}  fabricated example, not a run's result. issue: TEST-1\n")
        report = check(root, baseline_path=bl)
        assert report.ok
        assert report.n_waived == 1

    def test_a_baseline_entry_with_no_reason_waives_nothing(self, tmp_path: Path) -> None:
        root = _repo(tmp_path, {"proposals/x/baseline_results.json": "{}"})
        bl = root / "bl.txt"
        bl.write_text(f"{baseline_key(root / 'proposals/x/baseline_results.json', root)}\n")
        keys, complaints = load_baseline(bl)
        assert keys == set()
        assert any("waives nothing" in c for c in complaints)
        assert not check(root, baseline_path=bl).ok

    def test_a_baseline_entry_with_no_issue_reference_waives_nothing(self, tmp_path: Path) -> None:
        root = _repo(tmp_path, {"proposals/x/baseline_results.json": "{}"})
        bl = root / "bl.txt"
        bl.write_text(f"{baseline_key(root / 'proposals/x/baseline_results.json', root)}  known, it's fine\n")
        keys, complaints = load_baseline(bl)
        assert keys == set()
        assert any("issue:" in c for c in complaints)

    def test_a_baseline_entry_goes_stale_the_moment_the_file_changes(self, tmp_path: Path) -> None:
        """Keyed on the bytes, so a waiver cannot follow a fabricated example into becoming a real result."""
        root = _repo(tmp_path, {"proposals/x/baseline_results.json": json.dumps({"_note": "FABRICATED"})})
        target = root / "proposals/x/baseline_results.json"
        bl = root / "bl.txt"
        bl.write_text(f"{baseline_key(target, root)}  fabricated example. issue: TEST-1\n")
        assert check(root, baseline_path=bl).ok
        target.write_text(json.dumps({"_note": "now a real measured result", "false_prove_rate": 0.02}))
        report = check(root, baseline_path=bl)
        assert not report.ok
        assert report.stale_baseline, "the old key should be reported as no longer matching anything"

    def test_explicit_paths_are_not_waivable(self, tmp_path: Path) -> None:
        """Naming a file on the command line asks about that file; a baseline hit would answer a different
        question."""
        root = _repo(tmp_path, {"proposals/x/baseline_results.json": "{}"})
        target = root / "proposals/x/baseline_results.json"
        bl = root / "bl.txt"
        bl.write_text(f"{baseline_key(target, root)}  fabricated. issue: TEST-1\n")
        assert check(root, baseline_path=bl).ok
        assert not check(root, explicit=[target], baseline_path=bl).ok

    def test_a_failing_git_ls_files_is_a_violation_not_an_empty_scan(self, tmp_path: Path) -> None:
        """"git is not here" and "there is nothing to check" must not print the same green OK. This is the
        first bug the adversarial pass found in the guard itself: `_tracked_files` returned `[]` on a
        non-zero git exit."""
        not_a_repo = tmp_path / "plain_dir"
        not_a_repo.mkdir()
        report = check(not_a_repo, baseline_path=not_a_repo / "missing.txt")
        assert not report.ok
        assert any("scan-failed" in v for v in report.violations)
        assert any("NOT the same as nothing being wrong" in v for v in report.violations)

    def test_an_orphan_sealed_jsonl_is_a_violation(self, tmp_path: Path) -> None:
        """A sealed raw-outputs `.jsonl` committed with no result artefact beside it is a number with no
        denominator. It gets its own message, because a rows-only file cannot carry the block itself."""
        root = _repo(tmp_path, {"out/t2_harness_raw_outputs.jsonl": '{"item_id": "a", "p": 0.5}\n'})
        report = check(root, baseline_path=root / "missing.txt")
        assert not report.ok
        assert any("no-sidecar" in v for v in report.violations)

    def test_a_sealed_jsonl_that_a_tracked_artefact_names_is_not_an_orphan(self, tmp_path: Path) -> None:
        """...and when the result artefact IS committed beside it, the jsonl is covered by that artefact's
        own check rather than reported twice."""
        root = tmp_path / "repo"
        (root / "out").mkdir(parents=True)
        manifest = _write_jsonl(root / "out" / "selection.jsonl", [{"item_id": f"x{i}"} for i in range(3)])
        counted = _write_jsonl(root / "out" / "t2_harness_raw_outputs.jsonl", _scored_rows(3))
        (root / "out" / "t2_scored_result.json").write_text(
            json.dumps(
                {
                    BLOCK_KEY: {
                        "schema": SCHEMA,
                        "planned_from": {"path": "selection.jsonl", "sha256": _sha(manifest)},
                        "n_planned": 3,
                        "counted_file": {"path": "t2_harness_raw_outputs.jsonl", "sha256": _sha(counted)},
                        "score_field": "p_established",
                        "n_scored": 3,
                        "n_error_terminal": 0,
                        "n_error_retried": 0,
                        "truncated": False,
                    }
                }
            )
        )
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        report = check(root, baseline_path=root / "missing.txt")
        assert report.ok, report.violations
        assert report.n_with_block == 1

    def test_a_block_carrying_file_is_found_under_any_name(self, tmp_path: Path) -> None:
        """First prong: opting IN works even from a name no pattern would guess."""
        root = _repo(tmp_path, {"out/whatever.json": json.dumps({BLOCK_KEY: {"schema": "nope"}})})
        report = check(root, baseline_path=root / "missing.txt")
        assert report.n_with_block == 1
        assert not report.ok


# -- the guard against the real repository -----------------------------------------------------------------


class TestAgainstThisRepo:
    def test_the_real_repo_run_is_green(self) -> None:
        """Exactly as the `guards` job runs it. Zero `run_completeness` blocks and two baselined name
        matches, so this asserts the step lands green rather than turning CI red for everyone."""
        report = check(REPO_ROOT)
        assert report.ok, report.violations + report.baseline_complaints
        assert report.n_with_block == 0
        assert report.n_waived == report.n_name_matched == 2

    def test_the_run_says_out_loud_that_it_checked_nothing(self, capsys: pytest.CaptureFixture[str]) -> None:
        """A guard that prints a crisp OK having examined nothing is this repo's own recent bug. The honest
        reading of this step's green tick is "nothing to check", and it must say so on every run."""
        assert main(["--root", str(REPO_ROOT)]) == 0
        out = capsys.readouterr().out
        assert "checked no real result artefact" in out
        assert "NOT 'the numbers were verified'" in out

    def test_the_committed_baseline_entries_all_carry_a_reason_and_an_issue(self) -> None:
        keys, complaints = load_baseline(REPO_ROOT / "scripts" / "run_completeness_baseline.txt")
        assert complaints == []
        assert len(keys) == 2

    def test_the_fixture_directory_exclusion_is_what_keeps_the_broken_fixture_out(self) -> None:
        """`tests/run_completeness_fixtures/broken_scored_result.json` is tracked, matches `*_scored*.json`
        and has no block, so it WOULD fail the real build if the exclusion were not doing its job. Without
        this test, `test_the_real_repo_run_is_green` would be passing for a reason nobody had checked."""
        import check_run_completeness as guard

        fixture = FIXTURES / "broken_scored_result.json"
        assert fixture.is_file()
        assert [v.code for v in check_artefact(fixture, root=REPO_ROOT)] == ["no-block"]
        with_block, name_matched = guard.discover(REPO_ROOT)
        assert fixture.resolve() not in {p.resolve() for p in name_matched}
        assert "run_completeness_fixtures" in guard.EXCLUDED_DIR_NAMES
