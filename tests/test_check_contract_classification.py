"""Tests for `scripts/check_contract_classification.py` -- the contract-classification CI guard.

Every test builds a throwaway repo tree (config + registry module + sealed-digest test) and runs the guard's
real `check()` over it with `--root`, the same entry point the `guards` job uses. Nothing is stubbed: the ast
parse, the yaml load and the path resolution are all exercised.

THE POSITIVE CONTROL COMES FIRST, deliberately. Without it, a guard that always failed would satisfy every
negative test in this file, and the file would read as thorough coverage of nothing -- a trap this project
has walked into more than once (see `tests/test_sealed_set_digests.py`'s own docstring on prose that did not
hold a guard shut, and `TestEnforcedDigestPins`: "a pinned constant nobody compares against is decoration").
There are two positive controls: the real repo root, which must pass, and a synthetic correct tree, which
proves the fixture builder below can produce a passing input at all -- so a negative fixture failing is
evidence about the mutation, not about the builder.

Each terminal path has its own token (`OUTCOME_TOKENS`), and every assertion below names the token it
expects. `test_every_declared_outcome_token_is_exercised` closes the loop: a token that no test names is a
terminal path nobody checked.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GUARD = REPO_ROOT / "scripts" / "check_contract_classification.py"
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from check_contract_classification import (  # noqa: E402 -- path set just above, repo idiom (test_check_fail_open_defaults.py:28)
    CONFIG_REL,
    DOCUMENTED_KEY,
    OUTCOME_TOKENS,
    PINNED_REGISTRY_COUNT,
    REGISTRY_REL,
    SEALED_TEST_REL,
    VALIDATED_KEY,
    ClassificationFailure,
    check,
)

#: 37 synthetic ids, split 14/23 exactly as the shipped config splits the real registry.
ALL_IDS = [f"c{i:02d}" for i in range(37)]
VALIDATED_IDS = ALL_IDS[:14]
DOCUMENTED_IDS = ALL_IDS[14:]


def _registry_source(ids: list[str]) -> str:
    """The shipped shape: `frozenset({...})` over string literals.

    An empty set is spelled `frozenset([])`, not `frozenset({})` -- `{}` is an empty DICT literal in Python,
    which the guard (correctly) refuses as an unrecognised shape rather than reading as zero ids. That case
    has its own test; this builder is for the shapes the guard is meant to UNDERSTAND.
    """
    if not ids:
        return "from __future__ import annotations\n\nKNOWN_CONTRACT_IDS: frozenset[str] = frozenset([])\n"
    body = ", ".join(repr(i) for i in ids)
    return (
        "from __future__ import annotations\n\n"
        f"KNOWN_CONTRACT_IDS: frozenset[str] = frozenset({{{body}}})\n"
    )


def _sealed_source(expected_n: int | str) -> str:
    return (
        '"""stand-in for the sealed-digest test"""\n\n\n'
        "class TestKnownContractIds:\n"
        f"    EXPECTED_N = {expected_n}\n"
        '    EXPECTED_DIGEST = "0" * 64\n'
    )


def _config_source(
    *,
    validated: list[str] | None = None,
    documented: list[tuple[str, str | None]] | None = None,
    pinned: object = 37,
    omit_pinned: bool = False,
    omit_validated: bool = False,
    omit_documented: bool = False,
    documented_raw: str | None = None,
) -> str:
    lines = ["tau: 0.97", "pinned_score_sha256: " + "a" * 64]
    if not omit_pinned:
        rendered = "" if pinned is None else str(pinned)
        lines.append(f"{PINNED_REGISTRY_COUNT}: {rendered}")
    if not omit_validated:
        lines.append(f"{VALIDATED_KEY}:")
        for contract_id in validated if validated is not None else VALIDATED_IDS:
            lines.append(f"  - {contract_id}")
    if documented_raw is not None:
        lines.append(documented_raw.rstrip("\n"))
    elif not omit_documented:
        lines.append(f"{DOCUMENTED_KEY}:")
        entries = documented if documented is not None else [(i, f"reason for {i}") for i in DOCUMENTED_IDS]
        for contract_id, reason in entries:
            lines.append(f"  - id: {contract_id}")
            if reason is not None:
                lines.append(f"    reason: {reason}")
    return "\n".join(lines) + "\n"


def _tree(
    tmp_path: Path,
    *,
    config: str | None = None,
    registry: str | None = None,
    sealed: str | None = None,
) -> Path:
    """A throwaway repo root holding the three inputs at their real relative paths.

    Passing `None` for any of them OMITS that file, which is how the absent-input fixtures are built.
    """
    root = tmp_path / "repo"
    for rel, text in ((CONFIG_REL, config), (REGISTRY_REL, registry), (SEALED_TEST_REL, sealed)):
        if text is None:
            continue
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _correct(tmp_path: Path, **overrides: object) -> Path:
    """A tree that PASSES, with keyword overrides applied to whichever input the test mutates."""
    kwargs: dict[str, str | None] = {
        "config": _config_source(),
        "registry": _registry_source(ALL_IDS),
        "sealed": _sealed_source(37),
    }
    kwargs.update(overrides)  # type: ignore[arg-type]
    return _tree(tmp_path, **kwargs)


def _token(root: Path) -> str:
    with pytest.raises(ClassificationFailure) as excinfo:
        check(root)
    return excinfo.value.token


# -- positive controls -------------------------------------------------------------------------------------


class TestPositiveControl:
    """Read this class first. A checker that always failed would pass every other test in this file."""

    def test_the_real_repo_passes(self) -> None:
        """The shipped `configs/nyaya_agent.yaml` and registry really do classify every id exactly once."""
        lines = check(REPO_ROOT)
        assert len(lines) == 1
        assert lines[0].startswith("OK-CLASSIFICATION-COMPLETE")

    def test_the_real_repo_run_states_what_it_examined(self) -> None:
        """A green log that does not say what it looked at is indistinguishable from a green log over
        nothing -- the counts are part of the outcome, not decoration."""
        line = check(REPO_ROOT)[0]
        assert "37 registry id(s)" in line
        assert "14 validated" in line
        assert "23 documented-unvalidated" in line

    def test_a_synthetic_correct_tree_passes(self, tmp_path: Path) -> None:
        """Proves the fixture builder can produce a PASSING input, so every negative result below is
        evidence about the mutation and not about the builder."""
        line = check(_correct(tmp_path))[0]
        assert line.startswith("OK-CLASSIFICATION-COMPLETE")
        assert "37 registry id(s)" in line

    def test_the_cli_exits_zero_on_the_real_repo(self) -> None:
        """The `guards` job runs this as a plain script, not through pytest. Asserted as a subprocess with
        no pytest in sight, because that is the invocation that gates a merge."""
        done = subprocess.run(
            [sys.executable, str(GUARD), "--root", str(REPO_ROOT)], capture_output=True, text=True
        )
        assert done.returncode == 0, done.stderr
        assert done.stdout.startswith("OK-CLASSIFICATION-COMPLETE")

    def test_the_cli_exits_one_and_names_the_token_on_stderr(self, tmp_path: Path) -> None:
        root = _correct(tmp_path, config=_config_source(documented=[(i, f"r {i}") for i in DOCUMENTED_IDS[1:]]))
        done = subprocess.run(
            [sys.executable, str(GUARD), "--root", str(root)], capture_output=True, text=True
        )
        assert done.returncode == 1
        assert "FAIL-UNCLASSIFIED-ID" in done.stderr
        assert not done.stdout


# -- the partition itself ----------------------------------------------------------------------------------


class TestPartition:
    def test_an_id_in_neither_list_fails(self, tmp_path: Path) -> None:
        """The finding this guard exists for: a pin bump adds an id and nobody classifies it."""
        root = _correct(
            tmp_path, config=_config_source(documented=[(i, f"r {i}") for i in DOCUMENTED_IDS[:-1]])
        )
        with pytest.raises(ClassificationFailure) as excinfo:
            check(root)
        assert excinfo.value.token == "FAIL-UNCLASSIFIED-ID"
        assert DOCUMENTED_IDS[-1] in excinfo.value.message

    def test_an_id_in_both_lists_fails(self, tmp_path: Path) -> None:
        overlap = VALIDATED_IDS[0]
        documented = [(i, f"r {i}") for i in DOCUMENTED_IDS] + [(overlap, "also documented")]
        root = _correct(tmp_path, config=_config_source(documented=documented))
        with pytest.raises(ClassificationFailure) as excinfo:
            check(root)
        assert excinfo.value.token == "FAIL-CLASSIFIED-TWICE"
        assert overlap in excinfo.value.message

    def test_an_id_the_registry_does_not_know_fails(self, tmp_path: Path) -> None:
        """Drift the other way: a typo'd entry classifies nothing while looking like a decision."""
        documented = [(i, f"r {i}") for i in DOCUMENTED_IDS] + [("c14_typo", "typo")]
        root = _correct(tmp_path, config=_config_source(documented=documented))
        assert _token(root) == "FAIL-UNKNOWN-ID-CLASSIFIED"

    def test_a_duplicate_within_one_list_fails(self, tmp_path: Path) -> None:
        """A repeat makes any count argument over the two lists unsound, so it is refused before the
        partition is computed."""
        documented = [(i, f"r {i}") for i in DOCUMENTED_IDS] + [(DOCUMENTED_IDS[0], "again")]
        root = _correct(tmp_path, config=_config_source(documented=documented))
        assert _token(root) == "FAIL-DUPLICATE-WITHIN-LIST"


# -- the one-line reason -----------------------------------------------------------------------------------


class TestReasons:
    def test_a_missing_reason_fails(self, tmp_path: Path) -> None:
        documented: list[tuple[str, str | None]] = [(i, f"r {i}") for i in DOCUMENTED_IDS]
        documented[3] = (DOCUMENTED_IDS[3], None)
        root = _correct(tmp_path, config=_config_source(documented=documented))
        assert _token(root) == "FAIL-REASON-MISSING"

    def test_an_empty_reason_fails(self, tmp_path: Path) -> None:
        documented: list[tuple[str, str | None]] = [(i, f"r {i}") for i in DOCUMENTED_IDS]
        documented[5] = (DOCUMENTED_IDS[5], '"   "')
        root = _correct(tmp_path, config=_config_source(documented=documented))
        assert _token(root) == "FAIL-REASON-EMPTY"

    def test_a_multi_line_reason_fails(self, tmp_path: Path) -> None:
        """"a one-line reason" is the requirement, and a block scalar is how it gets quietly widened into an
        essay nobody reads in a diff."""
        entries = [f"  - id: {i}\n    reason: r {i}" for i in DOCUMENTED_IDS[1:]]
        block = f"  - id: {DOCUMENTED_IDS[0]}\n    reason: |\n      first line\n      second line"
        raw = "\n".join([f"{DOCUMENTED_KEY}:", block, *entries])
        root = _correct(tmp_path, config=_config_source(documented_raw=raw))
        assert _token(root) == "FAIL-REASON-NOT-ONE-LINE"

    def test_an_entry_that_is_not_a_mapping_fails(self, tmp_path: Path) -> None:
        """A bare `- c14` looks like the allowlist's shape and carries no reason at all."""
        entries = [f"  - id: {i}\n    reason: r {i}" for i in DOCUMENTED_IDS[1:]]
        raw = "\n".join([f"{DOCUMENTED_KEY}:", f"  - {DOCUMENTED_IDS[0]}", *entries])
        root = _correct(tmp_path, config=_config_source(documented_raw=raw))
        assert _token(root) == "FAIL-DOCUMENTED-ENTRY-WRONG-SHAPE"


# -- the registry count pin (Lead-2, 2026-09-27: exact equality, tied to the score digest) -----------------


class TestRegistryCountPin:
    def test_a_registry_that_grew_fails(self, tmp_path: Path) -> None:
        root = _correct(tmp_path, registry=_registry_source([*ALL_IDS, "c37"]))
        with pytest.raises(ClassificationFailure) as excinfo:
            check(root)
        assert excinfo.value.token == "FAIL-REGISTRY-COUNT-MISMATCH"
        assert "the registry changed" in excinfo.value.message

    def test_a_registry_that_shrank_fails(self, tmp_path: Path) -> None:
        """The case a floor or a bare non-empty check would MISS, and the reason the pin is an equality."""
        root = _correct(tmp_path, registry=_registry_source(ALL_IDS[:-1]))
        with pytest.raises(ClassificationFailure) as excinfo:
            check(root)
        assert excinfo.value.token == "FAIL-REGISTRY-COUNT-MISMATCH"
        assert "SHRANK" in excinfo.value.message

    def test_the_mismatch_message_names_the_three_things_to_update_together(self, tmp_path: Path) -> None:
        root = _correct(tmp_path, registry=_registry_source(ALL_IDS[:20]))
        with pytest.raises(ClassificationFailure) as excinfo:
            check(root)
        message = excinfo.value.message
        assert PINNED_REGISTRY_COUNT in message
        assert VALIDATED_KEY in message and DOCUMENTED_KEY in message
        assert "EXPECTED_DIGEST" in message

    def test_an_empty_registry_fails_rather_than_passing_vacuously(self, tmp_path: Path) -> None:
        """Zero ids makes "every id is classified" trivially true. This is the headline vacuity trap, and it
        is reported as EMPTY rather than as a count mismatch so the log says what actually happened."""
        root = _correct(tmp_path, registry=_registry_source([]))
        assert _token(root) == "FAIL-REGISTRY-EMPTY"

    def test_an_empty_dict_literal_is_refused_rather_than_read_as_zero_ids(self, tmp_path: Path) -> None:
        """`frozenset({})` LOOKS like an empty set and is an empty dict. Refused as an unrecognised shape --
        the guard never guesses at a container it does not understand."""
        root = _correct(tmp_path, registry="KNOWN_CONTRACT_IDS = frozenset({})\n")
        assert _token(root) == "FAIL-REGISTRY-UNPARSEABLE"

    def test_an_empty_registry_against_a_zero_pin_still_fails(self, tmp_path: Path) -> None:
        """Belt and braces: even if somebody "fixed" the count mismatch by pinning 0, an empty registry is
        still refused -- a non-positive pin cannot be read at all."""
        root = _correct(tmp_path, registry=_registry_source([]), config=_config_source(pinned=0))
        assert _token(root) == "FAIL-PINNED-COUNT-UNPARSEABLE"

    def test_an_absent_pin_fails(self, tmp_path: Path) -> None:
        root = _correct(tmp_path, config=_config_source(omit_pinned=True))
        assert _token(root) == "FAIL-PINNED-COUNT-MISSING"

    def test_an_empty_pin_fails(self, tmp_path: Path) -> None:
        """`pinned_registry_count:` with nothing after it -- yaml reads that as None."""
        root = _correct(tmp_path, config=_config_source(pinned=None))
        assert _token(root) == "FAIL-PINNED-COUNT-EMPTY"

    def test_a_blank_string_pin_fails(self, tmp_path: Path) -> None:
        root = _correct(tmp_path, config=_config_source(pinned='"   "'))
        assert _token(root) == "FAIL-PINNED-COUNT-EMPTY"

    def test_an_unparseable_pin_fails(self, tmp_path: Path) -> None:
        for value in ("thirty-seven", "37.5", "true", "-3"):
            root = _correct(tmp_path / value, config=_config_source(pinned=value))
            assert _token(root) == "FAIL-PINNED-COUNT-UNPARSEABLE", value


# -- the cross-check between the two places the number lives -----------------------------------------------


class TestPinAndSealAgree:
    def test_a_disagreement_between_the_config_pin_and_the_seal_fails(self, tmp_path: Path) -> None:
        """Two copies of one truth drift. Adding an uncoupled duplicate is the very thing this PR removes,
        so the guard refuses when they diverge -- in either direction."""
        root = _correct(tmp_path, sealed=_sealed_source(36))
        with pytest.raises(ClassificationFailure) as excinfo:
            check(root)
        assert excinfo.value.token == "FAIL-SEALED-PIN-DISAGREES"
        assert PINNED_REGISTRY_COUNT in excinfo.value.message
        assert "EXPECTED_N" in excinfo.value.message

    def test_the_disagreement_message_names_both_locations(self, tmp_path: Path) -> None:
        root = _correct(tmp_path, config=_config_source(pinned=99))
        with pytest.raises(ClassificationFailure) as excinfo:
            check(root)
        assert excinfo.value.token == "FAIL-SEALED-PIN-DISAGREES"
        assert CONFIG_REL in excinfo.value.message
        assert SEALED_TEST_REL in excinfo.value.message

    def test_an_absent_seal_file_fails(self, tmp_path: Path) -> None:
        root = _correct(tmp_path, sealed=None)
        assert _token(root) == "FAIL-SEALED-TEST-MISSING"

    def test_an_unparseable_seal_file_fails(self, tmp_path: Path) -> None:
        root = _correct(tmp_path, sealed="class TestKnownContractIds(:\n")
        assert _token(root) == "FAIL-SEALED-TEST-UNPARSEABLE"

    def test_a_renamed_seal_class_fails_rather_than_being_skipped(self, tmp_path: Path) -> None:
        """A name-matching check that matches nothing reads as coverage. This one refuses instead."""
        root = _correct(tmp_path, sealed=_sealed_source(37).replace("TestKnownContractIds", "TestRenamed"))
        assert _token(root) == "FAIL-SEALED-PIN-NOT-FOUND"

    def test_a_seal_constant_that_is_not_an_integer_literal_fails(self, tmp_path: Path) -> None:
        root = _correct(tmp_path, sealed=_sealed_source("len(SOMETHING)"))
        assert _token(root) == "FAIL-SEALED-PIN-NOT-FOUND"


# -- absent / malformed inputs, none of which may read as a pass -------------------------------------------


class TestFailClosedInputs:
    def test_an_absent_config_fails(self, tmp_path: Path) -> None:
        root = _correct(tmp_path, config=None)
        assert _token(root) == "FAIL-CONFIG-MISSING"

    def test_a_malformed_config_fails(self, tmp_path: Path) -> None:
        root = _correct(tmp_path, config="validated_contracts: [a, b\n  - broken: {{\n")
        assert _token(root) == "FAIL-CONFIG-UNPARSEABLE"

    def test_an_empty_config_file_fails(self, tmp_path: Path) -> None:
        """`yaml.safe_load("")` is None. `or {}` here -- the repo's usual idiom -- would turn an emptied
        config into "no classifications required", which is a pass over nothing."""
        root = _correct(tmp_path, config="")
        assert _token(root) == "FAIL-CONFIG-NOT-A-MAPPING"

    def test_a_config_that_is_a_list_fails(self, tmp_path: Path) -> None:
        root = _correct(tmp_path, config="- a\n- b\n")
        assert _token(root) == "FAIL-CONFIG-NOT-A-MAPPING"

    def test_an_absent_registry_module_fails(self, tmp_path: Path) -> None:
        root = _correct(tmp_path, registry=None)
        assert _token(root) == "FAIL-REGISTRY-MISSING"

    def test_an_unparseable_registry_module_fails(self, tmp_path: Path) -> None:
        root = _correct(tmp_path, registry="KNOWN_CONTRACT_IDS = frozenset({'a',\n")
        assert _token(root) == "FAIL-REGISTRY-UNPARSEABLE"

    def test_a_renamed_registry_constant_fails(self, tmp_path: Path) -> None:
        root = _correct(tmp_path, registry="CONTRACT_IDS: frozenset[str] = frozenset({'a'})\n")
        assert _token(root) == "FAIL-REGISTRY-UNPARSEABLE"

    def test_a_computed_registry_fails_rather_than_yielding_an_empty_set(self, tmp_path: Path) -> None:
        """The parser's own fail-open shape: a comprehension or a call it does not recognise must REFUSE,
        never fall through to `set()`, which would pass this guard while examining nothing."""
        for source in (
            "KNOWN_CONTRACT_IDS = frozenset(_load_from_binary())\n",
            "KNOWN_CONTRACT_IDS = frozenset({x for x in SOMETHING})\n",
            "KNOWN_CONTRACT_IDS = _SOME_OTHER_SET | _MORE\n",
            "KNOWN_CONTRACT_IDS = frozenset({'a'}, {'b'})\n",
        ):
            root = _correct(tmp_path / str(abs(hash(source))), registry=source)
            assert _token(root) == "FAIL-REGISTRY-UNPARSEABLE", source

    def test_a_non_string_registry_element_fails(self, tmp_path: Path) -> None:
        root = _correct(tmp_path, registry="KNOWN_CONTRACT_IDS = frozenset({'a', 7})\n")
        assert _token(root) == "FAIL-REGISTRY-UNPARSEABLE"

    def test_an_absent_allowlist_key_fails(self, tmp_path: Path) -> None:
        root = _correct(tmp_path, config=_config_source(omit_validated=True))
        assert _token(root) == "FAIL-VALIDATED-KEY-MISSING"

    def test_an_empty_allowlist_fails(self, tmp_path: Path) -> None:
        """An emptied allowlist would make every id "unclassified" rather than passing, but it is still a
        shape failure: the allowlist IS the runtime gate, and an empty one gates everything to REFER."""
        config = _config_source(omit_validated=True) + f"{VALIDATED_KEY}: []\n"
        root = _correct(tmp_path, config=config)
        assert _token(root) == "FAIL-VALIDATED-KEY-WRONG-SHAPE"

    def test_an_absent_documented_key_fails(self, tmp_path: Path) -> None:
        root = _correct(tmp_path, config=_config_source(omit_documented=True))
        assert _token(root) == "FAIL-DOCUMENTED-KEY-MISSING"

    def test_a_documented_key_that_is_a_mapping_fails(self, tmp_path: Path) -> None:
        """The shape deliberately NOT accepted: yaml keeps only the last of two duplicate keys, so an
        `id: reason` mapping would hide a repeated id."""
        raw = f"{DOCUMENTED_KEY}:\n" + "\n".join(f"  {i}: r {i}" for i in DOCUMENTED_IDS)
        root = _correct(tmp_path, config=_config_source(documented_raw=raw))
        assert _token(root) == "FAIL-DOCUMENTED-KEY-WRONG-SHAPE"

    def test_an_empty_documented_list_fails(self, tmp_path: Path) -> None:
        root = _correct(tmp_path, config=_config_source(documented_raw=f"{DOCUMENTED_KEY}: []"))
        assert _token(root) == "FAIL-DOCUMENTED-KEY-WRONG-SHAPE"


class TestAdversarialSelfReview:
    """The fixtures written in answer to "can it pass something it should fail, or skip an input silently?"
    rather than to the brief. Each one is a hole found by attacking the guard, not a restatement of a rule."""

    def test_a_duplicated_config_key_fails_rather_than_silently_overriding(self, tmp_path: Path) -> None:
        """YAML keeps only the LAST of two identical keys. A second `pinned_registry_count:` lower down the
        file would override the reviewed one invisibly -- the same fail-open family this PR is about."""
        root = _correct(tmp_path, config=_config_source() + f"{PINNED_REGISTRY_COUNT}: 1\n")
        assert _token(root) == "FAIL-CONFIG-DUPLICATE-KEY"

    def test_a_duplicated_allowlist_key_fails(self, tmp_path: Path) -> None:
        root = _correct(tmp_path, config=_config_source() + f"{VALIDATED_KEY}:\n  - {ALL_IDS[0]}\n")
        assert _token(root) == "FAIL-CONFIG-DUPLICATE-KEY"

    def test_a_registry_literal_padded_with_repeats_fails_the_count(self, tmp_path: Path) -> None:
        """37 elements in the source, one distinct id after the set collapses them. The count is taken over
        the PARSED SET, so padding the literal cannot satisfy the pin."""
        root = _correct(tmp_path, registry=_registry_source(["c00"] * 37))
        assert _token(root) == "FAIL-REGISTRY-COUNT-MISMATCH"

    def test_a_decoy_literal_followed_by_a_reassignment_fails(self, tmp_path: Path) -> None:
        """The worst thing this guard did before it was attacked. A first literal holding exactly the pinned
        37 ids, then a real reassignment under an `if` adding an unclassified id: `ast.walk` found the decoy,
        the count matched, the partition matched, and the guard passed while the LIVE set held an id nobody
        classified. More than one binding is now refused outright."""
        decoy = ", ".join(repr(i) for i in ALL_IDS)
        live = ", ".join(repr(i) for i in [*ALL_IDS, "c37"])
        source = (
            f"KNOWN_CONTRACT_IDS: frozenset[str] = frozenset({{{decoy}}})\n"
            f"if True:\n    KNOWN_CONTRACT_IDS = frozenset({{{live}}})\n"
        )
        root = _correct(tmp_path, registry=source)
        assert _token(root) == "FAIL-REGISTRY-REASSIGNED"

    def test_a_constant_bound_inside_a_class_or_function_fails(self, tmp_path: Path) -> None:
        """Same family: the guard's message says "module level", so it must actually require it. A
        class-scoped or conditionally-bound literal read as if it were the module's registry is a
        silent-wrong-answer, not a pass."""
        decoy = ", ".join(repr(i) for i in ALL_IDS)
        root = _correct(
            tmp_path, registry=f"class _X:\n    KNOWN_CONTRACT_IDS: frozenset[str] = frozenset({{{decoy}}})\n"
        )
        assert _token(root) == "FAIL-REGISTRY-NOT-MODULE-LEVEL"

    def test_an_augmented_assignment_is_not_read_as_a_registry(self, tmp_path: Path) -> None:
        root = _correct(tmp_path, registry="KNOWN_CONTRACT_IDS = frozenset({'a'})\nKNOWN_CONTRACT_IDS |= X\n")
        assert _token(root) == "FAIL-REGISTRY-REASSIGNED"

    def test_the_guard_never_imports_the_application_package(self) -> None:
        """`guards` may not have the application package installed, and a required check must not execute
        application code to decide whether to pass. Asserted over the guard's own imports with `ast`, the
        same way `scripts/import_guard.py` does it -- not by grepping for a word in a comment."""
        import ast as _ast

        tree = _ast.parse(GUARD.read_text(encoding="utf-8"), filename=str(GUARD))
        roots: set[str] = set()
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Import):
                roots.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, _ast.ImportFrom) and node.module and node.level == 0:
                roots.add(node.module.split(".")[0])
        assert roots, "found no imports at all in the guard -- this scan is reading the wrong file"
        assert "pravrudhi" not in roots, (
            f"the guard imports the application package (imports: {sorted(roots)}). It must read "
            "KNOWN_CONTRACT_IDS with `ast` instead; importing it makes a required static check depend on "
            "the package being installed and on application code running."
        )
        assert "pytest" not in roots, "the guard must run as a plain script, with no pytest dependency"


# -- the outcome space itself ------------------------------------------------------------------------------


class TestOutcomeTokens:
    def test_every_declared_outcome_token_is_exercised(self) -> None:
        """"Every terminal path must be separately identifiable" is only worth anything if every one of them
        is actually reached by a test. A token nobody names here is a path nobody checks."""
        source = Path(__file__).read_text(encoding="utf-8")
        unexercised = [token for token in OUTCOME_TOKENS if source.count(token) < 1]
        assert not unexercised, f"outcome tokens no test in this file names: {unexercised}"

    def test_the_tokens_are_distinct_and_none_is_a_prefix_of_another(self) -> None:
        """Two failures that read alike prove nothing, and a token that is a prefix of another makes a
        substring assertion ambiguous."""
        assert len(set(OUTCOME_TOKENS)) == len(OUTCOME_TOKENS)
        for token in OUTCOME_TOKENS:
            others = [o for o in OUTCOME_TOKENS if o != token and o.startswith(token)]
            assert not others, f"{token} is a prefix of {others}"

    def test_an_undeclared_token_cannot_be_raised(self) -> None:
        """The failure type refuses a token that is not in the declared set, so a new terminal path cannot
        be added without appearing in `OUTCOME_TOKENS` -- and therefore in the test above."""
        with pytest.raises(AssertionError, match="undeclared outcome token"):
            ClassificationFailure("FAIL-SOMETHING-NEW", "where", "message")
