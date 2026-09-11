"""Gate emit / check / sign. Gates are never hand-edited; this module is the only writer."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from pravrudhi.application.delegation import AGENT_IDENTITIES, load_delegation, unmet_conditions
from pravrudhi_kernel.schema import GateReport, Signoff

CARD_HEADER = re.compile(r"^# (L\d+|P\d+|H\d+) — (.+)$", re.M)


def find_card(card_id: str, contracts_dir: Path) -> tuple[Path, str]:
    for p in sorted(contracts_dir.glob(f"{card_id}_*.md")):
        m = CARD_HEADER.search(p.read_text())
        if m and m.group(1) == card_id:
            return p, m.group(2).strip()
    raise FileNotFoundError(f"no contract card for {card_id} under {contracts_dir}")


def _dump(report: GateReport, path: Path) -> Path:
    data: dict[str, Any] = json.loads(report.model_dump_json(by_alias=True))
    path.write_text(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    return path


def emit_gate(card_id: str, *, contracts_dir: Path, gates_dir: Path, evidence_file: Path, kernel_release: str) -> Path:
    find_card(card_id, contracts_dir)
    raw: dict[str, Any] = yaml.safe_load(evidence_file.read_text()) or {}
    kind = "phase" if card_id.startswith("P") else "hypothesis" if card_id.startswith("H") else "loop"
    report = GateReport.model_validate(
        raw
        | {
            "id": card_id,
            "kind": kind,
            "kernel_release": kernel_release,
            "signoff": raw.get("signoff") or {"by": None, "at": None, "note": None},
        }
    )
    gates_dir.mkdir(parents=True, exist_ok=True)
    return _dump(report, gates_dir / f"gate_{card_id}.json")


def check_gate(path: Path, *, contracts_dir: Path) -> list[str]:
    problems: list[str] = []
    try:
        report = GateReport.model_validate_json(path.read_text())
    except ValueError as e:  # pydantic ValidationError is a ValueError
        return [f"schema: {e}"]
    try:
        find_card(report.id, contracts_dir)
    except FileNotFoundError as e:
        problems.append(str(e))
    if report.status == "pruned" and report.hetvabhasa is None:
        problems.append("pruned without hetvabhasa")
    if "no_claim" in report.domain_gate.evidence and report.measure_class != "n/a":
        problems.append("domain_gate says no_claim but measure_class is not n/a")
    if report.status == "pass" and report.closure.signoff.verdict == "pass" and report.signoff.by is None:
        problems.append("closure.signoff pass without a signer")
    return problems


def sign_gate(path: Path, *, by: str, note: str) -> Path:
    if by.strip().lower() in AGENT_IDENTITIES:
        raise PermissionError("sign-off is a human act; refused for agent identity")
    return _sign(path, by=by, note=note)


def sign_gate_delegated(path: Path, *, root: Path, contracts_dir: Path) -> Path:
    """Close a gate as the delegation's identity, under the delegation's own conditions (ADR-0040).

    `sign_gate` refuses every agent identity, which is right for a caller asserting authority by argument. The
    authority here comes from `configs/delegation.yaml`, and the judgement a person used to apply by reading
    the pack is applied as conditions: the gate check is clean and every non-signoff closure layer already
    passes. A condition that cannot be evaluated counts as unmet, never as satisfied. The signature is always
    the delegation's identity, never a human name, and the note quotes the delegation so a reader can tell an
    autonomous close from one a person looked at.
    """
    delegation = load_delegation(root)
    if delegation is None:
        raise PermissionError("no delegation recorded in configs/delegation.yaml; sign-off is a human act here")
    allowed, why = delegation.permits("gate_signoff")
    if not allowed:
        raise PermissionError(f"autonomous gate sign-off refused: {why}")
    problems = check_gate(path, contracts_dir=contracts_dir)
    report = GateReport.model_validate_json(path.read_text())
    layers = report.closure.model_dump()
    failing = sorted(name for name, layer in layers.items() if name != "signoff" and layer["verdict"] != "pass")
    unmet = unmet_conditions(delegation, act="gate_signoff", gate_problems=problems, failing_layers=failing)
    if unmet:
        raise PermissionError("autonomous gate sign-off refused: " + "; ".join(unmet))
    note = f"autonomous close under the delegation of {delegation.granted}: {delegation.instruction.strip()}"
    return _sign(path, by=delegation.identity, note=note)


def _sign(path: Path, *, by: str, note: str) -> Path:
    report = GateReport.model_validate_json(path.read_text())
    signoff_layer = report.closure.signoff.model_copy(update={"verdict": "pass", "evidence": [f"signed_by={by}"]})
    signed = report.model_copy(
        update={
            "signoff": Signoff(by=by, at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"), note=note),
            "closure": report.closure.model_copy(update={"signoff": signoff_layer}),
        }
    )
    return _dump(GateReport.model_validate(signed.model_dump(by_alias=True)), path)
