"""efe/ has no I/O and no global RNG: checked by AST, not by convention."""

import ast
from pathlib import Path

EFE = Path(__file__).resolve().parents[2] / "src" / "pravrudhi_kernel" / "efe"
BANNED_MODULES = {
    "os",
    "sys",
    "io",
    "pathlib",
    "json",
    "random",
    "subprocess",
    "socket",
    "yaml",
    "requests",
    "torch",
}
BANNED_CALLS = {"open", "print", "input"}
BANNED_ATTRS = {("np", "random"), ("numpy", "random")}


def test_efe_modules_are_pure() -> None:
    problems = []
    for py in sorted(EFE.glob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.split(".")[0] in BANNED_MODULES:
                        problems.append(f"{py.name}:{node.lineno} import {a.name}")
            if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in BANNED_MODULES:
                problems.append(f"{py.name}:{node.lineno} from {node.module}")
            if isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Name) and f.id in BANNED_CALLS:
                    problems.append(f"{py.name}:{node.lineno} call {f.id}()")
                if (
                    isinstance(f, ast.Attribute)
                    and isinstance(f.value, ast.Attribute)
                    and (isinstance(f.value.value, ast.Name) and (f.value.value.id, f.value.attr) in BANNED_ATTRS)
                ):
                    problems.append(f"{py.name}:{node.lineno} np.random.* (global RNG)")
    assert not problems, problems
