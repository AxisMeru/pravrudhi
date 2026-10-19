"""S3: three fixed strings in quashing.py that were not hash-pinned (R1, post-merge read of #529); a change must fail a test."""

from __future__ import annotations

import hashlib

import pytest

from pravrudhi.application import quashing as Q

H = {
    "REFUSAL": "77937ce06f8924f6",
    "NO_PAPERS_CAPTION": "84efd3ca4d23ca39",
    "STORE_OFF": "56e7735a0e4bca9a",
}


@pytest.mark.parametrize(("name", "expected"), sorted(H.items()))
def test_each_fixed_string_has_the_ruled_hash(name: str, expected: str) -> None:
    assert hashlib.sha256(getattr(Q, name).encode()).hexdigest()[:16] == expected
