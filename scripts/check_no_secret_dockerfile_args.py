"""CI guard (2026-09-26 incident, `docs/decisions/LEG-LEDGER-2026-09-23.md`): a Dockerfile `ARG`/`ENV` naming
something that looks like a secret is printed in cleartext in the build log and in `docker history` the moment
a real value is passed for it -- BuildKit's own `SecretsUsedInArgOrEnv` warning flags this, and it was ignored
under time pressure on 2026-09-26, leaking the AxisMeru GitHub PAT. The fix for a real secret is
`RUN --mount=type=secret,id=...` (see `deploy/docker/Dockerfile`); this guard makes the mistake impossible to
reintroduce silently by failing CI on any `ARG` or `ENV` line whose name contains a secret-shaped word.

Usage: check_no_secret_dockerfile_args.py [--root DIR]. Walks git's own tracked-file list (`git ls-files`) for
paths named `Dockerfile` or ending in `.dockerfile`, exactly like `check_no_private_data.py`'s tracked-file
scan -- a gitignored Dockerfile is invisible to it by construction.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

#: Case-insensitive; a declared name is a violation if any underscore-separated word (or adjacent pair, for
#: "API_KEY"/"PRIVATE_KEY"/"ACCESS_KEY") is one of these. A plain `ARG VERSION` or `ENV PORT=8765` never
#: matches; `ARG GITHUB_TOKEN`, `ENV API_KEY`, `ARG DB_PASSWORD` all do. `\b` alone would miss this: `_` is a
#: word character, so "TOKEN" inside "GITHUB_TOKEN" has no word boundary before it.
SECRET_WORDS = frozenset({"TOKEN", "SECRET", "PASSWORD", "PASSWD"})
SECRET_PAIRS = frozenset({"API_KEY", "APIKEY", "PRIVATE_KEY", "ACCESS_KEY"})
DECL_RE = re.compile(r"^\s*(ARG|ENV)\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)


def _is_secret_name(name: str) -> bool:
    upper = name.upper()
    parts = upper.split("_")
    if any(p in SECRET_WORDS for p in parts):
        return True
    return any(pair in upper for pair in SECRET_PAIRS)


def _tracked_dockerfiles(root: Path) -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True
    ).stdout
    paths = []
    for line in out.splitlines():
        if not line.strip():
            continue
        name = Path(line).name
        if name == "Dockerfile" or name.endswith(".dockerfile"):
            paths.append(root / line)
    return paths


def check(root: Path) -> list[str]:
    violations: list[str] = []
    for path in _tracked_dockerfiles(root):
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            m = DECL_RE.match(line)
            if m and _is_secret_name(m.group(2)):
                violations.append(
                    f"{path.relative_to(root)}:{lineno}: `{m.group(1).upper()} {m.group(2)}` looks like a "
                    "secret declared as a build arg or env var -- these are printed in cleartext in the build "
                    "log and `docker history`. Use `RUN --mount=type=secret,id=...` instead "
                    "(see deploy/docker/Dockerfile)."
                )
    return violations


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    args = ap.parse_args()
    violations = check(Path(args.root).resolve())
    for v in violations:
        print(v)
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
