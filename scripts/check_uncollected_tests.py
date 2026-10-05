"""CI guard: no test file sits in the tree where pytest will never collect it.

WHY THIS EXISTS (2026-09-26). A pull request added a 221-line `test_*.py` that CI never collected, because
the file sat outside the configured test root. The job went green, and the green tick meant only that the
suite had not errored: the collected count was byte-identical to the base branch, so the new file
contributed exactly zero tests. Nothing in CI could tell that apart from real coverage, and nothing would
have -- a reviewer reads the diff, not the collection list. This guard makes that landing impossible.

WHAT IT CHECKS. Every tracked file whose name matches pytest's `python_files` patterns must sit inside a
root pytest actually collects, or carry an explicit, reasoned waiver in
`scripts/uncollected_tests_allowlist.txt`. The collected roots are READ FROM THIS REPO'S PYTEST
CONFIGURATION on every run and are never written down here -- see rule 1 below.

FIVE DESIGN RULES, each of which is here because of a specific way this class of guard has already failed
on this project:

1. THE ROOTS COME FROM CONFIGURATION, NEVER FROM A DEFAULT IN THIS FILE. This repo's `testpaths` names
   MORE THAN ONE root (`tests` and `pravrudhi_kernel/tests`), split across two CI jobs -- `engine` runs
   `pytest tests`, `kernel` runs `pytest pravrudhi_kernel/tests`, and a bare `pytest` would run both. A
   hardcoded root here would have been wrong for one of those jobs, and guessing `tests` alone is exactly
   the mistake that produced a false CI-coverage alarm on this repo. If no configuration file can be found,
   if one cannot be parsed, or if the one that wins pytest's own precedence names no `testpaths`, this
   guard FAILS -- it never falls back to a default, because a wrong root set silently changes what "a test
   file is fine where it is" means.

2. IT FAILS CLOSED, EVERYWHERE, WITH NO SILENT SKIPS. `git` failing, an empty file list, a configured
   testpath that does not exist in the tree, a file that cannot be read, a `conftest.py` that removes paths
   from collection, or a run that found no test file inside any collected root at all -- every one of those
   is a VIOLATION whose message says that the scan proved nothing. Four guards on this project shipped or
   nearly shipped with the opposite behaviour (a silent skip reported as "clean"), including one whose own
   negative fixture passed without the file ever being read, so each of these has a negative fixture of its
   own in `tests/test_check_uncollected_tests.py`.

3. A WAIVER CARRIES A WRITTEN REASON, OR IT WAIVES NOTHING. An allowlist line with a key and no reason is
   reported as an error and the build stays red, the same rule `scripts/secret_scan_baseline.txt` states
   for itself. Waived files are PRINTED as waived, with their reason, on every run: a waiver that
   disappears from the output is a waiver nobody re-reads.

4. A WAIVER IS KEYED ON THE PATH PLUS A CONTENT DIGEST, so it cannot follow a file into becoming something
   else. `scripts/secret_scan_baseline.txt` already keys on `sha256(path NUL value)`; this file keys on
   `<path>::<sha256(contents) truncated to 16 hex>` and keeps the path readable, the way
   `scripts/fail_open_defaults_baseline.txt` keys on `<path>::<expression>` -- a test file's contents are
   not a secret, so there is no reason to hide the path behind a digest. Consequence, stated rather than
   hidden: EDITING a waived file breaks its waiver and turns the build red. That is deliberate. A
   grandfathered uncollected test is grandfathered as it stood; someone touching it is someone who can move
   it into a collected root instead.

5. A STALE WAIVER IS PRINTED, NOT ENFORCED. An entry whose path has gone, or whose path is now inside a
   collected root, is reported as "remove this line" and does not fail the build -- so this file shrinks on
   its own as the findings go away, instead of quietly outliving them. This is the behaviour both existing
   baselines in `scripts/` document for themselves.

WHAT IT DOES NOT DO, so nobody reads more into a green tick than is there:
  * It reads TRACKED files (`git ls-files`), which is what a CI checkout contains. An untracked test file on
    someone's laptop is not in CI's tree and is not this guard's business.
  * It proves a test file is in a place pytest WOULD collect. It does not prove the tests inside it run, or
    pass, or assert anything -- a file full of `@pytest.mark.skip` is in a collected root and green here.
    `scripts/check_score_bin_gate_ran.py` is the guard for "these tests actually executed".
  * It cannot evaluate a `conftest.py`'s `collect_ignore` / `collect_ignore_glob`, which can remove a file
    from collection from inside a collected root. It therefore REFUSES (rule 2) rather than pretending: if
    one appears, this guard fails and says it can no longer certify the roots.

Usage:
    check_uncollected_tests.py [--root DIR] [--allowlist FILE]

Exit 1 and print `path: [rule] message` for every violation, exit 0 clean -- the same shape as
`import_guard.py`, `check_no_private_data.py` and `check_no_secrets_in_diff.py`.
"""

from __future__ import annotations

import argparse
import ast
import configparser
import hashlib
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

DEFAULT_ALLOWLIST = Path("scripts") / "uncollected_tests_allowlist.txt"

#: pytest's own documented default for `python_files`, used ONLY when the configuration does not set it.
#: This is a file-NAMING convention, not a collected root: rule 1 forbids defaulting the roots, and this is
#: not one. A run that falls back to this prints a notice saying so.
PYTEST_DEFAULT_PYTHON_FILES = ("test_*.py", "*_test.py")

#: pytest's own documented default for `norecursedirs`. A test file inside a collected root but under a
#: directory matching one of these is NOT collected, which is an escape by LOCATION rather than by name --
#: `tests/node_modules/test_x.py` and `tests/.scratch/test_x.py` both look fine to a naive prefix check.
PYTEST_DEFAULT_NORECURSEDIRS = ("*.egg", ".*", "_darcs", "build", "CVS", "dist", "node_modules", "venv", "{arch}")

#: Configuration sources in pytest's own precedence order: the first one that declares a pytest section
#: wins, and the rest are ignored by pytest itself.
CONFIG_SOURCES = (
    ("pytest.ini", "ini", "pytest"),
    ("pyproject.toml", "toml", "tool.pytest.ini_options"),
    ("tox.ini", "ini", "pytest"),
    ("setup.cfg", "ini", "tool:pytest"),
)

#: A conftest can remove paths from collection with either of these. This guard cannot evaluate them, so
#: their presence is a hard finding (rule 2) rather than something it scans past.
COLLECT_IGNORE_NAMES = ("collect_ignore_glob", "collect_ignore")


class ConfigError(Exception):
    """The pytest configuration could not be read, parsed, or did not name any testpaths."""


@dataclass
class PytestConfig:
    source: str
    section: str
    testpaths: tuple[str, ...]
    python_files: tuple[str, ...]
    norecursedirs: tuple[str, ...]
    #: True when `python_files` / `norecursedirs` fell back to pytest's documented defaults.
    python_files_defaulted: bool
    norecursedirs_defaulted: bool
    #: Lower-precedence files that ALSO declare testpaths. Ambiguity a reader cannot resolve; see read_config.
    conflicting_sources: tuple[str, ...]


def _as_list(value: object, name: str, source: str) -> tuple[str, ...]:
    """A pytest ini list value, from TOML (a real list) or from an ini file (whitespace/newline separated)."""
    if isinstance(value, str):
        items = value.split()
    elif isinstance(value, (list, tuple)):
        items = [str(v) for v in value]
    else:
        raise ConfigError(f"{source}: {name} is a {type(value).__name__}, which is not a list of paths")
    return tuple(i.strip() for i in items if i.strip())


def read_config(root: Path) -> PytestConfig:
    """The effective pytest configuration, or ConfigError. Never returns a guessed root set (rule 1)."""
    found: list[tuple[str, str, dict[str, object]]] = []
    for filename, kind, section in CONFIG_SOURCES:
        path = root / filename
        if not path.is_file():
            continue
        try:
            if kind == "toml":
                with path.open("rb") as fh:
                    data = tomllib.load(fh)
                table: object = data
                for part in section.split("."):
                    if not isinstance(table, dict) or part not in table:
                        table = None
                        break
                    table = table[part]
                if table is None:
                    continue
                if not isinstance(table, dict):
                    raise ConfigError(f"{filename}: [{section}] is not a table")
                found.append((filename, section, dict(table)))
            else:
                # No interpolation: an addopts value containing `%` is legal in an ini file and must not
                # blow up a guard that only wants testpaths out of it.
                parser = configparser.ConfigParser(interpolation=None)
                parser.read_string(path.read_text(encoding="utf-8"))
                if not parser.has_section(section):
                    continue
                found.append((filename, section, dict(parser.items(section))))
        except ConfigError:
            raise
        except Exception as exc:  # unparseable configuration is a hard failure, never a default (rule 1)
            raise ConfigError(f"{filename}: could not be parsed ({type(exc).__name__}: {exc})") from exc

    if not found:
        raise ConfigError(
            "no pytest configuration found (looked for "
            + ", ".join(f"{f} [{s}]" for f, _, s in CONFIG_SOURCES)
            + "). This guard will not assume a default test root: without the configuration it cannot tell "
            "a collected path from an uncollected one, and a wrong root set makes its green tick meaningless."
        )

    winner_file, winner_section, winner = found[0]
    if "testpaths" not in winner:
        raise ConfigError(
            f"{winner_file}: [{winner_section}] names no `testpaths`. With no testpaths a bare `pytest` "
            f"collects from the rootdir, so every test file in the tree is 'collected' and this guard would "
            f"pass vacuously for ever. Declare testpaths, or delete this guard deliberately."
        )
    testpaths = _as_list(winner["testpaths"], "testpaths", winner_file)
    if not testpaths:
        raise ConfigError(f"{winner_file}: [{winner_section}] `testpaths` is present but empty")
    absolute = [tp for tp in testpaths if PurePosixPath(tp).is_absolute()]
    if absolute:
        # Two of these repos have already shipped a `/home/ss` hardcode into CI. An absolute testpath is
        # also the one shape `root / testpath` would silently resolve OUTSIDE the checkout, so it is
        # refused here rather than half-checked below.
        raise ConfigError(
            f"{winner_file}: [{winner_section}] `testpaths` contains absolute path(s) "
            f"{', '.join(absolute)}. A testpath is resolved against the rootdir and must be relative; an "
            f"absolute one points outside the checkout, collects nothing on a CI runner, and makes every "
            f"test file in the tree read as uncollected."
        )

    conflicting = tuple(f for f, _, tbl in found[1:] if "testpaths" in tbl)

    python_files_raw = winner.get("python_files")
    norecursedirs_raw = winner.get("norecursedirs")
    return PytestConfig(
        source=winner_file,
        section=winner_section,
        testpaths=testpaths,
        python_files=(
            _as_list(python_files_raw, "python_files", winner_file)
            if python_files_raw is not None
            else PYTEST_DEFAULT_PYTHON_FILES
        ),
        norecursedirs=(
            _as_list(norecursedirs_raw, "norecursedirs", winner_file)
            if norecursedirs_raw is not None
            else PYTEST_DEFAULT_NORECURSEDIRS
        ),
        python_files_defaulted=python_files_raw is None,
        norecursedirs_defaulted=norecursedirs_raw is None,
        conflicting_sources=conflicting,
    )


@dataclass
class Finding:
    rel: str
    rule: str
    message: str

    def render(self) -> str:
        return f"{self.rel}: [{self.rule}] {self.message}"


@dataclass
class Report:
    violations: list[Finding] = field(default_factory=list)
    waived: list[Finding] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    stale_allowlist: list[str] = field(default_factory=list)
    #: Every test-named path this run actually examined. The non-vacuity handle: an assertion that a
    #: negative fixture produced no findings is meaningless unless it also asserts the file was examined.
    examined: list[str] = field(default_factory=list)
    #: Test files found INSIDE a collected root. Zero of these means the run proved nothing (rule 2).
    inside_roots: list[str] = field(default_factory=list)
    tracked_count: int = 0

    @property
    def ok(self) -> bool:
        return not self.violations


def content_key(rel: str, data: bytes) -> str:
    """The allowlist key for a file: `<path>::<sha256(contents) truncated to 16 hex>` (rule 4)."""
    return f"{rel}::{hashlib.sha256(data).hexdigest()[:16]}"


def load_allowlist(path: Path) -> tuple[dict[str, str], list[Finding]]:
    """(key -> reason, problems). A line is `<path>::<16-hex>  <reason>`; a key with no reason waives
    nothing and is itself reported (rule 3)."""
    keys: dict[str, str] = {}
    problems: list[Finding] = []
    if not path.exists():
        return keys, problems
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        where = f"{path.name}:{lineno}"
        parts = line.split(None, 1)
        key = parts[0]
        reason = parts[1].strip() if len(parts) > 1 else ""
        if "::" not in key:
            problems.append(Finding(where, "allowlist-malformed",
                                    f"not a `<path>::<16-hex digest>` key: {key!r}"))
            continue
        _, _, digest = key.rpartition("::")
        if len(digest) != 16 or any(c not in "0123456789abcdef" for c in digest):
            problems.append(Finding(where, "allowlist-malformed",
                                    f"key {key!r} does not end in a 16-hex content digest"))
            continue
        if not reason:
            problems.append(Finding(
                where, "allowlist-no-reason",
                f"key {key} has no reason, so it waives NOTHING and this build stays red. Write why this "
                f"file is allowed to sit outside the collected roots, and what would let the line be deleted.",
            ))
            continue
        if key in keys:
            problems.append(Finding(where, "allowlist-duplicate", f"key {key} is listed more than once"))
            continue
        keys[key] = reason
    return keys, problems


def _git(root: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)
    return out.stdout


def tracked_files(root: Path) -> list[tuple[str, str]]:
    """[(mode, repo-relative posix path)] for every tracked file. Raises on any git failure: a guard that
    treats "git did not answer" as "nothing to report" reports clean for a tree it never read (rule 2)."""
    raw = _git(root, "ls-files", "-s", "-z")
    entries: list[tuple[str, str]] = []
    for record in raw.split("\0"):
        if not record:
            continue
        # `<mode> <object> <stage>\t<path>`
        meta, tab, rel = record.partition("\t")
        if not tab:
            raise RuntimeError(f"git ls-files produced a record with no path separator: {record!r}")
        entries.append((meta.split()[0], rel))
    return entries


def _is_under(rel: str, root_path: str) -> bool:
    """Component-wise containment. NOT a string prefix: `tests_extra/test_x.py`.startswith('tests') is
    True, and treating that as collected is an escape by naming."""
    parts = PurePosixPath(rel).parts
    root_parts = PurePosixPath(root_path).parts
    return len(parts) > len(root_parts) and parts[: len(root_parts)] == root_parts


def _pruned_by(rel: str, root_path: str, norecursedirs: tuple[str, ...]) -> str | None:
    """The first directory component BELOW `root_path` that pytest's norecursedirs would prune, if any."""
    parts = PurePosixPath(rel).parts
    below = parts[len(PurePosixPath(root_path).parts) : -1]
    for component in below:
        for pattern in norecursedirs:
            if fnmatch(component, pattern):
                return f"{component} (matches norecursedirs {pattern!r})"
    return None


def check(root: Path, allowlist_path: Path | None = None) -> Report:
    report = Report()
    allowlist_path = allowlist_path or (root / DEFAULT_ALLOWLIST)

    allowlist, allowlist_problems = load_allowlist(allowlist_path)
    report.violations.extend(allowlist_problems)

    try:
        config = read_config(root)
    except ConfigError as exc:
        report.violations.append(Finding("pytest configuration", "config-unreadable", str(exc)))
        return report

    report.notices.append(
        f"collected roots read from {config.source} [{config.section}] testpaths: "
        + ", ".join(config.testpaths)
    )
    if config.python_files_defaulted:
        report.notices.append(
            "python_files is not configured; using pytest's own default "
            + ", ".join(PYTEST_DEFAULT_PYTHON_FILES)
        )
    if config.norecursedirs_defaulted:
        report.notices.append("norecursedirs is not configured; using pytest's own default")
    for other in config.conflicting_sources:
        report.violations.append(Finding(
            other, "config-ambiguous",
            f"{other} also declares `testpaths`, and so does {config.source}. pytest silently uses only "
            f"{config.source}; a reader cannot tell which set is live, and this guard will not pick for "
            f"them. Delete the testpaths from whichever file is not the real one.",
        ))

    try:
        tracked = tracked_files(root)
    except Exception as exc:
        report.violations.append(Finding(
            str(root), "scan-failed",
            f"could not list tracked files ({type(exc).__name__}: {exc}), so this run examined NOTHING. "
            f"That is reported as a failure, never as a pass: a guard that cannot read the tree has not "
            f"cleared it.",
        ))
        return report

    report.tracked_count = len(tracked)
    if not tracked:
        report.violations.append(Finding(
            str(root), "scan-empty",
            "git reported zero tracked files, so this run scanned nothing and proved nothing. A repository "
            "with no files is not a repository with no misplaced tests.",
        ))
        return report

    tracked_paths = {rel for _, rel in tracked}
    for testpath in config.testpaths:
        if not (root / testpath).is_dir() and testpath not in tracked_paths:
            report.violations.append(Finding(
                testpath, "testpath-missing",
                f"{config.source} names `{testpath}` as a testpath but it is not in the tree. pytest "
                f"collects nothing from a path that does not exist, so every test file this guard would "
                f"have measured against it is uncollected and unmeasured.",
            ))

    for _, rel in tracked:
        if PurePosixPath(rel).name != "conftest.py":
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            report.violations.append(Finding(
                rel, "unreadable",
                f"a conftest that cannot be read ({exc}) cannot be cleared of a collect_ignore, so the "
                f"collected roots cannot be certified. Reported rather than skipped.",
            ))
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError as exc:
            report.violations.append(Finding(
                rel, "conftest-unparseable",
                f"this conftest does not parse ({exc}), so it cannot be cleared of a collect_ignore -- and "
                f"pytest cannot load it either, which means the directory it governs collects nothing.",
            ))
            continue
        # A NAME NODE, not a substring: a conftest whose comment or docstring merely mentions
        # `collect_ignore` is not setting one, and a guard that goes red for saying the word is a guard
        # people route around. `ast.walk` over Name nodes catches an assignment, an annotated assignment
        # and a `collect_ignore.append(...)` alike, in any scope.
        used = sorted({
            node.id for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id in COLLECT_IGNORE_NAMES
        })
        if used:
            report.violations.append(Finding(
                rel, "collect-ignore-present",
                f"this conftest uses {', '.join('`' + u + '`' for u in used)}, which removes paths from "
                f"collection from INSIDE a collected root. This guard cannot evaluate it and will not "
                f"pretend otherwise: while it is there, being inside a testpath no longer proves that a "
                f"file is collected.",
            ))

    def matches_python_files(filename: str) -> bool:
        return any(fnmatch(filename, pattern) for pattern in config.python_files)

    used_keys: set[str] = set()
    for mode, rel in tracked:
        if not matches_python_files(PurePosixPath(rel).name):
            continue
        report.examined.append(rel)

        inside = next((tp for tp in config.testpaths if rel == tp or _is_under(rel, tp)), None)
        if inside is not None:
            pruned = _pruned_by(rel, inside, config.norecursedirs)
            if pruned is None and mode == "120000":
                report.violations.append(Finding(
                    rel, "symlinked-test",
                    "this test file is a SYMLINK. Whether pytest follows it into collection depends on the "
                    "runner and on --collect-symlinks, so it is not reliably collected even though it sits "
                    "inside a collected root. Commit the file itself.",
                ))
                continue
            if pruned is None:
                report.inside_roots.append(rel)
                continue
            reason_uncollected = (
                f"inside the collected root `{inside}`, but under {pruned}, so pytest prunes the directory "
                f"and never collects it"
            )
        else:
            case_only = next(
                (tp for tp in config.testpaths if _is_under(rel.lower(), tp.lower())),
                None,
            )
            if case_only is not None:
                reason_uncollected = (
                    f"differs from the collected root `{case_only}` ONLY IN CASE. git is case-sensitive and "
                    f"so is a Linux runner, so pytest does not collect it there even though it looks right "
                    f"on a case-insensitive filesystem"
                )
            else:
                reason_uncollected = (
                    "sits outside every collected root ("
                    + ", ".join(config.testpaths)
                    + f"), read from {config.source}, so pytest never collects it and the tests in it "
                    f"contribute nothing to any CI run"
                )

        try:
            data = (root / rel).read_bytes()
        except OSError as exc:
            report.violations.append(Finding(
                rel, "unreadable",
                f"{reason_uncollected}; and its contents could not be read ({exc}), so it cannot even be "
                f"matched against the allowlist. Reported rather than skipped.",
            ))
            continue

        key = content_key(rel, data)
        if key in allowlist:
            used_keys.add(key)
            report.waived.append(Finding(rel, "waived", f"{reason_uncollected} -- WAIVED: {allowlist[key]}"))
            continue

        same_path = [k for k in allowlist if k.rpartition("::")[0] == rel]
        if same_path:
            used_keys.update(same_path)
            report.violations.append(Finding(
                rel, "waiver-digest-moved",
                f"{reason_uncollected}. It IS listed in {allowlist_path.name}, but under a different "
                f"content digest ({', '.join(sorted(k.rpartition('::')[2] for k in same_path))}; the file "
                f"is now {key.rpartition('::')[2]}), so the waiver no longer covers it. A waiver is keyed "
                f"on path plus contents on purpose, so it cannot follow a file into becoming something "
                f"else: if you are editing this file anyway, move it into a collected root instead.",
            ))
            continue

        report.violations.append(Finding(
            rel, "uncollected-test",
            f"{reason_uncollected}. Move it into a collected root, or -- if it genuinely belongs where it "
            f"is -- add `{key}  <reason>` to {allowlist_path.name}.",
        ))

    if not report.inside_roots:
        report.violations.append(Finding(
            str(root), "scan-vacuous",
            f"this run found NO test file inside any collected root ({', '.join(config.testpaths)}) among "
            f"{report.tracked_count} tracked file(s). Either the patterns "
            f"({', '.join(config.python_files)}) match nothing in this tree or the roots are wrong; either "
            f"way the run proved nothing and is reported as a failure rather than a pass.",
        ))

    for key in sorted(set(allowlist) - used_keys):
        waived_path = key.rpartition("::")[0]
        if waived_path not in tracked_paths:
            report.stale_allowlist.append(
                f"{key} -- no such tracked file any more; remove this line"
            )
        else:
            report.stale_allowlist.append(
                f"{key} -- {waived_path} is now collected (or no longer matches the test-file patterns); "
                f"remove this line"
            )

    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    ap.add_argument("--allowlist", default=None, help=f"default: <root>/{DEFAULT_ALLOWLIST}")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    report = check(root, Path(args.allowlist).resolve() if args.allowlist else None)

    for n in report.notices:
        print(f"note: {n}")
    for w in report.waived:
        print(w.render())
    for s in report.stale_allowlist:
        print(f"allowlist: {s}")
    for v in report.violations:
        print(v.render())

    print(
        f"examined {len(report.examined)} test file(s) over {report.tracked_count} tracked file(s); "
        f"{len(report.inside_roots)} inside a collected root, {len(report.waived)} waived, "
        f"{len(report.violations)} violation(s)"
    )
    if report.violations:
        print(
            f"\nFAIL: {len(report.violations)} problem(s). A test file pytest never collects is not a test: "
            f"its green tick says only that the suite did not error, and the collected count does not move "
            f"when it lands. Move it into a collected root, or waive it with a written reason in "
            f"{DEFAULT_ALLOWLIST}."
        )
        return 1
    print("OK: every tracked test file sits inside a root this repo's pytest configuration collects.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
