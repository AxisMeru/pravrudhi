"""updates.py detects staleness against the GitHub releases API; the fetch is faked so no test touches the network."""

from __future__ import annotations

from typing import Any

import pytest

from pravrudhi import __version__
from pravrudhi.application import updates


def _fetch_returning(payload: Any) -> updates.FetchFn:
    def fetch(url: str, timeout: float) -> Any:
        return payload

    return fetch


def _fetch_raising(exc: Exception) -> updates.FetchFn:
    def fetch(url: str, timeout: float) -> Any:
        raise exc

    return fetch


def test_current_reports_engine_and_kernel_version() -> None:
    cur = updates.current()
    assert cur["version"] == __version__
    assert cur["kernel_version"]


def test_latest_newer_tag_marks_update_available_with_plausible_command() -> None:
    fetch = _fetch_returning({"tag_name": "v99.0.0", "html_url": "https://example.invalid/releases/v99.0.0"})

    result = updates.status(fetch=fetch)

    assert result["latest"] == {"tag": "v99.0.0", "url": "https://example.invalid/releases/v99.0.0"}
    assert result["update_available"] is True
    assert result["how"]
    assert "pravrudhi" in result["how"] or "git pull" in result["how"]


def test_latest_equal_to_current_is_not_an_update() -> None:
    fetch = _fetch_returning({"tag_name": f"v{__version__}", "html_url": ""})

    result = updates.status(fetch=fetch)

    assert result["update_available"] is False


def test_offline_fetch_returns_none_without_raising() -> None:
    fetch = _fetch_raising(OSError("network unreachable"))

    assert updates.latest(fetch=fetch) is None

    result = updates.status(fetch=fetch)
    assert result["latest"] is None
    assert result["update_available"] is False


def test_malformed_response_returns_none() -> None:
    assert updates.latest(fetch=_fetch_returning({"no_tag_here": True})) is None
    assert updates.latest(fetch=_fetch_returning(["not", "a", "dict"])) is None
    assert updates.latest(fetch=_fetch_returning({"tag_name": ""})) is None


def test_doctor_check_always_reports_ok() -> None:
    entry = updates.doctor_check()

    assert entry["name"] == "update_check"
    assert entry["ok"] is True
    assert isinstance(entry["detail"], str) and entry["detail"]


@pytest.mark.parametrize(
    ("tag", "current_version", "expected"),
    [
        ("v1.2.0", "1.1.0", True),
        ("v1.1.0", "1.1.0", False),
        ("v1.0.0", "1.1.0", False),
        ("nightly-build", "1.1.0", True),
    ],
)
def test_is_newer_compares_semver_when_possible(tag: str, current_version: str, expected: bool) -> None:
    assert updates._is_newer(tag, current_version) is expected


class TestNotKnowingIsNotBeingUpToDate:
    """A check that could not run must not read as a check that found nothing.

    `status` returned `update_available: false` whether GitHub said "nothing newer" or could not be reached at
    all, so a rate-limited or offline machine reported itself current. For an unattended updater that is the
    worst shape of failure: it stops updating and says everything is fine. Found by rate-limiting this very
    machine while cutting a release — the engine answered "no update available" for a release that existed.
    """

    def test_a_reachable_api_with_nothing_newer_is_a_completed_check(self) -> None:
        from pravrudhi.application.updates import status

        st = status(fetch=lambda _url, _t: {"tag_name": "v0.0.1", "html_url": "https://x"})
        assert st["checked"] is True
        assert st["update_available"] is False

    def test_an_unreachable_api_is_not_a_completed_check(self) -> None:
        from pravrudhi.application.updates import status

        def unreachable(_url: str, _timeout: float) -> dict[str, object]:
            raise OSError("rate limited")

        st = status(fetch=unreachable)
        assert st["checked"] is False, "a failed check reported itself as a completed one"
        assert st["latest"] is None
        assert st["update_available"] is False, "not knowing is never a reason to offer an update"

    def test_a_newer_release_is_still_offered(self) -> None:
        from pravrudhi.application.updates import status

        st = status(fetch=lambda _url, _t: {"tag_name": "v999.0.0", "html_url": "https://x"})
        assert st["checked"] is True and st["update_available"] is True
