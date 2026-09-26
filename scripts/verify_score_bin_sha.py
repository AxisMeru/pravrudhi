"""CI guard (Lead-2, 2026-09-26, option (a) of the score-binary CI gap): verifies a freshly-built prabhasa-
nyaya `score` binary's sha256 against `configs/nyaya_agent.yaml`'s own `pinned_score_sha256` -- FAILS LOUD on
any mismatch, same philosophy as the Docker build's own verification step (`deploy/docker/Dockerfile`'s
`NYAYA_SCORE_SHA256` build-arg check).

Reuses `nyaya_agent.BinaryRegistry`'s own sha check (never re-derives the comparison here) so this and the
engine's own runtime check can never silently drift apart -- a binary this script accepts is, by construction,
one the engine itself would also accept.

Usage: verify_score_bin_sha.py <path to the built score binary>
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from pravrudhi.application.nyaya_agent import BinaryRegistry, BinaryShaMismatch, load_agent_config  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_score_bin_sha.py <path to the built score binary>", file=sys.stderr)
        return 2
    bin_path = Path(sys.argv[1])
    if not bin_path.is_file():
        print(f"FAIL: {bin_path} does not exist or is not a file -- the build did not produce a binary there", file=sys.stderr)
        return 1

    cfg = load_agent_config(REPO_ROOT)
    if not cfg.pinned_score_sha256:
        print("FAIL: configs/nyaya_agent.yaml has no pinned_score_sha256 -- nothing to verify against", file=sys.stderr)
        return 1

    try:
        registry = BinaryRegistry(bin_path, pinned_sha256=cfg.pinned_score_sha256)
    except BinaryShaMismatch as e:
        print(f"FAIL: {e}", file=sys.stderr)
        print(
            "The freshly-built binary does NOT match configs/nyaya_agent.yaml's pinned_score_sha256. This "
            "means either PRABHASA_NYAYA_PINNED_COMMIT points at the wrong commit, or the pinned sha256 "
            "itself is stale -- fix the mismatch before this binary is trusted for anything.",
            file=sys.stderr,
        )
        return 1

    print(f"OK: {bin_path} sha256 {registry.sha256} matches the pin.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
