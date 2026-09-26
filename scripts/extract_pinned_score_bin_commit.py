"""CI helper (Lead-2, 2026-09-26, option (a) of the score-binary CI gap): prints the prabhasa-nyaya commit
sha CI must check out and build to reproduce `configs/nyaya_agent.yaml`'s own `pinned_score_sha256`.

Reads the `PRABHASA_NYAYA_PINNED_COMMIT:` line placed directly under `pinned_score_sha256:` in that file --
never a separate, independently-maintained value -- so a re-pin that updates the sha256 but forgets this line
is a stale-pin bug caught the same way a wrong sha256 already is: the build succeeds, the verification step
(`verify_score_bin_sha.py`) fails loud, because the two were never actually the same build.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG = REPO_ROOT / "configs" / "nyaya_agent.yaml"

_COMMIT_RE = re.compile(r"^\s*#\s*PRABHASA_NYAYA_PINNED_COMMIT:\s*([0-9a-f]{7,40})\b", re.MULTILINE)


def extract_pinned_commit(text: str) -> str:
    m = _COMMIT_RE.search(text)
    if not m:
        raise ValueError(
            f"no 'PRABHASA_NYAYA_PINNED_COMMIT: <sha>' line found in {CONFIG} -- the pin comment must name "
            "the exact prabhasa-nyaya commit CI builds, right next to pinned_score_sha256"
        )
    return m.group(1)


def main() -> int:
    print(extract_pinned_commit(CONFIG.read_text()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
