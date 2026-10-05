"""API consumers previously had to read route bodies to discover response fields."""

import json
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.routing import APIRoute, APIRouter
from fastapi.testclient import TestClient
from pydantic import TypeAdapter
from starlette.routing import BaseRoute

from pravrudhi.api.server import create_app
from pravrudhi.application.status import status


def _api_routes(routes: Sequence[BaseRoute]) -> Iterator[APIRoute]:
    """Included routers otherwise hide their response models on FastAPI builds that defer inclusion."""
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        else:
            router = getattr(route, "original_router", None)
            if isinstance(router, APIRouter):
                yield from _api_routes(router.routes)


def test_api_schemas(tmp_path: Path) -> None:
    """Successful routes previously advertised objects with no resource contract."""
    app = create_app(tmp_path)
    for endpoint in _api_routes(app.routes):
        if endpoint.path.startswith("/api/"):
            assert endpoint.response_model not in (None, dict[str, Any], list[dict[str, Any]])
    with TestClient(app, headers={"host": "127.0.0.1:8008"}) as client:
        document = client.get("/openapi.json").json()
    for path, operations in document["paths"].items():
        if not path.startswith("/api/"):
            continue
        for operation in operations.values():
            ok = next(v for k, v in operation["responses"].items() if k.startswith("2"))
            schema = ok["content"]["application/json"]["schema"]
            assert "$ref" in schema, path
            resource = document["components"]["schemas"][schema["$ref"].split("/")[-1]]
            assert resource.get("properties") or resource.get("anyOf") or resource.get("items", {}).get("$ref"), path


def test_real_status_round_trip() -> None:
    """An absent ledger must remain an absent ledger, without invented status fields."""
    root = Path(__file__).resolve().parents[1]
    app = create_app(root)
    route = next(r for r in _api_routes(app.routes) if r.path == "/api/status")
    adapter: TypeAdapter[Any] = TypeAdapter(route.response_model)
    # Compare wire form against wire form. `status()` keys its nights by integer, and JSON has no integer keys, so
    # a Python-level comparison would fail on a difference no client can ever observe.
    raw = json.loads(json.dumps(jsonable_encoder(status(root))))
    assert adapter.dump_python(adapter.validate_python(raw), mode="json", exclude_unset=True) == raw
    with TestClient(app, headers={"host": "127.0.0.1:8008"}) as client:
        assert client.get("/api/status").json() == raw


def test_packaged_objectives_round_trip() -> None:
    """An empty collection alone could not expose missing objective or recipe fields."""
    from pravrudhi.api.schemas import ObjectiveDetailResponse
    from pravrudhi.application.objectives import PACKAGED_OBJECTIVES, load, summary
    from pravrudhi.application.recipes import resolve

    root = Path(__file__).resolve().parents[1]
    paths = list(PACKAGED_OBJECTIVES.glob("*.yaml"))
    assert paths
    for path in paths:
        objective = load(path)
        raw = {**summary(root, objective), "recipe_detail": resolve(objective.recipes)}
        assert ObjectiveDetailResponse.model_validate(raw).model_dump(mode="json") == raw


def test_initialised_status_round_trip(tmp_path: Path) -> None:
    """Testing only an absent ledger left the replayed status branch unchecked."""
    from pravrudhi.api.schemas import StatusResponse
    from pravrudhi.application.init import init_project

    init_project(tmp_path)
    raw = jsonable_encoder(status(tmp_path))
    assert StatusResponse.model_validate(raw).model_dump(mode="json") == raw


def test_openapi_examples_match_models(tmp_path: Path) -> None:
    """Documentation examples could previously drift away from the resource schemas."""
    document = create_app(tmp_path).openapi()
    for path in ("/api/health", "/api/status", "/api/objectives", "/api/doctor"):
        schema = document["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        resource = document["components"]["schemas"][schema["$ref"].split("/")[-1]]
        assert resource["examples"]
        route = next(r for r in _api_routes(create_app(tmp_path).routes) if r.path == path)
        adapter: TypeAdapter[Any] = TypeAdapter(route.response_model)
        for example in resource["examples"]:
            assert adapter.dump_python(adapter.validate_python(example), mode="json") == example


def test_audit_extensions_and_sparse_results_survive() -> None:
    """Sparse external audits and counted night outcomes must not gain fields or lose extensions."""
    from pravrudhi.api.schemas import ExternalResponse, NightResult

    external = {"seq": 0, "night": 0, "kind": "external_eval", "tool": "evalplus", "extension": {"ready": True}}
    assert ExternalResponse.model_validate(external).model_dump(mode="json", exclude_unset=True) == external
    night = {"spent_gpu_h": None, "outcomes": {"kept": 1}, "incumbent": None}
    assert NightResult.model_validate(night).model_dump(mode="json") == night
