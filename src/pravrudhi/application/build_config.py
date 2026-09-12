"""What a root declares about its own build-mode loop: `.pravrudhi/config.yaml`'s `build:` block.

r-9c8646fc: build-mode dispatch always validated with the engine's own `BUILD_VALIDATE`
(`uv run ruff check src tests && uv run mypy src && uv run pytest -q tests`) and scoped writes to the engine's
own path prefixes (`src/`, `tests/`, `app/`, `scripts/`, `docs/`, `configs/`, `plugin/`) - fine for this repository,
which those commands and prefixes describe, and meaningless for a root that is a JavaScript/TypeScript product
repository: a heartbeat there would "validate" a Python test suite that is not even present, and could never name
a path under `frontend/` or `desktop/` as buildable.

A root that is not the engine's own studio checkout may instead declare `build: {validate: <command>,
allowed_prefixes: [...]}`, and every reader of this module treats an absent or empty block as "this root is the
studio, or has not said otherwise" and falls back to the engine's own defaults - so a root that never opts in
behaves exactly as it always has.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# A root's own layout, once it has one: preserved between init's inference and every later reader so that
# "which prefixes may a build criterion touch" always means the same list `pravrudhi doctor` prints.
DEFAULT_PREFIXES: tuple[str, ...] = ("src/", "tests/", "app/", "scripts/", "docs/", "configs/", "plugin/")

# Directories that are never their own npm package for the purpose of inferring a build loop, even when one of
# them happens to hold a stray package.json (a vendored dependency, a build cache).
_IGNORED_PACKAGE_DIRS = frozenset({
    "node_modules", "dist", "build", ".git", ".pravrudhi", "__pycache__", ".venv", "venv",
    ".next", "out", "coverage", ".turbo", ".worktrees",
})

_ROOT_SOURCE_DIR_CANDIDATES: tuple[str, ...] = ("src", "app", "lib", "test", "tests")


@dataclass(frozen=True)
class BuildConfig:
    """A root's own build declaration, or the empty default when it has not made one."""

    validate: str | None = None
    allowed_prefixes: tuple[str, ...] = ()


def config_path(root: Path) -> Path:
    return Path(root) / ".pravrudhi" / "config.yaml"


def _read_yaml(root: Path) -> dict[str, Any]:
    path = config_path(root)
    if not path.is_file():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return raw if isinstance(raw, dict) else {}


def load_build_config(root: Path) -> BuildConfig:
    """This root's own `build:` block, or the empty `BuildConfig` when it has none."""
    block = _read_yaml(Path(root)).get("build")
    if not isinstance(block, dict):
        return BuildConfig()
    validate = block.get("validate")
    prefixes = block.get("allowed_prefixes")
    return BuildConfig(
        validate=str(validate).strip() or None if validate not in (None, "") else None,
        allowed_prefixes=tuple(str(p) for p in prefixes if str(p).strip()) if isinstance(prefixes, list) else (),
    )


def resolved_build_validate(root: Path) -> str:
    """The command a build-mode criterion in this root validates with: its own declaration, or the engine's."""
    from pravrudhi.application.integrate import BUILD_VALIDATE

    return load_build_config(root).validate or BUILD_VALIDATE


def resolved_allowed_prefixes(root: Path | None) -> tuple[str, ...]:
    """The directory prefixes build-mode work in this root may write under: this root's own declaration, or the
    engine's own set. `root=None` (no workspace to read a declaration from, e.g. a bare unit test) always gets
    the engine's own set, same as a root that never declared one."""
    if root is not None:
        declared = load_build_config(root).allowed_prefixes
        if declared:
            return declared
    return DEFAULT_PREFIXES


def _package_dirs(root: Path) -> list[Path]:
    """This root's immediate subdirectories that are their own npm package (their own `package.json`), in the
    shape of a small JS/TS monorepo (`frontend/`, `desktop/`) rather than one package at the root."""
    if not root.is_dir():
        return []
    return sorted(
        p for p in root.iterdir()
        if p.is_dir() and p.name not in _IGNORED_PACKAGE_DIRS and not p.name.startswith(".")
        and (p / "package.json").is_file()
    )


def _npm_commands(pkg_dir: Path, *, prefix: str | None) -> list[str]:
    flag = f" --prefix {prefix}" if prefix else ""
    commands = [f"npm{flag} test"]
    if (pkg_dir / "tsconfig.json").is_file():
        commands.append(f"npx{flag} tsc --noEmit")
    return commands


def infer_build_config(root: Path) -> dict[str, Any] | None:
    """What `init` should write as this root's `build:` block, inferred from what is actually present - or
    `None` when nothing recognisable is, because a guessed command that might not even run is worse than no
    declaration (which already falls back to the engine's own default, honestly, via `resolved_build_validate`).

    Several packages (each an immediate subdirectory with its own `package.json`, e.g. `frontend/` and
    `desktop/`) chain their commands and each contributes its own directory to `allowed_prefixes`. One
    root-level `package.json` is the whole repository's package; a `pyproject.toml` with none of the above
    is read as a plain Python project. First match wins - a root is one of these, not several at once.
    """
    root = Path(root)
    packages = _package_dirs(root)
    if packages:
        commands: list[str] = []
        prefixes: list[str] = []
        for pkg in packages:
            commands.extend(_npm_commands(pkg, prefix=pkg.name))
            prefixes.append(f"{pkg.name}/")
        return {"validate": " && ".join(commands), "allowed_prefixes": prefixes}

    if (root / "package.json").is_file():
        commands = _npm_commands(root, prefix=None)
        prefixes = [f"{name}/" for name in _ROOT_SOURCE_DIR_CANDIDATES if (root / name).is_dir()]
        return {"validate": " && ".join(commands), "allowed_prefixes": prefixes or ["src/"]}

    if (root / "pyproject.toml").is_file():
        prefixes = [f"{name}/" for name in ("src", "tests") if (root / name).is_dir()]
        return {"validate": "uv run pytest -q", "allowed_prefixes": prefixes or ["src/"]}

    return None


__all__ = [
    "BuildConfig", "DEFAULT_PREFIXES", "config_path", "infer_build_config", "load_build_config",
    "resolved_allowed_prefixes", "resolved_build_validate",
]
