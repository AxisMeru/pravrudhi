"""Partner-visible audit log of analyse-facts calls (#148): who (key id), when, which run, which contracts, what
outcome and HTTP status. It never holds fact text, narrative, quotes or any judge output beyond the outcome label:
the writer takes only the fields below, and contract ids are stored only when they look like ids."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pravrudhi.application.portable_lock import exclusive_lock

_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_OUTCOME = re.compile(r"^[A-Za-z0-9_]{1,40}$")
MAX_PAGE = 200


def _path(root: Path) -> Path:
    return Path(root) / ".pravrudhi" / "audit" / "calls.jsonl"


def _clean_ids(ids: list[str]) -> list[str]:
    return [i if _ID.match(i) else "invalid-id" for i in ids[:5]]


def record(
    root: Path,
    *,
    key_id: str,
    mode: str,
    status_code: int,
    contract_ids: list[str],
    run_id: str | None = None,
    outcomes: dict[str, str] | None = None,
    retention_s: float,
    now: datetime | None = None,
) -> None:
    at = now or datetime.now(UTC)
    row = {
        "ts": at.isoformat(),
        "key_id": key_id,
        "mode": mode,
        "status_code": int(status_code),
        "run_id": run_id if run_id and _ID.match(run_id) else None,
        "contract_ids": _clean_ids(contract_ids),
        "outcomes": {
            (c if _ID.match(c) else "invalid-id"): (o if _OUTCOME.match(o) else "invalid") for c, o in (outcomes or {}).items()
        },
    }
    p = _path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(p.with_name(".calls.lock")):
        lines = p.read_text().splitlines() if p.exists() else []
        cutoff = at - timedelta(seconds=retention_s)
        lines = [ln for ln in lines if _ts(ln) >= cutoff]
        lines.append(json.dumps(row, sort_keys=True))
        tmp = p.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + "\n")
        tmp.replace(p)


def _ts(line: str) -> datetime:
    try:
        return datetime.fromisoformat(json.loads(line)["ts"])
    except (ValueError, KeyError, TypeError):
        return datetime.min.replace(tzinfo=UTC)


def page(
    root: Path, key_id: str, *, offset: int, limit: int, retention_s: float, now: Callable[[], datetime]
) -> tuple[list[dict[str, Any]], int | None]:
    """The calling key's rows, newest first, retention applied at read time as well (a row past retention is never
    served even if no write has pruned it yet). Returns (rows, next_offset or None)."""
    p = _path(root)
    if not p.exists():
        return [], None
    cutoff = now() - timedelta(seconds=retention_s)
    mine = [
        r
        for r in (json.loads(ln) for ln in p.read_text().splitlines() if ln.strip())
        if r.get("key_id") == key_id and datetime.fromisoformat(r["ts"]) >= cutoff
    ]
    mine.reverse()
    limit = max(1, min(limit, MAX_PAGE))
    rows = mine[offset : offset + limit]
    nxt = offset + limit if offset + limit < len(mine) else None
    return rows, nxt
