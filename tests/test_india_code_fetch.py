"""Tests for scripts/india_code_fetch.py: fetching a fixed, named set of Act PDFs from
indiacode.gov.in under the operator-approved constraints (one at a time, real pauses, a
truthful User-Agent, no retries, stop at the first 403/429). All network access is faked
here -- these tests never touch indiacode.gov.in.
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from india_code_fetch import (  # noqa: E402
    ACTS,
    USER_AGENT,
    FetchStopped,
    bitstream_url,
    download_bitstream,
    fetch_all,
    fetch_one,
)


def test_acts_registry_is_the_fixed_named_five():
    names = {a["act"] for a in ACTS}
    assert names == {
        "Indian Penal Code",
        "Code of Criminal Procedure",
        "Bharatiya Nyaya Sanhita",
        "Bharatiya Nagarik Suraksha Sanhita",
        "Indian Contract Act",
    }
    assert len(ACTS) == 5
    for a in ACTS:
        assert a["bitstream_id"] and a["filename"] and a["year"]


def test_user_agent_names_project_and_a_contact():
    assert "prabhasa-samskrutam" in USER_AGENT
    assert "@" in USER_AGENT  # a contact address, not an anonymous client string


def test_bitstream_url_format():
    url = bitstream_url("337c7a03-60c6-4953-8dd6-90e65c343629")
    assert url == (
        "https://indiacode.gov.in/server/api/core/bitstreams/"
        "337c7a03-60c6-4953-8dd6-90e65c343629/content"
    )


def test_fetch_one_writes_file_and_records_sha256(tmp_path):
    act = ACTS[0]
    payload = b"%PDF-1.4 fake content for test\n"
    calls = []

    def fake_fetch_bytes(url: str) -> bytes:
        calls.append(url)
        return payload

    record = fetch_one(act, tmp_path, fetch_bytes=fake_fetch_bytes)

    written = tmp_path / act["filename"]
    assert written.read_bytes() == payload
    assert record["sha256"] == hashlib.sha256(payload).hexdigest()
    assert record["size_bytes"] == len(payload)
    assert record["act"] == act["act"]
    assert record["filename"] == act["filename"]
    assert record["bitstream_id"] == act["bitstream_id"]
    assert "fetched_at" in record
    assert calls == [bitstream_url(act["bitstream_id"])]


def test_fetch_all_pauses_between_but_not_before_the_first(tmp_path):
    sleeps = []
    fetched = []

    def fake_fetch_bytes(url: str) -> bytes:
        fetched.append(url)
        return b"content"

    fetch_all(
        ACTS[:3],
        tmp_path,
        tmp_path / "manifest.json",
        fetch_bytes=fake_fetch_bytes,
        sleep_fn=lambda s: sleeps.append(s),
        pause_range=(30.0, 30.0),
    )

    assert len(fetched) == 3
    assert sleeps == [30.0, 30.0]  # one fewer pause than files: never before the first


def test_fetch_all_never_retries_a_failing_fetch(tmp_path):
    calls = []

    def fake_fetch_bytes(url: str) -> bytes:
        calls.append(url)
        raise TimeoutError("simulated network timeout")

    try:
        fetch_all(
            ACTS[:2],
            tmp_path,
            tmp_path / "manifest.json",
            fetch_bytes=fake_fetch_bytes,
            sleep_fn=lambda s: None,
            pause_range=(0.0, 0.0),
        )
    except TimeoutError:
        pass
    else:
        raise AssertionError("expected the timeout to propagate and stop the run")

    assert calls == [bitstream_url(ACTS[0]["bitstream_id"])]  # only one attempt, no retry


def test_fetch_all_stops_at_first_403_and_keeps_prior_progress(tmp_path):
    calls = []

    def fake_fetch_bytes(url: str) -> bytes:
        calls.append(url)
        if len(calls) == 2:
            raise FetchStopped(f"403 Forbidden from {url}")
        return b"content-" + str(len(calls)).encode()

    manifest_path = tmp_path / "manifest.json"
    try:
        fetch_all(
            ACTS[:3],
            tmp_path,
            manifest_path,
            fetch_bytes=fake_fetch_bytes,
            sleep_fn=lambda s: None,
            pause_range=(0.0, 0.0),
        )
    except FetchStopped:
        pass
    else:
        raise AssertionError("expected FetchStopped to propagate")

    # Only the first two bitstreams were ever requested -- the third (never attempted) is
    # not fetched after a stop.
    assert calls == [bitstream_url(ACTS[0]["bitstream_id"]), bitstream_url(ACTS[1]["bitstream_id"])]
    # The first act's success is preserved on disk even though the run then stopped.
    manifest = json.loads(manifest_path.read_text())
    assert len(manifest) == 1
    assert manifest[0]["act"] == ACTS[0]["act"]
    assert (tmp_path / ACTS[0]["filename"]).exists()
    assert not (tmp_path / ACTS[2]["filename"]).exists()


def test_download_bitstream_raises_fetchstopped_on_403(monkeypatch):
    def fake_urlopen(req, timeout):  # noqa: ARG001
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr("india_code_fetch.urlopen", fake_urlopen)
    try:
        download_bitstream("https://indiacode.gov.in/server/api/core/bitstreams/x/content")
    except FetchStopped as e:
        assert "403" in str(e)
    else:
        raise AssertionError("expected FetchStopped on a 403 response")


def test_download_bitstream_raises_fetchstopped_on_429(monkeypatch):
    def fake_urlopen(req, timeout):  # noqa: ARG001
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", {}, None)

    monkeypatch.setattr("india_code_fetch.urlopen", fake_urlopen)
    try:
        download_bitstream("https://indiacode.gov.in/server/api/core/bitstreams/x/content")
    except FetchStopped as e:
        assert "429" in str(e)
    else:
        raise AssertionError("expected FetchStopped on a 429 response")


def test_download_bitstream_other_http_errors_propagate_unwrapped(monkeypatch):
    def fake_urlopen(req, timeout):  # noqa: ARG001
        raise urllib.error.HTTPError(req.full_url, 503, "Service Unavailable", {}, None)

    monkeypatch.setattr("india_code_fetch.urlopen", fake_urlopen)
    try:
        download_bitstream("https://indiacode.gov.in/server/api/core/bitstreams/x/content")
    except urllib.error.HTTPError as e:
        assert e.code == 503
    else:
        raise AssertionError("expected the underlying HTTPError to propagate for non-403/429")
