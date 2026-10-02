"""The partner API's OpenAPI contract, generated from the router and checked in at `docs/api/openapi-v1.json`.

`python -m pravrudhi.api.partner_openapi --write` regenerates it; `tests/test_partner_openapi.py` fails when the
checked-in file differs from what the code produces, so a schema change must ship with its regenerated contract.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from pravrudhi.api.partner import build_partner_router

CONTRACT_PATH = Path("docs") / "api" / "openapi-v1.json"


def build_openapi() -> dict[str, Any]:
    app = FastAPI(title="Pravrudhi partner API", version="v1")
    app.include_router(build_partner_router(Path(".")))
    return app.openapi()


def render() -> str:
    return json.dumps(build_openapi(), indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    if "--write" in argv:
        CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONTRACT_PATH.write_text(render())
        return 0
    sys.stdout.write(render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
