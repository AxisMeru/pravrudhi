"""The general form of the guard r-workspace-scoped-writes (S11) and r-memory-per-user (S12) each added by
hand: a route whose handler is identity-aware (it takes a `user` parameter) but never resolves that identity
into a per-caller project or store anywhere in its own body reads the engine's own root instead - exactly the
shape both incidents were. `create_objective` took no workspace at all and called `write(root, obj)` directly;
`store_for(root, user)` was called with the bare engine root in four places across two files. Neither would
have been caught by a schema check (FastAPI dependencies like `user` never appear as OpenAPI parameters), so
this reads the route handlers' own source instead.

This is deliberately narrow, matching what both incidents actually were: a route that never calls one of the
known per-caller resolvers anywhere in its body. It is not a full data-flow analysis and cannot see a resolver
called on only one of several branches while another branch reads root directly - r-workspace-scoped-writes's
sibling defect in `GET /workspaces` (a genuinely anonymous, non-admin caller under enabled authentication read
`str(root)` because that branch checked only `user is None`, not `is_admin(user)`, while the same function's
other branch correctly resolved by `user.id`) is exactly that shape, and this check does not catch it - it was
found and fixed by reading the code, the same way both incidents originally were.

Routes classified `ADMIN_ONLY` in `api/roles.py` are exempt outright: the operator's project is unconditionally
the engine root, so there is nothing to resolve. Within `USER_FACING`, `EXEMPT_PATHS` names the small number of
routes that are identity-aware but touch no project or per-user state at all - today, only `/api/me`, which
echoes the caller's own identity fields and never reads or writes anything a workspace or a per-user store
would hold.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

# Functions whose presence in a route's own body (or transitively, in a same-file helper it calls) proves the
# route resolved its caller into something other than the bare engine root: server.py's `_project`/`_memory`,
# chat.py's own `_memory`, and the lower-level functions they and other routes call directly
# (`memory_store.store_for`, `workspace_root.root_for`).
BASE_RESOLVERS: frozenset[str] = frozenset({"_project", "_memory", "store_for", "root_for"})

# Identity-aware, user-facing routes that legitimately touch no project or per-user state. `/api/me` echoes the
# caller's own identity fields (id, email, edition, access, role) and reads or writes nothing a workspace or a
# per-user store would hold, so it has nothing to resolve.
EXEMPT_PATHS: frozenset[str] = frozenset({"/api/me"})

_HTTP_METHODS = frozenset({"get", "post", "patch", "delete", "put"})


@dataclass(frozen=True)
class UnscopedRoute:
    file: str
    method: str
    path: str
    function: str


def _route_decorator(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str, str | None] | None:
    """The route's `(method, path)` from a `@api.get("/x")`-shaped decorator, or `None` if this function is not
    a route at all."""
    for dec in node.decorator_list:
        if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr in _HTTP_METHODS:
            first = dec.args[0] if dec.args else None
            path = first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else None
            return dec.func.attr, path
    return None


def _has_user_param(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(a.arg == "user" for a in node.args.args + node.args.kwonlyargs)


def _collect_functions(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every function defined anywhere in the file, nested or not, by name - good enough for one file's own
    helpers, which is all `resolves_per_caller` ever looks up."""
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            functions[node.name] = node
            self.generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    Visitor().visit(tree)
    return functions


def _calls_and_user_id_args(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[list[str], bool]:
    """Every function name a call in this function's own body invokes (not descending into a nested def, whose
    calls belong to that function instead), and whether any of those calls passed `user.id` as an argument -
    the shape `application/workspaces.py`'s functions take instead of a root Path."""
    calls: list[str] = []
    passes_user_id = False

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            pass  # a nested function's calls are its own, not this route's

        visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

        def visit_Call(self, node: ast.Call) -> None:
            nonlocal passes_user_id
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else None
            if name:
                calls.append(name)
            for arg in node.args:
                if (
                    isinstance(arg, ast.Attribute) and arg.attr == "id"
                    and isinstance(arg.value, ast.Name) and arg.value.id == "user"
                ):
                    passes_user_id = True
            self.generic_visit(node)

    for stmt in node.body:
        Visitor().visit(stmt)
    return calls, passes_user_id


def _resolves_per_caller(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    seen: set[str] | None = None,
) -> bool:
    seen = seen if seen is not None else set()
    if node.name in seen:
        return False
    seen.add(node.name)
    calls, passes_user_id = _calls_and_user_id_args(node)
    if passes_user_id:
        return True
    for name in calls:
        if name in BASE_RESOLVERS:
            return True
        if name in functions and _resolves_per_caller(functions[name], functions, seen):
            return True
    return False


def unscoped_routes_in_file(path: Path, *, user_facing_paths: frozenset[str]) -> list[UnscopedRoute]:
    """Every identity-aware route in `path` whose own body (transitively, through same-file helpers) never
    resolves a per-caller project or store - restricted to `user_facing_paths` (pass `api.roles.USER_FACING`),
    since an admin-only route's project is unconditionally the engine root and has nothing to resolve."""
    tree = ast.parse(path.read_text())
    functions = _collect_functions(tree)
    out: list[UnscopedRoute] = []

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            decorated = _route_decorator(node)
            if decorated and _has_user_param(node):
                method, route_path = decorated
                if (
                    route_path in user_facing_paths
                    and route_path not in EXEMPT_PATHS
                    and not _resolves_per_caller(node, functions)
                ):
                    out.append(UnscopedRoute(file=path.name, method=method.upper(), path=route_path or "", function=node.name))
            self.generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    Visitor().visit(tree)
    return out


def unscoped_routes(files: list[Path]) -> list[UnscopedRoute]:
    """`unscoped_routes_in_file` over every file given, against the engine's own `USER_FACING` classification."""
    from pravrudhi.api.roles import USER_FACING

    out: list[UnscopedRoute] = []
    for path in files:
        if path.exists():
            out.extend(unscoped_routes_in_file(path, user_facing_paths=USER_FACING))
    return out
