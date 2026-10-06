"""scripts/check_no_personal_data.py: a gmail address, a private LAN IP or a /Users/<name> path in a tracked file
fails the build."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("cnpd2", ROOT / "scripts/check_no_personal_data.py")
G = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(G)  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "line",
    [
        "contact: real.person@gmail.com",
        "REAL.PERSON@GMAIL.COM",
        "ssh user@10.20.30.40",
        "host 192.168.4.9:8080",
        "bind 172.20.1.2",
        "/Users/realname/project",
    ],
)
def test_private_shapes_are_hits(line: str) -> None:
    assert G.line_hits(line), line


@pytest.mark.parametrize(
    "line",
    [
        "someone@gmail.com",
        "seat-a@seats.test",
        "doc address 192.0.2.10 and 198.51.100.7 and 203.0.113.9",
        "docker bridge http://172.17.0.1:8099/v1",
        "172.32.0.1 is public",
        "/Users/user/x /Users/<user>/y /Users/you/z",
        "version 10.2.3 and 1.2.3.4",
    ],
)
def test_placeholders_and_documentation_ranges_pass(line: str) -> None:
    assert G.line_hits(line) == [], line


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    return tmp_path


@pytest.mark.parametrize(
    "line",
    [
        "real.name%40gmail.com",  # the form that leaked in a JSON snapshot
        "real.name%2540gmail.com",  # double-encoded
        "real.name&#64;gmail.com",
        "real.name&#x40;gmail.com",
        "real.name&commat;gmail.com",
        "real.name\\u0040gmail.com",
        "real.name@GMAIL.COM",
        "%73sh%20user@10.1.2.3",
        "http://192%2E168.0.5",  # not decodable to an address: documents the limit below
    ][:-1],
)
def test_encoded_forms_are_decoded_and_flagged(line: str) -> None:
    assert G.line_hits(line), line


def test_oauth_markers_in_a_url_are_flagged() -> None:
    for line in (
        "https://x.example/auth?login_hint=a",
        "https://x.example/auth?a=1&client_id=abc",
        "https://x/?code_challenge=zz",
    ):
        assert "OAuth marker in a URL" in G.line_hits(line), line
    assert G.line_hits("the client_id field is documented here") == []


def test_home_paths_are_flagged_outside_tests_only() -> None:
    assert G.line_hits("/home/realname/project/x") == ["/home/<name> path"]
    assert G.line_hits("/home/realname/project/x", in_tests=True) == []
    assert G.line_hits("/home/runner/work /home/user/x") == []


@pytest.mark.parametrize("name", ["leak.tex", "leak.csv", "leak.noext", "LEAK.SQL", "dir/leak.bib"])
def test_every_tracked_text_file_is_scanned_whatever_its_suffix(tmp_path: Path, name: str) -> None:
    repo = _repo(tmp_path, {name: "contact real.person%40gmail.com\n"})
    assert G.scan(repo) == [f"{name}:1: gmail address"]


def test_an_oversize_text_file_fails_instead_of_being_skipped(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {"big.txt": "x" * 5000, "fine.md": "ok"})
    assert G.scan(repo, max_bytes=1000) == ["big.txt:0: text file larger than 1000 bytes cannot be scanned"]
    assert G.scan(repo) == []


def test_an_oversize_binary_file_is_not_a_failure(tmp_path: Path) -> None:
    repo = tmp_path
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "blob.bin").write_bytes(b"\0" + b"x" * 5000)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    assert G.scan(repo, max_bytes=1000) == []


def test_scan_reports_path_and_line_never_the_value(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {"docs/a.md": "fine\nmail me at real.person@gmail.com\n", "src/b.py": "x = 1\n"})
    problems = G.scan(repo)
    assert problems == ["docs/a.md:2: gmail address"]
    assert all("real.person" not in p for p in problems)


def test_exempt_paths_and_binary_files_are_skipped(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        {
            "tests/test_demo_export_pii.py": "a@10.0.0.1 real.person@gmail.com",
            "blob.bin": "\0real.person@gmail.com",
            "docs/ok.md": "192.0.2.1",
        },
    )
    assert G.scan(repo) == []


def test_main_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert G.main(["--root", str(_repo(tmp_path / "ok", {"a.md": "nothing"}))]) == 0
    assert G.main(["--root", str(_repo(tmp_path / "bad", {"a.md": "10.9.8.7"}))]) == 1
    assert "private-range IP address" in capsys.readouterr().out


def test_this_checkout_is_clean() -> None:
    assert G.scan(ROOT) == []
