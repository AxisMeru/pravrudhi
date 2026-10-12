"""The P3 citation verifier as a tool for `tool_runner.ToolRunner` (#300 slice B). The index is opened read-only
per call; a missing index is an error, never a guessed verdict. A locked database is the one transient fault."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from pravrudhi.application.tool_runner import ToolRunner, ToolSpec, TransientToolError
from pravrudhi.application.verify import verify

#: The same per-lookup budget as the partner API route (`PartnerApiConfig.verify_timeout_s`, default 5.0 s) and the same
#: mechanism (a progress handler that interrupts the statement once the deadline passes). Running out is an error, no verdict.
VERIFY_TIMEOUT_S = 5.0


class CitationIndexUnavailable(Exception):
    pass


class CitationLookupTimeout(Exception):
    """The lookup ran past `VERIFY_TIMEOUT_S`: not retried and never a status (no VERIFIED, no guessed NOT_IN_INDEX)."""


def register_verify_citation(runner: ToolRunner, index_path: Path | None) -> None:
    def verify_citation(citation: str, quote: str) -> str:
        if index_path is None or not Path(index_path).is_file():
            raise CitationIndexUnavailable
        conn = sqlite3.connect(f"file:{Path(index_path).resolve()}?mode=ro", uri=True)
        deadline = time.monotonic() + VERIFY_TIMEOUT_S
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
        try:
            return verify(conn, citation, quote).value
        except sqlite3.OperationalError as e:
            if "interrupt" in str(e).lower():
                raise CitationLookupTimeout(f"verify exceeded {VERIFY_TIMEOUT_S} s") from e
            if "locked" in str(e) or "busy" in str(e):
                raise TransientToolError(str(e)) from e
            raise
        finally:
            conn.close()

    runner.register(ToolSpec("verify_citation", verify_citation, max_attempts=3, backoff_s=0.2))
