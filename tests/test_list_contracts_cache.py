"""`--list-contracts` is a pure function of the binary: one subprocess per binary sha, never cached when it fails."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pravrudhi.application import nyaya_lean_registry as reg

OUT = "ni138\tNegotiable Instruments Act §138\nbns69\tBharatiya Nyaya Sanhita §69\n"


@pytest.fixture(autouse=True)
def _clear() -> None:
    reg._LIST_CACHE.clear()
    reg._SHA_BY_STAT.clear()


@pytest.fixture
def spawns(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    seen: list[list[str]] = []

    def run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=OUT, stderr="")

    monkeypatch.setattr(reg, "parse_list_contracts", lambda out: {"c": [out[:5]]})
    monkeypatch.setattr(subprocess, "run", run)
    return seen


def _binary(tmp_path: Path, name: str, content: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(content)
    return p


def test_one_subprocess_per_binary_across_many_requests(tmp_path: Path, spawns: list[list[str]]) -> None:
    b = _binary(tmp_path, "score", b"binary-v1")
    first = reg.list_contracts(score_bin=b)
    assert [reg.list_contracts(score_bin=b) for _ in range(5)] == [first] * 5
    assert len(spawns) == 1


def test_the_key_is_the_binary_hash_not_its_path(tmp_path: Path, spawns: list[list[str]]) -> None:
    a = _binary(tmp_path, "score-a", b"same-bytes")
    b = _binary(tmp_path, "score-b", b"same-bytes")
    reg.list_contracts(score_bin=a)
    reg.list_contracts(score_bin=b)  # identical content under another path: same binary, no new read
    assert len(spawns) == 1


def test_a_different_binary_is_read_again_even_at_the_same_path(tmp_path: Path, spawns: list[list[str]]) -> None:
    b = _binary(tmp_path, "score", b"binary-v1")
    reg.list_contracts(score_bin=b)
    b.write_bytes(b"binary-v2-different-length")
    reg.list_contracts(score_bin=b)
    assert len(spawns) == 2


def test_a_failed_read_is_not_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    b = _binary(tmp_path, "score", b"binary-v1")
    calls: list[int] = []

    def flaky(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        calls.append(1)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(cmd, 30)
        return subprocess.CompletedProcess(cmd, 0, stdout=OUT, stderr="")

    monkeypatch.setattr(subprocess, "run", flaky)
    with pytest.raises(subprocess.TimeoutExpired):
        reg.list_contracts(score_bin=b)
    assert reg.list_contracts(score_bin=b)  # the next request reads again and succeeds
    assert len(calls) == 2


def test_each_caller_gets_its_own_copy(tmp_path: Path, spawns: list[list[str]]) -> None:
    b = _binary(tmp_path, "score", b"binary-v1")
    first = reg.list_contracts(score_bin=b)
    first["c"].append("mutated")
    first["extra"] = ["x"]
    assert reg.list_contracts(score_bin=b) == {"c": [OUT[:5]]}
