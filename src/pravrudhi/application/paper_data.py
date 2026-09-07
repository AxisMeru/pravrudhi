"""Render the paper's results tables from the ledger alone.

The paper is a static LaTeX source; its numbers used to be typed by hand, so they drifted from the ledger the
moment anything ran. This module closes that gap for the numbers that live in the ledger: every external-scorer
result, the paired comparisons between a candidate and its baseline, and the nights that produced them. The same
Sākṣī rule as `application.external` applies: no number appears here that the ledger does not contain, and a
measurement the ledger does not yet have renders as an explicit dash with a footnote, never as a blank or a guess.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pravrudhi.application.discordance import discordance
from pravrudhi.application.external import external_rows, headlines
from pravrudhi_kernel.ledger.verify import iter_events

DO_NOT_EDIT = (
    "% GENERATED, DO NOT EDIT --- produced by `pravrudhi paper-data` from research/ledger.jsonl.\n"
    "% Edits here are overwritten the next time the paper's tables are regenerated.\n"
)

_LETTERS = "abcdefghijklmnopqrstuvwxyz"


def _esc(value: Any) -> str:
    s = str(value)
    for bad, good in (("\\", r"\textbackslash{}"), ("&", r"\&"), ("%", r"\%"), ("_", r"\_"), ("#", r"\#")):
        s = s.replace(bad, good)
    return s


class _Notes:
    """Collects the reasons an unmeasured cell is a dash, one footnote marker per distinct reason."""

    def __init__(self) -> None:
        self._reasons: list[str] = []

    def dash(self, reason: str) -> str:
        if reason not in self._reasons:
            self._reasons.append(reason)
        letter = _LETTERS[self._reasons.index(reason)]
        return f"---\\textsuperscript{{{letter}}}"

    def block(self) -> str:
        if not self._reasons:
            return ""
        lines = " \\\\\n".join(f"\\textsuperscript{{{_LETTERS[i]}}}~{_esc(r)}" for i, r in enumerate(self._reasons))
        return f"\n\\vspace{{2pt}}\n{{\\footnotesize\n{lines}\n}}\n"


def _table(caption: str, label: str, colspec: str, header: str, body_rows: list[str], notes: _Notes) -> str:
    rows = "\n".join(body_rows)
    return (
        f"{DO_NOT_EDIT}"
        "\\begin{table}[t]\n"
        "\\centering\n"
        f"\\caption{{{caption}}}\n"
        f"\\label{{{label}}}\n"
        f"\\begin{{tabular}}{{{colspec}}}\n"
        "\\toprule\n"
        f"{header} \\\\\n"
        "\\midrule\n"
        f"{rows}\n"
        "\\bottomrule\n"
        "\\end{tabular}\n"
        f"{notes.block()}"
        "\\end{table}\n"
    )


def _external_table(ledger: Path) -> str:
    notes = _Notes()
    rows = external_rows(ledger) if ledger.exists() else []
    body: list[str] = []
    for r in rows:
        try:
            marks = headlines(r)
        except (KeyError, StopIteration, ZeroDivisionError):
            continue
        for name, value, stderr, n in marks:
            body.append(
                f"{_esc(r.get('track'))} & {_esc(r.get('condition'))} & {_esc(r.get('model'))} & "
                f"{_esc(r.get('tool'))} & {_esc(name)} & {value:.4f} & {stderr:.4f} & {n} \\\\"
            )
    if not body:
        reason = "no ledger yet" if not ledger.exists() else "no external-evaluation row recorded in the ledger yet"
        dash = notes.dash(reason)
        body.append(f"{dash} & {dash} & {dash} & {dash} & {dash} & {dash} & {dash} & {dash} \\\\")
    return _table(
        "Every external-scorer result in the ledger's \\texttt{audit\\{kind: external\\_eval\\}} rows.",
        "tab:gen-external",
        "lllllccc",
        "\\textbf{Track} & \\textbf{Condition} & \\textbf{Model} & \\textbf{Tool} & \\textbf{Metric} & "
        "\\textbf{Value} & \\textbf{$\\pm$} & \\textbf{$n$}",
        body,
        notes,
    )


def _paired_table(ledger: Path) -> str:
    notes = _Notes()
    rows = external_rows(ledger) if ledger.exists() else []
    by: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for r in rows:
        try:
            marks = headlines(r)
        except (KeyError, StopIteration, ZeroDivisionError):
            continue
        for name, *_rest in marks:
            by.setdefault((str(r.get("track")), name), {})[str(r.get("condition") or "")] = r

    body: list[str] = []
    for (track, name), conds in sorted(by.items()):
        base = conds.get("base")
        others = {c: r for c, r in conds.items() if not (c == "base" or c.startswith("base-"))}
        for cond, row in sorted(others.items()):
            if base is None:
                dash = notes.dash(f"no baseline recorded for {track}/{name}")
                body.append(f"{_esc(track)} & {_esc(name)} & {_esc(cond)} & {dash} & {dash} & {dash} \\\\")
                continue
            base_items: dict[str, int] = base.get("items") or {}
            cond_items: dict[str, int] = row.get("items") or {}
            if not (set(base_items) & set(cond_items)):
                dash = notes.dash("no per-item vectors logged for this comparison")
                body.append(f"{_esc(track)} & {_esc(name)} & {_esc(cond)} & {dash} & {dash} & {dash} \\\\")
                continue
            d = discordance(base_items, cond_items)
            body.append(
                f"{_esc(track)} & {_esc(name)} & {_esc(cond)} & {d.wins} & {d.losses} & {d.p_mcnemar:.4f} \\\\"
            )
    if not body:
        reason = "no ledger yet" if not ledger.exists() else "no paired comparison recorded in the ledger yet"
        dash = notes.dash(reason)
        body.append(f"{dash} & {dash} & {dash} & {dash} & {dash} & {dash} \\\\")
    return _table(
        "Paired comparisons against the baseline, from shared per-item pass/fail vectors alone.",
        "tab:gen-paired",
        "lllccc",
        "\\textbf{Track} & \\textbf{Metric} & \\textbf{Condition} & \\textbf{Wins} & \\textbf{Losses} & "
        "\\textbf{$p_{\\mathrm{McNemar}}$}",
        body,
        notes,
    )


def _night_rows(ledger: Path) -> list[dict[str, Any]]:
    starts: dict[tuple[int, str], dict[str, Any]] = {}
    out: list[dict[str, Any]] = []
    for ev in iter_events(ledger):
        p = ev.payload
        if ev.kind != "audit":
            continue
        track = str(p.get("track") or "lora")
        if p.get("kind") == "night_start":
            starts[(ev.night, track)] = {"policy": p.get("selection_policy")}
        elif p.get("kind") == "night_end":
            s = starts.get((ev.night, track), {})
            outcomes: dict[str, str] = p.get("outcomes") or {}
            out.append(
                {
                    "night": ev.night,
                    "track": track,
                    "policy": s.get("policy"),
                    "spent_gpu_h": p.get("spent_gpu_h"),
                    "promoted": sum(1 for o in outcomes.values() if o == "promoted"),
                    "pruned": sum(1 for o in outcomes.values() if o == "pruned"),
                    "candidates": len(outcomes),
                }
            )
    return out


def _nights_table(ledger: Path) -> str:
    notes = _Notes()
    nights = _night_rows(ledger) if ledger.exists() else []
    body: list[str] = []
    for row in sorted(nights, key=lambda r: (r["night"], r["track"])):
        policy = _esc(row["policy"]) if row["policy"] else notes.dash("no selection policy recorded for this night")
        spent = f"{row['spent_gpu_h']:.3f}" if row["spent_gpu_h"] is not None else notes.dash(
            "no GPU-hours recorded for this night"
        )
        outcomes = f"{row['promoted']} promoted / {row['pruned']} pruned / {row['candidates']} candidates"
        body.append(f"{row['night']} & {_esc(row['track'])} & {policy} & {spent} & {outcomes} \\\\")
    if not body:
        reason = "no ledger yet" if not ledger.exists() else "no night has closed in this ledger yet"
        dash = notes.dash(reason)
        body.append(f"{dash} & {dash} & {dash} & {dash} & {dash} \\\\")
    return _table(
        "Nights closed in the ledger, with the selection policy and spend the kernel recorded for each.",
        "tab:gen-nights",
        "llllc",
        "\\textbf{Night} & \\textbf{Track} & \\textbf{Policy} & \\textbf{GPU-h} & \\textbf{Outcomes}",
        body,
        notes,
    )


def results_tables(root: Path) -> dict[str, str]:
    """The three generated tables, keyed by the basename each is written to under `paper/generated/`."""
    ledger = Path(root) / "research" / "ledger.jsonl"
    return {
        "external_benchmarks": _external_table(ledger),
        "paired_comparisons": _paired_table(ledger),
        "nights": _nights_table(ledger),
    }


def write_tables(root: Path) -> list[Path]:
    """Write the generated tables to paper/generated/*.tex, creating the directory if needed."""
    root = Path(root)
    out_dir = root / "paper" / "generated"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, body in results_tables(root).items():
        path = out_dir / f"{name}.tex"
        path.write_text(body)
        written.append(path)
    return written


__all__ = ["DO_NOT_EDIT", "results_tables", "write_tables"]
