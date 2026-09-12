"""r-9c8646fc: a root may declare its own build loop in `.pravrudhi/config.yaml`'s `build:` block, so a
JavaScript/TypeScript product repository gets its own validate command and its own allowed prefixes instead of
the engine's (`uv run ruff check src tests && uv run mypy src && uv run pytest -q tests`, `src/` / `tests/` / ...).
"""

from __future__ import annotations

from pathlib import Path

from pravrudhi.application import build_config


def _write_config(root: Path, body: str) -> None:
    (root / ".pravrudhi").mkdir(parents=True, exist_ok=True)
    (root / ".pravrudhi" / "config.yaml").write_text(body)


class TestLoadBuildConfig:
    def test_no_config_file_is_the_empty_default(self, tmp_path: Path) -> None:
        cfg = build_config.load_build_config(tmp_path)
        assert cfg.validate is None and cfg.allowed_prefixes == ()

    def test_a_config_with_no_build_block_is_the_empty_default(self, tmp_path: Path) -> None:
        _write_config(tmp_path, "version: 1\n")
        cfg = build_config.load_build_config(tmp_path)
        assert cfg.validate is None and cfg.allowed_prefixes == ()

    def test_a_declared_build_block_is_read_verbatim(self, tmp_path: Path) -> None:
        _write_config(tmp_path, "build:\n  validate: npm test\n  allowed_prefixes: [frontend/, desktop/]\n")
        cfg = build_config.load_build_config(tmp_path)
        assert cfg.validate == "npm test"
        assert cfg.allowed_prefixes == ("frontend/", "desktop/")

    def test_a_malformed_config_file_is_the_empty_default_not_a_crash(self, tmp_path: Path) -> None:
        _write_config(tmp_path, "build: [this, is, not, a, mapping]\n")
        cfg = build_config.load_build_config(tmp_path)
        assert cfg.validate is None and cfg.allowed_prefixes == ()


class TestResolvedBuildValidate:
    def test_falls_back_to_the_engine_default_when_undeclared(self, tmp_path: Path) -> None:
        from pravrudhi.application.integrate import BUILD_VALIDATE

        assert build_config.resolved_build_validate(tmp_path) == BUILD_VALIDATE

    def test_uses_the_root_s_own_declared_command(self, tmp_path: Path) -> None:
        _write_config(tmp_path, "build:\n  validate: npm --prefix frontend test\n")
        assert build_config.resolved_build_validate(tmp_path) == "npm --prefix frontend test"


class TestResolvedAllowedPrefixes:
    def test_falls_back_to_the_engine_default_set(self, tmp_path: Path) -> None:
        assert build_config.resolved_allowed_prefixes(tmp_path) == build_config.DEFAULT_PREFIXES

    def test_none_root_also_gets_the_engine_default_set(self) -> None:
        assert build_config.resolved_allowed_prefixes(None) == build_config.DEFAULT_PREFIXES

    def test_uses_the_root_s_own_declared_prefixes(self, tmp_path: Path) -> None:
        _write_config(tmp_path, "build:\n  allowed_prefixes: [frontend/, desktop/]\n")
        assert build_config.resolved_allowed_prefixes(tmp_path) == ("frontend/", "desktop/")


class TestInferBuildConfig:
    def test_no_recognisable_project_infers_nothing(self, tmp_path: Path) -> None:
        assert build_config.infer_build_config(tmp_path) is None

    def test_two_sibling_npm_packages_chain_their_commands_like_pravrudhi_app(self, tmp_path: Path) -> None:
        """The exact shape confirmed for r-9c8646fc: frontend/ and desktop/, frontend/ also carrying a
        tsconfig.json, each contributing only when its own package.json exists. Package directories are visited
        in a fixed (alphabetical) order, so the inferred command is deterministic across machines - "desktop"
        sorts before "frontend"."""
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        (frontend / "package.json").write_text("{}")
        (frontend / "tsconfig.json").write_text("{}")
        desktop = tmp_path / "desktop"
        desktop.mkdir()
        (desktop / "package.json").write_text("{}")

        built = build_config.infer_build_config(tmp_path)

        assert built is not None
        assert built["validate"] == (
            "npm --prefix desktop test && npm --prefix frontend test && npx --prefix frontend tsc --noEmit"
        )
        assert built["allowed_prefixes"] == ["desktop/", "frontend/"]

    def test_a_sibling_directory_with_no_package_json_is_not_a_package(self, tmp_path: Path) -> None:
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        (frontend / "package.json").write_text("{}")
        (tmp_path / "docs").mkdir()  # not a package: no package.json

        built = build_config.infer_build_config(tmp_path)

        assert built is not None and built["allowed_prefixes"] == ["frontend/"]

    def test_a_single_root_level_package_json_is_the_whole_repository(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text("{}")
        (tmp_path / "src").mkdir()

        built = build_config.infer_build_config(tmp_path)

        assert built is not None
        assert built["validate"] == "npm test"
        assert built["allowed_prefixes"] == ["src/"]

    def test_a_root_package_json_with_tsconfig_adds_a_type_check(self, tmp_path: Path) -> None:
        (tmp_path / "package.json").write_text("{}")
        (tmp_path / "tsconfig.json").write_text("{}")

        built = build_config.infer_build_config(tmp_path)

        assert built is not None and built["validate"] == "npm test && npx tsc --noEmit"

    def test_a_pyproject_with_no_package_json_infers_a_pytest_command(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
        (tmp_path / "tests").mkdir()

        built = build_config.infer_build_config(tmp_path)

        assert built is not None
        assert built["validate"] == "uv run pytest -q"
        assert built["allowed_prefixes"] == ["tests/"]

    def test_node_modules_is_never_mistaken_for_a_package(self, tmp_path: Path) -> None:
        nm = tmp_path / "node_modules" / "some-dep"
        nm.mkdir(parents=True)
        (nm / "package.json").write_text("{}")

        assert build_config.infer_build_config(tmp_path) is None
