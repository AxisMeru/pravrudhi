"""CI guard: no token-shaped string enters this repo through a pull request's own diff.

Two real events on this project, both of which this exists because of:

* 2026-09-26 -- while fixing the 0.5.40 image build a GitHub PAT was passed as a Docker `ARG` and BuildKit
  printed it in cleartext into a build log. Nothing was pushed and no image published; the token is being
  rotated and `scripts/check_no_secret_dockerfile_args.py` now makes that exact Dockerfile shape fail CI.
  That guard checks a DECLARATION (`ARG GITHUB_TOKEN`); this one checks a VALUE, anywhere in the diff, so
  the two do not overlap -- a literal token pasted into a test, a notebook, a README or a YAML file is
  invisible to the Dockerfile guard and caught here.
* 2026-09-11 -- a live Telegram bot token was committed in a tracked, published file. It has since been
  rotated (the old value now returns 401) but it is permanently in this repository's public history and
  cannot be removed from it. That is precisely why the baseline below exists, and why the baseline is keyed
  on a DIGEST and never on the value.

WHAT THIS DOES NOT PROTECT AGAINST, stated here rather than left to be discovered:
  * It reads the PULL REQUEST'S DIFF. A secret that is already on `main` is not in any later PR's diff and
    will never be found by this job. Use `--all-tracked` for a whole-tree sweep; that is an audit you run,
    not a gate that runs itself.
  * Nothing here can remove a secret from published history. Once a value is pushed to a public remote the
    only remedy is rotation. Treat every hit as "rotate it", not as "delete the line".
  * It is a pattern scan. A secret with no distinguishing shape -- a passphrase, a short key, a value split
    across lines or base64-wrapped one extra time -- has nothing to match on and will pass.

FOUR DESIGN RULES, each of which matters more than how many patterns this knows:

1. IT NEVER PRINTS A MATCHED SECRET. A CI log is readable by everyone who can see the run, so a scanner
   that echoes its findings publishes them. Output carries the path, the line, the rule, and a redacted
   fingerprint -- the first four characters, the length, and a digest -- and never the matched text. The
   guard's own tests assert that a planted fixture value does not appear anywhere in its output.
2. THE BASELINE DOES NOT CONTAIN THE SECRETS EITHER. `secret_scan_baseline.txt` is keyed on
   `sha256(<repo-relative path> NUL <matched text>)`, truncated. A waiver file listing literal values would
   itself be the leak -- which is the difference between this baseline and
   `scripts/fail_open_defaults_baseline.txt`, which keys on the matched expression because a fail-open
   default is not a secret. The digest is what CI prints, so adding a waiver is copy-and-paste from the
   failing log and never requires handling the value.
3. IT FAILS CLOSED. Nothing is skipped. A file that is not valid UTF-8 is decoded latin-1 and scanned
   anyway (a notice records that it was); a file git reports as binary in the diff is fetched whole at the
   head commit and scanned as bytes, because a diff gives no lines for it; a file that genuinely cannot be
   read is a HARD FINDING, not a pass. A scanner that silently skips what it cannot parse reports "clean"
   for the files it never looked at -- the first draft of `check_fail_open_defaults.py` did exactly that
   and made one of its own negative fixtures pass vacuously. Every negative fixture in this guard's tests
   carries a non-vacuity assertion that the file was actually scanned.
4. A WAIVER CARRIES A WRITTEN REASON. `# secret-scan-ok: <reason>` with no reason after the colon waives
   nothing and is itself reported. Waived lines are PRINTED as waived, with their reason, on every run:
   a waiver that disappears from the output is a waiver nobody re-reads.

Usage:
    check_no_secrets_in_diff.py [--root DIR] [--base REF] [--head REF] [--all-tracked] [--baseline FILE]

With no `--base`, the base is resolved from the environment the way CI supplies it: `GITHUB_BASE_REF`
(a pull request) wins, then `SECRET_SCAN_BEFORE` (a push's `github.event.before`), then `origin/main`, then
`HEAD~1`. Exit 1 and print `path:line: [rule] message` for every violation, exit 0 clean -- the same shape
as `import_guard.py` and `check_no_private_data.py`.

A base that RESOLVES is not necessarily one that is USABLE. If the range turns out to hold nothing -- no
base at all, a base naming the same commit as the head, or a merge base that already IS the head -- this
does not report a pass over an empty diff. It says so loudly and escalates to the whole-tree scan, then
requires that scan to have read at least one file (issue #93). Both refs are resolved to commit SHAS before
being compared, because `origin/main` and `HEAD` routinely name the same commit on a push to `main`. The
files-scanned floor applies ONLY to the whole-tree path: a change touching only exempt files legitimately
scans nothing, so the same floor on the diff path would be a false red. A legitimately empty diff is
reported as such, so it cannot be confused with the degenerate case.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BASELINE = Path("scripts") / "secret_scan_baseline.txt"

#: An inline waiver. The text AFTER the colon is the reason and is mandatory: `# secret-scan-ok:` alone is
#: reported as a waiver without a reason, so a marker can never be pasted in to make a red build green
#: without someone writing down why.
WAIVER_MARKER = "secret-scan-ok:"

#: This guard's own source names every pattern it looks for, and its fixtures contain constructed values
#: shaped like real credentials on purpose. Both are exempt, or the guard would fail on its own PR.
#: Consequence, stated rather than hidden: a real secret placed in one of these two locations is not seen
#: by this scan. They are small, they are read on every change to this guard, and no other path is exempt.
#: Violations that say the SCAN ITSELF did not do its job, as opposed to a token-shaped string found in
#: the content. They are equally red, but the remedy is completely different -- there is no value to
#: rotate and nothing to waive -- so the failure text must not offer credential advice for them.
SCAN_INTEGRITY_RULES = frozenset({"vacuous-scan", "unreadable"})

EXEMPT_NAMES = frozenset({
    "check_no_secrets_in_diff.py",
    "test_check_no_secrets_in_diff.py",
    "secret_scan_baseline.txt",
})
FIXTURE_DIR_NAME = "secret_scan_fixtures"


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]
    message: str
    #: Which capture group holds the secret itself. The fingerprint is computed from THIS, not from the
    #: whole match, so `Authorization: Bearer <tok>` fingerprints the token and not the word "Bearer".
    group: int = 1
    #: Whether a low-variety / obviously-illustrative value is allowed to suppress the match. False for
    #: shapes that are unambiguous enough that a placeholder of that exact shape is still worth flagging.
    allow_placeholder_suppression: bool = True


#: Shannon entropy floor, in bits per character, for the value rules. Measured over both repositories'
#: current trees: 3.5 keeps every real-looking credential and drops prose, paths, URLs, hashes-in-docs and
#: the long dotted identifiers these repos are full of. See the PR that added this file for the counts.
ENTROPY_FLOOR = 3.5
MIN_SECRET_LEN = 20

#: `_`/`-`-separated words in an assignment target that make the right-hand side a candidate secret.
SECRET_NAME_WORDS = frozenset({
    "token", "secret", "password", "passwd", "passphrase", "apikey", "credential", "credentials",
    "auth", "bearer", "signature", "privatekey", "accesskey", "secretkey", "apisecret", "clientsecret",
    "sessionkey", "authtoken", "accesstoken", "refreshtoken",
})
#: Adjacent pairs, for the `API_KEY` / `PRIVATE_KEY` / `ACCESS_KEY` shapes where neither word alone is
#: enough (`key` on its own matches dictionary keys, sort keys, cache keys -- 100+ false positives).
SECRET_NAME_PAIRS = ("api_key", "api-key", "private_key", "private-key", "access_key", "access-key",
                     "secret_key", "secret-key", "client_secret", "client-secret", "auth_token",
                     "auth-token", "access_token", "access-token", "refresh_token", "refresh-token",
                     "session_key", "session-key", "signing_key", "signing-key")

#: Names that contain a secret word but are NOT credentials, and are everywhere in both of these
#: repositories because they are machine-learning codebases: `token` in `pad_token_id`, `eos_token_id`,
#: `token_ids` means a TOKENISER token, not an API token. 22 of the 31 raw hits on prabhasa-nyaya's tree
#: and 5 of the 25 on pravrudhi's were this one class. Matched as a substring of the lowercased name.
NON_SECRET_NAME_SUBSTRINGS = (
    "token_id", "token-id", "tokenid", "token_ids", "pad_token", "eos_token", "bos_token", "unk_token",
    "sep_token", "cls_token", "mask_token", "special_token", "first_token", "last_token", "num_token",
    "n_token", "max_token", "min_token", "token_count", "token_index", "token_logprob", "token_type",
    "signature_id", "per_token",
)

#: Name SUFFIXES that say the value is the NAME OF, or the PATH TO, a credential rather than the credential
#: itself -- `api_key_env = "NYAYA_API_KEY"`, `credential_file = "~/.config/..."`,
#: `CLIENT_IP_SECRET_HEADER = "x-pravrudhi-client-ip"`. Seven of pravrudhi's raw hits were this class.
NON_VALUE_NAME_SUFFIXES = (
    "_env", "_envvar", "_env_var", "_file", "_filename", "_path", "_dir", "_url", "_uri", "_header",
    "_name", "_field", "_var", "_prefix", "_suffix", "_column", "_col", "_label", "_key_env",
)

#: An assignment to a secret-shaped name, in any of the four syntaxes these repos use: Python/TS `=`,
#: YAML/JSON `:`, shell `export X=`, and `.env` lines. The value may be quoted or bare; a BARE value is
#: filtered afterwards (see `_value_is_credential_shaped`), because the overwhelming majority of bare
#: right-hand sides are code -- `process.env.E2E_PASSWORD`, `self.tokenizer.pad_token_id`,
#: `ec.generate_private_key(...)` -- and matching those produced 12 of pravrudhi's 25 raw hits.
_ASSIGN_RE = re.compile(
    r"(?P<name>[A-Za-z_][A-Za-z0-9_.\-]{2,60})"
    r"\s*[:=]\s*"
    r"(?P<q>[\"']?)(?P<val>[A-Za-z0-9+/_\-.=~]{" + str(MIN_SECRET_LEN) + r",})(?P=q)"
)

#: A quoted value that is plainly an IDENTIFIER rather than a credential: an environment variable name, an
#: HTTP header name or a filesystem path. Each of these shapes was a measured false
#: positive on one of the two trees and none of them can be a generated credential.
_ENV_VAR_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_KEBAB_WORDS_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+){2,}$")
#: Longest hyphen-separated segment a kebab-case value may have and still read as WORDS rather than as a
#: credential. Deliberately tight: `sk-<40 random chars>` is lowercase and hyphenated too, and suppressing
#: it as "kebab case" would be a real hole. Three or more segments, none longer than this, is prose.
_KEBAB_MAX_SEGMENT = 10
#: A filesystem path, by its leading `~/`, `./` or `/`, or by a trailing file extension. Deliberately does
#: NOT include "looks like a dotted identifier": a JWT is `<base64>.<base64>.<base64>` and reads as exactly
#: that, so suppressing dotted values would blind the `Bearer` rule to the commonest bearer token there is.
_PATHY_RE = re.compile(r"^[~.]?/|\.[a-z]{1,5}$")
_BARE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _value_is_credential_shaped(value: str, quoted: bool) -> bool:
    """False for a right-hand side that cannot be a credential.

    Unquoted values must look like a raw token (`.env` and unquoted YAML are real cases), so a bare value
    containing a `.` or being a plain identifier is code, not a secret. Quoted values are rejected when
    they are an env-var name, a header name, a filesystem path.
    """
    if not quoted:
        return "." not in value and not _BARE_IDENT_RE.fullmatch(value)
    if _ENV_VAR_NAME_RE.fullmatch(value):
        return False
    if _KEBAB_WORDS_RE.fullmatch(value) and all(len(seg) <= _KEBAB_MAX_SEGMENT for seg in value.split("-")):
        return False
    return not _PATHY_RE.search(value)


#: NOTE ON ANCHORING, and it is the single most important line in this file. None of these patterns is
#: anchored with a LEADING `\b`. The first draft of every one of them was, and it made the guard miss the
#: real thing: pravrudhi's own 2026-09-11 Telegram token sits in `app/frontend/public/demo.json` at
#: `85ad860` inside an ESCAPED JSON string, so the character immediately before it is the `n` of a `\n`
#: escape -- a word character, so there is no word boundary there and `\b\d{8,10}:AA...` does not match a
#: live credential that is demonstrably in this repository's history. A secret that leaks leaks into a log
#: dump, a pasted traceback, a JSON blob or a URL, not into tidy isolation. So each pattern is anchored only
#: at the END, with a negative lookahead that stops the match being a PREFIX of a longer run of the same
#: character class, which is what makes the reported length correct. Verified: with the leading `\b` the
#: whole-tree scan of `85ad860` reported 0 findings; without it, it reports that file.
RULES: tuple[Rule, ...] = (
    Rule(
        "github-pat-classic",
        re.compile(r"(gh[pousr]_[A-Za-z0-9]{36})(?![A-Za-z0-9])"),
        "a GitHub personal access token (classic `ghp_`/`gho_`/`ghu_`/`ghs_`/`ghr_` prefix, 40 chars)",
    ),
    Rule(
        "github-pat-fine-grained",
        re.compile(r"(github_pat_[A-Za-z0-9_]{40,})(?![A-Za-z0-9_])"),
        "a GitHub fine-grained personal access token (`github_pat_` prefix). Matched on the prefix plus "
        "length rather than on the exact 22+59 layout, which GitHub has changed before",
    ),
    Rule(
        "telegram-bot-token",
        re.compile(r"(?<!\d)(\d{8,10}:AA[A-Za-z0-9_\-]{31,35})(?![A-Za-z0-9_\-])"),
        "a Telegram bot token (`<bot-id>:AA...`) -- the exact shape leaked on 2026-09-11",
    ),
    Rule(
        "aws-access-key-id",
        re.compile(r"((?:AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA)[A-Z0-9]{16})(?![A-Z0-9])"),
        "an AWS access key id",
    ),
    Rule(
        "private-key-pem",
        re.compile(r"(-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----)"),
        "a private key PEM header -- the key material itself follows it",
        allow_placeholder_suppression=False,
    ),
    # NO SLACK RULE, and the reason is worth writing down because it is a fact about this org's setup.
    # A `xox[baprs]-...` rule was written, tested and measured, and then removed: GitHub push protection is
    # enabled on AxisMeru/pravrudhi and rejects any commit containing a Slack-token-shaped literal, so the
    # rule cannot be given a test fixture there (`GH013 ... Slack API Token` on
    # tests/secret_scan_fixtures/trips_structural.py). An untested rule in one repo and a tested one in the
    # other is worse than neither, and push protection already covers that exact shape on pravrudhi.
    # Follow-up for the owners, and the more useful half of this finding: push protection let the
    # constructed GitHub PAT, Telegram, AWS and PEM fixtures in the same commit straight through, and
    # AxisMeru/prabhasa-nyaya accepted the Slack literal too -- so push protection is neither enabled
    # everywhere nor a substitute for this guard. Turning it on for prabhasa-nyaya is a one-setting change.
    Rule(
        "bearer-token",
        re.compile(r"[Bb]earer\s+([A-Za-z0-9._~+/\-]{20,}={0,2})(?![A-Za-z0-9._~+/\-=])"),
        "a literal `Bearer <token>` value",
    ),
)

#: Words that mark a value as illustrative rather than live. Matched case-insensitively as a substring: a
#: real credential does not contain "example" or "placeholder", and every one of these appears in these
#: repositories' docs and test fixtures against a token-shaped string.
PLACEHOLDER_WORDS = (
    "example", "placeholder", "changeme", "change-me", "your-", "your_", "dummy", "fake", "sample",
    "redacted", "xxxxxx", "notreal", "not-real", "test-token", "testtoken", "abcdef0123", "deadbeef",
    "insert", "replace", "<", ">", "${", "{{", "os.environ", "getenv", "secrets.",
)


def shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def looks_like_placeholder(value: str) -> bool:
    """True for a value that is illustrative rather than live.

    Three independent tests, all of which held over both trees: a placeholder word anywhere in it; fewer
    than 8 distinct characters (`ghp_xxxxxxxx...`, `AKIAIOSFODNN7EXAMPLE`-style filler); or a run of six or
    more identical characters, which no generated credential has.
    """
    low = value.lower()
    if any(w in low for w in PLACEHOLDER_WORDS):
        return True
    if len(set(value)) < 8:
        return True
    if re.search(r"(.)\1{5,}", value) is not None:
        return True
    # A run of eight or more consecutive characters in alphabet or digit order -- `abcdefghij`,
    # `0123456789`. Illustrative by construction; a generated credential does not contain one.
    run = 1
    for a, b in zip(value, value[1:], strict=False):
        run = run + 1 if ord(b) == ord(a) + 1 else 1
        if run >= 8:
            return True
    return False


def _name_is_secret_shaped(name: str) -> bool:
    low = name.lower()
    if any(sub in low for sub in NON_SECRET_NAME_SUBSTRINGS):
        return False
    if low.endswith(NON_VALUE_NAME_SUFFIXES):
        return False
    if any(p in low for p in SECRET_NAME_PAIRS):
        return True
    words = [w for w in re.split(r"[^a-z0-9]+", low) if w]
    return any(w in SECRET_NAME_WORDS for w in words)


@dataclass(frozen=True)
class Finding:
    rule: str
    path: str
    lineno: int
    prefix: str
    length: int
    key: str
    message: str
    waived_reason: str | None = None

    def render(self) -> str:
        tag = "WAIVED " if self.waived_reason else ""
        base = (
            f"{tag}{self.path}:{self.lineno}: [{self.rule}] {self.message}; "
            f"fingerprint: starts {self.prefix!r}, {self.length} chars, key {self.key}"
        )
        if self.waived_reason:
            return f"{base} -- waived: {self.waived_reason}"
        return base


def fingerprint(path: str, value: str) -> tuple[str, int, str]:
    """(first four characters, length, baseline key) for a matched value.

    The key digests the PATH together with the VALUE, so it is scoped to where the value was found rather
    than being a global oracle for "is this string a secret", and so moving a waived file does not silently
    carry its waiver along.
    """
    digest = hashlib.sha256(path.encode() + b"\x00" + value.encode()).hexdigest()[:16]
    return value[:4], len(value), digest


def _waiver_on(line: str) -> str | None | bool:
    """The reason text of an inline waiver, `False` for no waiver at all, `None` for a waiver with no reason."""
    idx = line.find(WAIVER_MARKER)
    if idx < 0:
        return False
    reason = line[idx + len(WAIVER_MARKER):].strip().strip("\"'#*/-)}] \t")
    return reason or None


def scan_line(path: str, lineno: int, line: str) -> list[Finding]:
    """Every finding on one line. Pure: takes text, returns findings, touches no filesystem."""
    out: list[Finding] = []
    seen: set[tuple[str, str]] = set()

    def add(rule: str, value: str, message: str) -> None:
        if (rule, value) in seen:
            return
        seen.add((rule, value))
        prefix, length, key = fingerprint(path, value)
        out.append(Finding(rule, path, lineno, prefix, length, key, message))

    for rule in RULES:
        for m in rule.pattern.finditer(line):
            value = m.group(rule.group)
            if rule.allow_placeholder_suppression and looks_like_placeholder(value):
                continue
            if rule.name == "bearer-token" and not _value_is_credential_shaped(value, quoted=True):
                continue
            add(rule.name, value, rule.message)

    structural = {v for (_r, v) in seen}
    for m in _ASSIGN_RE.finditer(line):
        name, value = m.group("name"), m.group("val")
        if value in structural:
            # Already reported by a rule that knows its exact shape; one line, one finding per value.
            continue
        if not _name_is_secret_shaped(name):
            continue
        if not _value_is_credential_shaped(value, quoted=bool(m.group("q"))):
            continue
        if looks_like_placeholder(value):
            continue
        if shannon_entropy(value) < ENTROPY_FLOOR:
            continue
        add(
            "secret-shaped-assignment",
            value,
            f"a high-entropy literal ({shannon_entropy(value):.2f} bits/char) assigned to `{name}`, "
            f"whose name says it holds a credential",
        )
    return out


def _decode(raw: bytes) -> tuple[str, bool]:
    """(text, was_undecodable). Never raises and never returns nothing: a file that is not UTF-8 is decoded
    latin-1 and scanned anyway, because the alternative is not scanning it."""
    try:
        return raw.decode("utf-8"), False
    except UnicodeDecodeError:
        return raw.decode("latin-1"), True


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout


def _rev_sha(root: Path, ref: str) -> str | None:
    """The full commit sha `ref` names, or None if it names nothing.

    Every comparison between two refs in this file goes through here first. `origin/main` and `HEAD` are
    different STRINGS that, on a push to `main`, name the SAME COMMIT -- comparing the names finds them
    unequal, and the degenerate empty range of issue #93 goes undetected. Compare shas, never names.
    """
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        cwd=root, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def _rev_exists(root: Path, ref: str) -> bool:
    return _rev_sha(root, ref) is not None


def _merge_base_sha(root: Path, base: str, head: str) -> str | None:
    """The merge base of two refs as a sha, or None if they have none (unrelated histories)."""
    proc = subprocess.run(
        ["git", "merge-base", base, head], cwd=root, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def degenerate_range(root: Path, base: str | None, head: str) -> str | None:
    """Why the diff `base...head` cannot be trusted to contain anything, or None if the range is usable.

    This is the gate on the vacuous pass of issue #93. `resolve_base` falls back to `origin/main`, and on a
    push to `main` the checked-out HEAD *is* main's tip: the range is then empty by construction, the scan
    reads 0 files and 0 lines, and the guard prints OK. A green tick meaning "examined nothing" is worse
    than no guard at all, because it is read as evidence.

    A reason returned here does NOT fail the run -- it escalates it to the whole-tree scan, which is what
    this file's docstring has promised all along. The reason is carried into the output so that a reader of
    the log can tell an escalated run from a diff run: a SILENT escalation is how the original pass hid.
    """
    if base is None:
        return (
            "no base ref could be resolved (no GITHUB_BASE_REF, no usable SECRET_SCAN_BEFORE, no "
            "origin/main, no HEAD~1), so there is no diff to scan"
        )
    head_sha = _rev_sha(root, head)
    if head_sha is None:
        return f"head ref {head!r} does not resolve to a commit, so no range can be computed"
    base_sha = _rev_sha(root, base)
    if base_sha is None:
        return f"base ref {base!r} does not resolve to a commit, so no range can be computed"
    if base_sha == head_sha:
        return (
            f"base {base!r} and head {head!r} are the SAME commit {base_sha[:12]} -- the push-to-main "
            f"shape, where the fallback base resolves to the very tip that is checked out -- so the range "
            f"is empty by construction and a diff scan would examine nothing"
        )
    merge_base = _merge_base_sha(root, base, head)
    if merge_base is None:
        return (
            f"base {base!r} ({base_sha[:12]}) and head {head!r} ({head_sha[:12]}) have no merge base, so "
            f"the range {base}...{head} cannot be computed at all"
        )
    if merge_base == head_sha:
        return (
            f"head {head!r} ({head_sha[:12]}) is already contained in base {base!r} ({base_sha[:12]}) -- "
            f"their merge base IS head -- so the range {base}...{head} holds no commits and a diff scan "
            f"would examine nothing"
        )
    return None


def resolve_base(root: Path, explicit: str | None) -> str | None:
    """The ref this diff is measured against. None means "no base" -- scan the whole tracked tree instead,
    which is the fail-closed answer when nothing can be resolved, never "scan nothing".

    Resolving a base is NOT the same as resolving a USABLE one: the `origin/main` fallback below resolves
    on a push to `main` and names the same commit as HEAD, which is issue #93. Judging that is
    `degenerate_range`'s job, and `check` escalates to the whole-tree scan on its verdict. Do not add a
    HEAD-equality test here -- this function reports what it found, the caller decides what it is worth.
    """
    if explicit:
        return explicit
    base_ref = os.environ.get("GITHUB_BASE_REF", "").strip()
    if base_ref and _rev_exists(root, f"origin/{base_ref}"):
        return f"origin/{base_ref}"
    before = os.environ.get("SECRET_SCAN_BEFORE", "").strip()
    if before and set(before) != {"0"} and _rev_exists(root, before):
        return before
    for candidate in ("origin/main", "HEAD~1"):
        if _rev_exists(root, candidate):
            return candidate
    return None


def _is_exempt(rel: str) -> bool:
    parts = Path(rel).parts
    return Path(rel).name in EXEMPT_NAMES or FIXTURE_DIR_NAME in parts


@dataclass
class Report:
    violations: list[Finding] = field(default_factory=list)
    waived: list[Finding] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    #: Every path this run actually read and scanned. The non-vacuity handle: a test asserting that a
    #: negative fixture produced no findings is meaningless unless it also asserts the file is in here.
    scanned: list[str] = field(default_factory=list)
    stale_baseline: list[str] = field(default_factory=list)
    lines_scanned: int = 0
    #: Which mode actually RAN, after any escalation: "diff" or "all-tracked". A caller that asked for a
    #: diff can legitimately read "all-tracked" here -- that is the escalation working, not a bug.
    mode: str = "diff"
    #: Why a requested diff scan was escalated to a whole-tree scan, or None if the range was usable. When
    #: set, this run examined the whole tree; the reason is printed so the log distinguishes the two paths.
    escalated_reason: str | None = None

    @property
    def ok(self) -> bool:
        return not self.violations


def load_baseline(path: Path) -> tuple[dict[str, str], list[str]]:
    """(key -> reason, problems). A baseline entry is `<16-hex key>  <reason text>`; a key with no reason
    waives nothing, exactly like an inline marker with no reason."""
    keys: dict[str, str] = {}
    problems: list[str] = []
    if not path.exists():
        return keys, problems
    for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        key = parts[0]
        reason = parts[1].strip() if len(parts) > 1 else ""
        if not re.fullmatch(r"[0-9a-f]{16}", key):
            problems.append(f"{path.name}:{lineno}: not a 16-hex baseline key: {key!r}")
            continue
        if not reason:
            problems.append(
                f"{path.name}:{lineno}: baseline key {key} has no reason. A bare key waives nothing -- "
                f"write why this value is safe to leave (revoked when, by whom, tracked where)."
            )
            continue
        keys[key] = reason
    return keys, problems


def _changed(root: Path, base: str | None, head: str) -> tuple[dict[str, list[tuple[int, str]]], list[str]]:
    """({path: [(lineno, added line)]}, [paths git calls binary]). Added lines only: a line REMOVED by this
    PR is not a secret entering the repo, and flagging it would make every deletion of an old hit red.

    A None base is a PROGRAMMING ERROR here, not an empty diff. This used to return `({}, [])` -- a literal
    scan-nothing path, unreachable from `main()` but reachable by any caller using `check()` as a library,
    so the two entry points could disagree about what "no base" means. `check()` now escalates a missing or
    degenerate base to a whole-tree scan before control ever reaches here (issue #93).
    """
    if base is None:
        raise ValueError(
            "_changed() was called with base=None, which would scan nothing and report it as clean. A "
            "missing base must be escalated to a whole-tree scan (see check() and degenerate_range()), "
            "never treated as an empty diff."
        )
    name_status = _git(root, "diff", "--name-only", "--diff-filter=ACMR", f"{base}...{head}")
    changed_paths = [p for p in name_status.splitlines() if p.strip()]
    binary: list[str] = []
    numstat = _git(root, "diff", "--numstat", "--diff-filter=ACMR", f"{base}...{head}")
    for row in numstat.splitlines():
        cols = row.split("\t")
        if len(cols) == 3 and cols[0] == "-" and cols[1] == "-":
            binary.append(cols[2])
    out: dict[str, list[tuple[int, str]]] = {}
    raw = subprocess.run(
        ["git", "diff", "--unified=0", "--diff-filter=ACMR", f"{base}...{head}"],
        cwd=root, capture_output=True, check=True,
    ).stdout
    text, _ = _decode(raw)
    cur: str | None = None
    lineno = 0
    for line in text.splitlines():
        if line.startswith("+++ b/"):
            cur = line[6:]
            continue
        if line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            lineno = int(m.group(1)) if m else 0
            continue
        if cur is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            out.setdefault(cur, []).append((lineno, line[1:]))
            lineno += 1
    for p in changed_paths:
        out.setdefault(p, [])
    return out, binary


def _tracked(root: Path) -> list[str]:
    return [p for p in _git(root, "ls-files").splitlines() if p.strip()]


def _read_blob(root: Path, rev: str | None, rel: str) -> bytes:
    if rev is None:
        return (root / rel).read_bytes()
    return subprocess.run(
        ["git", "show", f"{rev}:{rel}"], cwd=root, capture_output=True, check=True
    ).stdout


def check(
    root: Path,
    base: str | None = None,
    head: str = "HEAD",
    all_tracked: bool = False,
    baseline_path: Path | None = None,
) -> Report:
    report = Report()
    if not all_tracked:
        # Issue #93: a base that RESOLVES is not the same as a base that is USABLE. If the range cannot
        # contain anything, audit the whole tree -- never report a pass over an empty diff. This lives here
        # rather than in main() so that the library and CLI entry points cannot disagree.
        report.escalated_reason = degenerate_range(root, base, head)
        if report.escalated_reason is not None:
            all_tracked = True
    report.mode = "all-tracked" if all_tracked else "diff"
    baseline_file = baseline_path if baseline_path is not None else root / DEFAULT_BASELINE
    baseline, baseline_problems = load_baseline(baseline_file)
    used_keys: set[str] = set()

    if all_tracked:
        targets: dict[str, list[tuple[int, str]]] = {p: [] for p in _tracked(root)}
        binary: list[str] = []
        whole_file = set(targets)
        rev: str | None = None
    else:
        targets, binary = _changed(root, base, head)
        whole_file = set(binary)
        rev = head
        report.notices.append(
            f"scanning the diff {base}...{head}: {len(targets)} changed file(s), "
            f"{len(binary)} of them binary (scanned whole, as bytes -- a diff gives no lines for them)"
        )

    exempt_count = 0
    for rel in sorted(targets):
        if _is_exempt(rel):
            exempt_count += 1
            report.notices.append(f"exempt by name/directory, not scanned: {rel}")
            continue
        lines: list[tuple[int, str]]
        if rel in whole_file:
            try:
                raw = _read_blob(root, rev, rel)
            except (OSError, subprocess.CalledProcessError) as e:
                # FAIL CLOSED. A file that could not be read was NOT CHECKED, and an unchecked file is not
                # a clean file. This is a violation, not a skip and not a notice.
                report.violations.append(
                    Finding(
                        "unreadable", rel, 0, "", 0, "-" * 16,
                        f"could not be read, so NOTHING in it was scanned for secrets ({type(e).__name__}). "
                        f"An unchecked file is not a clean file",
                    )
                )
                continue
            text, undecodable = _decode(raw)
            if undecodable:
                report.notices.append(
                    f"{rel}: not valid UTF-8, decoded latin-1 and scanned as bytes anyway (not skipped)"
                )
            lines = list(enumerate(text.splitlines(), start=1))
        else:
            lines = targets[rel]
        report.scanned.append(rel)
        report.lines_scanned += len(lines)
        for lineno, line in lines:
            for finding in scan_line(rel, lineno, line):
                inline = _waiver_on(line)
                if inline is None:
                    report.violations.append(
                        Finding(
                            finding.rule, finding.path, finding.lineno, finding.prefix, finding.length,
                            finding.key,
                            f"{finding.message}. There is a `{WAIVER_MARKER}` marker on this line with NO "
                            f"REASON after it -- a bare marker waives nothing. Write why this value is safe",
                        )
                    )
                    continue
                if isinstance(inline, str):
                    report.waived.append(
                        Finding(**{**finding.__dict__, "waived_reason": f"inline -- {inline}"})
                    )
                    continue
                if finding.key in baseline:
                    used_keys.add(finding.key)
                    report.waived.append(
                        Finding(**{**finding.__dict__, "waived_reason": f"baseline -- {baseline[finding.key]}"})
                    )
                    continue
                report.violations.append(finding)

    report.notices.extend(baseline_problems)
    if all_tracked:
        report.stale_baseline = sorted(set(baseline) - used_keys)
        if not report.scanned:
            # THE FILES-SCANNED FLOOR, and it belongs here and nowhere else. A whole-tree audit of a
            # non-empty repository must yield files; zero means the scan is broken, not that the tree is
            # clean. The message distinguishes the two ways it can happen, because they need different
            # fixes. The same assertion on the DIFF path would be wrong -- see the notice below.
            detail = (
                f"every one of the {exempt_count} tracked file(s) is exempt by name or directory, so this "
                f"guard cannot gate this tree at all -- the exemption list has swallowed the repository"
                if exempt_count
                else "the checkout reports NO TRACKED FILES AT ALL, so this is a broken or empty checkout"
            )
            report.violations.append(
                Finding(
                    "vacuous-scan", "<whole tree>", 0, "", 0, "-" * 16,
                    f"a whole-tree scan examined ZERO files, so it proves nothing and must not report a "
                    f"pass: {detail}",
                )
            )
    elif not report.scanned:
        # A legitimately empty diff over a USABLE, DISTINCT base. This is a real pass and stays one: a
        # change touching only files this guard exempts scans nothing through no fault of its own, so a
        # files-scanned floor here would be a FALSE RED. Say so explicitly, so this run cannot be mistaken
        # in the log for the degenerate base of issue #93, which escalates instead of passing.
        report.notices.append(
            f"the diff {base}...{head} scanned no files, and that is a LEGITIMATE PASS, not the degenerate "
            f"case: the base is a distinct commit with a real range behind it, and that range simply "
            f"changed nothing this guard scans ({exempt_count} changed file(s) exempt by name or "
            f"directory). No files-scanned floor is enforced on the diff path for exactly this reason; it "
            f"is enforced on the whole-tree path, where zero files scanned is not a legitimate outcome."
        )
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    ap.add_argument("--base", default=None, help="base ref; default resolved from the CI environment")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--all-tracked", action="store_true",
                    help="audit every tracked file instead of the diff (a sweep, not the gate)")
    ap.add_argument("--baseline", default=None, help=f"default: <root>/{DEFAULT_BASELINE}")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    base = None if args.all_tracked else resolve_base(root, args.base)

    report = check(
        root, base=base, head=args.head, all_tracked=args.all_tracked,
        baseline_path=Path(args.baseline).resolve() if args.baseline else None,
    )

    if report.escalated_reason:
        print(
            f"ESCALATED to a whole-tree scan: {report.escalated_reason} -- auditing the whole tracked "
            f"tree instead rather than reporting a vacuous pass. This run is deliberately WIDER and "
            f"slower than the diff gate: a guard that prints OK after examining zero lines is worse than "
            f"no guard, because the green tick is read as evidence (issue #93)."
        )
    for n in report.notices:
        print(f"note: {n}")
    for w in report.waived:
        print(w.render())
    for s in report.stale_baseline:
        print(f"baseline: key {s} no longer matches anything in the tree -- remove this line")
    for v in report.violations:
        print(v.render())

    print(
        f"scanned {len(report.scanned)} file(s), {report.lines_scanned} line(s) [mode: {report.mode}]; "
        f"{len(report.waived)} waived, {len(report.violations)} violation(s)"
    )
    if report.violations:
        broken = [v for v in report.violations if v.rule in SCAN_INTEGRITY_RULES]
        token_hits = [v for v in report.violations if v.rule not in SCAN_INTEGRITY_RULES]
        if broken:
            print(
                f"\nFAIL: {len(broken)} finding(s) above mean this run did not examine what it was meant "
                f"to. That is a BROKEN GUARD, not a clean tree: there is no value to rotate and nothing to "
                f"waive, and the fix is to make the scan able to read the content again. A guard that "
                f"reports a pass over what it never looked at is worse than no guard, because the green "
                f"tick is read as evidence."
            )
        if token_hits:
            print(
                f"\nFAIL: {len(token_hits)} token-shaped string(s) entering the repo. The value itself is "
                f"NOT printed above, by design -- a CI log is public to everyone who can see the run. If a hit "
                f"is real: ROTATE IT FIRST (it is already in the branch's history; deleting the line does not "
                f"unpublish it), then remove it from the diff. If it is not a credential, waive it where the "
                f"next reader will see it: `# {WAIVER_MARKER} <reason>` on the line, or -- for a historical "
                f"value that cannot be removed from published history -- its printed key plus a reason in "
                f"{DEFAULT_BASELINE}."
            )
        return 1
    print("OK: no unwaived token-shaped strings in the scanned content.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
