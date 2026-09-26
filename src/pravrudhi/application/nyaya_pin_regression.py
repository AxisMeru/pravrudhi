"""Byte-identical regression check between two `score` binaries, for every contract id the OLD binary
knows about: `--describe-source`, `--describe-contract` (elements + denials), and `check_registry` for
the PROOF path (every required element satisfied), every DENIAL path (each denial individually asserted
alongside every element), and every missing-element path (each required element dropped one at a time,
not just the first).

Written for the 2026-09-26 pin bump (29f6eaed... -> 700de3aa..., prabhasa-nyaya
assistant/trackA/wave23-integration @636c540): every one of the OLD binary's 26 contracts came back
byte-identical against the candidate except ni138's source text (a deliberate fix on the candidate
branch, not a regression -- see that pin-bump commit and configs/nyaya_agent.yaml's ni138 comment).
Committed per R1's ask so the next pin bump re-runs this mechanically instead of rebuilding it from
scratch. CLI wrapper: scripts/nyaya_pin_regression.py.

Compares two binaries directly -- no repo config (pinned_score_sha256, KNOWN_CONTRACT_IDS, etc.) is read
or touched. The NEW binary may know more contract ids than the OLD one (that's the whole point of a pin
bump adding contracts); this module only requires that the OLD binary's ids are a subset of the NEW
binary's, and only compares the ids they share, since a NEW-only id has nothing "old" to regress against.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from pravrudhi.application import nyaya_lean_registry as reg


def describe_source(binpath: Path, contract_id: str) -> str:
    proc = subprocess.run(  # noqa: S603
        [str(binpath), "--describe-source", contract_id],
        capture_output=True, text=True, timeout=30, check=False,
    )
    return proc.stdout.strip()


def list_contract_ids(binpath: Path) -> set[str]:
    proc = subprocess.run(  # noqa: S603
        [str(binpath), "--list-contracts"], capture_output=True, text=True, timeout=30, check=False,
    )
    return set(reg.parse_list_contracts(proc.stdout).keys())


def new_contract_ids(old: Path, new: Path) -> set[str]:
    """Ids the NEW binary knows that the OLD one does not -- exactly the ids a pin bump is introducing."""
    return list_contract_ids(new) - list_contract_ids(old)


def assert_new_ids_not_validated(old: Path, new: Path, validated_contracts: frozenset[str]) -> None:
    """The whole point of the `validated_contracts` allowlist (issue #36, fail-closed by construction): a pin
    bump can only ever introduce ids that start out UNvalidated -- the element judge has not been measured
    on an id that didn't exist in the registry a moment ago. If a new id from this bump is already in
    `validated_contracts`, something added it to the allowlist before this exact binary was ever eval'd --
    that is a real safety defect in the config, not a false alarm, so this raises rather than warns.

    Called from `scripts/nyaya_pin_regression.py`'s CLI wrapper, against the real `validated_contracts` the
    repo's own `configs/nyaya_agent.yaml` loads (`load_agent_config(...).validated_contracts`) -- never a
    hardcoded set here, so this stays correct as the allowlist itself changes."""
    already_validated = new_contract_ids(old, new) & validated_contracts
    if already_validated:
        raise ValueError(
            f"pin bump introduces new contract id(s) that are ALREADY in validated_contracts (must be "
            f"eval'd and explicitly added to the allowlist AFTER this pin lands, never before): "
            f"{sorted(already_validated)}"
        )


def compare_pins(old: Path, new: Path) -> list[str]:
    """Returns diff descriptions found comparing `old` against `new`; empty means byte-identical
    everywhere compared. Raises nothing itself -- a missing binary surfaces as a subprocess failure the
    caller can see in the traceback, since a silently-empty diff list from a broken binary path would be
    far worse than a loud crash."""
    old_ids = list_contract_ids(old)
    new_ids = list_contract_ids(new)

    missing_from_new = old_ids - new_ids
    if missing_from_new:
        return [f"CRITICAL: ids the OLD binary knows that the NEW binary does not: {sorted(missing_from_new)}"]

    diffs: list[str] = []

    for contract_id in sorted(old_ids):
        old_src = describe_source(old, contract_id)
        new_src = describe_source(new, contract_id)
        if old_src != new_src:
            diffs.append(
                f"[{contract_id}] describe-source DIFFERS\n"
                f"  OLD: {old_src[:200]!r}\n"
                f"  NEW: {new_src[:200]!r}"
            )

        old_d = reg.describe_contract_detail(contract_id, score_bin=old)
        new_d = reg.describe_contract_detail(contract_id, score_bin=new)
        if old_d.elements != new_d.elements or old_d.denials != new_d.denials:
            diffs.append(
                f"[{contract_id}] describe-contract DIFFERS: "
                f"OLD elements={old_d.elements} denials={old_d.denials} "
                f"NEW elements={new_d.elements} denials={new_d.denials}"
            )
            continue  # scenarios below assume the same element/denial shape on both sides

        elements = old_d.elements
        denials = old_d.denials

        old_out = reg.check_registry(dict.fromkeys(elements, True), contract_id, score_bin=old)
        new_out = reg.check_registry(dict.fromkeys(elements, True), contract_id, score_bin=new)
        if old_out != new_out:
            diffs.append(f"[{contract_id}] PROOF path DIFFERS: OLD={old_out} NEW={new_out}")

        for deny in denials:
            assertions = dict.fromkeys(elements, True)
            assertions[deny] = True
            old_out = reg.check_registry(assertions, contract_id, score_bin=old)
            new_out = reg.check_registry(assertions, contract_id, score_bin=new)
            if old_out != new_out:
                diffs.append(f"[{contract_id}] DENIAL path ({deny!r}) DIFFERS: OLD={old_out} NEW={new_out}")

        for i in range(len(elements)):
            kept = elements[:i] + elements[i + 1 :]
            old_out = reg.check_registry(dict.fromkeys(kept, True), contract_id, score_bin=old)
            new_out = reg.check_registry(dict.fromkeys(kept, True), contract_id, score_bin=new)
            if old_out != new_out:
                diffs.append(
                    f"[{contract_id}] missing-element path (dropped {elements[i]!r}) DIFFERS: "
                    f"OLD={old_out} NEW={new_out}"
                )

    return diffs
