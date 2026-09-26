"""CI guard on the COMMITTED `demo.json` itself, not just the redaction logic that produces it.

`test_demo_export_pii.py` proves `redact_secrets` catches these shapes; this file proves the file actually
checked into the repository -- the one the public site serves -- carries none of them right now. The two
are deliberately separate: a future change to `write_demo`'s call site, a hand-edited snapshot, or a stale
commit predating a redaction fix would all pass the first file's tests while still shipping a leak, and only
a check against the real committed bytes catches that.

2026-09-14: the committed copy held 872 occurrences of internal agent-to-agent `<cross-session-message>`
relays (session socket paths under `/run/user/<uid>/cc-socks/` and `/tmp/cc-socks/`), published hourly to
this public repository, because the requests backlog stores an operator's ask verbatim by design and an
auto-capture hook sometimes recorded a teammate's relay as the ask text itself.

R1's rejection of the first fix (liaison-log 22c1451): 6 of 957 relay fields were truncated at 300 chars with
no closing tag, and the first fix's catch-all stripped only the bare word, leaving the real body -- an
operator directive, a session id -- sitting right there. `_FORBIDDEN_PATTERNS` below is broader than the
three literals that first fix checked for specifically so a future regression of THAT shape (a marker with
content still trailing after it, or an attribute name/directive phrase surviving even without the wrapping
tag's own name) gets caught too, not just a repeat of the exact original bug.
"""

from __future__ import annotations

import re
from pathlib import Path

DEMO_JSON = Path("app/frontend/public/demo.json")

#: (name, compiled pattern). Kept as a list of (name, pattern) pairs, not inline literals scattered through
#: the test body, so adding a new forbidden shape later is a one-line addition here rather than a new
#: assertion to write from scratch -- and so a failure names exactly which pattern matched, not just that
#: the file failed some check. Each pattern is `re.escape`d already where it's a literal substring; the two
#: that aren't (the trailing-content checks) are genuine regexes.
_FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("cc-socks", re.compile(re.escape("cc-socks"))),
    ("cross-session-message", re.compile(re.escape("cross-session-message"))),
    ("home-path", re.compile(re.escape("/home/"))),
    # A relay's own attribute names/value-prefix -- catches a wrapped block even if some future change
    # renamed or otherwise avoided the literal tag-name string itself.
    ("relay-from-attribute", re.compile(re.escape('from="local_'))),
    ("relay-from-session-attribute", re.compile(re.escape("from-session="))),
    ("relay-from-name-attribute", re.compile(re.escape("from-name="))),
    # A real operator directive phrase seen surviving in the R1-found leak -- if this exact phrase ever
    # shows up again, something is publishing raw internal instruction text again.
    ("operator-directive-phrase", re.compile(re.escape("DIRECTION FROM THE OPERATOR"))),
    # The regression this whole rejection was about: either redaction marker followed by more content
    # before the enclosing JSON string ends means the redaction stopped short and left a body behind --
    # `demo_export.py`'s own shapes are bounded to always consume through to the string's true end, so a
    # clean marker is immediately followed by the closing quote, never by more escaped-or-plain content.
    (
        "internal-relay-marker-with-trailing-content",
        re.compile(r'<redacted:internal-relay>(?:\\.|[^"\\])'),
    ),
    (
        "internal-marker-residue-with-trailing-content",
        re.compile(r'<redacted:internal-marker>(?:\\.|[^"\\])'),
    ),
)


def test_demo_json_carries_no_internal_relay_or_home_path_markers() -> None:
    if not DEMO_JSON.is_file():
        return  # nothing committed yet (a fresh checkout before the first publish run); nothing to guard
    text = DEMO_JSON.read_text()
    found = [name for name, pattern in _FORBIDDEN_PATTERNS if pattern.search(text)]
    assert not found, (
        f"app/frontend/public/demo.json matches forbidden pattern(s) {found} -- internal team/session "
        "content, a real filesystem path, or an incompletely-redacted relay leaked into the public "
        "snapshot; see demo_export.py's _PII_SHAPES"
    )
