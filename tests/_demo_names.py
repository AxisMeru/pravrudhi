"""A TEMPORARY private-name list for the demo-export tests.

Tests never read the real list and never contain a real name: these are invented."""

from __future__ import annotations

import hashlib
from pathlib import Path

TEST_NAMES = ("Zorblat Quux", "Wibble", "zorblat_quux")


def names_kwargs(tmp_path: Path, names: tuple[str, ...] = TEST_NAMES) -> dict[str, str]:
    """Write a list of invented names under tmp_path and return the keyword arguments `write_demo` requires."""
    f = tmp_path / "private_names.txt"
    f.write_text("# invented names for tests\n" + "\n".join(names) + "\n")
    return {"private_names_path": str(f), "private_names_sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
