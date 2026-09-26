"""CI guard (operator, 2026-09-26, after the fail-open-default sweep of both repos): a missing, empty or
unparseable verdict / confidence / score that defaults to a NUMBER instead of to `None` or an error, so an
item which could not be scored silently counts as a pass, a safe outcome, or a negative finding.

The canonical instance this exists to catch: a scoring script used `v.get("confidence", 0) >= THRESHOLD`, so
a malformed verdict record defaulted to confidence 0, short-circuited before the verdict field was read, and
the instance scored as "not fooled" -- i.e. a pass. With an empty or unparseable verdict file the script
reported a 0.0% false-prove rate at full n, with a plausible confidence bound and no warning.

Three rules, deliberately at THREE DIFFERENT STRENGTHS, matching the false-positive rate each one was
measured at over this repo during the sweep (see the PR that added this file):

* RULE 1 -- HARD FAILURE. `x.get("<decision-bearing key>", <falsy literal>)`. ~10-20% measured false
  positives. Includes the IDENTITY keys (`endpoint_id`, `adapter_sha`, ...): a default that compares EQUAL
  TO ITSELF is the specific hazard -- two sides both defaulting to `""` make an identity check pass
  vacuously, which is how a record gate can go inert while still being called.

  A `False` default is legitimate where the code treats False as REFUSE/FAIL, and is the bug where False
  means "no problem found". That distinction is not decidable from the AST, so per the operator's
  instruction this rule FLAGS BOTH and requires an explicit, visible waiver -- never silently allows.

* RULE 2 -- ADVISORY ONLY, never affects the exit code. Coalescing a score with `or 0` / `or 0.0` /
  `or False`, scoped to `SCORER_PATHS`. Measured ~85-90% false positives unscoped and ~30% scoped: almost
  every real match is a token count, a GPU-hour total or a wall-clock reading where zero-when-absent is
  genuinely correct. Too noisy to block a merge, so it reports and the baseline records the current count
  so a reviewer can see growth.

* RULE 3 -- HARD FAILURE. Within `SCORER_PATHS`, a rate / mean / sd / confidence interval / calibration
  error must return `None` or raise when its denominator is zero, never a numeric literal. The raw greps
  for this shape measured 60-90% false positives; this rule is the narrow, enforceable form instead -- it
  fires only when a STATISTIC-NAMED binding or return is guarded by an EMPTINESS OR ZERO-COUNT test and
  falls back to a NUMERIC LITERAL.

Waivers. A line may be waived two ways, both of which leave the waived line visible:
  * inline, `# fail-open-ok: <reason>` on the offending line or the line above it (for new code); or
  * in `scripts/fail_open_defaults_baseline.txt`, for the occurrences that already existed when this guard
    landed -- every one of them listed with its reason and the issue it belongs to. The baseline is keyed on
    (path, exact matched expression source), NOT on a line number, so an unrelated edit above a waived line
    does not turn CI red. The consequence, stated rather than hidden: a SECOND, byte-identical occurrence
    added to an already-baselined file is not caught.

Usage: check_fail_open_defaults.py [--root DIR] [--baseline FILE]. Mirrors `import_guard.py` /
`check_no_private_data.py`: exit 1 and print `path:line: message` for every violation, exit 0 clean.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

#: Where a number standing in for an absent measurement is a MEASUREMENT rather than a display default.
#: Deliberately the paths this repo's own CI already lints and runs, plus `proposals/` -- the vendored
#: proposal scorers, where the sweep found real instances of this class and where a future promotion into
#: `src/` would carry them in.
SCORER_PATHS: tuple[str, ...] = (
    "src/pravrudhi/application",
    "pravrudhi_kernel/src/pravrudhi_kernel/metrics",
    "pravrudhi_kernel/src/pravrudhi_kernel/stats",
    "scripts",
    "proposals",
)

#: Every path Rule 1 reads. Wider than SCORER_PATHS: a decision-bearing key defaulting to a falsy literal is
#: a hazard in the API and serving layers too, not only where a rate is computed.
RULE1_PATHS: tuple[str, ...] = (
    "src",
    "pravrudhi_kernel/src",
    "scripts",
    "proposals",
)

#: `_`-separated tokens of a dict key that make it decision-bearing. Matched as WHOLE TOKENS, never as
#: substrings: substring matching flagged `MemAvailable` (a /proc/meminfo field), `rate_limit_notice`,
#: `strategy` (for "rate") and `validate` (for "valid") during calibration, four false positives out of nine
#: hits. Token matching removes all four.
DECISION_TOKENS: frozenset[str] = frozenset({
    "verdict", "confidence", "score", "scores", "label", "outcome", "decision",
    "status", "established", "unestablished", "fooled", "parity", "agree", "agreement",
    "tau", "threshold", "floor", "gold", "correct", "passed", "vetoed", "abstain",
    "available", "unavailable", "p", "prob", "probability", "confident",
    "sha256", "digest", "checksum",
})

#: IDENTITY keys, where the hazard is a default that compares EQUAL TO ITSELF: both sides of a check
#: defaulting to `""` make the check pass vacuously, so an unverified endpoint or adapter reads as the
#: verified one. Flagged only when a SUBJECT token and an ID token both appear -- `endpoint_id` and
#: `adapter_sha` are identities, while a bare `endpoint` is a URL read from config (three false positives
#: during calibration: `api/chat.py`'s `PRAVRUDHI_CHAT_ENDPOINT`, and `night.py`/`harness_track.py`'s
#: `cfg["proposer"].get("endpoint", "")`).
IDENTITY_SUBJECT_TOKENS: frozenset[str] = frozenset({
    "endpoint", "adapter", "model", "judge", "binary", "corpus", "registry", "record", "weights", "snapshot",
})
IDENTITY_ID_TOKENS: frozenset[str] = frozenset({
    "id", "sha", "sha256", "digest", "revision", "rev", "checksum", "hash", "commit", "version",
})

#: Tokens too generic to flag wherever they appear: counted only as the FINAL token, or as the whole key.
#: `pass_rate` / `false_prove_rate` are measurements; `rate_limit_notice` is a display string.
TRAILING_ONLY_TOKENS: frozenset[str] = frozenset({"rate", "ok", "valid", "pass"})

#: Names that make a binding or a function a STATISTIC for Rule 3. Matched as whole `_`-separated tokens, or
#: as the whole name. `wilson` / `clopper` / `pearson` / `binom` are named for the interval they compute
#: rather than for the quantity, so they are listed by name.
STAT_TOKENS: frozenset[str] = frozenset({
    "rate", "mean", "avg", "average", "sd", "std", "stdev", "sigma", "var", "variance",
    "ci", "ci95", "interval", "bound", "upper", "lower", "lo", "hi", "ece", "calibration",
    "accuracy", "precision", "recall", "f1", "median", "fraction", "proportion", "ratio",
    "wilson", "clopper", "pearson", "binom", "kappa", "delta", "dp", "drift", "exactness",
})

#: Falsy literals that can be mistaken for a measurement. `None` is the CORRECT default and is never flagged.
_FALSY_SCALARS: tuple[object, ...] = (0, 0.0, False, "")

WAIVER_MARKER = "fail-open-ok:"

DEFAULT_BASELINE = Path("scripts") / "fail_open_defaults_baseline.txt"

#: The baseline directive recording how many RULE 2 advisories existed when this guard landed. Rule 2 is
#: summary-only by default: 297 of them at that point, spread across 40-odd files (the `str(x or "")` /
#: `int(x or 0)` idiom for optional config), so printing every one on every CI run would bury the two hard
#: rules' output. `--advisory-detail` prints them all; growth past this number prints a note, never a
#: failure.
RULE2_COUNT_DIRECTIVE = "rule2-advisory-count:"

#: This guard's own source and its fixtures name every pattern it looks for, so they are never violations.
EXEMPT_NAMES = frozenset({
    "check_fail_open_defaults.py",
    "test_check_fail_open_defaults.py",
})

#: The fixture tree the guard's own tests point it at. Excluded from a normal repo scan by path, so the
#: deliberately-bad fixtures never fail the real build.
FIXTURE_DIR_NAME = "fail_open_fixtures"


@dataclass(frozen=True)
class Finding:
    rule: str
    path: str
    lineno: int
    snippet: str
    message: str

    #: (path, snippet) -- what the baseline is keyed on. Never the line number: an edit above a waived line
    #: must not turn CI red.
    @property
    def key(self) -> str:
        return f"{self.path}::{self.snippet}"

    def render(self) -> str:
        return f"{self.path}:{self.lineno}: [{self.rule}] {self.message}"


def _tokens(name: str) -> list[str]:
    return [t for t in name.lower().split("_") if t]


def _is_decision_key(key: str) -> bool:
    toks = _tokens(key)
    if not toks:
        return False
    if any(t in DECISION_TOKENS for t in toks):
        return True
    if any(t in IDENTITY_SUBJECT_TOKENS for t in toks) and any(t in IDENTITY_ID_TOKENS for t in toks):
        return True
    return toks[-1] in TRAILING_ONLY_TOKENS


#: Tokens that make a name count-shaped, for the `X > 0` / `X == 0` form of the no-data guard. The bare
#: truthiness form (`if xs:`) accepts any name -- it reads as an emptiness test whatever it is called -- but
#: a COMPARISON against a small integer does not: `if den > 0` in `pravrudhi_kernel/.../stats/bca.py:94`
#: guards a degenerate float denominator, where the BCa acceleration constant really is 0.0, and
#: `if successes == 0` in `application/discordance.py:38` is the exact Clopper-Pearson edge case where the
#: lower bound really is 0.0. Both were false positives before this restriction.
COUNT_TOKENS: frozenset[str] = frozenset({
    "n", "count", "total", "size", "len", "rows", "items", "trials", "samples", "observations",
    "denominator", "num", "nitems", "bins",
})


def _is_count_named(node: ast.expr) -> bool:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "len":
        return True
    name: str | None = None
    if isinstance(node, ast.Name):
        name = node.id
    elif isinstance(node, ast.Attribute):
        name = node.attr
    if name is None:
        return False
    return any(t in COUNT_TOKENS for t in _tokens(name))


def _binding_names(target: ast.expr) -> list[str]:
    """Every name an assignment target binds, unpacking tuple/list targets one level or more."""
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, ast.Attribute):
        return [target.attr]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [n for elt in target.elts for n in _binding_names(elt)]
    return []


def _is_stat_name(name: str) -> bool:
    toks = _tokens(name)
    if not toks:
        return False
    return any(t in STAT_TOKENS for t in toks)


def _falsy_literal(node: ast.expr) -> str | None:
    """A rendering of `node` when it is a falsy literal that could pass for a measurement, else None.

    `None` as a default is the correct behaviour this guard exists to push code towards, so a `None` default
    is never a finding. `True` is not falsy but IS a fail-open default for a check whose False means "a
    problem was found", so it is included.
    """
    if isinstance(node, ast.Constant):
        if node.value is None:
            return None
        if node.value is True:
            return "True"
        for falsy in _FALSY_SCALARS:
            # `0 == False` and `0 == 0.0` in Python, so compare types too or `False` matches `0`.
            if type(node.value) is type(falsy) and node.value == falsy:
                return repr(node.value)
        return None
    if isinstance(node, ast.List) and not node.elts:
        return "[]"
    if isinstance(node, ast.Tuple) and not node.elts:
        return "()"
    if isinstance(node, ast.Dict) and not node.keys:
        return "{}"
    if isinstance(node, ast.Set):  # `set()` is a Call, not a literal; `{*()}` is vanishingly rare
        return None
    return None


def _numeric_literal_fallback(node: ast.expr) -> str | None:
    """A rendering of `node` when it is a numeric literal, or a tuple/list of them -- the thing a statistic
    must never fall back to. `(0.0, 0.0)` (a fabricated confidence interval) is the shape this catches that a
    bare-scalar check would miss."""
    if isinstance(node, ast.Constant) and type(node.value) in (int, float) and node.value is not True:
        return repr(node.value)
    if isinstance(node, (ast.Tuple, ast.List)) and node.elts:
        parts = [_numeric_literal_fallback(e) for e in node.elts]
        if all(p is not None for p in parts):
            return "(" + ", ".join(p for p in parts if p is not None) + ")"
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _numeric_literal_fallback(node.operand)
        return f"-{inner}" if inner else None
    return None


def _is_emptiness_or_zero_test(node: ast.expr) -> bool:
    """Whether `node` reads as "is there any data at all?" -- the guard whose false branch is where a
    fabricated statistic gets substituted.

    Covers `if xs`, `if not xs`, `if len(xs)`, `if n`, `if n > 0`, `if len(xs) > 1`, `if n == 0`,
    `if n <= 0`, and `if xs is not None`. Deliberately NOT a general truthiness test: a condition that
    compares two measurements (`if a > b`) is a real branch, not a no-data guard, and a comparison against a
    small integer counts only when its left side is COUNT-SHAPED (see `COUNT_TOKENS`).
    """
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return _is_emptiness_or_zero_test(node.operand)
    if isinstance(node, ast.Compare) and len(node.ops) == 1 and len(node.comparators) == 1:
        right = node.comparators[0]
        ops_ok = isinstance(node.ops[0], (ast.Gt, ast.GtE, ast.Lt, ast.LtE, ast.Eq, ast.NotEq))
        right_small_int = isinstance(right, ast.Constant) and type(right.value) is int and right.value <= 2
        if ops_ok and right_small_int:
            return _is_count_named(node.left)
        # `if x is None` / `if x is not None` -- also a "did any data arrive?" guard.
        return (
            isinstance(node.ops[0], (ast.Is, ast.IsNot))
            and isinstance(right, ast.Constant)
            and right.value is None
        )
    return isinstance(node, (ast.Name, ast.Attribute, ast.Subscript)) or _is_count_named(node)


class _Rule1And3Visitor(ast.NodeVisitor):
    """One AST walk per file for both hard rules. Rule 3 needs to know the NAME a value is being bound to
    (`mean = ... if xs else 0.0`) or returned from (`def agree_rate(): return ... if n else 0.0`), which a
    flat `ast.walk` cannot supply -- hence a visitor carrying the enclosing function and assignment target.
    """

    def __init__(self, path: str, lines: list[str], *, in_scorer_path: bool, in_rule1_path: bool) -> None:
        self.path = path
        self.lines = lines
        self.in_scorer_path = in_scorer_path
        self.in_rule1_path = in_rule1_path
        self.findings: list[Finding] = []
        self._func_names: list[str] = []
        self._target_names: list[str] = []

    # -- helpers

    def _src(self, node: ast.AST) -> str:
        seg = ast.get_source_segment("\n".join(self.lines), node)
        if seg is None:
            return ""
        return " ".join(seg.split())

    def _stat_context(self) -> str | None:
        """The statistic-shaped name in scope for Rule 3: the assignment target first (innermost, most
        specific), then the enclosing function. None when neither is statistic-shaped."""
        for name in reversed(self._target_names):
            if _is_stat_name(name):
                return name
        for name in reversed(self._func_names):
            if _is_stat_name(name):
                return name
        return None

    # -- traversal that tracks names

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802 -- ast.NodeVisitor's API
        self._func_names.append(node.name)
        self.generic_visit(node)
        self._func_names.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._func_names.append(node.name)
        self.generic_visit(node)
        self._func_names.pop()

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        # Tuple targets are unpacked too: `lo, hi = boot_ci(...) if n else (0.0, 0.0)` (the fabricated
        # confidence-interval shape, e.g. pravrudhi_kernel/.../stats/tost.py:34) names the statistic only in
        # its unpacked targets, so collecting `ast.Name` targets alone would miss it.
        names: list[str] = []
        for target in node.targets:
            names.extend(_binding_names(target))
        self._target_names.extend(names)
        self.generic_visit(node)
        for _ in names:
            self._target_names.pop()

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        name = node.target.id if isinstance(node.target, ast.Name) else None
        if name:
            self._target_names.append(name)
        self.generic_visit(node)
        if name:
            self._target_names.pop()

    def visit_keyword(self, node: ast.keyword) -> None:
        # `ParityResult(median_abs_dp=statistics.median(xs) if xs else 0.0)` -- the keyword name is the only
        # place the statistic is named, so it counts as a Rule 3 binding exactly like an assignment target.
        if node.arg:
            self._target_names.append(node.arg)
        self.generic_visit(node)
        if node.arg:
            self._target_names.pop()

    # -- rule 1

    def visit_Call(self, node: ast.Call) -> None:
        if (
            self.in_rule1_path
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and len(node.args) == 2
            and not node.keywords
        ):
            key = node.args[0]
            if isinstance(key, ast.Constant) and isinstance(key.value, str) and _is_decision_key(key.value):
                rendered = _falsy_literal(node.args[1])
                if rendered is not None:
                    self.findings.append(
                        Finding(
                            rule="rule1",
                            path=self.path,
                            lineno=node.lineno,
                            snippet=self._src(node),
                            message=(
                                f".get({key.value!r}, {rendered}) -- a decision-bearing key defaulting to a "
                                f"falsy literal. An absent, empty or unparseable value becomes a "
                                f"MEASUREMENT: {rendered} reads as a real verdict/score/identity rather than "
                                f"as \"not scored\". Use None (and handle it), exclude the item and count the "
                                f"exclusion, or let a KeyError raise. If {rendered} genuinely means "
                                f"\"refuse/fail\" here, waive it with `# {WAIVER_MARKER} <reason>`."
                            ),
                        )
                    )
        self.generic_visit(node)

    # -- rule 3

    def visit_IfExp(self, node: ast.IfExp) -> None:
        if self.in_scorer_path:
            stat = self._stat_context()
            fallback = _numeric_literal_fallback(node.orelse)
            if stat and fallback and _is_emptiness_or_zero_test(node.test):
                self.findings.append(self._rule3(node, stat, fallback))
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> None:
        # `if n <= 0: return (0.0, 0.0)` -- the early-return form of the same substitution.
        if self.in_scorer_path and _is_emptiness_or_zero_test(node.test) and len(node.body) == 1:
            stmt = node.body[0]
            if isinstance(stmt, ast.Return) and stmt.value is not None:
                stat = self._stat_context()
                fallback = _numeric_literal_fallback(stmt.value)
                if stat and fallback:
                    self.findings.append(self._rule3(stmt, stat, fallback))
        self.generic_visit(node)

    def _rule3(self, node: ast.AST, stat: str, fallback: str) -> Finding:
        return Finding(
            rule="rule3",
            path=self.path,
            lineno=getattr(node, "lineno", 0),
            snippet=self._src(node),
            message=(
                f"{stat!r} falls back to the numeric literal {fallback} when its denominator is empty or "
                f"zero. A statistic computed from no data is not {fallback}, it is undefined: return None "
                f"(or raise) and let the caller report \"not computed\". A fabricated 0.0 rate reads as a "
                f"clean result and a fabricated 0.0 sd reads as zero variance. "
                f"Waive with `# {WAIVER_MARKER} <reason>` if this really is a measured zero."
            ),
        )


def _rule2_advisory(path: str, lines: list[str]) -> list[Finding]:
    """Rule 2, ADVISORY: `float(x or 0)` / `int(x or 0)` / a bare `... or 0` coalescing what may be a score.

    Regex-free and AST-based so `or` inside a string or a comment is never matched, but deliberately
    unfiltered by key name -- the whole point of the measured 30% false-positive rate is that this rule
    cannot tell a token count from a pass rate, which is why it never fails the build.
    """
    findings: list[Finding] = []
    try:
        tree = ast.parse("\n".join(lines))
    except SyntaxError:
        return findings
    for node in ast.walk(tree):
        if not isinstance(node, ast.BoolOp) or not isinstance(node.op, ast.Or):
            continue
        last = node.values[-1]
        rendered = _falsy_literal(last)
        if rendered is None or rendered == "True":
            continue
        seg = ast.get_source_segment("\n".join(lines), node)
        findings.append(
            Finding(
                rule="rule2",
                path=path,
                lineno=node.lineno,
                snippet=" ".join(seg.split()) if seg else "",
                message=(
                    f"coalesces to {rendered} with `or`. Correct for a count, a spend or a duration; a "
                    f"fail-open default if the left side can be an unscored measurement. ADVISORY ONLY -- "
                    f"this never fails the build (measured ~30% false positives even scoped to scorers)."
                ),
            )
        )
    return findings


def _waived_inline(lines: list[str], lineno: int) -> bool:
    """A `# fail-open-ok: <reason>` on the offending line or the line immediately above it. The reason is
    required: a bare marker with nothing after the colon does not waive anything."""
    for candidate in (lineno, lineno - 1):
        if 1 <= candidate <= len(lines):
            line = lines[candidate - 1]
            if WAIVER_MARKER in line and line.split(WAIVER_MARKER, 1)[1].strip():
                return True
    return False


def load_baseline(path: Path) -> tuple[set[str], int | None]:
    """(waived keys, recorded rule-2 advisory count).

    Keys are one per line, as `<path>::<matched expression source>`. Blank lines and `#` comments are
    ignored -- the reason and the issue reference for each waived line live in those comments, so a reviewer
    reads WHY beside WHAT. One comment is also a directive: `# rule2-advisory-count: <n>`.
    """
    if not path.is_file():
        return set(), None
    keys: set[str] = set()
    rule2_count: int | None = None
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            body = stripped.lstrip("#").strip()
            if body.startswith(RULE2_COUNT_DIRECTIVE):
                raw = body[len(RULE2_COUNT_DIRECTIVE):].strip()
                if raw.isdigit():
                    rule2_count = int(raw)
            continue
        keys.add(stripped)
    return keys, rule2_count


def _python_files(root: Path, prefixes: tuple[str, ...]) -> list[tuple[str, Path]]:
    """Tracked `.py` files under any of `prefixes`, as (repo-relative posix path, absolute path).

    Git's own tracked-file list, like `check_no_private_data.py`: a gitignored local scratch file is
    invisible to this guard by construction, exactly like every other tracked-file check here.
    """
    listed = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.split()
    out: list[tuple[str, Path]] = []
    for rel in sorted(listed):
        if not rel.startswith(prefixes):
            continue
        if Path(rel).name in EXEMPT_NAMES or FIXTURE_DIR_NAME in Path(rel).parts:
            continue
        out.append((rel, root / rel))
    return out


def scan(root: Path) -> tuple[list[Finding], list[Finding]]:
    """(hard, advisory) findings over the whole tracked tree, before any waiver is applied."""
    hard: list[Finding] = []
    advisory: list[Finding] = []
    for rel, path in _python_files(root, RULE1_PATHS + SCORER_PATHS):
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        lines = text.splitlines()
        try:
            tree = ast.parse(text, filename=rel)
        except SyntaxError as e:
            # A file that does not parse is a file that was NOT CHECKED, which is this guard's own version
            # of the bug it looks for -- so it is reported, never skipped. (Skipping it silently is not a
            # hypothetical: the first draft of this guard did, and a fixture with an unterminated docstring
            # made `test_the_correct_patterns_produce_no_hard_findings` pass vacuously.) Zero tracked `.py`
            # files in this repo are unparseable, so this cannot fire spuriously on landing.
            hard.append(
                Finding(
                    rule="parse",
                    path=rel,
                    lineno=e.lineno or 0,
                    snippet="<unparseable>",
                    message=(
                        f"could not be parsed, so NOTHING in it was checked for fail-open defaults: {e.msg} "
                        f"(line {e.lineno}). An unchecked file is not a clean file. NOTE: this guard parses "
                        f"with the interpreter running it, currently Python "
                        f"{sys.version_info.major}.{sys.version_info.minor} -- if that is older than the "
                        f"project's own (3.13), valid modern syntax reads as a syntax error here. Run it "
                        f"the way CI does: `uv run python scripts/check_fail_open_defaults.py`."
                    ),
                )
            )
            continue
        in_scorer = rel.startswith(SCORER_PATHS)
        in_rule1 = rel.startswith(RULE1_PATHS)
        visitor = _Rule1And3Visitor(rel, lines, in_scorer_path=in_scorer, in_rule1_path=in_rule1)
        visitor.visit(tree)
        hard.extend(f for f in visitor.findings if not _waived_inline(lines, f.lineno))
        if in_scorer:
            advisory.extend(f for f in _rule2_advisory(rel, lines) if not _waived_inline(lines, f.lineno))
    return hard, advisory


@dataclass(frozen=True)
class Report:
    """`violations` is the only thing that fails the build: hard (Rule 1 / Rule 3) findings that are neither
    inline-waived nor baselined. `advisories` (Rule 2) and `stale_baseline` never affect the exit code --
    stale entries are reported so the baseline SHRINKS as the findings are fixed, rather than quietly
    outliving them."""

    violations: list[str]
    advisories: list[str]
    stale_baseline: list[str]
    rule2_recorded: int | None

    @property
    def ok(self) -> bool:
        return not self.violations


def check(root: Path, baseline_path: Path | None = None) -> Report:
    baseline_file = baseline_path if baseline_path is not None else root / DEFAULT_BASELINE
    baseline, rule2_recorded = load_baseline(baseline_file)
    hard, advisory = scan(root)
    seen = {f.key for f in hard}
    return Report(
        violations=[f.render() for f in hard if f.key not in baseline],
        advisories=[f.render() for f in advisory],
        stale_baseline=sorted(baseline - seen),
        rule2_recorded=rule2_recorded,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    ap.add_argument("--baseline", default=None, help=f"default: <root>/{DEFAULT_BASELINE}")
    ap.add_argument(
        "--advisory-detail", action="store_true",
        help="print every rule2 advisory line instead of just the count (never changes the exit code)",
    )
    args = ap.parse_args()
    root = Path(args.root).resolve()
    baseline = Path(args.baseline).resolve() if args.baseline else None
    report = check(root, baseline)

    # Rule 2: summary by default. See RULE2_COUNT_DIRECTIVE for why this is not printed line by line.
    n_adv = len(report.advisories)
    if args.advisory_detail:
        for a in report.advisories:
            print(a)
    print(f"advisory (rule2, never fails the build): {n_adv} `or <falsy>` coalescing site(s) in scorer paths")
    if report.rule2_recorded is not None and n_adv > report.rule2_recorded:
        print(
            f"advisory: that is up from the {report.rule2_recorded} recorded in the baseline. Worth a look "
            f"at the new ones (`--advisory-detail`), but this is NOT a failure -- ~30% of these are correct "
            f"zero-when-absent counts even inside a scorer."
        )

    for s in report.stale_baseline:
        print(f"baseline: no longer matches anything, remove this line: {s}")

    for v in report.violations:
        print(v)
    if report.violations:
        print(
            f"\nFAIL: {len(report.violations)} fail-open default(s) not waived. Each one is a place where an "
            f"item that could NOT be scored is given a number that reads as a measurement. Fix it (None, "
            f"exclusion-with-a-count, or raise), or -- if the default is genuinely correct -- waive the line "
            f"with `# {WAIVER_MARKER} <reason>` so the decision is visible to the next reader."
        )
        return 1
    print("OK: no unwaived fail-open defaults in decision-bearing keys or scorer statistics.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
