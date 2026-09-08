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


class TestAskingGitHubAsSomebody:
    """Unauthenticated GitHub allows 60 requests an hour per address, and this machine spends that from three
    places at once: the engine's hourly release check, each desktop's update check, and anything else on the
    box. Exhausting it is not hypothetical — it happened while cutting 0.4.0, and the engine then reported
    "no update available" for a release that existed.

    A token raises the same calls to 5000 an hour. It is read from the environment so nothing is stored in the
    repository, and its absence changes nothing: the call goes out anonymously exactly as before.
    """

    @staticmethod
    def _headers_sent(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
        """Intercept at the opener, which is where the request now goes — patching `urlopen` would let a real
        request escape and prove nothing about what was sent."""
        from pravrudhi.application import updates

        seen: dict[str, str] = {}

        class FakeResponse:
            def read(self) -> bytes:
                return b"{}"

            def __enter__(self) -> FakeResponse:
                return self

            def __exit__(self, *_a: object) -> None:
                return None

        class FakeOpener:
            def open(self, request: Any, timeout: float = 0) -> FakeResponse:
                seen.update(dict(request.header_items()))
                return FakeResponse()

        monkeypatch.setattr(updates, "github_opener", lambda: FakeOpener())
        updates._default_fetch("https://api.github.com/x", 5.0)
        return seen

    def test_a_token_in_the_environment_is_sent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PRAVRUDHI_GITHUB_TOKEN", "a-token")
        seen = self._headers_sent(monkeypatch)
        assert any(v == "Bearer a-token" for v in seen.values()), f"no token was sent: {sorted(seen)}"

    def test_without_a_token_the_call_carries_no_authorization(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An anonymous check must stay anonymous rather than sending an empty credential."""
        monkeypatch.delenv("PRAVRUDHI_GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        seen = self._headers_sent(monkeypatch)
        assert not any("authorization" in k.lower() for k in seen), f"sent a credential anyway: {sorted(seen)}"


class TestTheTokenNeverLeavesGitHub:
    """A release asset's URL always redirects to a CDN host, so a request that carries a credential and follows
    redirects hands that credential to whatever it lands on. Not theoretical: fetching a CI log in this session
    redirected to Azure blob storage, and the header followed it there.

    urllib's default redirect handler copies headers onto the new request. The opener used here removes the
    Authorization header the moment the host stops being GitHub's.
    """

    @staticmethod
    def _redirected(opener_factory, target: str) -> dict[str, str]:
        import urllib.request

        opener = opener_factory()
        handler = next(h for h in opener.handlers if isinstance(h, urllib.request.HTTPRedirectHandler))
        original = urllib.request.Request(
            "https://api.github.com/x", headers={"Authorization": "Bearer secret-token"}
        )
        new = handler.redirect_request(original, None, 302, "Found", {}, target)
        return {} if new is None else dict(new.header_items())

    def test_a_redirect_off_github_drops_the_credential(self) -> None:
        from pravrudhi.application.updates import github_opener

        headers = self._redirected(github_opener, "https://objects.githubusercontent.com/asset")
        assert not any("authorization" in k.lower() for k in headers), (
            f"the token followed the redirect to a CDN: {sorted(headers)}"
        )

    def test_a_redirect_within_github_keeps_it(self) -> None:
        """The API redirects within its own hosts too, and dropping the credential there would break the call."""
        from pravrudhi.application.updates import github_opener

        headers = self._redirected(github_opener, "https://api.github.com/y")
        assert any("authorization" in k.lower() for k in headers), "the credential was dropped on GitHub itself"

    def test_a_lookalike_host_does_not_count_as_github(self) -> None:
        from pravrudhi.application.updates import github_opener

        for target in ("https://api.github.com.evil.test/x", "https://notgithub.com/x", "http://api.github.com/x"):
            headers = self._redirected(github_opener, target)
            assert not any("authorization" in k.lower() for k in headers), f"credential sent to {target}"
