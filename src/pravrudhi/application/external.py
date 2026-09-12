"""Admit external-scorer results (lm-eval, EvalPlus) into the ledger and render them.

The Sākṣī rule: no number is stated that the ledger does not contain. External benchmarks are run by third-party
tooling outside the kernel, so their result files are admitted by hash as `audit{kind: external_eval}` rows with the
tier stated as `external`: the kernel did not execute them, the file hash makes them reproducible, and the evidence
document renders only from those rows.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pravrudhi_kernel.ledger import LedgerWriter
from pravrudhi_kernel.ledger.verify import iter_events
from pravrudhi_kernel.sandbox.observe import sha256_file
from pravrudhi_kernel.stats import wilson_ci

#: A file admitted by hash: third-party tooling ran it outside the kernel.
TIER_EXTERNAL = "external"
#: A row this engine computed itself, right now, with no third-party file to admit by hash. First used by
#: `pravrudhi.application.nyaya_validity.record`.
TIER_KERNEL = "kernel"
TOOL_PRABHASA = "prabhasa"
#: The kernel's own classical-Nyaya validity check (`nyaya_validity.py`); tier `kernel`, not `external`.
TOOL_NYAYA_VALIDITY = "pravrudhi-nyaya-validity"


def _lm_eval_items(r: dict[str, Any]) -> dict[str, int]:
    """Per-doc pass/fail for the first task's `exact_match`, when `--log_samples` wrote them.

    lm-eval only writes a `samples` section when invoked with `--log_samples`; most result files carry none, and
    `parse_lm_eval` must omit the `items` key entirely for those rather than store an empty dict.
    """
    samples = r.get("samples") or {}
    task = next(iter(r.get("results") or {}), None)
    if task is None:
        return {}
    out: dict[str, int] = {}
    for row in samples.get(task) or []:
        doc_id = row.get("doc_id")
        val = row.get("exact_match")
        if doc_id is None or val is None:
            continue
        out[str(doc_id)] = int(round(float(val)))
    return out


def _trust_remote_code(model_args: str | None) -> bool:
    """Whether `model_args` opted into loading custom model code, as its own explicit field.

    `scripts/ext_eval.sh` builds `model_args` as a comma-joined string (lm-eval's own CLI
    convention); `trust_remote_code=True` is buried in there like any other key. A reader
    of an admitted row should not have to parse that string to tell whether a run loaded
    code the kernel never reviewed - so this is surfaced as its own typed field rather than
    left implicit. Off by default: absent or any value other than a case-insensitive "true"
    reads as False, matching lm-eval/Studio's own opt-in-only stance on custom code.
    """
    if not model_args:
        return False
    for part in model_args.split(","):
        key, _, value = part.partition("=")
        if key.strip() == "trust_remote_code":
            return value.strip().lower() == "true"
    return False


def parse_lm_eval(path: Path) -> dict[str, Any]:
    r = json.loads(path.read_text())
    metrics: dict[str, dict[str, float]] = {}
    for task, m in r["results"].items():
        metrics[task] = {k: float(v) for k, v in m.items() if isinstance(v, (int, float))}
    model_args = (r.get("config") or {}).get("model_args")
    parsed: dict[str, Any] = {
        "tool": "lm-eval",
        "tool_version": r.get("lm_eval_version"),
        "transformers_version": r.get("transformers_version"),
        "n_samples": {t: v.get("effective") for t, v in (r.get("n-samples") or {}).items()},
        "n_shot": r.get("n-shot"),
        "model_args": model_args,
        "trust_remote_code": _trust_remote_code(model_args),
        "metrics": metrics,
    }
    items = _lm_eval_items(r)
    if items:
        parsed["items"] = items
    return parsed


def parse_evalplus(path: Path, dataset: str) -> dict[str, Any]:
    r = json.loads(path.read_text())
    rows = r["eval"]
    n = len(rows)
    base = sum(1 for v in rows.values() if v[0]["base_status"] == "pass")
    plus = sum(1 for v in rows.values() if v[0]["base_status"] == "pass" and v[0]["plus_status"] == "pass")
    items = {
        task_id: int(v[0]["base_status"] == "pass" and v[0]["plus_status"] == "pass") for task_id, v in rows.items()
    }
    return {
        "tool": "evalplus",
        "tool_version": "0.3.1",
        "dataset": dataset,
        "n_samples": {dataset: n},
        "metrics": {
            dataset: {"pass@1_base": base / n, "pass@1_plus": plus / n},
            f"{dataset}_counts": {"n": n, "base_pass": base, "plus_pass": plus},
        },
        "items": items,
    }


# Keys that name a denominator rather than a result. `n_pairs` is how many pairs were scored, not a score;
# reporting it as a metric would print 100 in a column of rates.
_N_KEYS = ("n_test_sentences", "n_syllogisms_tested", "n_invalid_tested", "n_docs", "n_pairs", "n_test", "n")

# Numeric keys describing the instrument rather than the result: how many labels a probe had, where a rate was
# cut. Kept out of `metrics` so a score column holds only scores.
_DESCRIPTOR_KEYS = ("n_classes", "roles_covered", "threshold")


def _scored(node: dict[str, Any]) -> dict[str, float]:
    """The numeric leaves of one group that are actually results."""
    return {
        k: float(v) for k, v in node.items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
        and k not in _N_KEYS and k not in _DESCRIPTOR_KEYS
    }


def _panel_groups(node: Any, prefix: str = "") -> dict[str, dict[str, Any]]:
    """Every leaf group in a panel, keyed by its dotted path.

    A leaf group is a dict holding at least one numeric value that is neither a denominator nor a descriptor.
    Recursing rather than hard-coding the panel's shape means a group added upstream is picked up without a
    change here: the panel is another project's artifact and will grow.
    """
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(node, dict):
        return out
    if prefix and _scored(node):
        out[prefix] = node
    for key, value in node.items():
        if isinstance(value, dict):
            out.update(_panel_groups(value, f"{prefix}.{key}" if prefix else key))
    return out


def parse_prabhasa_panel(path: Path) -> dict[str, Any]:
    """Admit prabhasa-samskrutam's benchmark panel as an external scorer's result.

    The product measures itself - Nyaya validity and hetvabhasa rejection (its verifier), plus the karaka and
    morphology probes, BPB and round-trip fidelity (its model). None of it was visible to this engine, because
    `record_external` admitted lm-eval and EvalPlus and nothing else. So the product could improve or regress
    and Studio, whose whole purpose is to improve it, had no signal either way.

    One pseudo-task per leaf group, named by dotted path, so an objective can name a figure the way it already
    names `mmlu_professional_law acc,none`. The sample size travels with it, because `nyaya_validity` reads 1.0
    at n=2 and a rate without its denominator looks like a solved problem.

    Recorded under its own tool name rather than passed off as lm-eval output: the tool is part of the row's
    provenance, and mislabelling it to avoid writing a parser would be a false pramana tag.
    """
    panel = json.loads(path.read_text())
    if not isinstance(panel, dict):
        raise ValueError(f"{path.name} is not a benchmark panel object")
    metrics: dict[str, dict[str, float]] = {}
    n_samples: dict[str, int] = {}
    for name, node in _panel_groups(panel).items():
        metrics[name] = _scored(node)
        n_samples[name] = next(
            (int(node[k]) for k in _N_KEYS if isinstance(node.get(k), (int, float))), 0
        )
    return {
        "tool": TOOL_PRABHASA,
        "tool_version": str(panel.get("version") or panel.get("milestone") or "") or None,
        "metrics": metrics,
        "n_samples": n_samples,
        # Panel-level scalars are provenance, not results: what the model was trained on, not how it scored.
        "panel": {k: v for k, v in panel.items() if not isinstance(v, (dict, list))},
    }


def record_external(
    root: Path,
    path: Path,
    *,
    tool: str,
    track: str,
    condition: str,
    model: str,
    night: int,
    dataset: str = "",
    seed: int | None = None,
) -> dict[str, Any]:
    # Explicit, not a fallthrough. This read `parse_lm_eval(path) if tool == "lm-eval" else
    # parse_evalplus(path, dataset)`, so ANY value that was not exactly "lm-eval" - a new scorer, or a typo -
    # was handed to the EvalPlus parser, and whatever that produced was admitted by hash as evidence. An
    # unknown scorer is now a refusal that names itself.
    if tool == "lm-eval":
        parsed = parse_lm_eval(path)
    elif tool == "evalplus":
        parsed = parse_evalplus(path, dataset)
    elif tool == TOOL_PRABHASA:
        parsed = parse_prabhasa_panel(path)
    else:
        raise ValueError(f"unknown external scorer {tool!r}; known: lm-eval, evalplus, {TOOL_PRABHASA}")
    ledger = root / "research" / "ledger.jsonl"
    w = LedgerWriter.open(ledger, "0.1.0")
    payload = {
        "kind": "external_eval",
        "severity": "info",
        "tier": TIER_EXTERNAL,
        "track": track,
        "condition": condition,
        "model": model,
        "seed": seed,
        "file": str(path.relative_to(root)) if path.is_relative_to(root) else str(path),
        "sha256": sha256_file(path),
        **parsed,
    }
    ev = w.append("audit", "auditor", payload, epoch=0, night=night)
    return {"seq": ev.seq, **payload}


def external_rows(ledger: Path) -> list[dict[str, Any]]:
    out = []
    for ev in iter_events(ledger):
        if ev.kind == "audit" and ev.payload.get("kind") == "external_eval":
            out.append({"seq": ev.seq, "night": ev.night, **ev.payload})
    return out


def stderr_key(key: str) -> str:
    """lm-eval names a metric `<name>,<filter>` and its standard error `<name>_stderr,<filter>`.

    The earlier form of this substituted the literal string `exact_match`, which is correct for GSM8K and wrong for
    every other task: for `acc,none` the substitution matched nothing, the lookup fell back to the metric key
    itself, and the rendered ± column printed the value a second time. Any objective on a task that is not scored
    by exact match would have inherited that.
    """
    head, sep, tail = key.partition(",")
    return f"{head}_stderr{sep}{tail}"


def headlines(row: dict[str, Any]) -> list[tuple[str, float, float, int]]:
    """Every metric a row carries, not only the first.

    An lm-eval run over two tasks writes one results file with both; admitting it as one row and reading only the
    first task made the second invisible to any objective that named it, so a legal objective with two law tasks
    could be measured and still show one of them as unmeasured. EvalPlus rows carry one dataset and yield one."""
    m = row["metrics"]
    if row["tool"] in (TOOL_PRABHASA, TOOL_NYAYA_VALIDITY):
        # One line per metric, like the lm-eval branch. A panel carries many groups and reading only the first
        # would leave a named metric showing as unmeasured - the same fault the lm-eval branch was fixed for.
        # No stderr: the panel reports rates and probe accuracies, and inventing an interval for them would
        # state a precision the source does not claim.
        return [
            (f"{group} {key}", value, 0.0, int((row.get("n_samples") or {}).get(group) or 0))
            for group, metrics in m.items()
            for key, value in metrics.items()
        ]
    if row["tool"] != "lm-eval":
        return [_headline(row)]
    out: list[tuple[str, float, float, int]] = []
    for task, metrics in m.items():
        if not metrics:
            continue
        n = int((row.get("n_samples") or {}).get(task) or 0)
        # EVERY metric, not one per task. This took `next(iter(metrics))` and so reported a single key, which
        # was invisible until a task carried more than one thing worth naming: the nyaya citation tasks report
        # precision over answered items, abstention, and accuracy over all, and an objective that declared the
        # second and third showed them `unmeasured` while the row held their values. A stderr is the interval
        # on another metric rather than a metric, so it is not a headline of its own.
        for key in metrics:
            if "_stderr" in key:
                continue
            out.append((f"{task} {key}", metrics[key], metrics.get(stderr_key(key), 0.0), n))
    return out


def _headline(row: dict[str, Any]) -> tuple[str, float, float, int]:
    m = row["metrics"]
    if row["tool"] == "lm-eval":
        task = next(iter(m))
        key = "exact_match,strict-match" if "exact_match,strict-match" in m[task] else next(iter(m[task]))
        n = int((row.get("n_samples") or {}).get(task) or 0)
        return f"{task} {key}", m[task][key], m[task].get(stderr_key(key), 0.0), n
    ds = row["dataset"]
    n = m[f"{ds}_counts"]["n"]
    p = m[ds]["pass@1_plus"]
    lo, hi = wilson_ci(int(round(p * n)), n)
    return f"{ds}+ pass@1", p, (hi - lo) / 2, n


def render_external(ledger: Path) -> str:
    rows = external_rows(ledger)
    lines = [
        "# External proof tier",
        "",
        "Rendered from the ledger's `audit{kind: external_eval}` rows alone. Every row was scored by third-party "
        "tooling outside the kernel (tier: external); the result file is admitted by SHA-256. The kernel's own "
        "selection record is in the night documents.",
        "",
        "| seq | track | condition | model | scorer | metric | value | ±  | n | file sha256 |",  # noqa: E501
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    # `headlines`, not `_headline`. A row can carry many metrics - an lm-eval run over two tasks, or a whole
    # benchmark panel - and this rendered one per row, so every metric after the first was invisible in the
    # evidence document even though `headlines` had already been fixed to return them all for objectives.
    # A panel row would not render at all: `_headline`'s fallback branch reads row["dataset"], an EvalPlus key.
    for r in rows:
        for name, v, e, n in headlines(r):
            lines.append(
                f"| {r['seq']} | {r['track']} | {r['condition']} | {r['model']} | {r['tool']} "
                f"{r.get('tool_version') or ''} | {name} | {v:.4f} | {e:.4f} | {n} | {r['sha256'][:16]} |"
            )
    lines += ["", "## Paired differences", ""]
    # Keyed by (track, metric name), so a base and a candidate are paired per metric rather than per row.
    by: dict[tuple[str, str], dict[str, tuple[float, float, int]]] = {}
    for r in rows:
        for name, v, e, n in headlines(r):
            by.setdefault((r["track"], name), {})[r["condition"]] = (v, e, n)
    for (track, name), conds in sorted(by.items()):
        base = conds.get("base")
        if not base:
            continue
        bv, be, _bn = base
        for cond, (v, e, n) in sorted(conds.items()):
            if cond == "base":
                continue
            lines.append(
                f"- {track} {name}: {cond} − base = {v - bv:+.4f} "
                f"(base {bv:.4f}±{be:.4f}, {cond} {v:.4f}±{e:.4f}, n={n})"
            )
    if len(lines) and lines[-1] == "":
        lines.append("- (no paired pair yet)")
    lines += [
        "",
        "## Tensions",
        "",
        "External rows are not kernel-executed: they carry tier `external`, not pratyakṣa in the kernel sense. "
        "Their standard errors are the scorer's own (lm-eval) or a Wilson half-width (EvalPlus), not the loop's σ_seed.",
        "",
    ]
    return "\n".join(lines)
