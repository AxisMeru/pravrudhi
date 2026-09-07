"""Checked-in capability evidence and a repeatable source of work for the existing rivalry drive.

Orca-IDE in agents/orca_agent.py is internal scaffolding, NOT the competitor product tracked as `orca` here.
This source does not add a drive or dispatch work. Call next_gap on each planning pass; changes to the matrix
survive sessions. Unknown rival capabilities are not evidence of a gap. Matrix order is editorial priority;
verified rival advantages take precedence, followed by the number of rivals ahead, then matrix order.

Evidence is a relative file path (existence only), or `command: <argv>` executed without a shell at root.
Commands are trusted, checked-in checks and must be read-only. A passing check supports only its stated scope,
not an assertion of end-to-end usability. Missing matrices are empty; malformed matrices fail visibly.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

Status = Literal["have", "partial", "none"]
RivalStatus = Literal["have", "partial", "none", "unknown"]
MATRIX = Path("src/pravrudhi/assets/configs/parity.yaml")
_LEVEL = {"none": 0, "unknown": 0, "partial": 1, "have": 2}


class Rivals(BaseModel):
    model_config = ConfigDict(extra="forbid")
    orca: RivalStatus
    claude_desktop: RivalStatus
    codex: RivalStatus
    openclaw: RivalStatus


class Capability(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1)
    capability: str = Field(min_length=1)
    why_it_matters: str = Field(min_length=1)
    rivals: Rivals
    ours: Status
    evidence: list[str]
    notes: str


class Verification(BaseModel):
    id: str
    verified: bool
    failures: list[str]


class Coverage(BaseModel):
    """Verified full capabilities / all tracked capabilities; empty coverage is undefined."""

    numerator: int
    denominator: int
    fraction: float | None


class Matrix(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: list[Capability]

    @model_validator(mode="after")
    def unique_ids(self) -> Matrix:
        if len({row.id for row in self.rows}) != len(self.rows):
            raise ValueError("parity capability ids must be unique")
        return self


def load(root: Path) -> list[Capability]:
    path = root / MATRIX
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text())
    return Matrix.model_validate({"rows": raw} if isinstance(raw, list) else raw).rows


def _check(root: Path, evidence: str) -> bool:
    if evidence.startswith("command:"):
        try:
            argv = shlex.split(evidence.removeprefix("command:").strip())
            if not argv or any(Path(arg).is_absolute() for arg in argv):
                return False
            result = subprocess.run(
                argv, cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=30, check=False,
            )
            return result.returncode == 0
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return False
    path = Path(evidence)
    if not evidence.strip() or path.is_absolute():
        return False
    try:
        target = (root / path).resolve()
        return target.is_relative_to(root.resolve()) and target.is_file()
    except (OSError, ValueError):
        return False


def _verify(root: Path, rows: list[Capability]) -> list[Verification]:
    results = []
    for row in rows:
        failures = [e for e in row.evidence if not _check(root, e)]
        # A row claiming the capability is absent has nothing to evidence, and demanding some pushed toward
        # either inventing a citation or quietly dropping the row. An honest "we do not have this" is the most
        # useful entry in the table, because it is the backlog.
        if not row.evidence and row.ours != "none":
            failures.append("no evidence supplied")
        results.append(Verification(id=row.id, verified=not failures, failures=failures))
    return results


def verify(root: Path) -> list[Verification]:
    """Re-run every evidence reference, including partial and absent claims."""
    return _verify(root, load(root))


def _ahead(row: Capability, verified: bool) -> int:
    level = _LEVEL[row.ours] if verified else 0
    return sum(_LEVEL[value] > level for value in row.rivals.model_dump().values())


def gaps(root: Path) -> list[Capability]:
    rows = load(root)
    return [row for row, proof in zip(rows, _verify(root, rows), strict=True) if _ahead(row, proof.verified)]


def _coverage(rows: list[Capability], proofs: list[Verification]) -> Coverage:
    numerator = sum(row.ours == "have" and proof.verified for row, proof in zip(rows, proofs, strict=True))
    denominator = len(rows)
    return Coverage(numerator=numerator, denominator=denominator, fraction=numerator / denominator if denominator else None)


def coverage(root: Path) -> Coverage:
    rows = load(root)
    return _coverage(rows, _verify(root, rows))


def _next(rows: list[Capability], proofs: list[Verification]) -> Capability | None:
    candidates = [(row, proof) for row, proof in zip(rows, proofs, strict=True)
                  if row.ours != "have" or not proof.verified]
    best = max(candidates, key=lambda pair: _ahead(pair[0], pair[1].verified), default=None)
    return best[0] if best else None


def next_gap(root: Path) -> Capability | None:
    rows = load(root)
    return _next(rows, _verify(root, rows))


class ParityReport(BaseModel):
    rows: list[Capability]
    verification: list[Verification]
    coverage: Coverage
    gaps: list[Capability]
    next_gap: Capability | None


def report(root: Path) -> ParityReport:
    """One snapshot: evidence commands run once per report, not once per derived field."""
    rows = load(root)
    proofs = _verify(root, rows)
    return ParityReport(
        rows=rows, verification=proofs, coverage=_coverage(rows, proofs),
        gaps=[row for row, proof in zip(rows, proofs, strict=True) if _ahead(row, proof.verified)],
        next_gap=_next(rows, proofs),
    )
