"""CI guard (Lead-2, 2026-09-27, P2 hardening of the registry-to-gate coupling): every id in the pinned
registry must be a VISIBLE, CHECKED classification decision -- either on the `validated_contracts` allowlist
or on the `unvalidated_contracts_documented` list with a one-line reason. An id in NEITHER list fails; an id
in BOTH fails.

Why this exists. `validated_contracts` (issue #36) is an ALLOWLIST and therefore fails CLOSED: a registry id
nobody lists is refused at the gate, never silently proved. That is the safe direction, and it is why this is
hardening rather than an incident. What the allowlist does NOT do is make the decision visible: a pin bump
that adds eleven ids to `KNOWN_CONTRACT_IDS` changes nothing about `validated_contracts`, so every config-side
test still passes while eleven contracts have been classified by default rather than by anyone. This guard is
the missing coupling -- it forces the bump author to write down, per new id, which side it is on and why.

What it enforces, and nothing more:
  * every registry id appears in exactly one of the two lists (partition: neither and both both fail);
  * no id appears in either list that the registry does not know (drift the other way);
  * no id is repeated within a list (a duplicate makes a count argument unsound);
  * every `unvalidated_contracts_documented` entry carries a non-empty, single-line `reason`;
  * the registry holds EXACTLY `pinned_registry_count` ids, and that pin agrees with the digest seal.

What it does NOT enforce: whether a contract BELONGS on the allowlist. That is Lead-2's decision after a
signed eval, pinned by digest in `tests/test_sealed_set_digests.py::TestValidatedContractsAllowlist`. This
guard only insists the decision was made in the open.

THE VACUITY TRAP THIS IS BUILT AGAINST. "Every registry id is classified" is trivially TRUE over an empty or
truncated registry, so a parser that silently yielded an empty set would turn this guard into a green tick
that examined nothing. Every input is therefore fail-closed:

  * a missing, empty or unparseable config / pinned count / sealed-digest test / registry is a FAILURE, never
    an empty list and never a default;
  * the registry parser accepts only the shapes listed in `_parse_known_contract_ids` and FAILS on anything
    else, rather than falling through to `set()`;
  * the registry count is asserted as an EXACT EQUALITY against the pin, in both directions. A floor, or a
    bare non-empty check, would still silently accept a registry that SHRANK -- which is the same vacuity
    hole one size smaller, so equality it is (Lead-2, 2026-09-27);
  * a clean run PRINTS the three counts it examined, so a green log states what it looked at.

WHERE THE NUMBER LIVES, AND WHY IT IS CHECKED TWICE. The pin is `pinned_registry_count:` in
`configs/nyaya_agent.yaml`, deliberately sitting next to `pinned_score_sha256:` -- the registry's id count is
a property of the pinned Lean binary, so a pin bump has to touch both lines, on purpose, in one diff. It is
NOT a constant in this script: a number in the checker is a number the checker can quietly agree with
itself about.

That number also already exists as `tests/test_sealed_set_digests.py::TestKnownContractIds.EXPECTED_N`, which
is the digest seal (a count AND a sha256 over the sorted ids) and the real protection against a registry
edit. Two copies of one truth drift, and an uncoupled duplicate is exactly what this PR exists to eliminate,
so this guard reads BOTH statically and REFUSES if they disagree, naming both locations. A pin bump therefore
has to update `pinned_registry_count`, the seal's `EXPECTED_N`/`EXPECTED_DIGEST`, and the classification lists
in the same commit, or `guards` goes red.

PARSED, NEVER IMPORTED. `KNOWN_CONTRACT_IDS` is read out of
`src/pravrudhi/application/nyaya_lean_registry.py` with `ast`, the same way `scripts/import_guard.py` reads
imports -- `guards` is the static-checks job and a required check must never execute application code to
decide whether to pass. `configs/nyaya_agent.yaml` is read with `yaml.safe_load` (a declared project
dependency, not the application package), not through `load_agent_config`, for the same reason. No pytest
dependency anywhere: this runs as a plain `python scripts/check_contract_classification.py` step.

Usage: check_contract_classification.py [--root DIR]. Mirrors `import_guard.py` / `check_no_private_data.py`:
exit 1 and print one `<TOKEN> path: message` line per violation, exit 0 clean. Every terminal path prints a
DISTINCT stable token (`OUTCOME_TOKENS` below), so a test can assert WHICH outcome occurred -- two failures
that read alike prove nothing.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path
from typing import Any

import yaml

REGISTRY_REL = "src/pravrudhi/application/nyaya_lean_registry.py"
CONFIG_REL = "configs/nyaya_agent.yaml"
SEALED_TEST_REL = "tests/test_sealed_set_digests.py"

REGISTRY_CONSTANT = "KNOWN_CONTRACT_IDS"
SEALED_CLASS = "TestKnownContractIds"
SEALED_CONSTANT = "EXPECTED_N"
VALIDATED_KEY = "validated_contracts"
DOCUMENTED_KEY = "unvalidated_contracts_documented"

#: The yaml key holding the registry id count. Lowercase to match every other key in that file
#: (`pinned_score_sha256`, `validated_contracts`, ...); Lead-2's instruction named the constant
#: PINNED_REGISTRY_COUNT, which is what this module-level name is.
PINNED_REGISTRY_COUNT = "pinned_registry_count"

#: Every terminal outcome, each with its own token. Listed in one place so a reader can see the full outcome
#: space, and so `tests/test_check_contract_classification.py` can assert each one is actually exercised
#: rather than trusting that a negative fixture tripped "some" failure.
OUTCOME_TOKENS = (
    "OK-CLASSIFICATION-COMPLETE",
    "FAIL-CONFIG-MISSING",
    "FAIL-CONFIG-UNPARSEABLE",
    "FAIL-CONFIG-NOT-A-MAPPING",
    "FAIL-CONFIG-DUPLICATE-KEY",
    "FAIL-PINNED-COUNT-MISSING",
    "FAIL-PINNED-COUNT-EMPTY",
    "FAIL-PINNED-COUNT-UNPARSEABLE",
    "FAIL-SEALED-TEST-MISSING",
    "FAIL-SEALED-TEST-UNPARSEABLE",
    "FAIL-SEALED-PIN-NOT-FOUND",
    "FAIL-SEALED-PIN-DISAGREES",
    "FAIL-REGISTRY-MISSING",
    "FAIL-REGISTRY-UNPARSEABLE",
    "FAIL-REGISTRY-REASSIGNED",
    "FAIL-REGISTRY-NOT-MODULE-LEVEL",
    "FAIL-REGISTRY-EMPTY",
    "FAIL-REGISTRY-COUNT-MISMATCH",
    "FAIL-VALIDATED-KEY-MISSING",
    "FAIL-VALIDATED-KEY-WRONG-SHAPE",
    "FAIL-DOCUMENTED-KEY-MISSING",
    "FAIL-DOCUMENTED-KEY-WRONG-SHAPE",
    "FAIL-DOCUMENTED-ENTRY-WRONG-SHAPE",
    "FAIL-REASON-MISSING",
    "FAIL-REASON-EMPTY",
    "FAIL-REASON-NOT-ONE-LINE",
    "FAIL-DUPLICATE-WITHIN-LIST",
    "FAIL-UNKNOWN-ID-CLASSIFIED",
    "FAIL-CLASSIFIED-TWICE",
    "FAIL-UNCLASSIFIED-ID",
)


class ClassificationFailure(Exception):
    """A terminal failure carrying its own outcome token. Raised rather than returned so no caller can drop
    it on the floor and continue to a pass."""

    def __init__(self, token: str, where: str, message: str) -> None:
        if token not in OUTCOME_TOKENS:
            raise AssertionError(f"undeclared outcome token {token!r} -- add it to OUTCOME_TOKENS")
        super().__init__(f"{token} {where}: {message}")
        self.token = token
        self.where = where
        self.message = message


def _read(path: Path, missing_token: str) -> str:
    if not path.is_file():
        raise ClassificationFailure(
            missing_token,
            str(path),
            "input is absent -- refusing to treat a missing file as an empty list or a default, which "
            "would make 'every id is classified' vacuously true",
        )
    return path.read_text(encoding="utf-8")


def _assert_no_duplicate_keys(text: str, where: str) -> None:
    """Refuse a duplicated mapping key anywhere in the config.

    Found in this guard's own adversarial self-review, and it is the same fail-open family the PR is about:
    YAML silently keeps only the LAST of two identical keys, so a second `validated_contracts:` or
    `pinned_registry_count:` further down the file would override the first with no warning from
    `yaml.safe_load` and no sign in a reviewed diff that read only the first copy. Composed as a node graph
    rather than constructed, so detection happens before any value this guard trusts is built.
    """
    try:
        root = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        raise ClassificationFailure(
            "FAIL-CONFIG-UNPARSEABLE", where, f"yaml does not parse: {str(exc).splitlines()[0]}"
        ) from exc
    if root is None:
        return
    stack: list[yaml.Node] = [root]
    while stack:
        node = stack.pop()
        if isinstance(node, yaml.MappingNode):
            seen: set[str] = set()
            for key_node, value_node in node.value:
                if isinstance(key_node, yaml.ScalarNode):
                    if key_node.value in seen:
                        raise ClassificationFailure(
                            "FAIL-CONFIG-DUPLICATE-KEY",
                            where,
                            f"duplicate mapping key {key_node.value!r} at line "
                            f"{key_node.start_mark.line + 1}. YAML keeps only the LAST one, so a second "
                            "copy of a key this guard reads overrides the first invisibly",
                        )
                    seen.add(key_node.value)
                stack.append(key_node)
                stack.append(value_node)
        elif isinstance(node, yaml.SequenceNode):
            stack.extend(node.value)


def _load_config(path: Path) -> dict[str, Any]:
    text = _read(path, "FAIL-CONFIG-MISSING")
    _assert_no_duplicate_keys(text, str(path))
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ClassificationFailure(
            "FAIL-CONFIG-UNPARSEABLE", str(path), f"yaml does not parse: {str(exc).splitlines()[0]}"
        ) from exc
    if not isinstance(loaded, dict):
        raise ClassificationFailure(
            "FAIL-CONFIG-NOT-A-MAPPING",
            str(path),
            f"top level is {type(loaded).__name__}, not a mapping -- an empty or non-mapping config is a "
            "failure, never an empty set of classifications",
        )
    return loaded


def _pinned_count(config: dict[str, Any], where: str) -> int:
    """`pinned_registry_count` from the config, fail-closed on all three degenerate states.

    Absent, empty (`key:` with no value, or a blank string) and unparseable (anything that is not a positive
    integer literal) are three DISTINCT failures, because this constant is now config the checker depends on
    and it gets the same treatment as every other input: no default, ever. `bool` is rejected explicitly --
    `True` is an `int` in Python and `pinned_registry_count: yes` parses to it.
    """
    if PINNED_REGISTRY_COUNT not in config:
        raise ClassificationFailure(
            "FAIL-PINNED-COUNT-MISSING",
            where,
            f"no `{PINNED_REGISTRY_COUNT}:` key. It is the registry id count, pinned beside "
            "`pinned_score_sha256:` because it is a property of the pinned binary -- absent is a failure, "
            "never a default",
        )
    raw = config[PINNED_REGISTRY_COUNT]
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raise ClassificationFailure(
            "FAIL-PINNED-COUNT-EMPTY",
            where,
            f"`{PINNED_REGISTRY_COUNT}:` is present but empty ({raw!r}). An unset pin cannot be compared "
            "against anything, so it fails rather than being skipped",
        )
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        raise ClassificationFailure(
            "FAIL-PINNED-COUNT-UNPARSEABLE",
            where,
            f"`{PINNED_REGISTRY_COUNT}:` must be a positive integer, got {type(raw).__name__} {raw!r}",
        )
    return raw


def _parse_known_contract_ids(source: str, where: str) -> set[str]:
    """`KNOWN_CONTRACT_IDS` as a set of strings, read statically.

    Accepted shapes, exhaustively: `NAME[: ann] = frozenset({...})`, `frozenset([...])`, `frozenset((...))`,
    or a bare `{...}` set literal, whose every element is a string constant. ANY other shape -- a
    comprehension, a union of names, a call this function does not recognise, a non-string element -- is a
    FAILURE and never a silent empty set, because an empty set is the one value that makes this whole guard
    pass without examining anything.
    """
    try:
        tree = ast.parse(source, filename=where)
    except SyntaxError as exc:
        raise ClassificationFailure(
            "FAIL-REGISTRY-UNPARSEABLE", where, f"python source does not parse: {exc}"
        ) from exc

    # EVERY binding of the name, anywhere in the file, not the first one `ast.walk` happens to reach.
    # Found by attacking this guard (see tests/test_check_contract_classification.py's
    # TestAdversarialSelfReview): a decoy literal holding the pinned count, followed by a real reassignment
    # under an `if`, passed the whole check while the live set held an unclassified id. Taking "the first
    # match" is a silent-wrong-answer shape, so anything other than exactly one binding is refused.
    bindings: list[tuple[ast.stmt, ast.expr | None]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == REGISTRY_CONSTANT:
                bindings.append((node, node.value))
        elif isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == REGISTRY_CONSTANT for t in node.targets):
                bindings.append((node, node.value))
        elif isinstance(node, ast.AugAssign):
            target = node.target
            if isinstance(target, ast.Name) and target.id == REGISTRY_CONSTANT:
                bindings.append((node, None))

    if not bindings:
        raise ClassificationFailure(
            "FAIL-REGISTRY-UNPARSEABLE",
            where,
            f"no assignment to {REGISTRY_CONSTANT} was found. If it was renamed or is now built at "
            "runtime, update this guard to the new shape -- do not delete the check",
        )
    if len(bindings) > 1:
        raise ClassificationFailure(
            "FAIL-REGISTRY-REASSIGNED",
            where,
            f"{REGISTRY_CONSTANT} is bound {len(bindings)} times (lines "
            f"{[stmt.lineno for stmt, _ in bindings]}). This guard reads ONE literal; with several it would "
            "be reading whichever it happened to find first, which is how a decoy literal could satisfy "
            "the pin while a later reassignment held an unclassified id",
        )

    statement, value = bindings[0]
    if statement not in tree.body:
        raise ClassificationFailure(
            "FAIL-REGISTRY-NOT-MODULE-LEVEL",
            where,
            f"{REGISTRY_CONSTANT} is assigned at line {statement.lineno}, inside a class, function or "
            "conditional rather than at module level. A conditionally-bound registry cannot be read "
            "statically, so this guard refuses instead of reading one branch of it",
        )
    if value is None:
        raise ClassificationFailure(
            "FAIL-REGISTRY-UNPARSEABLE",
            where,
            f"{REGISTRY_CONSTANT}'s assignment carries no readable value (a bare annotation or an augmented "
            "assignment)",
        )

    container: ast.expr = value
    if isinstance(value, ast.Call):
        if not (isinstance(value.func, ast.Name) and value.func.id == "frozenset"):
            raise ClassificationFailure(
                "FAIL-REGISTRY-UNPARSEABLE",
                where,
                f"{REGISTRY_CONSTANT} is built by a call this guard does not recognise "
                f"({ast.dump(value.func)[:80]}); only frozenset(<literal>) is understood",
            )
        if len(value.args) != 1 or value.keywords:
            raise ClassificationFailure(
                "FAIL-REGISTRY-UNPARSEABLE",
                where,
                f"{REGISTRY_CONSTANT} = frozenset(...) takes exactly one literal argument here, got "
                f"{len(value.args)} positional and {len(value.keywords)} keyword",
            )
        container = value.args[0]

    if not isinstance(container, ast.Set | ast.List | ast.Tuple):
        raise ClassificationFailure(
            "FAIL-REGISTRY-UNPARSEABLE",
            where,
            f"{REGISTRY_CONSTANT} is not a set/list/tuple literal (got {type(container).__name__}); a "
            "comprehension or a computed value cannot be read statically, so this guard refuses rather "
            "than guessing",
        )

    ids: set[str] = set()
    for element in container.elts:
        if not (isinstance(element, ast.Constant) and isinstance(element.value, str)):
            raise ClassificationFailure(
                "FAIL-REGISTRY-UNPARSEABLE",
                where,
                f"{REGISTRY_CONSTANT} holds a non-string element at line {element.lineno}",
            )
        ids.add(element.value)
    return ids


def _parse_sealed_expected_n(source: str, where: str) -> int:
    """`TestKnownContractIds.EXPECTED_N`, read statically out of the sealed-digest test.

    Read rather than imported for the same reason as the registry: this runs in `guards`, which must not
    depend on pytest or on collecting the test suite. Both the class name and the attribute name are asserted
    present -- a pattern that matches nothing would read as coverage.
    """
    try:
        tree = ast.parse(source, filename=where)
    except SyntaxError as exc:
        raise ClassificationFailure(
            "FAIL-SEALED-TEST-UNPARSEABLE", where, f"python source does not parse: {exc}"
        ) from exc

    for node in ast.walk(tree):
        if not (isinstance(node, ast.ClassDef) and node.name == SEALED_CLASS):
            continue
        for stmt in node.body:
            targets: list[ast.expr] = []
            if isinstance(stmt, ast.AnnAssign):
                targets = [stmt.target]
            elif isinstance(stmt, ast.Assign):
                targets = list(stmt.targets)
            else:
                continue
            if not any(isinstance(t, ast.Name) and t.id == SEALED_CONSTANT for t in targets):
                continue
            expr = stmt.value
            if not (
                isinstance(expr, ast.Constant)
                and isinstance(expr.value, int)
                and not isinstance(expr.value, bool)
            ):
                raise ClassificationFailure(
                    "FAIL-SEALED-PIN-NOT-FOUND",
                    where,
                    f"{SEALED_CLASS}.{SEALED_CONSTANT} is not an integer literal, so it cannot be read "
                    "statically by a required check",
                )
            return expr.value
        raise ClassificationFailure(
            "FAIL-SEALED-PIN-NOT-FOUND",
            where,
            f"{SEALED_CLASS} carries no {SEALED_CONSTANT} assignment. If the seal moved, point this guard "
            "at its new home -- do not drop the cross-check",
        )

    raise ClassificationFailure(
        "FAIL-SEALED-PIN-NOT-FOUND",
        where,
        f"no class {SEALED_CLASS} in this file. It is the digest seal over the registry id set; if it was "
        f"renamed, update {SEALED_CLASS} in this guard rather than removing the cross-check",
    )


def _validated_ids(config: dict[str, Any], where: str) -> list[str]:
    if VALIDATED_KEY not in config:
        raise ClassificationFailure(
            "FAIL-VALIDATED-KEY-MISSING", where, f"no `{VALIDATED_KEY}:` key -- the allowlist is the gate"
        )
    raw = config[VALIDATED_KEY]
    if not isinstance(raw, list) or not raw or not all(isinstance(item, str) for item in raw):
        raise ClassificationFailure(
            "FAIL-VALIDATED-KEY-WRONG-SHAPE",
            where,
            f"`{VALIDATED_KEY}:` must be a non-empty list of id strings, got {type(raw).__name__} "
            f"({raw!r:.80})",
        )
    return list(raw)


def _documented_entries(config: dict[str, Any], where: str) -> list[tuple[str, str]]:
    """`unvalidated_contracts_documented` as (id, reason) pairs.

    Shape: a list of mappings, each with an `id` and a one-line `reason`. A list of mappings rather than a
    single `id: reason` mapping on purpose -- YAML silently keeps only the LAST of two duplicate keys, so a
    mapping would make a duplicated id invisible, and this guard's whole argument rests on the two lists
    partitioning the registry.
    """
    if DOCUMENTED_KEY not in config:
        raise ClassificationFailure(
            "FAIL-DOCUMENTED-KEY-MISSING",
            where,
            f"no `{DOCUMENTED_KEY}:` key. Every registry id not on the allowlist belongs here with a "
            "one-line reason; an absent key is a failure, not an empty list",
        )
    raw = config[DOCUMENTED_KEY]
    if not isinstance(raw, list) or not raw:
        raise ClassificationFailure(
            "FAIL-DOCUMENTED-KEY-WRONG-SHAPE",
            where,
            f"`{DOCUMENTED_KEY}:` must be a non-empty list of `- id: ... / reason: ...` mappings, got "
            f"{type(raw).__name__}",
        )

    entries: list[tuple[str, str]] = []
    for index, item in enumerate(raw):
        at = f"{where} [{DOCUMENTED_KEY}][{index}]"
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip():
            raise ClassificationFailure(
                "FAIL-DOCUMENTED-ENTRY-WRONG-SHAPE",
                at,
                f"entry must be a mapping with a non-empty string `id`, got {item!r:.80}",
            )
        contract_id = item["id"].strip()
        if "reason" not in item:
            raise ClassificationFailure(
                "FAIL-REASON-MISSING",
                at,
                f"{contract_id} carries no `reason`. The reason IS the classification decision; an entry "
                "without one records that somebody typed an id, not that anybody decided anything",
            )
        reason = item["reason"]
        if not isinstance(reason, str) or not reason.strip():
            raise ClassificationFailure(
                "FAIL-REASON-EMPTY", at, f"{contract_id} has an empty `reason` ({reason!r:.60})"
            )
        if "\n" in reason.strip():
            raise ClassificationFailure(
                "FAIL-REASON-NOT-ONE-LINE",
                at,
                f"{contract_id}'s `reason` spans {len(reason.strip().splitlines())} lines; one line, so it "
                "is readable in a diff and in this job's log",
            )
        entries.append((contract_id, reason.strip()))
    return entries


def _duplicates(ids: list[str]) -> list[str]:
    seen: set[str] = set()
    dupes: list[str] = []
    for value in ids:
        if value in seen and value not in dupes:
            dupes.append(value)
        seen.add(value)
    return sorted(dupes)


def check(root: Path) -> list[str]:
    """Run every assertion. Returns the lines to print on success; raises `ClassificationFailure` otherwise.

    Order is deliberate. The config is read first because it now holds the pin; the pin is reconciled with
    the digest seal SECOND, so the count comparison below is always made against a number two files agree
    on; only then is the registry parsed and counted; the partition is checked last.
    """
    config_path = root / CONFIG_REL
    where = str(config_path)
    config = _load_config(config_path)
    pinned = _pinned_count(config, where)

    sealed_path = root / SEALED_TEST_REL
    sealed_n = _parse_sealed_expected_n(_read(sealed_path, "FAIL-SEALED-TEST-MISSING"), str(sealed_path))
    if sealed_n != pinned:
        raise ClassificationFailure(
            "FAIL-SEALED-PIN-DISAGREES",
            where,
            f"the registry id count is pinned in two places and they disagree: "
            f"{CONFIG_REL}'s `{PINNED_REGISTRY_COUNT}:` is {pinned}, while "
            f"{SEALED_TEST_REL}'s {SEALED_CLASS}.{SEALED_CONSTANT} is {sealed_n}. One truth, one commit: "
            f"update `{PINNED_REGISTRY_COUNT}:`, the seal's {SEALED_CONSTANT} AND EXPECTED_DIGEST, and the "
            "classification lists together",
        )

    registry_path = root / REGISTRY_REL
    registry_ids = _parse_known_contract_ids(
        _read(registry_path, "FAIL-REGISTRY-MISSING"), str(registry_path)
    )
    if not registry_ids:
        raise ClassificationFailure(
            "FAIL-REGISTRY-EMPTY",
            str(registry_path),
            f"{REGISTRY_CONSTANT} parsed to zero ids. 'Every id is classified' is vacuously true over an "
            "empty registry, so this is a failure rather than a pass over nothing",
        )
    if len(registry_ids) != pinned:
        direction = "grew" if len(registry_ids) > pinned else "SHRANK"
        raise ClassificationFailure(
            "FAIL-REGISTRY-COUNT-MISMATCH",
            str(registry_path),
            f"the registry changed: {REGISTRY_CONSTANT} holds {len(registry_ids)} ids and the pin says "
            f"{pinned}, so it {direction}. This is an exact equality, not a floor, in both directions -- a "
            f"floor would silently accept a registry that shrank. Update `{PINNED_REGISTRY_COUNT}:` in "
            f"{CONFIG_REL}, the classification lists (`{VALIDATED_KEY}:` / `{DOCUMENTED_KEY}:`) and the "
            f"digest seal in {SEALED_TEST_REL} ({SEALED_CLASS}.{SEALED_CONSTANT} and EXPECTED_DIGEST) "
            "together, in the same commit",
        )

    validated = _validated_ids(config, where)
    documented = _documented_entries(config, where)
    documented_ids = [contract_id for contract_id, _ in documented]

    for key, ids in ((VALIDATED_KEY, validated), (DOCUMENTED_KEY, documented_ids)):
        dupes = _duplicates(ids)
        if dupes:
            raise ClassificationFailure(
                "FAIL-DUPLICATE-WITHIN-LIST",
                where,
                f"`{key}:` repeats {dupes} -- a duplicate makes the partition count unsound",
            )

    validated_set, documented_set = set(validated), set(documented_ids)

    stranger = sorted((validated_set | documented_set) - registry_ids)
    if stranger:
        raise ClassificationFailure(
            "FAIL-UNKNOWN-ID-CLASSIFIED",
            where,
            f"classified id(s) the pinned registry does not list: {stranger}. A typo here classifies "
            "nothing while looking like a decision",
        )

    both = sorted(validated_set & documented_set)
    if both:
        raise ClassificationFailure(
            "FAIL-CLASSIFIED-TWICE",
            where,
            f"id(s) on BOTH `{VALIDATED_KEY}:` and `{DOCUMENTED_KEY}:`: {both}. Exactly one side -- a "
            "contract is either covered by a signed eval or it is not",
        )

    neither = sorted(registry_ids - validated_set - documented_set)
    if neither:
        raise ClassificationFailure(
            "FAIL-UNCLASSIFIED-ID",
            where,
            f"registry id(s) in NEITHER `{VALIDATED_KEY}:` nor `{DOCUMENTED_KEY}:`: {neither}. A pin bump "
            f"added {len(neither)} id(s) nobody classified: put each on the allowlist (only by Lead-2's "
            f"decision after a signed eval covering it) or on `{DOCUMENTED_KEY}:` with a one-line reason",
        )

    return [
        f"OK-CLASSIFICATION-COMPLETE {len(registry_ids)} registry id(s) each classified exactly once: "
        f"{len(validated_set)} validated, {len(documented_set)} documented-unvalidated "
        f"(pin {CONFIG_REL}:{PINNED_REGISTRY_COUNT}={pinned}, seal {SEALED_CLASS}.{SEALED_CONSTANT}="
        f"{sealed_n})",
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__ and __doc__.splitlines()[0])
    ap.add_argument("--root", default=".", help="repo root to check (default: cwd)")
    root = Path(ap.parse_args().root).resolve()
    try:
        for line in check(root):
            print(line)
    except ClassificationFailure as failure:
        print(str(failure), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
