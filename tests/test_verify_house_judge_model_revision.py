"""Tests for `scripts/verify_house_judge_model_revision.py` -- the fail-closed HF revision check for the
house judge (mirrors the second-judge 32B's own boot-time verify step, an ops-only artifact not committed
to any repo; see the script's own module docstring). Every `model_info_fn` here is a test double -- no
network access, no `huggingface_hub` import needed to run these tests."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import NamedTuple

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from verify_house_judge_model_revision import InvalidPin, RevisionMismatch, main, verify_revision  # noqa: E402


class _Info(NamedTuple):
    sha: str | None


def _fixed(sha: str | None):
    def fn(repo: str, revision: str) -> _Info:
        return _Info(sha=sha)
    return fn


def _raising(exc: Exception):
    def fn(repo: str, revision: str) -> _Info:
        raise exc
    return fn


class TestVerifyRevision:
    def test_a_matching_full_sha_passes(self) -> None:
        full = "8ead9b1d95d843ae0d0883f97995a110274de1a8"
        assert verify_revision("AxisMeru/prabhasa-nyaya-element-judge-4b-v0", full, model_info_fn=_fixed(full)) == full

    def test_a_short_pin_matches_the_full_resolved_sha(self) -> None:
        """The 32B wrapper's own check is `sha.startswith(pin)`, never `==` -- a pin is often the short
        form of a full commit sha (e.g. the 32B adapter's own `0cb1d508`)."""
        full = "0cb1d5081234567890abcdef1234567890abcdef"
        assert verify_revision("AxisMeru/nyaya-element-judge-32b-v0", "0cb1d508", model_info_fn=_fixed(full)) == full

    def test_a_mismatched_sha_raises_revision_mismatch(self) -> None:
        with pytest.raises(RevisionMismatch, match="does not match pinned revision"):
            verify_revision("repo/x", "8ead9b1d", model_info_fn=_fixed("f6512880deadbeef"))

    def test_a_none_sha_raises_rather_than_silently_passing(self) -> None:
        """A resolved-but-empty sha is a failure, not a `None == None`-style accidental pass."""
        with pytest.raises(RevisionMismatch):
            verify_revision("repo/x", "8ead9b1d", model_info_fn=_fixed(None))

    def test_an_empty_string_sha_also_raises(self) -> None:
        with pytest.raises(RevisionMismatch):
            verify_revision("repo/x", "8ead9b1d", model_info_fn=_fixed(""))

    def test_a_resolution_error_propagates_out_of_verify_revision(self) -> None:
        """`verify_revision` itself does not catch resolution failures (network, unknown repo/revision) --
        `main`'s own broad except is where those become a FATAL exit; a caller of `verify_revision` directly
        gets the real exception, not a swallowed one."""
        with pytest.raises(RuntimeError, match="repo not found"):
            verify_revision("repo/x", "8ead9b1d", model_info_fn=_raising(RuntimeError("repo not found")))


class TestMainCLI:
    def _patch_model_info(self, monkeypatch: pytest.MonkeyPatch, fn) -> None:
        import verify_house_judge_model_revision as mod

        monkeypatch.setattr(mod, "_default_model_info_fn", fn)

    def test_matching_revision_via_flags_exits_zero(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        full = "8ead9b1d95d843ae0d0883f97995a110274de1a8"
        self._patch_model_info(monkeypatch, _fixed(full))
        code = main(["--repo", "AxisMeru/prabhasa-nyaya-element-judge-4b-v0", "--revision", full])
        assert code == 0
        assert "OK:" in capsys.readouterr().out

    def test_mismatched_revision_via_flags_exits_one(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._patch_model_info(monkeypatch, _fixed("f6512880deadbeef"))
        code = main(["--repo", "repo/x", "--revision", "8ead9b1d"])
        assert code == 1
        assert "FATAL:" in capsys.readouterr().err

    def test_a_resolution_exception_via_flags_exits_one_not_zero(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The broad except in `main` -- a network failure or an unknown repo/revision must never look like
        success just because no RevisionMismatch was specifically raised."""
        self._patch_model_info(monkeypatch, _raising(ConnectionError("could not reach huggingface.co")))
        code = main(["--repo", "repo/x", "--revision", "8ead9b1d"])
        assert code == 1
        assert "FATAL:" in capsys.readouterr().err

    def test_reads_model_name_and_model_revision_env_vars_when_flags_omitted(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The exact env var names the 4B endpoint's own deployment already uses (MODEL_NAME/MODEL_REVISION)
        -- so this script can be pointed at that endpoint's real env with zero translation."""
        full = "8ead9b1d95d843ae0d0883f97995a110274de1a8"
        monkeypatch.setenv("MODEL_NAME", "AxisMeru/prabhasa-nyaya-element-judge-4b-v0")
        monkeypatch.setenv("MODEL_REVISION", full)
        self._patch_model_info(monkeypatch, _fixed(full))
        assert main([]) == 0

    def test_missing_repo_is_refused_with_exit_code_2(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.delenv("MODEL_NAME", raising=False)
        monkeypatch.delenv("MODEL_REVISION", raising=False)
        code = main(["--revision", "8ead9b1d"])
        assert code == 2
        assert "MODEL_NAME" in capsys.readouterr().err

    def test_missing_revision_is_refused_with_exit_code_2(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """This is the exact gap that left the 4B judge unpinned before 2026-09-27 -- a repo with no
        revision at all must be refused outright, not silently 'verified' against nothing."""
        monkeypatch.delenv("MODEL_REVISION", raising=False)
        code = main(["--repo", "AxisMeru/prabhasa-nyaya-element-judge-4b-v0"])
        assert code == 2
        assert "MODEL_REVISION" in capsys.readouterr().err

    def test_flags_win_over_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MODEL_NAME", "wrong/repo")
        monkeypatch.setenv("MODEL_REVISION", "wrongrev")
        full = "8ead9b1d95d843ae0d0883f97995a110274de1a8"
        captured: dict[str, str] = {}

        def fn(repo: str, revision: str) -> _Info:
            captured["repo"] = repo
            captured["revision"] = revision
            return _Info(sha=full)

        self._patch_model_info(monkeypatch, fn)
        code = main(["--repo", "AxisMeru/prabhasa-nyaya-element-judge-4b-v0", "--revision", full])
        assert code == 0
        assert captured == {"repo": "AxisMeru/prabhasa-nyaya-element-judge-4b-v0", "revision": full}


class TestPinFormat:
    """R1 (review 5417648996): a weak pin such as "8" is a prefix of almost any sha and must not pass."""

    @pytest.mark.parametrize(
        "pin",
        ["", "8", "abc", "8ead9b1", "ABCDEF12", "main", "v1.0", "8ead9b1d95d843ae0d0883f97995a110274de1a8a", " 8ead9b1d"],
    )
    def test_a_weak_or_malformed_pin_is_refused_before_any_lookup(self, pin):
        def boom(repo: str, revision: str):
            raise AssertionError("model_info must not be called for an invalid pin")

        with pytest.raises(InvalidPin):
            verify_revision("repo/x", pin, model_info_fn=boom)

    @pytest.mark.parametrize("pin", ["8ead9b1d", "8ead9b1d95d843ae0d0883f97995a110274de1a8"])
    def test_eight_to_forty_lowercase_hex_is_accepted(self, pin):
        full = "8ead9b1d95d843ae0d0883f97995a110274de1a8"
        assert verify_revision("repo/x", pin, model_info_fn=_fixed(full)) == full

    def test_main_exits_2_with_fatal_on_a_weak_pin(self, monkeypatch, capsys):
        import verify_house_judge_model_revision as m

        monkeypatch.setattr(m, "_default_model_info_fn", lambda r, v: (_ for _ in ()).throw(AssertionError("no lookup")))
        assert main(["--repo", "repo/x", "--revision", "8"]) == 2
        assert "FATAL" in capsys.readouterr().err
