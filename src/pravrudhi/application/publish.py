"""Put what the engine has done in front of the operator, without anyone remembering to do it.

Every visible failure in this project's first day had the same shape: work was finished, tested and committed, and
the operator still could not see it. A page shipped before the recorded snapshot carried its data, so it rendered
"this recording predates..." on the public site while working perfectly against a live engine. A base path was
wrong, so the identical build worked on one host and failed on the other. A commit sat unpushed, so the deployment
that rebuilds on push never ran.

So publishing is one operation with a fixed order, and each step is a precondition of the next:

    export the snapshot -> build the interface -> verify the pages render -> commit -> push

The order matters. Exporting after building leaves the built bundle carrying yesterday's data. Committing before
verifying publishes a page that answers 200 and shows an error. Pushing is last because it is the only step that
reaches other people.

`publish` refuses rather than half-completes: a failing build, a page that renders an error, or a dirty tree
carrying files it was not asked to commit all stop it with a reason. Nothing here forces anything.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pravrudhi.application.integrate import COMMIT_IDENTITY

RunnerFn = Callable[[list[str], Path], "subprocess.CompletedProcess[str]"]

# Pages that must render real content, not an error, before anything is pushed. Each is checked for the strings
# a broken page shows rather than for HTTP 200, which every one of these returned while broken.
CHECK_PAGES = (
    "/", "/tour", "/appetite", "/objectives", "/progress", "/inbox", "/requests", "/candidates",
    "/swarm", "/diffs", "/memory", "/heartbeat", "/catalogue", "/runs", "/models", "/machines", "/settings",
    "/search",
)
"""Every page the interface serves. The list was a subset once, and the appetite page shipped hanging on
"Loading..." because nothing checked it. A page added without being added here is a page nobody verifies."""

BROKEN_MARKERS = (
    "could not reach",
    "couldn't load",
    "failed to load",
    "predates",
    "no engine reachable",
)


@dataclass
class Step:
    name: str
    ok: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass
class PublishResult:
    published: bool
    reason: str
    steps: list[Step] = field(default_factory=list)
    commit: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "published": self.published, "reason": self.reason, "commit": self.commit,
            "steps": [s.to_dict() for s in self.steps],
        }


def _default_runner(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=1800)


def export_snapshot(root: Path, runner: RunnerFn, *, write_root: Path | None = None) -> Step:
    """Record what the engine has done. Always first: the build bakes this file into the bundle.

    `write_root` is where the exported `demo.json` lands; it defaults to `root` (today's single-root
    behaviour, unchanged). `root` is always where the ledger is read from - the two differ only when the
    publisher runs from its own clone (ADR-0053 §2) while reading the loop root's actual results.
    """
    dest = (write_root if write_root is not None else root) / "app" / "frontend" / "public" / "demo.json"
    result = runner(["uv", "run", "pravrudhi", "demo-export", "--root", str(root), "--dest", str(dest)], root)
    if result.returncode != 0:
        return Step("export", False, (result.stderr or result.stdout).strip()[:400])
    try:
        keys = sorted(json.loads(dest.read_text()))
    except (OSError, json.JSONDecodeError) as e:
        return Step("export", False, f"snapshot unreadable: {e}")
    return Step("export", True, f"{len(keys)} sections: {', '.join(keys[:8])}")


def generate_paper(root: Path, runner: RunnerFn, *, write_root: Path | None = None) -> Step:
    """Regenerate the paper's ledger-sourced tables, and rebuild its PDF if LaTeX is installed.

    Table generation reads the same ledger the snapshot does, so a failure there is a real defect and stops the
    publish before anything is built. Rebuilding the PDF is a nicety on top: most hosts running this engine have
    never installed a LaTeX toolchain, and that absence must not block publishing the demo.

    `write_root` (defaulting to `root`, as in `export_snapshot`) is where `paper/generated/*.tex` is written and
    where `make` runs - `paper_data.write_tables` couples the read and the write to one root, so its two lines
    are inlined here via the read-only `results_tables` instead of calling it, rather than changing that
    module's own single-root contract for callers that have no reason to split the two.
    """
    from pravrudhi.application.paper_data import results_tables

    write_root = write_root if write_root is not None else root
    try:
        tables = results_tables(root)
    except Exception as e:  # noqa: BLE001 (a generation failure must be reported, not swallowed)
        return Step("paper", False, f"table generation failed: {e}")
    out_dir = write_root / "paper" / "generated"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, body in tables.items():
        (out_dir / f"{name}.tex").write_text(body)

    paper_dir = write_root / "paper"
    if not (paper_dir / "Makefile").exists():
        return Step("paper", True, "tables regenerated; no paper/Makefile to build")
    if shutil.which("latexmk") is None:
        return Step("paper", True, "tables regenerated; no LaTeX toolchain installed, PDF not rebuilt")
    result = runner(["make", "-C", str(paper_dir)], write_root)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()[-6:]
        return Step("paper", True, f"tables regenerated; PDF build failed: {' / '.join(tail)[:300]}")
    return Step("paper", True, "tables regenerated and PDF rebuilt")


def build_interface(root: Path, runner: RunnerFn, *, base_path: str = "") -> Step:
    """Build the static export.

    `base_path` is empty for the bundle the engine serves at its own root and set for a host that serves the app
    under a sub-path. Building the local bundle with a sub-path breaks the app the wheel ships.
    """
    fe = root / "app" / "frontend"
    if not (fe / "package.json").exists():
        return Step("build", False, "no interface source at app/frontend")
    env_prefix = ["env", f"NEXT_PUBLIC_BASE_PATH={base_path}"] if base_path else []
    result = runner([*env_prefix, "npm", "run", "build"], fe)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()[-6:]
        return Step("build", False, " / ".join(tail)[:400])
    return Step("build", True, "static export written")


def verify_pages(root: Path, *, fetch: Callable[[str], str] | None = None, origin: str = "") -> Step:
    """Confirm the built pages carry content rather than an error.

    A page that answers 200 while rendering "Loading..." or an error banner has failed. This project shipped
    exactly that twice, because only the status code was ever checked.
    """
    if fetch is None:
        out = root / "app" / "frontend" / "out"
        if not (out / "index.html").exists():
            return Step("verify", False, "no built export to check")

        def read_file(page: str) -> str:
            name = "index.html" if page == "/" else f"{page.strip('/')}.html"
            path = out / name
            return path.read_text() if path.exists() else ""

        fetch = read_file

    missing: list[str] = []
    broken: list[str] = []
    for page in CHECK_PAGES:
        try:
            body = fetch(f"{origin}{page}" if origin else page)
        except Exception as e:  # noqa: BLE001 (an unreachable page is a failed check, not a crash)
            broken.append(f"{page} ({e})")
            continue
        if not body:
            missing.append(page)
            continue
        low = body.lower()
        hit = next((m for m in BROKEN_MARKERS if m in low), None)
        if hit:
            broken.append(f"{page} shows {hit!r}")
    if missing or broken:
        return Step("verify", False, "; ".join([*(f"{p} not built" for p in missing), *broken])[:400])
    return Step("verify", True, f"{len(CHECK_PAGES)} pages carry content")


def commit(root: Path, runner: RunnerFn, message: str, paths: list[str]) -> tuple[Step, str | None]:
    """Commit only the named paths, under the house identity, with no attribution trailer."""
    staged = runner(["git", "add", "--", *paths], root)
    if staged.returncode != 0:
        return Step("commit", False, (staged.stderr or staged.stdout).strip()[:300]), None
    pending = runner(["git", "diff", "--cached", "--name-only"], root)
    if not pending.stdout.strip():
        return Step("commit", True, "nothing to commit"), None
    result = runner(
        ["git", "-c", f"user.name={COMMIT_IDENTITY['GIT_AUTHOR_NAME']}",
         "-c", f"user.email={COMMIT_IDENTITY['GIT_AUTHOR_EMAIL']}", "commit", "-m", message], root
    )
    if result.returncode != 0:
        return Step("commit", False, (result.stderr or result.stdout).strip()[:300]), None
    head = runner(["git", "rev-parse", "--short", "HEAD"], root)
    sha = head.stdout.strip() or None
    return Step("commit", True, sha or "committed"), sha


def push(root: Path, runner: RunnerFn, *, remote: str = "origin", branch: str = "main") -> Step:
    result = runner(["git", "push", remote, branch], root)
    if result.returncode != 0:
        return Step("push", False, (result.stderr or result.stdout).strip()[:300])
    return Step("push", True, f"{remote} {branch}")


def publish(
    root: Path,
    *,
    write_root: Path | None = None,
    message: str = "Refresh the recorded snapshot so the published pages show what the engine has done",
    runner: RunnerFn | None = None,
    fetch: Callable[[str], str] | None = None,
    do_push: bool = True,
) -> PublishResult:
    """Export, build, verify, commit, push — stopping at the first step that fails, with the reason.

    `root` is read from throughout (the ledger, the paper data); `write_root` (ADR-0053 §2, defaulting to
    `root` so every existing call site is unchanged) is where the exported snapshot, the built interface and
    the commit/push all land. The two differ when the publisher runs from its own clone while the loop root
    holds the actual results - the main checkout the lead merges into must have exactly one writer.

    `research/` is gitignored, so a clone that has never run the engine has no ledger at all. That is a
    legitimate empty bundle for a single fresh install (`demo_export.build_demo`'s own deliberate design), but
    it is a misconfigured `root` in the two-root case - the point of the split is reading a real loop root's
    results, and an empty snapshot published to the live pages from the wrong `root` would be worse than this
    refusing outright. So the check applies only when `write_root` is actually given.
    """
    root = Path(root).resolve()
    effective_write_root = Path(write_root).resolve() if write_root is not None else root
    if write_root is not None and not (root / "research" / "ledger.jsonl").exists():
        return PublishResult(
            False, f"read root {root} has no research/ledger.jsonl - refusing to publish an empty snapshot", [],
        )
    runner = runner or _default_runner
    steps: list[Step] = []

    for step in (
        export_snapshot(root, runner, write_root=effective_write_root),
        generate_paper(root, runner, write_root=effective_write_root),
        build_interface(effective_write_root, runner),
        verify_pages(effective_write_root, fetch=fetch),
    ):
        steps.append(step)
        if not step.ok:
            return PublishResult(False, f"{step.name} failed: {step.detail}", steps)

    paths = ["app/frontend/public/demo.json", "paper/generated"]
    step, sha = commit(effective_write_root, runner, message, paths)
    steps.append(step)
    if not step.ok:
        return PublishResult(False, f"commit failed: {step.detail}", steps)

    if not do_push:
        return PublishResult(True, "built and committed; not pushed", steps, sha)

    step = push(effective_write_root, runner)
    steps.append(step)
    if not step.ok:
        return PublishResult(False, f"push failed: {step.detail}", steps, sha)
    return PublishResult(True, f"published {sha}" if sha else "published, nothing new to commit", steps, sha)


__all__ = [
    "BROKEN_MARKERS", "CHECK_PAGES", "PublishResult", "Step",
    "build_interface", "commit", "export_snapshot", "generate_paper", "publish", "push", "verify_pages",
]
