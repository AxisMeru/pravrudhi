"""Does a reviewer sign made on one commit still cover a later commit?

Today's session repeatedly found a sign's stated head diverged from the commit actually being merged (a PR's
GitHub-reported base is the target branch's CURRENT tip at query time, not the commit the sign was actually
read against; a rebase changes a branch's head sha outright). Each time this was caught by manually diffing
the sign's sha against the merge target -- correct, but manual, and skippable under time pressure. This is the
machine-checkable version: given a sign record (the sha it was made against, the paths it covers, and each
path's sha256 at that sha) and a target sha, it says CARRIES or RE-SIGN NEEDED, never guesses.

A sign record is a plain dict:
    {"sha": "<sign's head sha>", "paths": ["a/b.py", ...], "file_sha256": {"a/b.py": "<hex>", ...}}
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class SignRecordError(ValueError):
    """The sign record itself is malformed -- not a carryover question at all."""


@dataclass(frozen=True)
class CarryoverResult:
    carries: bool
    changed_paths: tuple[str, ...] = field(default_factory=tuple)
    #: path -> a one-line reason, only populated for changed_paths
    reasons: dict[str, str] = field(default_factory=dict)


def _parse_sign_record(raw: dict[str, Any]) -> tuple[str, tuple[str, ...], dict[str, str]]:
    for key in ("sha", "paths", "file_sha256"):
        if key not in raw:
            raise SignRecordError(f"sign record missing required key {key!r}")
    sha = raw["sha"]
    paths = tuple(raw["paths"])
    file_sha256 = raw["file_sha256"]
    missing = [p for p in paths if p not in file_sha256]
    if missing:
        raise SignRecordError(f"paths listed but with no file_sha256 entry: {missing}")
    return sha, paths, file_sha256


def _blob_sha256(repo_root: Path, ref: str, path: str) -> str | None:
    """The sha256 of `path` as it exists at `ref`, or None if that path does not exist there (deleted, or
    never existed) -- `git show` on a missing path exits nonzero, which is the real, distinguishable signal
    a caller needs, not an exception to swallow."""
    proc = subprocess.run(
        ["git", "show", f"{ref}:{path}"], cwd=repo_root, capture_output=True, check=False
    )
    if proc.returncode != 0:
        return None
    return hashlib.sha256(proc.stdout).hexdigest()


def check_carryover(sign_record: dict[str, Any], target_sha: str, *, repo_root: Path) -> CarryoverResult:
    """Does `sign_record` still cover `target_sha`? CARRIES iff every path it lists is byte-identical, by
    sha256, between the sign's own sha and `target_sha` -- and iff the sign record's own recorded hash for
    each path actually matches what was really at the sign's sha (an inconsistent record is treated as not
    carrying, on the path it's inconsistent about, since it cannot be trusted for that path either way)."""
    sign_sha, paths, recorded = _parse_sign_record(sign_record)
    changed: list[str] = []
    reasons: dict[str, str] = {}

    for path in paths:
        at_sign = _blob_sha256(repo_root, sign_sha, path)
        if at_sign is None:
            changed.append(path)
            reasons[path] = f"not found at the sign's own sha {sign_sha}"
            continue
        if at_sign != recorded[path]:
            changed.append(path)
            reasons[path] = (
                f"sign record's file_sha256 ({recorded[path]}) does not match what is actually at "
                f"{sign_sha} ({at_sign}) -- the sign record itself is inconsistent for this path"
            )
            continue
        at_target = _blob_sha256(repo_root, target_sha, path)
        if at_target is None:
            changed.append(path)
            reasons[path] = f"not found at the target sha {target_sha} (deleted, or never existed there)"
            continue
        if at_target != at_sign:
            changed.append(path)
            reasons[path] = f"content changed between {sign_sha} and {target_sha}"

    return CarryoverResult(carries=not changed, changed_paths=tuple(changed), reasons=reasons)
