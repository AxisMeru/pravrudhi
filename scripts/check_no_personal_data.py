"""CI guard: no operator-identifying data in the PUBLIC tree. Fails on, in any tracked text file:

* a gmail address (any `@gmail.com`) unless its local part is an obvious placeholder
  (`someone`, `someone-else`, `example`, `user`, `you`, `name`, `test`);
* a private-range IPv4 address (10/8, 192.168/16, 172.16/12) unless it is one of the few documented
  constants (`ALLOWED_IPS`, e.g. Docker's default bridge gateway);
* a `/Users/<name>` home path unless `<name>` is a placeholder (`user`, `you`, `name`, `someone`,
  `x`, `example`, `me`).

Seat addresses live in local, uncommitted configuration (`agents/seat_identity.py`); real hosts in a
local `configs/hosts.yaml` (gitignored). Use documentation addresses (192.0.2.0/24, 198.51.100.0/24,
203.0.113.0/24) and `seat-a@seats.test`-style placeholders in tests and docs. Files whose JOB is to hold
redaction fixtures (`EXEMPT_PATHS`) are skipped. A hit prints `path:line: rule`, never the matched value.

Usage: check_no_personal_data.py [--root DIR]    exit 0 clean, 1 on any hit.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

GMAIL = re.compile(r"([A-Za-z0-9._%+-]+)@gmail\.com", re.I)
GMAIL_OK_LOCAL = frozenset({"someone", "someone-else", "example", "user", "you", "name", "test"})
PRIVATE_IP = re.compile(
    r"(?<![0-9.])(10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?![0-9])"
)
#: Documented constants that are not anyone's LAN: Docker's default bridge gateway (the host as seen from a container).
ALLOWED_IPS = frozenset({"172.17.0.1"})
USERS_PATH = re.compile(r"/Users/([A-Za-z_][A-Za-z0-9_.-]*)")
USERS_OK = frozenset({"user", "you", "name", "someone", "x", "example", "me", "shared"})

#: Files that exist to test redaction or this guard, and so must hold private-shaped strings.
EXEMPT_PATHS = frozenset(
    {
        "scripts/check_no_personal_data.py",
        "tests/test_check_no_personal_data.py",
        "src/pravrudhi/application/demo_export.py",
        "tests/test_demo_export_pii.py",
        "tests/test_demo_export_allowlist.py",
        "tests/test_demo_export_product.py",
        "tests/test_demo_json_no_internal_leakage.py",
        "tests/test_check_no_private_data_ids.py",
    }
)
TEXT_SUFFIXES = frozenset(
    {".py", ".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".cfg", ".ini"}
    | {".js", ".mjs", ".ts", ".tsx", ".sh", ".html", ".css", ".sql", ".rst"}
)
MAX_BYTES = 12_000_000


def line_hits(line: str) -> list[str]:
    hits = []
    for m in GMAIL.finditer(line):
        if m.group(1).lower() not in GMAIL_OK_LOCAL:
            hits.append("gmail address")
    for m in PRIVATE_IP.finditer(line):
        if m.group(1) not in ALLOWED_IPS:
            hits.append("private-range IP address")
    for m in USERS_PATH.finditer(line):
        if m.group(1) not in USERS_OK:
            hits.append("/Users/<name> path")
    return hits


def tracked_files(root: Path) -> list[str]:
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True).stdout
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]


def scan(root: Path) -> list[str]:
    problems: list[str] = []
    for rel in tracked_files(root):
        path = root / rel
        if rel in EXEMPT_PATHS or path.suffix.lower() not in TEXT_SUFFIXES or not path.is_file():
            continue
        try:
            if path.stat().st_size > MAX_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for rule in line_hits(line):
                problems.append(f"{rel}:{n}: {rule}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=Path.cwd())
    args = ap.parse_args(argv)
    problems = scan(args.root)
    for p in problems:
        print(p)
    if problems:
        print(
            f"{len(problems)} personal-data hit(s): use placeholders or local configuration (see the docstring)", file=sys.stderr
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
