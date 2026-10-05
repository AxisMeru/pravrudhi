"""Every configured pytest `testpaths` root must be run by a CI job (#96).

The converse of `check_uncollected_tests.py`: that guard proves every test file sits inside a configured
root; this one proves every configured root is actually named by a pytest invocation in
`.github/workflows/*.yml`. A root nobody invokes collects nothing in CI while the other guard reports the
tree clean.

Strict on purpose. A root counts as run by an invocation only when that invocation
  * names the root, or one of its ancestors, as an argument (path components, never substrings: the
    `tests/governance` job does NOT run the `tests` root), or names no path at all (pytest then uses
    `testpaths`), and
  * applies no `-k`, `-m`, `--ignore`, `--ignore-glob` or `--deselect` filter, which can skip most of it.
An indirect spelling (a `make` target, a variable) is not recognised and fails with the fix: spell the root.

Also (the second case in #96): a `pyproject.toml` below the repo root that declares its own `testpaths`
must resolve to roots the winning root configuration also names (a narrower package scope is fine; a root only
the nested file knows is never run by CI).

Fails closed: unreadable workflows, no workflow files, or no pytest invocation at all is a violation.

Usage: check_testpaths_run_by_ci.py [--root DIR]
Exit 1 and print `path: [rule] message` per violation; exit 0 clean.
"""

from __future__ import annotations

import argparse
import os
import shlex
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_uncollected_tests import ConfigError, read_config  # noqa: E402

#: pytest options that take a separate value argument; their value is not a path to run.
_VALUE_FLAGS = frozenset({
    "-k", "-m", "-p", "-c", "-o", "-n", "--cov", "--cov-report", "--cov-fail-under", "--cov-config", "--ignore",
    "--ignore-glob", "--deselect", "--rootdir", "--maxfail", "--junitxml", "--durations", "--timeout", "--tb",
    "--basetemp", "--import-mode", "--log-level", "--confcutdir", "--override-ini", "--durations-min",
})
_FILTER_FLAGS = ("-k", "-m", "--ignore", "--ignore-glob", "--deselect")
_SEPARATORS = frozenset({"&&", "||", ";", "|", "&"})
_PRUNE = frozenset({"node_modules", "venv", "build", "dist"})


@dataclass(frozen=True)
class Invocation:
    workflow: str
    paths: tuple[str, ...]  # normalised positional roots; empty means "uses testpaths"
    filtered: bool


@dataclass(frozen=True)
class Violation:
    where: str
    rule: str
    message: str

    def render(self) -> str:
        return f"{self.where}: [{self.rule}] {self.message}"


def _norm(p: str) -> str:
    p = p.split("::", 1)[0]
    parts = [c for c in PurePosixPath(p).parts if c not in (".", "/")]
    return "/".join(parts)


def _covers(arg: str, root: str) -> bool:
    """`arg` runs `root` when it is `root` itself or an ancestor directory (path components, not a prefix)."""
    if arg == "":
        return True  # `.` -> the whole rootdir
    return root == arg or root.startswith(arg + "/")


def _lex(line: str) -> shlex.shlex:
    lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = "#"
    return lexer


def invocations_in(command: str, workflow: str) -> list[Invocation]:
    """Every pytest invocation in one `run:` script. Backslash continuations are joined first."""
    out: list[Invocation] = []
    for line in command.replace("\\\n", " ").splitlines():
        try:
            tokens = list(_lex(line))
        except ValueError:
            continue
        i = 0
        while i < len(tokens):
            is_pytest = tokens[i] == "pytest" or (tokens[i] == "-m" and i + 1 < len(tokens) and tokens[i + 1] == "pytest")
            if not is_pytest:
                i += 1
                continue
            i += 2 if tokens[i] == "-m" else 1
            paths: list[str] = []
            filtered = False
            while i < len(tokens) and tokens[i] not in _SEPARATORS:
                t = tokens[i]
                if t.startswith("-"):
                    name = t.split("=", 1)[0]
                    filtered = filtered or name in _FILTER_FLAGS
                    if "=" not in t and name in _VALUE_FLAGS:
                        i += 1
                else:
                    paths.append(_norm(t))
                i += 1
            out.append(Invocation(workflow, tuple(paths), filtered))
    return out


def workflow_invocations(root: Path) -> tuple[list[Invocation], list[Violation]]:
    wf_dir = root / ".github" / "workflows"
    files = sorted([*wf_dir.glob("*.yml"), *wf_dir.glob("*.yaml")]) if wf_dir.is_dir() else []
    if not files:
        return [], [Violation(".github/workflows", "no-workflows", "no workflow files found; this run proved nothing")]
    found: list[Invocation] = []
    bad: list[Violation] = []
    for f in files:
        rel = f.relative_to(root).as_posix()
        try:
            doc = yaml.safe_load(f.read_text())
        except (OSError, yaml.YAMLError) as e:
            bad.append(Violation(rel, "unreadable", f"cannot read workflow: {e}"))
            continue
        for job in ((doc or {}).get("jobs") or {}).values():
            for step in (job or {}).get("steps") or []:
                run = (step or {}).get("run")
                if isinstance(run, str):
                    found.extend(invocations_in(run, rel))
    return found, bad


def _nested_testpaths(root: Path) -> list[tuple[str, tuple[str, ...]]]:
    out: list[tuple[str, tuple[str, ...]]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in _PRUNE]
        d = Path(dirpath)
        if d == root or "pyproject.toml" not in filenames:
            continue
        with (d / "pyproject.toml").open("rb") as fh:
            table = tomllib.load(fh).get("tool", {}).get("pytest", {}).get("ini_options", {})
        tp = table.get("testpaths")
        if tp:
            base = d.relative_to(root).as_posix()
            out.append((f"{base}/pyproject.toml", tuple(_norm(f"{base}/{p}") for p in ([tp] if isinstance(tp, str) else tp))))
    return out


def check(root: Path) -> list[Violation]:
    try:
        cfg = read_config(root)
    except ConfigError as e:
        return [Violation("pyproject.toml", "no-config", f"cannot read the pytest configuration: {e}")]
    roots = [_norm(p) for p in cfg.testpaths]
    invs, violations = workflow_invocations(root)
    if not invs and not violations:
        violations.append(
            Violation(".github/workflows", "no-pytest", "no pytest invocation found in any workflow; this run proved nothing")
        )
    usable = [i for i in invs if not i.filtered]
    for r in roots:
        if not any(not i.paths or any(_covers(a, r) for a in i.paths) for i in usable):
            violations.append(Violation(
                cfg.source, "root-not-run",
                f"testpaths root {r!r} is not run by any CI pytest invocation. Add a job that runs `pytest {r}` "
                f"spelled exactly like that (an indirect or filtered invocation does not count).",
            ))
    winning = set(roots)
    for where, paths in _nested_testpaths(root):
        missing = sorted(set(paths) - winning)
        if missing:
            violations.append(Violation(
                where, "testpaths-drift",
                f"declares testpaths {missing} that the configuration CI uses ({cfg.source}, roots {sorted(winning)}) "
                f"does not name, so CI never runs them; add them there.",
            ))
    return violations


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    root = Path(ap.parse_args().root).resolve()
    violations = check(root)
    for v in violations:
        print(v.render())
    if violations:
        print(f"\nFAIL: {len(violations)} problem(s). A configured test root no CI job runs is green by omission.")
        return 1
    print("OK: every configured testpaths root is run by a CI pytest invocation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
