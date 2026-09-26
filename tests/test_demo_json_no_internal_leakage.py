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
"""

from __future__ import annotations

from pathlib import Path

DEMO_JSON = Path("app/frontend/public/demo.json")

#: Each one alone is enough to prove a leak of this shape; kept as a flat list (not one combined regex) so a
#: failing assertion names exactly which marker was found, not just that the file failed some check.
_FORBIDDEN_MARKERS = ("cc-socks", "cross-session-message", "/home/")


def test_demo_json_carries_no_internal_relay_or_home_path_markers() -> None:
    if not DEMO_JSON.is_file():
        return  # nothing committed yet (a fresh checkout before the first publish run); nothing to guard
    text = DEMO_JSON.read_text()
    found = [m for m in _FORBIDDEN_MARKERS if m in text]
    assert not found, (
        f"app/frontend/public/demo.json contains {found} -- internal team/session content or a real "
        "filesystem path leaked into the public snapshot; see demo_export.py's _PII_SHAPES"
    )
