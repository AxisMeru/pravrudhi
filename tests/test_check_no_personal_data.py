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


def test_scan_reports_path_and_line_never_the_value(tmp_path: Path) -> None:
    repo = _repo(tmp_path, {"docs/a.md": "fine\nmail me at real.person@gmail.com\n", "src/b.py": "x = 1\n"})
    problems = G.scan(repo)
    assert problems == ["docs/a.md:2: gmail address"]
    assert all("real.person" not in p for p in problems)


def test_exempt_paths_and_non_text_files_are_skipped(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        {
            "tests/test_demo_export_pii.py": "a@10.0.0.1 real.person@gmail.com",
            "blob.bin": "real.person@gmail.com",
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
