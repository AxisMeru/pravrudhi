"""Objectives: what the user is trying to achieve, and whether it is happening.

Until this module existed the engine improved things the engine had chosen. Both tracks were hard-wired — the model
track to GSM8K accuracy, the harness track to HumanEval+ pass@1 — so a user who wanted a legal-domain model had no
way to say so and no screen that answered whether it was working. "Improve against a benchmark" is an empty claim
when the benchmark was picked by whoever wrote the engine rather than by whoever wants the result.

An objective holds the intent verbatim, the track under which its evidence accumulates, the benchmarks that measure
it, the recipes it may draw on, and optionally a target. Progress is never stored: it is recomputed from the
ledger's external-eval rows every time it is asked for, so a number on the objective screen is a number the ledger
contains. Three states are kept distinguishable rather than collapsed to zero — no baseline, baseline only, and
measured — because a screen that renders "no data" as 0.0 tells the user the loop failed when in fact it has not
yet run.

See docs/superpowers/specs/2026-09-05-pravrudhi-objectives-design.md.
"""

from __future__ import annotations

import os
import re
import stat
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from pravrudhi.application.discordance import discordance
from pravrudhi.application.external import external_rows, headlines
from pravrudhi_kernel.stats import wilson_ci

PACKAGED_OBJECTIVES = Path(__file__).resolve().parents[1] / "assets" / "objectives"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
DIRECTIONS = ("up", "down")


class ObjectiveError(ValueError):
    """An objective that cannot be measured is not an objective."""


@dataclass(frozen=True)
class Benchmark:
    """One measuring instrument. `metric` is the name as the external renderer forms it, so an objective and the
    evidence document cannot disagree about which number they mean."""

    id: str
    tool: str
    metric: str
    direction: str = "up"

    def better(self, delta: float) -> bool:
        return delta > 0 if self.direction == "up" else delta < 0


@dataclass(frozen=True)
class Objective:
    id: str
    intent: str
    track: str
    benchmarks: tuple[Benchmark, ...]
    domain: str = ""
    recipes: tuple[str, ...] = ()
    target_delta: float | None = None
    created: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["benchmarks"] = [asdict(b) for b in self.benchmarks]
        d["recipes"] = list(self.recipes)
        return d


@dataclass(frozen=True)
class Measurement:
    """One external-scorer result, carried with the provenance that admitted it."""

    value: float
    stderr: float
    n: int
    model: str
    night: int
    seq: int
    sha256: str


@dataclass(frozen=True)
class Progress:
    """What the ledger says about one benchmark of one objective.

    `state` is the load-bearing field. `unmeasured` and a delta of zero are different facts and the UI must not
    render them the same way.
    """

    benchmark: str
    state: str  # "unmeasured" | "baseline_only" | "measured"
    reason: str = ""
    baseline: Measurement | None = None
    latest: Measurement | None = None
    delta: float | None = None
    delta_lo: float | None = None
    delta_hi: float | None = None
    target_delta: float | None = None
    met: bool | None = None
    paired: bool = False
    wins: int | None = None
    losses: int | None = None
    p_mcnemar: float | None = None

    @property
    def significant(self) -> bool:
        """True only when the interval excludes zero. A delta whose interval spans zero is not an improvement and
        this module never calls it one. When `paired` is True, `delta_lo`/`delta_hi` already hold the paired
        interval, so this check needs no branch of its own for the paired case."""
        if self.delta_lo is None or self.delta_hi is None:
            return False
        return self.delta_lo > 0.0 or self.delta_hi < 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["significant"] = self.significant
        return d


def parse(raw: Any, *, oid: str | None = None) -> Objective:
    """Build an objective from loaded YAML, refusing the shapes that would produce a screen that lies. A file that is not a
    mapping, or whose fields have the wrong kind (a benchmark that is not a mapping or has no id, a list where a number is
    expected), is an `ObjectiveError` like any other malformed file, never an AttributeError, KeyError or TypeError."""
    if not isinstance(raw, dict):
        raise ObjectiveError("an objective file must hold a mapping of fields")
    try:
        return _parse(raw, oid=oid)
    except (AttributeError, KeyError, TypeError, ValueError) as e:
        if isinstance(e, ObjectiveError):
            raise
        raise ObjectiveError(f"an objective field has the wrong kind ({type(e).__name__})") from None


def _parse(raw: dict[str, Any], *, oid: str | None = None) -> Objective:
    ident = str(raw.get("id") or oid or "").strip()
    if not ID_RE.match(ident):
        raise ObjectiveError(f"objective id {ident!r} must be lowercase letters, digits and hyphens (2-63 chars)")
    intent = str(raw.get("intent") or "").strip()
    if not intent:
        raise ObjectiveError(f"objective {ident} has no intent; the intent is the whole point of an objective")
    track = str(raw.get("track") or "").strip()
    if not track:
        raise ObjectiveError(f"objective {ident} has no track, so no ledger rows can be attributed to it")
    rows = raw.get("benchmarks") or []
    if not rows:
        raise ObjectiveError(f"objective {ident} declares no benchmark; an unmeasurable goal is a wish, not an objective")
    marks: list[Benchmark] = []
    for b in rows:
        direction = str(b.get("direction") or "up")
        if direction not in DIRECTIONS:
            raise ObjectiveError(f"objective {ident} benchmark {b.get('id')!r}: direction must be one of {DIRECTIONS}")
        if not b.get("metric"):
            raise ObjectiveError(f"objective {ident} benchmark {b.get('id')!r} names no metric")
        marks.append(
            Benchmark(id=str(b["id"]), tool=str(b.get("tool") or "lm-eval"), metric=str(b["metric"]), direction=direction)
        )
    target = raw.get("target_delta")
    return Objective(
        id=ident,
        intent=intent,
        track=track,
        benchmarks=tuple(marks),
        domain=str(raw.get("domain") or ""),
        recipes=tuple(str(r) for r in (raw.get("recipes") or [])),
        target_delta=None if target is None else float(target),
        created=str(raw.get("created") or ""),
        notes=str(raw.get("notes") or ""),
    )


def objectives_dir(root: Path) -> Path:
    return Path(root) / ".pravrudhi" / "objectives"


def load(path: Path) -> Objective:
    """Read one objective file. Text that is not valid UTF-8, and YAML nested too deeply for the parser, are `ObjectiveError`."""
    try:
        text = path.read_text()
    except UnicodeDecodeError:
        raise ObjectiveError("an objective file must be UTF-8 text") from None
    try:
        raw = yaml.safe_load(text)
    except RecursionError:
        raise ObjectiveError("an objective file is nested too deeply") from None
    except ValueError:  # e.g. a NUL character: the parser refuses it with a ValueError, not a YAMLError
        raise ObjectiveError("an objective file holds characters YAML cannot read") from None
    return parse({} if raw is None else raw, oid=path.stem)


def load_all(root: Path) -> list[Objective]:
    """Every objective in the workspace, by id. A malformed file is skipped loudly rather than failing the list —
    one bad file must not hide the others — and the reason surfaces through `problems`."""
    d = objectives_dir(root)
    out: list[Objective] = []
    for p in sorted(d.glob("*.yaml")) if d.exists() else []:
        try:
            out.append(load(p))
        except (ObjectiveError, yaml.YAMLError, OSError):  # an OSError: the file vanished or became unreadable after the glob
            continue
    return out


def problems(root: Path) -> list[tuple[str, str]]:
    """(file, reason) for each objective file that will not load."""
    d = objectives_dir(root)
    out: list[tuple[str, str]] = []
    for p in sorted(d.glob("*.yaml")) if d.exists() else []:
        try:
            load(p)
        except FileNotFoundError:
            continue  # removed after the glob: nothing to report
        except (ObjectiveError, yaml.YAMLError) as e:
            out.append((p.name, str(e)))
        except OSError as e:  # the message of an OSError carries the absolute path: report the cause only
            out.append((p.name, f"could not be read ({e.strerror or type(e).__name__})"))
    return out


def _umask() -> int:
    current = os.umask(0)
    os.umask(current)
    return current


def write(root: Path, obj: Objective) -> Path:
    d = objectives_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{obj.id}.yaml"
    body = obj.to_dict()
    body.pop("id")
    if not body.get("created"):
        body["created"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = yaml.safe_dump(body, sort_keys=False, allow_unicode=True)
    # Atomic: a reader never sees a half-written file. Write a UNIQUE sibling (concurrent writers of one objective never
    # share a temporary name), flush it to disk, give it the old file's mode, then rename it over the target. Stated limits:
    # a hard link to the file is replaced by a regular file; the directory entry itself is not fsynced.
    target = path.resolve() if path.is_symlink() else path  # a symlinked file is updated in place, not replaced by a regular file
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.chmod(tmp, stat.S_IMODE(target.stat().st_mode))  # keep the mode of the file being replaced
        except FileNotFoundError:
            os.chmod(tmp, 0o666 & ~_umask())  # a new file: the mode write_text() would have given it
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def examples() -> list[str]:
    d = PACKAGED_OBJECTIVES
    return sorted(p.stem for p in d.glob("*.yaml")) if d.exists() else []


def copy_example(root: Path, name: str) -> Path:
    src = PACKAGED_OBJECTIVES / f"{name}.yaml"
    if not src.exists():
        raise ObjectiveError(f"no packaged example named {name!r}; have {', '.join(examples()) or 'none'}")
    obj = load(src)
    return write(root, obj)


def _measures(row: dict[str, Any]) -> list[tuple[str, Measurement]]:
    """Every (metric name, measurement) a row carries. A two-task lm-eval file is one row, and reading only its
    first task left the second task's benchmark showing as unmeasured on an objective that named it."""
    return [
        (
            name,
            Measurement(
                value=value,
                stderr=stderr,
                n=n,
                model=str(row.get("model") or ""),
                night=int(row.get("night") or 0),
                seq=int(row.get("seq") or 0),
                sha256=str(row.get("sha256") or ""),
            ),
        )
        for name, value, stderr, n in headlines(row)
    ]


def _paired_stats(base_items: dict[str, int], latest_items: dict[str, int]) -> tuple[float, float, float, int, int, float]:
    """A Wilson-style interval for the paired delta, derived from wins/losses over the discordant pairs alone.

    `discordance()` gives wins, losses and delta = (wins-losses)/n over the *full* shared set, plus an exact
    McNemar p-value — but no interval for delta itself. Model each discordant pair as an independent Bernoulli
    trial that lands a win with probability p; `wilson_ci` gives the interval for p from wins/(wins+losses). Since
    delta = (wins+losses)/n * (2p - 1) is monotone increasing in p, the endpoints of the p-interval carry straight
    over to delta by that same transform.
    """
    d = discordance(base_items, latest_items)
    total = d.wins + d.losses
    if total == 0 or d.n == 0:
        return 0.0, 0.0, 0.0, d.wins, d.losses, d.p_mcnemar
    lo_p, hi_p = wilson_ci(d.wins, total)
    scale = total / d.n
    return d.delta, scale * (2 * lo_p - 1), scale * (2 * hi_p - 1), d.wins, d.losses, d.p_mcnemar


def progress(obj: Objective, ledger: Path) -> list[Progress]:
    """Recompute this objective's standing from the ledger. Nothing here is cached or stored."""
    rows = [r for r in external_rows(ledger) if r.get("track") == obj.track]
    measured: dict[str, dict[str, list[tuple[Measurement, dict[str, Any]]]]] = {}
    for r in rows:
        try:
            pairs = _measures(r)
        except (KeyError, StopIteration, ZeroDivisionError):
            continue
        for name, m in pairs:
            measured.setdefault(name, {}).setdefault(str(r.get("condition") or ""), []).append((m, r))

    out: list[Progress] = []
    for b in obj.benchmarks:
        conds = measured.get(b.metric)
        if not conds:
            out.append(
                Progress(
                    benchmark=b.metric,
                    state="unmeasured",
                    reason=f"no external result on track {obj.track!r} reports {b.metric!r}",
                    target_delta=obj.target_delta,
                )
            )
            continue
        # A condition named `base` is the baseline; `base-replicate` and anything else in the base family is a
        # re-measurement of the baseline, not a candidate. Counting a replication as a candidate would report the
        # noise floor as an effect, which is exactly the error the noise-floor study exists to prevent.
        bases = conds.get("base") or []
        others = [mr for c, ms in conds.items() if not (c == "base" or c.startswith("base-")) for mr in ms]
        if not bases:
            out.append(
                Progress(
                    benchmark=b.metric,
                    state="unmeasured",
                    reason=f"track {obj.track!r} has results for {b.metric!r} but none scored as the baseline "
                    f"(condition 'base'), so there is nothing to improve against",
                    target_delta=obj.target_delta,
                )
            )
            continue
        base, base_row = max(bases, key=lambda mr: mr[0].seq)
        if not others:
            out.append(
                Progress(
                    benchmark=b.metric,
                    state="baseline_only",
                    reason="the baseline is measured; nothing has been compared against it yet",
                    baseline=base,
                    target_delta=obj.target_delta,
                )
            )
            continue
        latest, latest_row = max(others, key=lambda mr: mr[0].seq)
        base_items: dict[str, int] = base_row.get("items") or {}
        latest_items: dict[str, int] = latest_row.get("items") or {}
        paired = bool(set(base_items) & set(latest_items))
        wins: int | None = None
        losses: int | None = None
        p_mcnemar: float | None = None
        if paired:
            delta, lo, hi, wins, losses, p_mcnemar = _paired_stats(base_items, latest_items)
        else:
            delta = latest.value - base.value
            half = 1.96 * ((latest.stderr**2 + base.stderr**2) ** 0.5)
            lo, hi = delta - half, delta + half
        met: bool | None = None
        if obj.target_delta is not None:
            reached = delta >= obj.target_delta if b.direction == "up" else delta <= obj.target_delta
            met = bool(reached and (lo > 0.0 or hi < 0.0))
        out.append(
            Progress(
                benchmark=b.metric,
                state="measured",
                reason="",
                baseline=base,
                latest=latest,
                delta=delta,
                delta_lo=lo,
                delta_hi=hi,
                target_delta=obj.target_delta,
                met=met,
                paired=paired,
                wins=wins,
                losses=losses,
                p_mcnemar=p_mcnemar,
            )
        )
    return out


def summary(root: Path, obj: Objective) -> dict[str, Any]:
    """The objective plus its standing, in the shape the API and the UI consume."""
    ledger = Path(root) / "research" / "ledger.jsonl"
    rows = (
        progress(obj, ledger)
        if ledger.exists()
        else [
            Progress(
                benchmark=b.metric, state="unmeasured", reason="this workspace has no ledger yet", target_delta=obj.target_delta
            )
            for b in obj.benchmarks
        ]
    )
    return {**obj.to_dict(), "progress": [p.to_dict() for p in rows]}
