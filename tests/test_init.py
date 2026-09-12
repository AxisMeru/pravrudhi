"""r-9c8646fc: `pravrudhi init --root <dir>` writes a `build:` block into `.pravrudhi/config.yaml` when the root
has no `src/pravrudhi/` of its own (a product or artifact repository, not this engine's checkout), inferring the
validate command from what is actually present."""

from __future__ import annotations

from pathlib import Path

import yaml

from pravrudhi.application.init import init_project


def _config(root: Path) -> dict:
    return yaml.safe_load((root / ".pravrudhi" / "config.yaml").read_text())


class TestInitInfersABuildBlockForAProductRepo:
    def test_a_root_with_the_engine_s_own_src_pravrudhi_gets_no_build_block(self, tmp_path: Path) -> None:
        (tmp_path / "src" / "pravrudhi").mkdir(parents=True)
        init_project(tmp_path)
        assert "build" not in _config(tmp_path)

    def test_a_root_with_nothing_recognisable_gets_no_build_block(self, tmp_path: Path) -> None:
        init_project(tmp_path)
        assert "build" not in _config(tmp_path)

    def test_a_js_ts_monorepo_shaped_like_pravrudhi_app_gets_a_chained_build_block(self, tmp_path: Path) -> None:
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        (frontend / "package.json").write_text("{}")
        (frontend / "tsconfig.json").write_text("{}")
        desktop = tmp_path / "desktop"
        desktop.mkdir()
        (desktop / "package.json").write_text("{}")

        init_project(tmp_path)

        build = _config(tmp_path)["build"]
        assert build["validate"] == (
            "npm --prefix desktop test && npm --prefix frontend test && npx --prefix frontend tsc --noEmit"
        )
        assert build["allowed_prefixes"] == ["desktop/", "frontend/"]

    def test_a_plain_pyproject_root_gets_a_pytest_build_block(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
        (tmp_path / "tests").mkdir()

        init_project(tmp_path)

        build = _config(tmp_path)["build"]
        assert build["validate"] == "uv run pytest -q"
        assert build["allowed_prefixes"] == ["tests/"]

    def test_never_overwrites_an_existing_config(self, tmp_path: Path) -> None:
        """init is idempotent; a build: block is only ever inferred at first creation, same as every other
        default in DEFAULT_CONFIG."""
        (tmp_path / ".pravrudhi").mkdir()
        (tmp_path / ".pravrudhi" / "config.yaml").write_text("version: 1\n")
        (tmp_path / "package.json").write_text("{}")

        init_project(tmp_path)

        assert _config(tmp_path) == {"version": 1}


class TestDoctorReportsWhatInitInferred:
    def test_the_inferred_command_is_what_doctor_prints(self, tmp_path: Path) -> None:
        from pravrudhi.application.doctor import run_doctor

        (tmp_path / "package.json").write_text("{}")
        init_project(tmp_path)

        report = run_doctor(tmp_path)
        check = next(c for c in report["checks"] if c["name"] == "build_validate")
        assert "npm test" in check["detail"]
