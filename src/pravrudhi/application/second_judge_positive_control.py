"""Issue #44 (standing second-judge positive control) + Lead-2's 2026-09-26 dry-run-driven spec
clarification: a PRESENT-and-WRONG second judge is not caught by availability monitoring alone -- the
endpoint answers, so it reads as healthy even if silently miscalibrated or serving the wrong weights.

Three checks against two fixed, sealed control sets (research/gates/P2b/configC/second_judge_positive_
control/ in prabhasa-nyaya: `established_200.json`, `ne_discrimination_71.json`, each element carrying a
`sealed_reference_p` captured from the sealed production run):

1. PARITY (serving integrity, gates): the LIVE second judge's own tau-decisions on all 271 elements must
   agree with the SEALED reference decisions on >=`parity_floor`, AND median(|p_live-p_sealed|) must be
   <=`parity_median_abs_dp`. Answers "is this the same calibrated model that was sealed", not "is the model
   good at law" -- trivially 100%/0 if scored against itself (a dry-run), only meaningful against a live
   endpoint.
2. NE DISCRIMINATION vs gold (safety, gates): p_live<`ne_discrimination_tau` on >=`ne_discrimination_min` of
   the 71 NE elements. UNCHANGED from the original spec -- one more fooled element fails this closed, the
   safe direction.
3. Established accuracy vs gold (recorded, NEVER gates): count with p_live>=0.5 and count with
   p_live>=ne_discrimination_tau, out of 200. A miss here fails safe (not_confirmed -> REFER, a coverage
   loss, never a false proof) -- alerted (not failed) if it drops more than `est_accuracy_alert_drop`
   percentage points below the sealed baseline on the same 200.

Below the parity floor OR the NE discrimination floor (including when the live endpoint could not be
reached/timed out at all -- `EndpointUnavailable`): the second judge is treated as unavailable, the SAME
fail-closed-to-REFER path `AndGateJudge` already uses for an absent/errored endpoint -- no new decision
logic in the judge itself. This module only decides `available: bool`; wiring that decision to the deployed
second_judge config is an operational action, not code here.

TRIGGER WIRING (Lead-2, 2026-09-26; OFF BY DEFAULT since 5 Oct -- the gate is active only when
`second_judge_positive_control.record_path` is set, so an engine with no record keeps serving): when on,
the engine refuses to let a real second-judge call happen at all unless
a RECORD of a passing live check exists, is younger than `second_judge_positive_control.max_age_hours`, and
names the CURRENT `second_judge.endpoint_id` / `second_judge.adapter_sha` -- no record, a stale record, or a
mismatched record all fail closed the same way, closing the loop without depending on anyone remembering to
run the preflight CLI. `write_record`/`read_record`/`check_record` are the pure functions; `RecordGatedJudge`
is the `Judge` wrapper `nyaya_agent._build_judge` puts around the real second judge -- it raises
`RecordCheckFailed` instead of ever calling the real endpoint when the record doesn't clear, and
`AndGateJudge`'s existing generic except-Exception handling turns that into `second_judge_unavailable` for
every element, exactly as if the endpoint itself had errored.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import statistics
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import yaml


class EndpointUnavailable(Exception):
    """The live second-judge endpoint could not be scored at all (connection error, timeout, malformed
    reply) -- distinct from a reachable-but-miscalibrated endpoint (which fails PARITY/NE instead). Both
    outcomes lead to the SAME fail-closed decision; this exception exists so the caller can tell them apart
    for logging/alerting without changing the availability verdict."""


class PrivateControlDataUnavailable(RuntimeError):
    """pravrudhi is PUBLIC. The sealed control sets (item ids, sealed reference p values) and the v1
    eval_items.jsonl (scenario/statute text) are private -- they live only in prabhasa-nyaya, never
    committed here. Raised by `resolve_private_root` when the private path isn't configured or doesn't
    contain what's expected; the CLI refuses to run rather than silently skipping the check or falling back
    to some public substitute (there is no safe public substitute)."""


def resolve_private_root(env: Mapping[str, str] | None = None) -> Path:
    """Reads `PRABHASA_NYAYA_ROOT` (or the given `env` mapping, for tests) and verifies the private control
    data actually exists there before returning it. Never returns a path that doesn't have the expected
    files -- the caller can trust the result without re-checking."""
    env = env if env is not None else os.environ
    raw = env.get("PRABHASA_NYAYA_ROOT")
    if not raw:
        raise PrivateControlDataUnavailable(
            "PRABHASA_NYAYA_ROOT is not set. The sealed control sets and eval data are private "
            "(prabhasa-nyaya) and are never committed to this public repo -- refusing to run."
        )
    root = Path(raw)
    configc = root / "research" / "gates" / "P2b" / "configC" / "second_judge_positive_control"
    required = [
        configc / "established_200.json",
        configc / "ne_discrimination_71.json",
        root / "research" / "gates" / "P2b" / "element_judgment_v1" / "eval_items.jsonl",
    ]
    missing = [str(p) for p in required if not p.is_file()]
    if missing:
        raise PrivateControlDataUnavailable(
            f"PRABHASA_NYAYA_ROOT={raw!r} is set but is missing required file(s): {missing} -- "
            "refusing to run."
        )
    return root


@dataclass(frozen=True)
class ControlElement:
    item_id: str
    element_id: str
    sealed_reference_p: float
    gold_status: str  # "established" or "not_established"


# ---------------------------------------------------------------------------------------------------
# Issue #100: count-and-digest verification of the sealed control sets, at load time.
#
# The two fail-opens #100 verified on main were (1) the established half could be truncated to 1 element
# or emptied entirely and the verdict stayed `available=True`, because parity is a RATE over
# `established + ne` and `decide_availability` never read `result.established` at all, and (2) the CLI
# exited 0 when nothing was evaluated. The rule this block enforces is a COUNT IDENTITY against a number
# that came from an artefact this run did not produce, keyed by that artefact's sha256 -- never
# `len(whatever_was_loaded)`, which a truncated input satisfies by construction.
#
# The pins live in `configs/sealed_control_manifest.yaml`. Pinned 2026-09-27 (computed by Lead-2 from
# prabhasa-nyaya add40e6, git objects, off-repo) after being unpinned since this file's introduction --
# see that file's header for why no digest existed here before then, and who filled it in. An unset pin
# RAISES here: it is not a warning, not a skip, and not a "pinning disabled" mode. The control refuses to
# run until it is pinned, which is the safe direction -- see the manifest header for the full history.
# ---------------------------------------------------------------------------------------------------

#: The literal, obviously-not-a-digest value a manifest field carries before anyone has pinned it. Chosen so
#: it can never be confused with a real value: every pinned digest must match `_SHA256_RE`, and every pinned
#: count must be a positive int, so the sentinel fails both by shape as well as by name.
SEALED_PIN_UNSET = "UNPINNED"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

#: How `n_rows` is counted for each supported file shape. Part of the pin: a sealed file whose shape changed
#: is a different file, and must not be counted under the old rule.
SEALED_SET_KINDS = frozenset({"json_ids", "jsonl"})

DEFAULT_SEALED_MANIFEST = Path("configs") / "sealed_control_manifest.yaml"


class SealedControlVerificationError(RuntimeError):
    """A sealed control set could not be VERIFIED -- as distinct from `PrivateControlDataUnavailable`, which
    means it could not be FOUND. Raised for an unset or malformed pin, a digest mismatch, a row-count
    mismatch, an unreadable or unparseable sealed file, and a manifest that does not cover a file the run
    needs. Every one of those is a refusal: there is no code path in this module that downgrades any of them
    to a warning, a skip, or a smaller sample."""


class SealedPinUnset(SealedControlVerificationError):
    """The manifest carries the `SEALED_PIN_UNSET` sentinel where a digest or a row count belongs, so there
    is nothing to verify against. Its own class so a caller can tell "nobody has pinned this yet" from "the
    pin exists and the file does not match it" -- both refuse, and neither is ever a pass."""


@dataclass(frozen=True)
class SealedSetPin:
    """One manifest entry: the expected shape of a sealed file the run did not produce.

    Constructing one validates it, so a `SealedSetPin` in hand is always a usable pin -- there is no
    half-valid instance and no `is_valid` flag for a later code path to forget to consult."""

    name: str
    relative_path: str
    kind: str
    n_rows: int
    sha256: str

    def __post_init__(self) -> None:
        if not self.name or not self.relative_path:
            raise SealedControlVerificationError(
                f"manifest entry {self.name!r} is missing a name or relative_path"
            )
        if self.kind not in SEALED_SET_KINDS:
            raise SealedControlVerificationError(
                f"manifest entry {self.name!r} has kind {self.kind!r}, expected one of "
                f"{sorted(SEALED_SET_KINDS)}"
            )
        if not _SHA256_RE.match(self.sha256):
            raise SealedControlVerificationError(
                f"manifest entry {self.name!r} has sha256 {self.sha256!r}, which is not a lowercase "
                "64-hex sha256 -- refusing to run against an unverifiable pin"
            )
        if not isinstance(self.n_rows, int) or isinstance(self.n_rows, bool) or self.n_rows <= 0:
            raise SealedControlVerificationError(
                f"manifest entry {self.name!r} has n_rows {self.n_rows!r}; a pinned row count must be a "
                "positive int (a pin of 0 would make an empty file satisfy its own expectation)"
            )


@dataclass(frozen=True)
class PinnedCounts:
    """The planned n for each half, read off the verified manifest -- NOT off the elements that happened to
    load. `decide_availability` requires this and has no default for it: a default would be exactly the
    fail-open this fixes, silently reappearing the first time a new caller forgets the argument.

    Refuses a non-positive count at construction, so "expected zero elements" is not expressible."""

    established: int
    ne: int
    established_sha256: str
    ne_sha256: str

    def __post_init__(self) -> None:
        for label, value in (("established", self.established), ("ne", self.ne)):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise SealedControlVerificationError(
                    f"pinned {label} count is {value!r}; must be a positive int -- a planned n of 0 would "
                    "make a run that evaluated nothing report itself complete"
                )
        for label, digest in (("established", self.established_sha256), ("ne", self.ne_sha256)):
            if not _SHA256_RE.match(digest):
                raise SealedControlVerificationError(
                    f"pinned {label} sha256 is {digest!r}; the count is only meaningful keyed by the "
                    "digest of the artefact it was read from"
                )

    @property
    def total(self) -> int:
        """The number of elements parity is scored over -- `established + ne`, both pinned."""
        return self.established + self.ne


def parse_sealed_manifest(text: str) -> dict[str, SealedSetPin]:
    """Parses `configs/sealed_control_manifest.yaml`'s text into validated pins, keyed by `name`.

    Raises `SealedPinUnset` naming EVERY unpinned entry at once (so whoever fills them in sees the whole
    list, not the first one), and `SealedControlVerificationError` for a malformed manifest. Never returns a
    partial mapping: either every entry is a usable pin or nothing comes back."""
    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise SealedControlVerificationError(
            "sealed control manifest is empty or is not a YAML mapping -- refusing to run"
        )
    if "sealed_sets" not in loaded:
        raise SealedControlVerificationError(
            "sealed control manifest has no `sealed_sets` key -- refusing to run"
        )
    raw_sets = loaded["sealed_sets"]
    if not isinstance(raw_sets, list) or not raw_sets:
        raise SealedControlVerificationError(
            "sealed control manifest's `sealed_sets` is empty or not a list; an empty manifest would "
            "verify nothing while looking like a manifest -- refusing to run"
        )

    unset: list[str] = []
    pins: dict[str, SealedSetPin] = {}
    for index, raw in enumerate(raw_sets):
        if not isinstance(raw, dict):
            raise SealedControlVerificationError(
                f"sealed control manifest entry {index} is not a mapping: {raw!r}"
            )
        for required in ("name", "relative_path", "kind", "n_rows", "sha256"):
            # Explicit key presence, never `raw.get(key, <falsy>)`: a defaulted digest or count is the
            # fail-open class this whole change exists to remove.
            if required not in raw:
                raise SealedControlVerificationError(
                    f"sealed control manifest entry {index} is missing the {required!r} field"
                )
        name = str(raw["name"])
        entry_unset = [
            field_name
            for field_name in ("n_rows", "sha256")
            if isinstance(raw[field_name], str) and raw[field_name] == SEALED_PIN_UNSET
        ]
        if entry_unset:
            unset.append(f"{name} ({', '.join(entry_unset)})")
            continue
        pin = SealedSetPin(
            name=name,
            relative_path=str(raw["relative_path"]),
            kind=str(raw["kind"]),
            n_rows=raw["n_rows"],
            sha256=str(raw["sha256"]),
        )
        if pin.name in pins:
            raise SealedControlVerificationError(
                f"sealed control manifest names {pin.name!r} twice; a duplicate entry makes which pin is "
                "enforced depend on ordering"
            )
        pins[pin.name] = pin

    if unset:
        raise SealedPinUnset(
            f"sealed control manifest still carries the {SEALED_PIN_UNSET!r} sentinel for: "
            f"{'; '.join(unset)}. The second-judge positive control REFUSES TO RUN until every sealed set "
            "is pinned by row count and sha256 -- an unpinned control cannot tell a complete sealed set "
            "from a truncated one, which is the defect issue #100 reports. Whoever holds the sealed sets "
            "fills these in; see the manifest header."
        )
    return pins


def load_sealed_manifest(repo_root: Path) -> dict[str, SealedSetPin]:
    """`parse_sealed_manifest` over `<repo_root>/configs/sealed_control_manifest.yaml`. A missing manifest
    raises rather than disabling verification."""
    path = repo_root / DEFAULT_SEALED_MANIFEST
    if not path.is_file():
        raise SealedControlVerificationError(
            f"sealed control manifest {path} is missing. It pins the row count and sha256 of every sealed "
            "control set; without it there is nothing to verify the sealed sets against -- refusing to run."
        )
    return parse_sealed_manifest(path.read_text())


def count_sealed_rows(kind: str, raw_bytes: bytes) -> int:
    """The row count of a sealed file, counted the way its `kind` says. Raises on anything it cannot parse
    -- an unparseable sealed file yields an exception, never a count of 0 that would then be compared
    against a pin."""
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as e:
        raise SealedControlVerificationError(f"sealed file is not valid UTF-8: {e}") from e
    if kind == "json_ids":
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as e:
            raise SealedControlVerificationError(f"sealed file is not parseable JSON: {e}") from e
        if not isinstance(parsed, dict) or "ids" not in parsed:
            raise SealedControlVerificationError(
                "sealed file has no top-level `ids` list -- refusing to treat an unrecognised shape as "
                "zero rows"
            )
        ids = parsed["ids"]
        if not isinstance(ids, list):
            raise SealedControlVerificationError(f"sealed file's `ids` is not a list: {type(ids).__name__}")
        return len(ids)
    if kind == "jsonl":
        n = 0
        for lineno, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                json.loads(line)
            except json.JSONDecodeError as e:
                raise SealedControlVerificationError(
                    f"sealed file line {lineno} is not parseable JSON: {e}"
                ) from e
            n += 1
        return n
    raise SealedControlVerificationError(f"unsupported sealed set kind {kind!r}")


def verify_sealed_file(path: Path, pin: SealedSetPin) -> int:
    """Verifies one sealed file against its pin and returns its VERIFIED row count.

    Both the digest and the count are checked. The digest alone would subsume the count (a truncated file
    cannot rehash to the pinned value), but the returned count is what `decide_availability`'s count
    identity is measured against, so it is asserted here in its own right rather than inferred.

    Raises `SealedControlVerificationError` on an unreadable file, a digest mismatch, a count mismatch, or
    a count of zero. There is no return value that means "could not check"."""
    try:
        raw_bytes = path.read_bytes()
    except OSError as e:
        raise SealedControlVerificationError(
            f"sealed set {pin.name!r} at {path} could not be read: {e}"
        ) from e
    digest = hashlib.sha256(raw_bytes).hexdigest()
    if digest != pin.sha256:
        raise SealedControlVerificationError(
            f"sealed set {pin.name!r} at {path} has sha256 {digest}, pinned {pin.sha256} -- refusing to "
            "run against a sealed set that is not the one that was pinned"
        )
    n_rows = count_sealed_rows(pin.kind, raw_bytes)
    if n_rows == 0:
        raise SealedControlVerificationError(
            f"sealed set {pin.name!r} at {path} has zero rows; an empty sealed set can only produce a "
            "verdict over nothing -- refusing to run"
        )
    if n_rows != pin.n_rows:
        raise SealedControlVerificationError(
            f"sealed set {pin.name!r} at {path} has {n_rows} rows, pinned {pin.n_rows} -- truncated, "
            "extended, or the wrong file; refusing to run"
        )
    return n_rows


@dataclass(frozen=True)
class VerifiedControlSets:
    """The elements to score, together with the planned n for each half and the digest each n was keyed to.

    The only way to obtain a `PinnedCounts` in this repo is `load_verified_control_sets`, which produces it
    from the manifest AFTER verifying both files -- and `decide_availability` cannot be called without one.
    That is the structural reason the count identity cannot be bypassed: not a flag someone remembers to
    check, but the absence of any other route to the number."""

    established: list[ControlElement]
    ne: list[ControlElement]
    pinned: PinnedCounts


def _to_elements(name: str, raw_bytes: bytes) -> list[ControlElement]:
    """Parses a verified `json_ids` sealed set into elements. Every field is read by subscript, so a row
    missing `sealed_reference_p` or `gold_status` raises instead of defaulting to a score of 0.0 or an
    empty gold label."""
    parsed = json.loads(raw_bytes.decode("utf-8"))
    elements: list[ControlElement] = []
    for index, row in enumerate(parsed["ids"]):
        if not isinstance(row, dict):
            raise SealedControlVerificationError(f"{name} row {index} is not a mapping")
        for required in ("item_id", "element_id", "sealed_reference_p", "gold_status"):
            if required not in row:
                raise SealedControlVerificationError(
                    f"{name} row {index} is missing {required!r} -- refusing to score an element whose "
                    "sealed reference or gold label is absent"
                )
        elements.append(
            ControlElement(
                str(row["item_id"]),
                str(row["element_id"]),
                float(row["sealed_reference_p"]),
                str(row["gold_status"]),
            )
        )
    return elements


def load_verified_control_sets(
    nyaya_root: Path, manifest: Mapping[str, SealedSetPin]
) -> VerifiedControlSets:
    """Loads both sealed control sets, verifying each against its pin FIRST, and returns them with the
    pinned counts. The single entry point for getting control elements: there is no unverified loader left
    for a caller to reach for.

    `eval_items.jsonl` is verified here too -- it is the file `build_score_fn` looks each element's request
    content up in, so a truncated copy of it makes elements unscoreable, which #100's non-blocking item 4
    showed surfaces as a spurious "endpoint outage"."""
    required = ("established_200", "ne_discrimination_71", "eval_items_v1")
    absent = [name for name in required if name not in manifest]
    if absent:
        raise SealedControlVerificationError(
            f"sealed control manifest does not cover {absent} -- every sealed set the run reads must be "
            "pinned; refusing to run with an unverified input"
        )

    verified_n: dict[str, int] = {}
    for name in required:
        pin = manifest[name]
        verified_n[name] = verify_sealed_file(nyaya_root / pin.relative_path, pin)

    established_pin = manifest["established_200"]
    ne_pin = manifest["ne_discrimination_71"]
    established = _to_elements(
        established_pin.name, (nyaya_root / established_pin.relative_path).read_bytes()
    )
    ne = _to_elements(ne_pin.name, (nyaya_root / ne_pin.relative_path).read_bytes())

    # The elements actually parsed must match the row count just verified against the digest. A shape where
    # `ids` has 200 entries but only 150 parse into elements would otherwise pass the digest check and then
    # quietly score a smaller set.
    for label, elements, name in (
        ("established", established, "established_200"),
        ("ne", ne, "ne_discrimination_71"),
    ):
        if len(elements) != verified_n[name]:
            raise SealedControlVerificationError(
                f"{label} half parsed {len(elements)} elements from a file verified at "
                f"{verified_n[name]} rows -- refusing to score a set that shrank between verification "
                "and parsing"
            )

    return VerifiedControlSets(
        established=established,
        ne=ne,
        pinned=PinnedCounts(
            established=verified_n["established_200"],
            ne=verified_n["ne_discrimination_71"],
            established_sha256=established_pin.sha256,
            ne_sha256=ne_pin.sha256,
        ),
    )


class ScoreFn(Protocol):
    """Scores one control element live, production prompt/template. Raises EndpointUnavailable (or lets a
    lower-level exception propagate -- callers should catch broadly) on any failure to get a real score."""

    def __call__(self, element: ControlElement) -> float: ...


@dataclass(frozen=True)
class ParityResult:
    n_total: int
    n_tau_agree: int
    median_abs_dp: float | None  # None iff n_total == 0 -- a median of no differences is undefined, not 0

    @property
    def agree_rate(self) -> float | None:
        """None iff n_total == 0 -- an agreement rate over zero elements is undefined, not a measured 0%."""
        return self.n_tau_agree / self.n_total if self.n_total else None


@dataclass(frozen=True)
class NEResult:
    n_total: int
    n_correct: int


@dataclass(frozen=True)
class EstablishedResult:
    n_total: int
    n_p_ge_half: int
    n_p_ge_tau: int

    @property
    def accuracy(self) -> float | None:
        """None iff n_total == 0 -- an accuracy over zero elements is undefined, not a measured 0%."""
        return self.n_p_ge_half / self.n_total if self.n_total else None


@dataclass(frozen=True)
class ControlCheckResult:
    parity: ParityResult | None  # None iff the endpoint was unavailable (no live scores at all)
    ne: NEResult | None
    established: EstablishedResult | None
    endpoint_unavailable: bool = False
    endpoint_error: str | None = None


@dataclass(frozen=True)
class Verdict:
    available: bool
    reasons: list[str] = field(default_factory=list)


def compute_parity(elements: list[ControlElement], live_scores: dict[str, float], tau: float) -> ParityResult:
    n_agree = 0
    abs_dps = []
    for el in elements:
        key = f"{el.item_id}__{el.element_id}"
        p_live = live_scores[key]
        p_sealed = el.sealed_reference_p
        if (p_live >= tau) == (p_sealed >= tau):
            n_agree += 1
        abs_dps.append(abs(p_live - p_sealed))
    return ParityResult(n_total=len(elements), n_tau_agree=n_agree,
                         median_abs_dp=statistics.median(abs_dps) if abs_dps else None)


def compute_ne_discrimination(ne_elements: list[ControlElement], live_scores: dict[str, float], tau: float) -> NEResult:
    n_correct = 0
    for el in ne_elements:
        key = f"{el.item_id}__{el.element_id}"
        if live_scores[key] < tau:
            n_correct += 1
    return NEResult(n_total=len(ne_elements), n_correct=n_correct)


def compute_established_accuracy(
    established_elements: list[ControlElement], live_scores: dict[str, float], tau: float
) -> EstablishedResult:
    n_ge_half = n_ge_tau = 0
    for el in established_elements:
        key = f"{el.item_id}__{el.element_id}"
        p = live_scores[key]
        if p >= 0.5:
            n_ge_half += 1
        if p >= tau:
            n_ge_tau += 1
    return EstablishedResult(n_total=len(established_elements), n_p_ge_half=n_ge_half, n_p_ge_tau=n_ge_tau)


def decide_availability(
    result: ControlCheckResult, *, parity_floor: float, parity_median_abs_dp: float, ne_discrimination_min: int,
    pinned: PinnedCounts,
) -> Verdict:
    """Issue #100: `pinned` is REQUIRED and has no default. It carries the planned n for each half, read off
    `configs/sealed_control_manifest.yaml` and keyed by each sealed file's sha256 -- an artefact this run did
    not produce. The completeness test below is a COUNT IDENTITY against those numbers, not against
    `len(whatever_loaded)`: a truncated established half computes a smaller "expected" total under the
    latter and reports itself complete, which is exactly what #100 reproduced (200 -> 1 -> 0 elements, all
    three `available=True`, `reasons=[]`).

    A default for `pinned` would reintroduce that hole the first time a new caller omitted it, so there is
    none, and `PinnedCounts` itself refuses a non-positive count."""
    if (
        result.endpoint_unavailable
        or result.parity is None
        or result.ne is None
        or result.established is None
    ):
        return Verdict(available=False, reasons=[f"endpoint_unavailable: {result.endpoint_error}"])
    reasons = []
    # COUNT IDENTITY (issue #100, finding 1). Checked before any rate is looked at, and on the ESTABLISHED
    # half as well -- `decide_availability` previously never read `result.established` at all, so the
    # established set could go from 200 elements to zero with no change in the verdict. The NE half was
    # only ever protected by `ne_discrimination_min` happening to be an absolute count.
    if result.established.n_total != pinned.established:
        reasons.append(
            f"count_identity(established): {result.established.n_total} elements evaluated != "
            f"{pinned.established} planned (pinned by sha256 {pinned.established_sha256})"
        )
    if result.ne.n_total != pinned.ne:
        reasons.append(
            f"count_identity(not_established): {result.ne.n_total} elements evaluated != {pinned.ne} "
            f"planned (pinned by sha256 {pinned.ne_sha256})"
        )
    if result.parity.n_total != pinned.total:
        reasons.append(
            f"count_identity(total_scored): {result.parity.n_total} elements scored != {pinned.total} "
            f"planned ({pinned.established} + {pinned.ne})"
        )
    # "Checked nothing" gets its own reason, distinct from "checked and clean" AND from a count shortfall:
    # a run that scored zero items is the state a positive control exists to make impossible to mistake for
    # a pass, so it is named rather than left to be inferred from a count mismatch.
    if result.parity.n_total == 0:
        reasons.append(
            "zero_items_evaluated: the control ran but scored no elements at all -- checked nothing, "
            "which is not checked-and-clean"
        )
    # An empty control set (n_total == 0) makes agree_rate/median_abs_dp None -- undefined, not a passing
    # measurement. Fail closed explicitly rather than comparing None to a float (which would raise).
    if result.parity.n_total == 0:
        reasons.append("parity: no elements scored (empty control set) -- cannot verify")
    else:
        # n_total > 0 here, so agree_rate/median_abs_dp are real floats, not None (see their own
        # docstrings) -- mypy can't narrow across the sibling n_total check, so assert it explicitly.
        assert result.parity.agree_rate is not None
        assert result.parity.median_abs_dp is not None
        if result.parity.agree_rate < parity_floor:
            reasons.append(f"parity {result.parity.n_tau_agree}/{result.parity.n_total} "
                            f"({result.parity.agree_rate:.4%}) < floor {parity_floor:.2%}")
        if result.parity.median_abs_dp > parity_median_abs_dp:
            reasons.append(f"parity median|dp| {result.parity.median_abs_dp:.4f} > {parity_median_abs_dp}")
    if result.ne.n_total == 0:
        reasons.append("ne_discrimination: no elements scored (empty control set) -- cannot verify")
    elif result.ne.n_correct < ne_discrimination_min:
        reasons.append(f"ne_discrimination {result.ne.n_correct}/{result.ne.n_total} "
                        f"< floor {ne_discrimination_min}/{result.ne.n_total}")
    return Verdict(available=not reasons, reasons=reasons)


def run_live_check(
    established: list[ControlElement], ne: list[ControlElement], *, score_fn: Callable[[ControlElement], float],
    parity_tau: float,
) -> ControlCheckResult:
    """Scores every element in both control sets via `score_fn` (the real implementation calls the live
    second-judge endpoint with the production prompt/template; tests inject a stub). ANY exception from
    `score_fn` -- connection error, timeout, malformed reply -- is caught here and turned into
    `endpoint_unavailable=True`, never a partial/silent result: a positive control that can't reach the
    endpoint at all must fail closed exactly like one that reaches it and finds it miscalibrated."""
    live_scores: dict[str, float] = {}
    try:
        for el in established + ne:
            live_scores[f"{el.item_id}__{el.element_id}"] = score_fn(el)
    except Exception as e:  # noqa: BLE001
        return ControlCheckResult(parity=None, ne=None, established=None, endpoint_unavailable=True,
                                   endpoint_error=f"{type(e).__name__}: {e}")

    parity = compute_parity(established + ne, live_scores, parity_tau)
    ne_result = compute_ne_discrimination(ne, live_scores, parity_tau)
    est_result = compute_established_accuracy(established, live_scores, parity_tau)
    return ControlCheckResult(parity=parity, ne=ne_result, established=est_result)


# -- trigger wiring: the record the engine gates on (Lead-2, 2026-09-26) ------------------------------------

class RecordCheckFailed(Exception):
    """No passing live-check record exists, or the one that does is too old or names a different endpoint/
    adapter than the one about to be used. Raised by `RecordGatedJudge` INSTEAD OF calling the real second
    judge -- `AndGateJudge`'s existing generic except-Exception handling converts this into
    `second_judge_unavailable` for every element, the same fail-closed path an absent/errored endpoint
    already uses."""


def write_record(
    path: Path, *, available: bool, endpoint_id: str | None, adapter_sha: str | None, reasons: list[str],
    timestamp: float | None = None,
) -> None:
    """Called by the live preflight CLI after every run -- the durable record `check_record` reads later.
    Written atomically (write to a temp file, then rename) so a reader never sees a half-written record."""
    record = {
        "timestamp": timestamp if timestamp is not None else time.time(),
        "available": available, "endpoint_id": endpoint_id, "adapter_sha": adapter_sha, "reasons": reasons,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(record, indent=2) + "\n")
    tmp.replace(path)


def read_record_checked(path: Path) -> tuple[dict[str, Any] | None, str]:
    """(record, reason). record is None whenever there is no usable record, and `reason` says WHY in words an audit reader
    can use: absent file, unreadable file, not UTF-8, not valid JSON, or JSON that is not an object. Never raises for a bad
    record."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None, "no record (file absent)"
    except OSError as e:
        return None, f"record unreadable ({type(e).__name__})"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, "record unreadable: not valid UTF-8"
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None, "record unreadable: not valid JSON"
    if not isinstance(parsed, dict):
        return None, "record unreadable: JSON is not an object"
    return parsed, "ok"


def read_record(path: Path) -> dict[str, Any] | None:
    """None for "no record" (file absent, or unreadable/malformed/non-UTF-8) -- a missing or corrupt record is exactly
    as fail-closed-worthy as a stale one, never an error that crashes the caller. See `read_record_checked` for the reason."""
    return read_record_checked(path)[0]


def valid_max_age_hours(value: Any) -> bool:
    """True only for a finite number > 0 (a bool, NaN, inf, zero or a negative window is not a usable max age)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def check_record(
    record: dict[str, Any] | None, *, max_age_hours: float, expected_endpoint_id: str | None,
    expected_adapter_sha: str | None, now: float | None = None,
) -> tuple[bool, str]:
    """(ok, reason). ok=False for: no record, a record whose own `available` was False, a record older than
    `max_age_hours`, or a record naming a different endpoint_id/adapter_sha -- all four are indistinguishable
    to the caller (RecordGatedJudge raises the same way for any of them), but the reason string says which."""
    if not valid_max_age_hours(max_age_hours):
        return False, (
            f"max_age_hours={max_age_hours!r} is not a finite number > 0: refusing (never an unbounded or zero window)"
        )
    if record is None:
        return False, "no record"
    # Explicit `is not True` rather than `.get("available", False)`: a missing/malformed "available" key
    # must never be silently treated the same as a real measured False -- both fail closed here, but the
    # reason string (and any future caller) can tell "no verdict recorded" apart from "verdict was failure".
    if record.get("available") is not True:
        return False, f"last check failed or malformed record: {record.get('reasons')}"
    if expected_endpoint_id is None or expected_adapter_sha is None:
        return False, (
            "second_judge.endpoint_id/adapter_sha not configured -- cannot verify record identity "
            "(never treat an unset expected identity as matching an unset recorded one)"
        )
    now = now if now is not None else time.time()
    ts = record.get("timestamp")
    if not isinstance(ts, (int, float)) or isinstance(ts, bool) or not math.isfinite(ts):
        return False, f"record timestamp {ts!r} is missing or not a finite number"
    age_hours = (now - ts) / 3600.0
    if age_hours < 0:
        return False, f"record is future-dated ({-age_hours:.2f}h ahead of now): invalid"
    if age_hours > max_age_hours:
        return False, f"record is {age_hours:.1f}h old, max_age_hours={max_age_hours}"
    if record.get("endpoint_id") != expected_endpoint_id:
        return False, f"record endpoint_id={record.get('endpoint_id')!r} != current {expected_endpoint_id!r}"
    if record.get("adapter_sha") != expected_adapter_sha:
        return False, f"record adapter_sha={record.get('adapter_sha')!r} != current {expected_adapter_sha!r}"
    return True, "ok"


class RecordGatedJudge:
    """Wraps a real second-judge `Judge`. Before EVERY call, checks the positive-control record -- if it
    isn't fresh and matching, raises `RecordCheckFailed` instead of ever reaching the real endpoint.
    `AndGateJudge` sees this exactly like a live endpoint error (its generic except-Exception path), so every
    element fails closed to REFER, never falls back to a primary-alone decision. No new decision logic in
    `AndGateJudge` itself -- the gate lives entirely in this wrapper."""

    def __init__(
        self, inner: Any, *, record_path: Path, max_age_hours: float, expected_endpoint_id: str | None,
        expected_adapter_sha: str | None,
    ) -> None:
        if not valid_max_age_hours(max_age_hours):
            raise ValueError(f"max_age_hours={max_age_hours!r} must be a finite number > 0")
        self.inner = inner
        self.record_path = record_path
        self.max_age_hours = max_age_hours
        self.expected_endpoint_id = expected_endpoint_id
        self.expected_adapter_sha = expected_adapter_sha
        self.name = getattr(inner, "name", "second")

    def judge(self, request: Any) -> Any:
        record, read_reason = read_record_checked(self.record_path)
        ok, reason = (False, read_reason) if record is None else check_record(
            record, max_age_hours=self.max_age_hours, expected_endpoint_id=self.expected_endpoint_id,
            expected_adapter_sha=self.expected_adapter_sha,
        )
        if not ok:
            raise RecordCheckFailed(reason)
        return self.inner.judge(request)
