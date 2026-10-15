"""CI guard: no operator-identifying data in the PUBLIC tree. Scans EVERY tracked non-binary file (whatever its suffix)
and fails on:

* a gmail address (any `@gmail.com`) unless its local part is an obvious placeholder
  (`someone`, `someone-else`, `example`, `user`, `you`, `name`, `test`);
* a private-range IPv4 address (10/8, 192.168/16, 172.16/12) unless it is a documented constant (`ALLOWED_IPS`, e.g.
  Docker's bridge gateway);
* a `/Users/<name>` path, or a `/home/<name>` path outside `tests/`, unless `<name>` is a placeholder;
* an OAuth marker in a URL query (`login_hint=`, `client_id=`, `code_challenge=`).

Each line is checked as written AND decoded (percent-encoding, HTML entities, `\\uXXXX` escapes, repeated up to three times), so
`name%40gmail.com`, `name&#64;gmail.com` and `name\\u0040gmail.com` are caught too. A tracked text file larger than
`MAX_BYTES` FAILS (it
cannot be scanned, and a silent skip is how a leak hides); binary files (a NUL byte in the first 8 KB) are skipped.

Seat addresses live in local, uncommitted configuration (`agents/seat_identity.py`); real hosts in a local, gitignored
`configs/hosts.yaml`.
Use documentation addresses (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24) and `seat-a@seats.test` style placeholders.
Files whose JOB is to
hold redaction fixtures (`EXEMPT_PATHS`) are skipped. A hit prints `path:line: rule`, never the matched value.

Usage: check_no_personal_data.py [--root DIR]    exit 0 clean, 1 on any hit.
"""

from __future__ import annotations

import argparse
import html
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

GMAIL = re.compile(r"([A-Za-z0-9._%+-]+)@gmail\.com", re.I)
GMAIL_OK_LOCAL = frozenset({"someone", "someone-else", "example", "user", "you", "name", "test"})
PRIVATE_IP = re.compile(
    r"(?<![0-9.])(10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?![0-9])"
)
#: Documented constants that are not anyone's LAN: Docker's default bridge gateway (the host as seen from a container).
ALLOWED_IPS = frozenset({"172.17.0.1"})
USERS_PATH = re.compile(r"/Users/([A-Za-z_][A-Za-z0-9_.-]*)")
HOME_PATH = re.compile(r"/home/([A-Za-z_][A-Za-z0-9_.-]*)/")
NAME_OK = frozenset({"user", "you", "name", "someone", "x", "example", "me", "shared", "runner", "ubuntu"})
OAUTH_MARKER = re.compile(r"[?&;](login_hint|client_id|code_challenge)=", re.I)
_UNICODE_ESC = re.compile(r"\\u([0-9a-fA-F]{4})")

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
MAX_BYTES = 8_000_000


def variants(line: str) -> list[str]:
    """The line as written, then decoded (percent, HTML entities, \\uXXXX) up to three times, each distinct form once."""
    out, cur = [line], line
    for _ in range(3):
        nxt = html.unescape(urllib.parse.unquote(_UNICODE_ESC.sub(lambda m: chr(int(m.group(1), 16)), cur)))
        if nxt == cur:
            break
        out.append(nxt)
        cur = nxt
    return out


def line_hits(line: str, *, in_tests: bool = False) -> list[str]:
    hits: list[str] = []
    for v in variants(line):
        for m in GMAIL.finditer(v):
            if m.group(1).lower() not in GMAIL_OK_LOCAL:
                hits.append("gmail address")
        for m in PRIVATE_IP.finditer(v):
            if m.group(1) not in ALLOWED_IPS:
                hits.append("private-range IP address")
        for m in USERS_PATH.finditer(v):
            if m.group(1) not in NAME_OK:
                hits.append("/Users/<name> path")
        if not in_tests:
            for m in HOME_PATH.finditer(v):
                if m.group(1) not in NAME_OK:
                    hits.append("/home/<name> path")
        if OAUTH_MARKER.search(v):
            hits.append("OAuth marker in a URL")
    return list(dict.fromkeys(hits))


def tracked_files(root: Path) -> list[str]:
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True).stdout
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]


def scan(root: Path, *, max_bytes: int = MAX_BYTES) -> list[str]:
    problems: list[str] = []
    for rel in tracked_files(root):
        path = root / rel
        if rel in EXEMPT_PATHS or not path.is_file():
            continue
        try:
            size = path.stat().st_size
            if size > max_bytes:
                with path.open("rb") as fh:
                    binary = b"\0" in fh.read(8192)
                if not binary:
                    problems.append(f"{rel}:0: text file larger than {max_bytes} bytes cannot be scanned")
                continue
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue  # binary
        in_tests = rel.startswith("tests/") or "/tests/" in rel or "/test/" in rel
        for n, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
            for rule in line_hits(line, in_tests=in_tests):
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
