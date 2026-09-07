"""A request reaches `verified` only when something outside the record itself checks the claim.

`requests.meet` already refuses to mark a criterion met without evidence, and `requests.advance` already refuses
`delivered` while any criterion is unmet. Neither checks that the evidence is real. A criterion can carry a commit
hash that was never made, a file path that was never written, or a command that used to pass and no longer does,
and today the record accepts all three because attaching evidence and the evidence being true have been treated as
the same act.

This module is the check. It borrows two ideas already proven elsewhere in this project rather than inventing a
third: a promise is kept by running something, not by reading what was written about it (`application/ralph.py`),
and a sandboxed reviewer works under a declared, read-only policy rather than a hand-assembled one
(`application/sandbox_policy.py`). `check_evidence` re-verifies every piece of evidence on one criterion.
`adversarial_review` dispatches one read-only agent whose only job is to find the reason a request's evidence does
not actually satisfy what was asked — and a review that says "satisfied" without saying why is treated as having
failed at that job, for the same reason a confident summary from a build agent is not trusted here: the whole
discipline exists because "it looks done" has reached the operator as a defect before. `gate` is the conjunction of
both plus an end-to-end command, and it is what `requests-advance <id> verified` must pass through.
"""

from __future__ import annotations

import ipaddress
import json
import re
import shlex
import socket
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from pravrudhi.application import ralph
from pravrudhi.application.delegate import TaskSpec
from pravrudhi.application.requests import Request, RequestError, get
from pravrudhi.application.sandbox_policy import apply_policy, policy_for

PACKAGED_CONFIG = Path(__file__).resolve().parents[1] / "assets" / "configs" / "completion.yaml"

RunCommandFn = Callable[[list[str], Path, int], "tuple[bool, str]"]
"""Takes a vetted argv, never a command string: nothing here reaches a shell."""
UrlCheckFn = Callable[[str, int], "tuple[bool, str]"]
DispatchFn = Callable[[TaskSpec], str]

# Words too common to say anything about which file a commit ought to touch.
_STOPWORDS = frozenset({
    "this", "that", "with", "from", "have", "which", "should", "would", "criterion", "must", "will", "when",
    "then", "there", "their", "about", "every", "each", "into", "onto", "under", "over", "make", "makes",
    "gate", "evidence", "request",
})

# An adversarial review that names one of these has found a reason the evidence does not hold, whatever else it
# says. Checked before the pass markers so "not satisfied" cannot be read as "satisfied".
_REVIEW_FAIL_MARKERS = (
    "does not satisfy", "not satisfied", "unsatisfied", "does not meet", "fails to", "is not met", "not met",
    "does not hold", "no evidence", "cannot verify", "insufficient", "does not actually",
)
# A review must affirmatively say the request holds, not merely fail to object to it.
_REVIEW_PASS_MARKERS = (
    "satisfied", "is met", "meets the criterion", "criteria are met", "no issue", "nothing wrong", "holds up",
)
# Below this many words, "satisfied" is an assertion rather than a finding — the same failure mode ralph.py's
# preamble names: a confident summary that does not show its work.
_MIN_REASONED_WORDS = 8


class CompletionError(RuntimeError):
    """Raised when the gate is asked to judge a request that cannot be judged at all."""


@dataclass(frozen=True)
class EvidenceResult:
    kind: str
    ref: str
    verified: bool
    reason: str


@dataclass(frozen=True)
class EvidenceCheck:
    request_id: str
    index: int
    results: list[EvidenceResult] = field(default_factory=list)

    @property
    def verified(self) -> bool:
        """Whether every piece of evidence on this criterion re-verified. No evidence is not verified evidence."""
        return bool(self.results) and all(r.verified for r in self.results)

    def unverified(self) -> list[EvidenceResult]:
        return [r for r in self.results if not r.verified]


@dataclass(frozen=True)
class ReviewResult:
    findings: str
    blocking: bool
    reason: str


@dataclass(frozen=True)
class GateResult:
    request_id: str
    passed: bool
    reason: str
    evidence: list[EvidenceCheck] = field(default_factory=list)
    review: ReviewResult | None = None
    e2e_passed: bool = False
    e2e_detail: str = ""


def load_completion_config(path: Path | None = None) -> dict[str, Any]:
    raw = yaml.safe_load((path or PACKAGED_CONFIG).read_text()) or {}
    return {
        "allowed_commands": tuple(str(x) for x in (raw.get("allowed_commands") or ())),
        "timeout_s": int(raw.get("timeout_s") or 300),
    }


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-zA-Z_]{4,}", text.lower()) if w not in _STOPWORDS}


def _verify_commit(root: Path, ref: str, criterion_text: str) -> tuple[bool, str]:
    exists = subprocess.run(
        ["git", "cat-file", "-e", f"{ref}^{{commit}}"], cwd=root, capture_output=True, text=True, timeout=30
    )
    if exists.returncode != 0:
        return False, f"no such commit {ref!r} in this repository"
    shown = subprocess.run(
        ["git", "show", "--name-only", "--pretty=format:", ref], cwd=root, capture_output=True, text=True, timeout=30
    )
    files = [f for f in shown.stdout.splitlines() if f.strip()]
    if not files:
        return False, f"commit {ref} touches no files"
    tokens = _tokens(criterion_text)
    if tokens and not any(any(t in f.lower() for t in tokens) for f in files):
        return False, f"commit {ref} touches {', '.join(files[:5])}, none of which look related to the criterion"
    return True, f"commit {ref} touches {', '.join(files[:5])}"


def _verify_file(root: Path, ref: str) -> tuple[bool, str]:
    # `~` is how an operator writes a path in a note, and an unexpanded tilde reported real evidence as missing.
    path = Path(ref).expanduser()
    if not path.is_absolute():
        path = Path(root) / ref
    if not path.exists():
        return False, f"no such file {ref}"
    size = path.stat().st_size
    if size == 0:
        return False, f"{ref} exists but is empty"
    return True, f"{ref} exists ({size} bytes)"


SHELL_METACHARACTERS = (";", "|", "&", "`", "$(", ">", "<", "\n", "\r")


def _split_cwd(command: str) -> tuple[str, str]:
    """Separate a leading `cd <dir> &&` from the command itself.

    Several genuine checks run in a subdirectory, and `cd app/desktop && node --test` is how a person records
    one. The directory is taken as data and joined to the workspace root; it never reaches a shell.
    """
    text = command.strip()
    if not text.startswith("cd "):
        return "", text
    head, sep, tail = text.partition("&&")
    if not sep:
        return "", text
    return head[3:].strip(), tail.strip()


def _allowed_argv(command: str, patterns: tuple[str, ...]) -> tuple[list[str], str] | None:
    """The argv to run and the directory to run it in, or None when nothing on the allow-list matches.

    This matched the whole command against shell globs and then ran it with `shell=True`. A pattern like
    `uv run pravrudhi *` also matches `uv run pravrudhi status; rm -rf ~`, so anything able to attach a piece of
    evidence could have the gate execute arbitrary shell for it later, outside the sandbox its writer ran in.
    Evidence is written by agents, which makes that an escalation path rather than a theoretical one.

    So the command is tokenised, any shell metacharacter disqualifies it outright, and it must match an
    allow-list entry token for token as a prefix. What comes back is an argv run without a shell.
    """
    cwd, rest = _split_cwd(command)
    if any(meta in rest for meta in SHELL_METACHARACTERS):
        return None
    if cwd and (Path(cwd).is_absolute() or ".." in Path(cwd).parts):
        return None
    try:
        argv = shlex.split(rest)
    except ValueError:
        return None
    if not argv:
        return None
    for pattern in patterns:
        pattern_cwd, pattern_rest = _split_cwd(pattern)
        if pattern_cwd != cwd:
            continue
        try:
            wanted = shlex.split(pattern_rest.rstrip("*").strip())
        except ValueError:
            continue
        if wanted and argv[: len(wanted)] == wanted:
            return argv, cwd
    return None


def _default_run_command(argv: list[str], cwd: Path, timeout_s: int) -> tuple[bool, str]:
    """Run a vetted argv with no shell, so nothing in the evidence string can be read as syntax."""
    try:
        result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout_s)
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, f"the command did not run: {e}"
    tail = "\n".join((result.stdout + result.stderr).strip().splitlines()[-8:])[:800]
    return result.returncode == 0, tail


def _verify_command(
    root: Path, ref: str, config: dict[str, Any], run_command: RunCommandFn | None
) -> tuple[bool, str]:
    match = _allowed_argv(ref, config["allowed_commands"])
    if match is None:
        return False, f"{ref!r} is not on the completion command allow-list, or carries shell syntax"
    argv, cwd = match
    where = Path(root) / cwd if cwd else Path(root)
    if not where.is_dir():
        return False, f"{ref!r} names a directory that does not exist: {cwd}"
    runner = run_command or _default_run_command
    ok, detail = runner(argv, where, config["timeout_s"])
    return ok, (detail or f"`{ref}` re-ran and " + ("passed" if ok else "failed"))


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect is a second request to an address nobody vetted, so it is refused rather than followed."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        raise urllib.error.HTTPError(newurl, code, f"redirect to {newurl} refused", headers, fp)


def _public_address(host: str) -> tuple[bool, str]:
    """Whether a hostname resolves only to addresses outside this machine and this network.

    Evidence is written by agents. A URL naming the loopback interface, a private range, or the cloud metadata
    address would have the gate fetch, on the operator's behalf, something its writer could not reach itself.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as e:
        return False, f"{host} does not resolve: {e}"
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False, f"{host} resolved to something that is not an address"
        if (address.is_loopback or address.is_private or address.is_link_local
                or address.is_reserved or address.is_multicast or address.is_unspecified):
            return False, f"{host} resolves to the non-public address {address}"
    return True, ""


def _default_url_check(url: str, timeout_s: int) -> tuple[bool, str]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, f"{url} is not an http or https address"
    if not parsed.hostname:
        return False, f"{url} names no host"
    public, why = _public_address(parsed.hostname)
    if not public:
        return False, why
    opener = urllib.request.build_opener(_NoRedirects)
    req = urllib.request.Request(url, method="GET")
    try:
        with opener.open(req, timeout=timeout_s) as resp:
            status = int(getattr(resp, "status", 200))
    except urllib.error.HTTPError as e:
        return e.code < 400, f"{url} responded {e.code}"
    except Exception as e:  # noqa: BLE001 (an unreachable URL is a failed check, not a crash)
        return False, f"{url} unreachable: {e}"
    return status < 400, f"{url} responded {status}"


def _verify_url(url: str, timeout_s: int, url_check: UrlCheckFn | None) -> tuple[bool, str]:
    checker = url_check or _default_url_check
    try:
        return checker(url, timeout_s)
    except Exception as e:  # noqa: BLE001 (an unreachable URL is a failed check, not a crash)
        return False, f"could not reach {url}: {e}"


def _verify_ledger_seq(root: Path, ref: str) -> tuple[bool, str]:
    ledger = Path(root) / "research" / "ledger.jsonl"
    if not ledger.exists():
        return False, "no ledger at research/ledger.jsonl"
    try:
        wanted = int(ref)
    except ValueError:
        return False, f"{ref!r} is not a ledger sequence number"
    for line in ledger.read_text().splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("seq") == wanted:
            return True, f"ledger seq {wanted} found"
    return False, f"no ledger row with seq {wanted}"


def check_evidence(
    root: Path,
    request_id: str,
    index: int,
    *,
    config_path: Path | None = None,
    run_command: RunCommandFn | None = None,
    url_check: UrlCheckFn | None = None,
) -> EvidenceCheck:
    """Re-verify every piece of evidence on one criterion. Nothing here mutates the request: a check that cannot
    confirm a piece of evidence reports it `unverified`, with the reason, rather than dropping or accepting it."""
    root = Path(root)
    req = get(root, request_id)
    if req is None:
        raise RequestError(f"no request {request_id}")
    if not 0 <= index < len(req.criteria):
        raise RequestError(f"{request_id} has no criterion {index}")
    criterion = req.criteria[index]
    config = load_completion_config(config_path)
    results: list[EvidenceResult] = []
    for ev in criterion.evidence:
        if ev.kind == "commit":
            ok, reason = _verify_commit(root, ev.ref, criterion.text)
        elif ev.kind in ("file", "screenshot"):
            ok, reason = _verify_file(root, ev.ref)
        elif ev.kind == "command":
            ok, reason = _verify_command(root, ev.ref, config, run_command)
        elif ev.kind == "url":
            ok, reason = _verify_url(ev.ref, config["timeout_s"], url_check)
        elif ev.kind == "ledger_seq":
            ok, reason = _verify_ledger_seq(root, ev.ref)
        else:
            ok, reason = False, f"unknown evidence kind {ev.kind!r}"
        results.append(EvidenceResult(kind=ev.kind, ref=ev.ref, verified=ok, reason=reason))
    return EvidenceCheck(request_id=request_id, index=index, results=results)


def _review_brief(req: Request) -> str:
    lines = [
        "You are an adversarial reviewer of a completion claim. You do not write code and you fix nothing; your "
        "only job is to find the reason this does NOT satisfy what the operator asked.",
        "",
        "Here is the operator's request, verbatim:",
        req.text,
        "",
        "Here are its acceptance criteria and the evidence attached to each:",
    ]
    for i, c in enumerate(req.criteria):
        lines.append(f"[{i}] {c.text}")
        if not c.evidence:
            lines.append("    (no evidence attached)")
        for e in c.evidence:
            lines.append(f"    - {e.kind}: {e.ref}" + (f" ({e.note})" if e.note else ""))
    lines += [
        "",
        "Inspect what the evidence actually shows; do not take the criteria's wording, or the fact evidence was "
        "attached, on trust. Find the reason this does not satisfy what was asked.",
        "If, after genuinely checking, you find no such reason, say so explicitly and give the reasoning that led "
        "you there. A bare assertion that it is satisfied, with no reasoning, will be treated as a failed review.",
    ]
    return "\n".join(lines)


def _judge_review(text: str) -> tuple[bool, str]:
    """Whether this review's findings block the gate, fail-closed: anything short of an explained, affirmative
    "satisfied" is treated as an unanswered finding, the same discipline `ralph.py` applies to a confident
    summary that does not show its work."""
    stripped = text.strip()
    if not stripped:
        return True, "the review produced no findings"
    low = stripped.lower()
    if any(m in low for m in _REVIEW_FAIL_MARKERS):
        return True, "the review found a reason this does not satisfy the request"
    if any(m in low for m in _REVIEW_PASS_MARKERS):
        if len(stripped.split()) < _MIN_REASONED_WORDS:
            return True, "the review asserted satisfaction without reasoning"
        return False, ""
    return True, "the review did not affirmatively find the request satisfied"


def adversarial_review(root: Path, request_id: str, dispatch: DispatchFn) -> ReviewResult:
    """Dispatch one read-only agent, under the `review` sandbox policy, to look for the reason this request's
    evidence does not hold. `dispatch` runs the prepared task and returns the agent's raw findings text."""
    root = Path(root)
    req = get(root, request_id)
    if req is None:
        raise RequestError(f"no request {request_id}")
    spec = TaskSpec(task_id=f"review-{request_id}", prompt=_review_brief(req), allowed_paths=())
    task = apply_policy(spec, policy_for("review"))
    findings = dispatch(task)
    blocking, reason = _judge_review(findings)
    return ReviewResult(findings=findings, blocking=blocking, reason=reason)


def gate(
    root: Path,
    request_id: str,
    *,
    dispatch: DispatchFn,
    e2e: str,
    config_path: Path | None = None,
    run_command: RunCommandFn | None = None,
    url_check: UrlCheckFn | None = None,
) -> GateResult:
    """Refuse `verified` unless every criterion's evidence re-verifies, the adversarial review raises no unanswered
    finding, and the end-to-end command passes. This is what `requests-advance <id> verified` must pass through;
    nothing here mutates the request itself."""
    root = Path(root)
    req = get(root, request_id)
    if req is None:
        raise RequestError(f"no request {request_id}")
    if not req.criteria:
        raise RequestError(f"{request_id} has no acceptance criteria, so there is nothing to verify")

    checks = [
        check_evidence(root, request_id, i, config_path=config_path, run_command=run_command, url_check=url_check)
        for i in range(len(req.criteria))
    ]
    failing = [c for c in checks if not c.verified]
    if failing:
        bad = failing[0]
        reasons = "; ".join(r.reason for r in bad.unverified())
        return GateResult(request_id, False, f"criterion {bad.index} has unverified evidence: {reasons}", checks)

    review = adversarial_review(root, request_id, dispatch)
    if review.blocking:
        return GateResult(request_id, False, f"adversarial review: {review.reason}", checks, review)

    verify = ralph.command_verifier(e2e)
    e2e_ok, e2e_detail = verify(root)
    if not e2e_ok:
        return GateResult(request_id, False, f"end-to-end check failed: {e2e_detail}", checks, review, False, e2e_detail)

    return GateResult(
        request_id, True,
        "every criterion's evidence re-verified, the review raised nothing, and the end-to-end check passed",
        checks, review, True, e2e_detail,
    )


__all__ = [
    "PACKAGED_CONFIG",
    "CompletionError",
    "DispatchFn",
    "EvidenceCheck",
    "EvidenceResult",
    "GateResult",
    "ReviewResult",
    "RunCommandFn",
    "UrlCheckFn",
    "adversarial_review",
    "check_evidence",
    "gate",
    "load_completion_config",
]
