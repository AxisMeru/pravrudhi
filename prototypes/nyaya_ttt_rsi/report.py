"""Renderer for a run's `report.json` (written by the loop agent) into
`report.md` (GitHub-flavored markdown) and `report.html` (a single
self-contained file: inline CSS, inline SVG, no JS, no external assets,
readable in light and dark mode via `prefers-color-scheme`).

Every key in the schema is optional: whatever is present is rendered,
whatever is missing is skipped, and malformed values degrade to a dash
instead of raising. CLI:

    python3 -m prototypes.nyaya_ttt_rsi.report <run_dir>

reads `<run_dir>/report.json` and writes `<run_dir>/report.md` plus
`<run_dir>/report.html`.
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

DASH = "\u2013"
EMDASH = "\u2014"
ARROW = "\u2192"
MIDDOT = "\u00b7"

PAIRED_HEADERS = ["a " + ARROW + " b", "Metric", "b only", "c only", "McNemar p", "Bootstrap CI"]
TIMING_HEADERS = ["Condition", "Wall time (s)", "Peak VRAM (GiB)"]

SERIES_COLORS = ["#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#b07aa1", "#76b7b2", "#edc949", "#ff9da7"]

CSS = """
:root {
  color-scheme: light dark;
  --bg: #ffffff; --fg: #1f2328; --muted: #59636e; --border: #d0d7de; --panel: #f6f8fa;
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #0d1117; --fg: #e6edf3; --muted: #9198a1; --border: #3d444d; --panel: #161b22; }
}
body {
  margin: 0; padding: 2rem 1.5rem; background: var(--bg); color: var(--fg);
  font: 16px/1.5 -apple-system, "Segoe UI", Helvetica, Arial, sans-serif;
}
main { max-width: 60rem; margin: 0 auto; }
h1 { font-size: 1.6rem; margin: 0 0 .5rem; }
h2 { font-size: 1.25rem; margin: 2rem 0 .5rem; border-bottom: 1px solid var(--border); padding-bottom: .25rem; }
h3 { font-size: 1.05rem; margin: 1.25rem 0 .4rem; }
dl.meta { display: flex; flex-wrap: wrap; gap: 0 1.75rem; margin: .25rem 0 0; padding: 0; }
dl.meta dt { font-weight: 600; color: var(--muted); font-size: .78rem; text-transform: uppercase; letter-spacing: .03em; }
dl.meta dd { margin: 0 0 .5rem; }
table { border-collapse: collapse; margin: .75rem 0; width: 100%; font-size: .92rem; }
th, td { border: 1px solid var(--border); padding: .4rem .6rem; text-align: left; vertical-align: top; font-variant-numeric: tabular-nums; }
thead th { background: var(--panel); }
tbody th { font-weight: 600; }
ul { margin: .5rem 0; padding-left: 1.4rem; }
figure.chart { margin: .75rem 0; padding: 0; }
figure.chart svg { max-width: 100%; height: auto; display: block; }
figure.chart figcaption { color: var(--muted); font-size: .85rem; margin-bottom: .4rem; }
.legend { display: flex; flex-wrap: wrap; gap: .4rem 1rem; margin: .4rem 0; font-size: .85rem; }
.legend .item { display: inline-flex; align-items: center; gap: .35rem; }
.legend .swatch { width: .85rem; height: .85rem; border-radius: 2px; display: inline-block; }
svg .axis { stroke: var(--muted); stroke-width: 1; fill: none; }
svg .grid { stroke: var(--border); stroke-width: 1; stroke-dasharray: 2 3; }
svg .tick { fill: var(--muted); font-size: 10px; }
svg .axis-title { fill: var(--muted); font-size: 11px; }
footer { margin-top: 2.5rem; color: var(--muted); font-size: .8rem; }
""".strip()


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else []


def _num(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        x = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if x != x or x == float("inf") or x == float("-inf"):
        return None
    return x


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return " ".join(value.split())
    return " ".join(str(value).split())


def _count_text(value: object) -> str:
    x = _num(value)
    if x is not None and x == int(x):
        return str(int(x))
    return _text(value)


def _fmt3(value: object) -> str | None:
    x = _num(value)
    return f"{x:.3f}" if x is not None else None


def _fmt_sig3(value: object) -> str | None:
    x = _num(value)
    return f"{x:.3g}" if x is not None else None


def _wilson(value: object) -> tuple[float, float] | None:
    seq = _as_list(value)
    if len(seq) < 2:
        return None
    lo, hi = _num(seq[0]), _num(seq[1])
    if lo is None or hi is None:
        return None
    return (lo, hi)


def _fmt_ci(value: object) -> str | None:
    wil = _wilson(value)
    return f"[{wil[0]:.3f}, {wil[1]:.3f}]" if wil is not None else None


def _md_cell(value: object) -> str:
    return _text(value).replace("|", "\\|")


def _e(value: object) -> str:
    return html.escape(_text(value), quote=True)


def _metric_cell(value: object) -> str:
    m = _as_dict(value)
    pieces: list[str] = []
    rate = _fmt3(m.get("rate"))
    if rate is not None:
        pieces.append(rate)
    k, n = m.get("k"), m.get("n")
    if k is not None or n is not None:
        k_text = _count_text(k) if k is not None else "?"
        n_text = _count_text(n) if n is not None else "?"
        pieces.append(f"({k_text}/{n_text})")
    wil = _wilson(m.get("wilson"))
    if wil is not None:
        pieces.append(f"[{wil[0]:.3f}{DASH}{wil[1]:.3f}]")
    return " ".join(pieces) if pieces else DASH


def _rate_band_cell(value: object) -> str:
    m = _as_dict(value)
    pieces: list[str] = []
    rate = _fmt3(m.get("rate"))
    if rate is not None:
        pieces.append(rate)
    wil = _wilson(m.get("wilson"))
    if wil is not None:
        pieces.append(f"[{wil[0]:.3f}{DASH}{wil[1]:.3f}]")
    return " ".join(pieces) if pieces else DASH


def _condition_columns(report: dict) -> list[tuple[str, dict]]:
    conditions = _as_dict(report.get("conditions"))
    cols: list[tuple[str, dict]] = []
    for key in sorted(conditions, key=str):
        cond = _as_dict(conditions.get(key))
        name = _text(cond.get("name"))
        label = f"{key} {EMDASH} {name}" if name else str(key)
        cols.append((label, cond))
    return cols


def _metric_names(metrics_dicts: list[dict]) -> list:
    names: list = []
    for metrics in metrics_dicts:
        for name in metrics:
            if name not in names:
                names.append(name)
    return names


def _timing_rows(cols: list[tuple[str, dict]]) -> list[list[str]]:
    rows: list[list[str]] = []
    for label, cond in cols:
        timing = _as_dict(cond.get("timing"))
        if not timing:
            continue
        rows.append(
            [
                label,
                _text(timing.get("wall_s")) or DASH,
                _text(timing.get("peak_vram_gib")) or DASH,
            ]
        )
    return rows


def _paired_rows(report: dict) -> list[list[str]]:
    rows: list[list[str]] = []
    for raw in _as_list(report.get("paired")):
        p = _as_dict(raw)
        if not p:
            continue
        a = _text(p.get("a")) or "?"
        b = _text(p.get("b")) or "?"
        rows.append(
            [
                f"{a} {ARROW} {b}",
                _text(p.get("metric")) or DASH,
                _count_text(p.get("b_only")) or DASH,
                _count_text(p.get("c_only")) or DASH,
                _fmt_sig3(p.get("mcnemar_p")) or DASH,
                _fmt_ci(p.get("bootstrap_ci")) or DASH,
            ]
        )
    return rows


def _gate_info(report: dict) -> dict | None:
    gate = _as_dict(report.get("gate"))
    if not gate:
        return None
    accepted = _count_text(gate.get("accepted"))
    rejected = _count_text(gate.get("rejected"))
    reasons = [
        (_text(reason), _count_text(count) or DASH)
        for reason, count in _as_dict(gate.get("reasons")).items()
    ]
    if not accepted and not rejected and not reasons:
        return None
    return {"accepted": accepted, "rejected": rejected, "reasons": reasons}


def _rounds_entries(report: dict) -> list[tuple[str, float | None, dict, dict]]:
    entries: list[tuple[str, float | None, dict, dict]] = []
    for i, raw in enumerate(_as_list(report.get("rounds"))):
        r = _as_dict(raw)
        label = _text(r.get("round")) or str(i + 1)
        entries.append((label, _num(r.get("round")), _as_dict(r.get("metrics")), _as_dict(r.get("gate"))))
    return entries


def _rounds_table(entries: list[tuple[str, float | None, dict, dict]]) -> tuple[list[str], list[list[str]]] | None:
    if not entries:
        return None
    names = _metric_names([metrics for _, _, metrics, _ in entries])
    has_gate = any(gate for _, _, _, gate in entries)
    headers = ["Round"] + [_text(name) for name in names]
    if has_gate:
        headers += ["Accepted", "Rejected"]
    rows: list[list[str]] = []
    for label, _, metrics, gate in entries:
        row = [label] + [_rate_band_cell(metrics.get(name)) for name in names]
        if has_gate:
            row += [_count_text(gate.get("accepted")) or DASH, _count_text(gate.get("rejected")) or DASH]
        rows.append(row)
    return headers, rows


def _nice_step(ymax: float) -> float:
    raw = ymax / 4.0
    if raw <= 0:
        return 0.25
    mag = 1.0
    while raw >= 10:
        raw /= 10
        mag *= 10
    while raw < 1:
        raw *= 10
        mag /= 10
    for m in (1.0, 2.0, 2.5, 5.0, 10.0):
        if raw <= m:
            return m * mag
    return 10.0 * mag


def _tick_label(v: float) -> str:
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _rounds_chart(entries: list[tuple[str, float | None, dict, dict]]) -> tuple[str, list[tuple[str, str]]] | None:
    """Inline SVG line chart (no JS): one polyline per metric's rate over
    rounds, Wilson interval as a translucent polygon, labeled axes."""
    if not entries:
        return None
    names = _metric_names([metrics for _, _, metrics, _ in entries])
    if not names:
        return None
    all_numeric = all(x is not None for _, x, _, _ in entries)
    xs = [x if all_numeric else float(i) for i, (_, x, _, _) in enumerate(entries)]
    if all_numeric:
        ordered = sorted(zip(xs, entries), key=lambda t: t[0])
        xs = [x for x, _ in ordered]
        entries = [e for _, e in ordered]

    series: list[tuple[object, list[tuple[float, float, tuple[float, float] | None]]]] = []
    for name in names:
        points: list[tuple[float, float, tuple[float, float] | None]] = []
        for i, (_, _, metrics, _) in enumerate(entries):
            m = _as_dict(metrics.get(name))
            rate = _num(m.get("rate"))
            if rate is None:
                continue
            points.append((xs[i], rate, _wilson(m.get("wilson"))))
        if points:
            series.append((name, points))
    if not series:
        return None

    xvals = [p[0] for _, pts in series for p in pts]
    xmin, xmax = min(xvals), max(xvals)
    if xmin == xmax:
        xmin, xmax = xmin - 0.5, xmax + 0.5
    ymax = max(
        [p[1] for _, pts in series for p in pts]
        + [p[2][1] for _, pts in series for p in pts if p[2] is not None]
        + [0.0]
    )
    if ymax <= 0:
        ymax = 1.0
    step = _nice_step(ymax)
    buckets = int(ymax / step)
    if buckets < ymax / step:
        buckets += 1
    ytop = max(buckets, 1) * step

    width, height = 720, 240
    left, right, top, bottom = 52, 16, 16, 36
    plot_w = width - left - right
    plot_h = height - top - bottom
    base_y = top + plot_h

    def sx(v: float) -> float:
        return left + (v - xmin) / (xmax - xmin) * plot_w

    def sy(v: float) -> float:
        return top + (1.0 - max(v, 0.0) / ytop) * plot_h

    parts = [
        f'<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Metric rate over rounds" xmlns="http://www.w3.org/2000/svg">'
    ]
    n_ticks = int(round(ytop / step))
    for i in range(n_ticks + 1):
        v = i * step
        y = sy(v)
        parts.append(f'<line class="grid" x1="{left}" y1="{y:.2f}" x2="{left + plot_w}" y2="{y:.2f}"/>')
        parts.append(f'<text class="tick" x="{left - 6}" y="{y + 3.5:.2f}" text-anchor="end">{_tick_label(v)}</text>')
    for i, (label, _, _, _) in enumerate(entries):
        x = sx(xs[i])
        parts.append(f'<line class="axis" x1="{x:.2f}" y1="{base_y:.2f}" x2="{x:.2f}" y2="{base_y + 5:.2f}"/>')
        parts.append(
            f'<text class="tick" x="{x:.2f}" y="{base_y + 18:.2f}" text-anchor="middle">{html.escape(label)}</text>'
        )
    parts.append(f'<line class="axis" x1="{left}" y1="{base_y:.2f}" x2="{left + plot_w}" y2="{base_y:.2f}"/>')
    parts.append(f'<line class="axis" x1="{left}" y1="{top}" x2="{left}" y2="{base_y:.2f}"/>')
    parts.append(
        f'<text class="axis-title" x="{left + plot_w / 2:.2f}" y="{height - 6}" text-anchor="middle">Round</text>'
    )
    parts.append(
        f'<text class="axis-title" transform="translate(14 {top + plot_h / 2:.2f}) rotate(-90)" '
        f'text-anchor="middle">Rate</text>'
    )

    legend: list[tuple[str, str]] = []
    for si, (name, points) in enumerate(series):
        color = SERIES_COLORS[si % len(SERIES_COLORS)]
        legend.append((color, _text(name)))
        if any(p[2] is not None for p in points):
            upper = " ".join(
                f"{sx(p[0]):.2f},{sy(p[2][1] if p[2] else p[1]):.2f}" for p in points
            )
            lower = " ".join(
                f"{sx(p[0]):.2f},{sy(p[2][0] if p[2] else p[1]):.2f}" for p in reversed(points)
            )
            parts.append(f'<polygon points="{upper} {lower}" fill="{color}" fill-opacity="0.18"/>')
        if len(points) >= 2:
            coords = " ".join(f"{sx(p[0]):.2f},{sy(p[1]):.2f}" for p in points)
            parts.append(f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="2"/>')
        for p in points:
            parts.append(f'<circle cx="{sx(p[0]):.2f}" cy="{sy(p[1]):.2f}" r="2.5" fill="{color}"/>')
    parts.append("</svg>")
    return "\n".join(parts), legend


def _md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(_md_cell(h) for h in headers) + " |"]
    out.append("|" + " --- |" * len(headers))
    for row in rows:
        out.append("| " + " | ".join(_md_cell(cell) for cell in row) + " |")
    return out


def render_markdown(report: dict) -> str:
    report = _as_dict(report)
    out: list[str] = []

    run = _text(report.get("run"))
    out.append(f"# Run report: {run}" if run else "# Run report")
    out.append("")
    meta: list[str] = []
    checkpoint = _text(report.get("checkpoint"))
    created = _text(report.get("created"))
    n_heldout = _count_text(report.get("n_heldout"))
    if checkpoint:
        meta.append(f"- **Checkpoint:** {checkpoint}")
    if created:
        meta.append(f"- **Created:** {created}")
    if n_heldout:
        meta.append(f"- **Held-out queries:** {n_heldout}")
    if meta:
        out.extend(meta)
        out.append("")

    cols = _condition_columns(report)
    if cols:
        metric_dicts = [_as_dict(cond.get("metrics")) for _, cond in cols]
        names = _metric_names(metric_dicts)
        timing = _timing_rows(cols)
        if names or timing:
            out.append("## Conditions")
            out.append("")
            if names:
                headers = ["Metric"] + [label for label, _ in cols]
                rows = [
                    [_text(name)] + [_metric_cell(md.get(name)) for md in metric_dicts] for name in names
                ]
                out.extend(_md_table(headers, rows))
                out.append("")
            if timing:
                if names:
                    out.append("### Timing")
                    out.append("")
                out.extend(_md_table(TIMING_HEADERS, timing))
                out.append("")

    paired = _paired_rows(report)
    if paired:
        out.append("## Paired comparisons")
        out.append("")
        out.extend(_md_table(PAIRED_HEADERS, paired))
        out.append("")

    gate = _gate_info(report)
    if gate:
        lines: list[str] = []
        if gate["accepted"]:
            lines.append(f"- **Accepted:** {gate['accepted']}")
        if gate["rejected"]:
            lines.append(f"- **Rejected:** {gate['rejected']}")
        if gate["reasons"]:
            lines.append("- **Reasons:**")
            for reason, count in gate["reasons"]:
                lines.append(f"  - {_md_cell(reason)}: {count}")
        if lines:
            out.append("## Gate")
            out.append("")
            out.extend(lines)
            out.append("")

    entries = _rounds_entries(report)
    table = _rounds_table(entries)
    if table:
        headers, rows = table
        out.append("## Rounds")
        out.append("")
        out.extend(_md_table(headers, rows))
        out.append("")

    notes = [_text(note) for note in _as_list(report.get("notes"))]
    notes = [note for note in notes if note]
    if notes:
        out.append("## Notes")
        out.append("")
        for note in notes:
            out.append(f"- {_md_cell(note)}")
        out.append("")

    return "\n".join(out)


def _html_table(headers: list[str], rows: list[list[str]], row_header: bool = False) -> str:
    out = ["<table>", "<thead><tr>"]
    out.extend(f"<th>{_e(h)}</th>" for h in headers)
    out.append("</tr></thead>")
    out.append("<tbody>")
    for row in rows:
        out.append("<tr>")
        for i, cell in enumerate(row):
            if i == 0 and row_header:
                out.append(f'<th scope="row">{_e(cell)}</th>')
            else:
                out.append(f"<td>{_e(cell)}</td>")
        out.append("</tr>")
    out.append("</tbody>")
    out.append("</table>")
    return "\n".join(out)


def render_html(report: dict) -> str:
    report = _as_dict(report)
    run = _text(report.get("run"))
    heading = f"Run report: {run}" if run else "Run report"
    title = f"{run} {EMDASH} run report" if run else "Run report"
    parts: list[str] = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{_e(title)}</title>",
        f"<style>{CSS}</style>",
        "</head>",
        "<body>",
        "<main>",
        f"<h1>{_e(heading)}</h1>",
    ]

    meta: list[str] = []
    for dt, dd in (
        ("Run", run),
        ("Checkpoint", _text(report.get("checkpoint"))),
        ("Created", _text(report.get("created"))),
        ("Held-out queries", _count_text(report.get("n_heldout"))),
    ):
        if dd:
            meta.append(f"<div><dt>{dt}</dt><dd>{_e(dd)}</dd></div>")
    if meta:
        parts.append('<dl class="meta">' + "".join(meta) + "</dl>")

    cols = _condition_columns(report)
    if cols:
        metric_dicts = [_as_dict(cond.get("metrics")) for _, cond in cols]
        names = _metric_names(metric_dicts)
        timing = _timing_rows(cols)
        if names or timing:
            parts.append("<h2>Conditions</h2>")
            if names:
                headers = ["Metric"] + [label for label, _ in cols]
                rows = [
                    [_text(name)] + [_metric_cell(md.get(name)) for md in metric_dicts] for name in names
                ]
                parts.append(_html_table(headers, rows, row_header=True))
            if timing:
                if names:
                    parts.append("<h3>Timing</h3>")
                parts.append(_html_table(TIMING_HEADERS, timing))

    paired = _paired_rows(report)
    if paired:
        parts.append("<h2>Paired comparisons</h2>")
        parts.append(_html_table(PAIRED_HEADERS, paired))

    gate = _gate_info(report)
    if gate:
        bits: list[str] = []
        if gate["accepted"]:
            bits.append(f"<strong>Accepted:</strong> {_e(gate['accepted'])}")
        if gate["rejected"]:
            bits.append(f"<strong>Rejected:</strong> {_e(gate['rejected'])}")
        if bits or gate["reasons"]:
            parts.append("<h2>Gate</h2>")
            if bits:
                parts.append(f"<p>{(' ' + MIDDOT + ' ').join(bits)}</p>")
            if gate["reasons"]:
                parts.append("<h3>Reasons</h3>")
                parts.append("<ul>")
                for reason, count in gate["reasons"]:
                    parts.append(f"<li>{_e(reason)}: {_e(count)}</li>")
                parts.append("</ul>")

    entries = _rounds_entries(report)
    table = _rounds_table(entries)
    chart = _rounds_chart(entries)
    if table or chart:
        parts.append("<h2>Rounds</h2>")
    if table:
        headers, rows = table
        parts.append(_html_table(headers, rows))
    if chart:
        svg, legend = chart
        parts.append('<figure class="chart">')
        parts.append(
            "<figcaption>Rate per metric across rounds; the shaded band is the Wilson interval.</figcaption>"
        )
        legend_items = "".join(
            f'<span class="item"><span class="swatch" style="background:{color}"></span>{_e(name)}</span>'
            for color, name in legend
        )
        parts.append(f'<div class="legend">{legend_items}</div>')
        parts.append(svg)
        parts.append("</figure>")

    notes = [_text(note) for note in _as_list(report.get("notes"))]
    notes = [note for note in notes if note]
    if notes:
        parts.append("<h2>Notes</h2>")
        parts.append("<ul>")
        for note in notes:
            parts.append(f"<li>{_e(note)}</li>")
        parts.append("</ul>")

    parts.append("<footer>Generated by prototypes.nyaya_ttt_rsi.report</footer>")
    parts.extend(["</main>", "</body>", "</html>"])
    return "\n".join(parts) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m prototypes.nyaya_ttt_rsi.report",
        description="Render <run_dir>/report.json into <run_dir>/report.md and <run_dir>/report.html.",
    )
    parser.add_argument("run_dir", help="run directory containing report.json")
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir)
    source = run_dir / "report.json"
    try:
        report = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        parser.error(f"cannot read {source}: {exc}")
    except json.JSONDecodeError as exc:
        parser.error(f"invalid JSON in {source}: {exc}")

    report = _as_dict(report)
    (run_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")
    (run_dir / "report.html").write_text(render_html(report), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
