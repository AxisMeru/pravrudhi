"""The P3 citation verifier as a tool for `tool_runner.ToolRunner` (#300 slice B). The index is opened read-only
per call; a missing index is an error, never a guessed verdict. A locked database is the one transient fault."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from pravrudhi.application.tool_runner import ToolRunner, ToolSpec, TransientToolError
from pravrudhi.application.verify import verify


class CitationIndexUnavailable(Exception):
    pass


def register_verify_citation(runner: ToolRunner, index_path: Path | None) -> None:
    def verify_citation(citation: str, quote: str) -> str:
        if index_path is None or not Path(index_path).is_file():
            raise CitationIndexUnavailable
        conn = sqlite3.connect(f"file:{Path(index_path).resolve()}?mode=ro", uri=True)
        try:
            return verify(conn, citation, quote).value
        except sqlite3.OperationalError as e:
            if "locked" in str(e) or "busy" in str(e):
                raise TransientToolError(str(e)) from e
            raise
        finally:
            conn.close()

    runner.register(ToolSpec("verify_citation", verify_citation, max_attempts=3, backoff_s=0.2))
