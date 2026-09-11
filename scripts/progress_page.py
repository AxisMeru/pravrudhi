"""Render the public progress page from sources that are already public.

The page is built from the last commits (subject and date only), the recorded demo snapshot that also backs
the hosted frontend's playback mode, and the engine's declared version. Every benchmark row reports either a
value taken from `demo.json` or the word "unmeasured" -- nothing here is computed or invented.
"""

from __future__ import annotations

import argparse
import html
import json
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

STATE_LABELS = {
    "measured": "measured",
    "baseline_only": "baseline recorded, not yet compared",
    "unmeasured": "unmeasured",
}

STYLE = """
:root { color-scheme: light dark; --bg:#ffffff; --fg:#111111; --muted:#5a5a5a; --border:#dddddd; --accent:#2b6cb0; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#111111; --fg:#eeeeee; --muted:#aaaaaa; --border:#333333; --accent:#63b3ed; }
}
* { box-sizing: border-box; }
body { margin:0; padding:2rem 1rem; background:var(--bg); color:var(--fg); font-family:system-ui,sans-serif; line-height:1.5; }
header, main { max-width:860px; margin:0 auto; }
h1 { margin-bottom:0.25rem; }
.version { color:var(--muted); margin-top:0; }
.links { list-style:none; padding:0; display:flex; gap:1rem; }
.links a { color:var(--accent); text-decoration:none; }
.links a:hover { text-decoration:underline; }
section { margin-top:2rem; }
.intro { font-size:1.05rem; line-height:1.6; margin-bottom:1.5rem; }
.products { display:grid; grid-template-columns:1fr 1fr; gap:1.5rem; margin-bottom:2rem; }
@media (max-width:640px) { .products { grid-template-columns:1fr; } }
.product-card { border:1px solid var(--border); border-radius:8px; padding:1.5rem; }
.product-card h3 { margin-top:0; margin-bottom:0.5rem; }
.product-card p { margin:0.75rem 0; color:var(--muted); font-size:0.95rem; }
.product-links { display:flex; flex-direction:column; gap:0.5rem; margin-top:1rem; }
.product-links a { color:var(--accent); text-decoration:none; }
.product-links a:hover { text-decoration:underline; }
.demo-section { margin:1.5rem 0; }
.demo-section a { color:var(--accent); text-decoration:none; }
.demo-section a:hover { text-decoration:underline; }
.objective { border:1px solid var(--border); border-radius:8px; padding:1rem; margin-bottom:1rem; }
.intent { margin-top:0; }
.benchmarks { list-style:none; padding:0; margin:0; }
.benchmark { display:flex; flex-wrap:wrap; gap:0.5rem; align-items:baseline; padding:0.35rem 0; }
.benchmark { border-top:1px solid var(--border); }
.benchmark:first-child { border-top:none; }
.benchmark-name { font-weight:600; min-width:12rem; }
.benchmark-values { color:var(--muted); }
.state { margin-left:auto; font-size:0.85rem; color:var(--muted); }
.commits { padding-left:1.25rem; }
.commits li { margin-bottom:0.25rem; }
.commits time { color:var(--muted); margin-right:0.5rem; }
"""


@dataclass(frozen=True)
class Commit:
    date: str
    subject: str


def recent_commits(repo_root: Path, count: int = 30) -> list[Commit]:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "log", f"-n{count}", "--date=short", "--pretty=format:%ad%x1f%s"],
        capture_output=True,
        text=True,
        check=True,
    )
    commits = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        date, _, subject = line.partition("\x1f")
        commits.append(Commit(date=date, subject=subject))
    return commits


def engine_version(pyproject_path: Path) -> str:
    data = tomllib.loads(pyproject_path.read_text())
    return str(data["project"]["version"])


def load_objectives(demo_path: Path) -> list[dict[str, Any]]:
    data = json.loads(demo_path.read_text())
    objectives = data.get("objectives", {})
    return list(objectives.get("objectives", []))


def _fmt_value(measurement: dict[str, Any] | None) -> str:
    if not measurement:
        return "unmeasured"
    value = measurement.get("value")
    if isinstance(value, int | float):
        return f"{value:.3f}"
    return "unmeasured"


def _render_benchmark(progress: dict[str, Any]) -> str:
    state = str(progress.get("state", "unmeasured"))
    label = STATE_LABELS.get(state, state)
    baseline = _fmt_value(progress.get("baseline"))
    latest = _fmt_value(progress.get("latest"))
    benchmark = html.escape(str(progress.get("benchmark", "")))
    return (
        '<li class="benchmark">'
        f'<span class="benchmark-name">{benchmark}</span>'
        f'<span class="benchmark-values">{html.escape(baseline)} → {html.escape(latest)}</span>'
        f'<span class="state state-{html.escape(state)}">{html.escape(label)}</span>'
        "</li>"
    )


def _render_objective(objective: dict[str, Any]) -> str:
    intent = html.escape(str(objective.get("intent", "")))
    rows = "".join(_render_benchmark(p) for p in objective.get("progress", []))
    return f'<article class="objective"><p class="intent">{intent}</p><ul class="benchmarks">{rows}</ul></article>'


def _render_commits(commits: list[Commit]) -> str:
    items = "".join(
        f'<li><time datetime="{html.escape(c.date)}">{html.escape(c.date)}</time> {html.escape(c.subject)}</li>'
        for c in commits
    )
    return f'<ol class="commits">{items}</ol>'


def render_page(
    *,
    objectives: list[dict[str, Any]],
    commits: list[Commit],
    version: str,
    app_present: bool,
    paper_present: bool,
) -> str:
    # Introduction
    intro_text = (
        "Pravrudhi is an installable recursive self-improvement engine. "
        "Set an objective, run an improvement loop against a benchmark that measures it, "
        "and see whether the change justifies keeping it. "
        "The engine computes all evidence from the benchmark results you provide."
    )
    intro_html = f'<p class="intro">{html.escape(intro_text)}</p>'

    # Product cards
    products_html = """<div class="products">
<div class="product-card">
<h3>Pravrudhi Studio</h3>
<p>The operator's instance where the engine improves itself.</p>
<div class="product-links">
<a href="https://pravrudhi.vercel.app">Open the web app</a>
<a href="https://github.com/AxisMeru/pravrudhi/releases">Desktop installers</a>
</div>
</div>
<div class="product-card">
<h3>Pravrudhi</h3>
<p>A user's own recursive self-improvement engine for their model, agent or app.</p>
<div class="product-links">
<a href="https://pravrudhi-app.vercel.app">Open the web app</a>
<a href="https://pravrudhi-app.vercel.app/signin">Create an account</a>
<a href="https://github.com/AxisMeru/pravrudhi-app/releases">Desktop installers</a>
</div>
</div>
</div>"""

    # Demo and paper section
    demo_html = ""
    if app_present or paper_present:
        demo_parts = []
        if app_present:
            demo_parts.append('<a href="app/">Recorded demo</a>')
        if paper_present:
            demo_parts.append('<a href="paper/main.pdf">Paper (PDF)</a>')
        demo_html = f"""<section aria-label="demo">
<h2>See it run</h2>
<p class="demo-section">{' • '.join(demo_parts)}</p>
</section>"""

    # Progress content (formerly "Objectives")
    objectives_html = "".join(_render_objective(o) for o in objectives) or "<p>No objectives recorded yet.</p>"
    commits_html = _render_commits(commits)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pravrudhi</title>
<style>{STYLE}</style>
</head>
<body>
<header>
<h1>Pravrudhi</h1>
<p class="version">Engine version {html.escape(version)}</p>
</header>
<main>
{intro_html}
{products_html}
{demo_html}
<section aria-label="progress">
<h2>What the engine has done</h2>
{objectives_html}
</section>
<section aria-label="recent commits">
<h2>Recent commits</h2>
{commits_html}
</section>
</main>
</body>
</html>
"""


def build(repo_root: Path, site_dir: Path) -> None:
    commits = recent_commits(repo_root)
    version = engine_version(repo_root / "pyproject.toml")
    objectives = load_objectives(repo_root / "app" / "frontend" / "public" / "demo.json")
    app_present = (site_dir / "app" / "index.html").exists()
    paper_present = (site_dir / "paper" / "main.pdf").exists()
    page = render_page(
        objectives=objectives, commits=commits, version=version, app_present=app_present, paper_present=paper_present
    )
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "index.html").write_text(page)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the public progress page into a site directory.")
    parser.add_argument("site_dir", type=Path)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    build(REPO_ROOT, args.site_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
