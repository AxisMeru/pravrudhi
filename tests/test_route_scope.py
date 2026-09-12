"""route_scope.unscoped_routes: the general form of the guards r-workspace-scoped-writes (S11) and
r-memory-per-user (S12) each added by hand, over synthetic source first and the real API files last."""

from __future__ import annotations

from pathlib import Path

from pravrudhi.application.route_scope import unscoped_routes_in_file

USER_FACING = frozenset({"/api/objectives", "/api/memory", "/api/me", "/api/other"})


def _write(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "routes.py"
    path.write_text(source)
    return path


def test_a_route_with_no_resolver_call_at_all_is_flagged(tmp_path: Path) -> None:
    source = """
class API:
    def post(self, path): return lambda f: f

api = API()

@api.post("/api/objectives")
def create_objective(req, user=None):
    write(root, obj)
"""
    found = unscoped_routes_in_file(_write(tmp_path, source), user_facing_paths=USER_FACING)
    assert [(r.method, r.path, r.function) for r in found] == [("POST", "/api/objectives", "create_objective")]


def test_a_route_calling_project_directly_is_not_flagged(tmp_path: Path) -> None:
    source = """
class API:
    def post(self, path): return lambda f: f

api = API()

@api.post("/api/objectives")
def create_objective(req, user=None, workspace=None):
    here = _project(user, workspace)
    write(here, obj)
"""
    assert unscoped_routes_in_file(_write(tmp_path, source), user_facing_paths=USER_FACING) == []


def test_a_route_calling_a_local_helper_that_itself_resolves_is_not_flagged(tmp_path: Path) -> None:
    """`server.py`'s own `_keys(user, workspace)` calls `_project` internally; a route that only calls `_keys`
    must be recognised as resolved too, transitively."""
    source = """
class API:
    def get(self, path): return lambda f: f

api = API()

def _keys(user, workspace):
    return store_for_project(_project(user, workspace))

@api.get("/api/other")
def providers_ep(user=None, workspace=None):
    configured = _keys(user, workspace).configured()
    return configured
"""
    assert unscoped_routes_in_file(_write(tmp_path, source), user_facing_paths=USER_FACING) == []


def test_a_route_passing_user_id_to_a_call_is_not_flagged(tmp_path: Path) -> None:
    """`application/workspaces.py`'s functions take `user_id`, not a root `Path` - a call passing `user.id`
    directly is the same guarantee under a different name."""
    source = """
class API:
    def get(self, path): return lambda f: f

api = API()

@api.get("/api/objectives")
def workspaces_ep(user=None):
    return list_workspaces(user.id)
"""
    assert unscoped_routes_in_file(_write(tmp_path, source), user_facing_paths=USER_FACING) == []


def test_a_route_with_no_user_parameter_is_not_identity_aware_and_is_not_flagged(tmp_path: Path) -> None:
    source = """
class API:
    def get(self, path): return lambda f: f

api = API()

@api.get("/api/objectives")
def health_ep():
    return read(root)
"""
    assert unscoped_routes_in_file(_write(tmp_path, source), user_facing_paths=USER_FACING) == []


def test_a_route_outside_the_user_facing_set_is_not_flagged(tmp_path: Path) -> None:
    """An admin-only route's project is unconditionally the engine root; it is not this check's business
    unless the caller passes its path in `user_facing_paths` (api.roles.USER_FACING in real use)."""
    source = """
class API:
    def get(self, path): return lambda f: f

api = API()

@api.get("/api/candidates")
def candidates_ep(user=None):
    return read(root)
"""
    assert unscoped_routes_in_file(_write(tmp_path, source), user_facing_paths=USER_FACING) == []


def test_me_is_exempt_even_though_it_never_resolves_anything(tmp_path: Path) -> None:
    """`/api/me` echoes the caller's own identity fields and touches no project or per-user state at all."""
    source = """
class API:
    def get(self, path): return lambda f: f

api = API()

@api.get("/api/me")
def me(user=None):
    return {"id": user.id if user else None}
"""
    assert unscoped_routes_in_file(_write(tmp_path, source), user_facing_paths=USER_FACING) == []


def test_a_function_with_no_route_decorator_is_never_flagged_whatever_it_does(tmp_path: Path) -> None:
    source = """
def helper(user=None):
    return read(root)
"""
    assert unscoped_routes_in_file(_write(tmp_path, source), user_facing_paths=USER_FACING) == []


# --- Against the real, current API files: the live regression guard --------------------------------------


def test_no_unscoped_route_exists_in_the_real_api_files() -> None:
    from pravrudhi.application.route_scope import unscoped_routes

    api_dir = Path(__file__).resolve().parents[1] / "src" / "pravrudhi" / "api"
    files = [api_dir / "server.py", api_dir / "chat.py", api_dir / "runs.py"]
    assert all(p.exists() for p in files), "the real API files moved; update this test's paths"
    found = unscoped_routes(files)
    assert found == [], f"routes touching project state without resolving a per-caller project or store: {found}"
