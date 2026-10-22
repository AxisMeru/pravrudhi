"""Write the flag-OFF goldens of GET /api/v1/status and the 503 judges_offline refusal (#1038, for Track A).

Run from a clean checkout at 385dd908 (0.6.28 tree): `python -m scripts.gen_cold_start_goldens`.
Mocked judge health only; no network, no RunPod call.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from tests.test_judges_offline import _client, _fetcher, _health

OUT = Path(__file__).resolve().parent.parent / "tests" / "golden" / "cold_start"


def _dump(name: str, status: int, body: object) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps({"status_code": status, "body": body}, indent=2, sort_keys=True) + "\n")


def main() -> None:
    from tests.test_api_partner import _req

    with tempfile.TemporaryDirectory() as d:
        c: TestClient
        c, _ = _client(Path(d), _fetcher(_health()))
        r = c.get("/api/v1/status")
        body = r.json()
        body["engine_version"] = "<version>"
        _dump("status_flag_off.json", r.status_code, body)
    with tempfile.TemporaryDirectory() as d:
        c, _ = _client(Path(d), _fetcher(_health()))
        r = c.post("/api/v1/analyse-facts", json=_req())
        _dump("judges_offline_refusal.json", r.status_code, r.json())


if __name__ == "__main__":
    main()
