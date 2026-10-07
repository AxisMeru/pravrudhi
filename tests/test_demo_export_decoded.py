"""The demo export decodes before it checks and redacts, requires the private-name list, and publishes no captured
asks (2026-10-07).

On 2026-10-06 the public demo.json was found carrying captured sign-in URLs (a `login_hint` holding a percent-encoded
team email, a client id)
and teammates' first names inside captured operator asks. These tests pin the fixes: product content only; a value is decoded
(percent-encoding, \\uXXXX, HTML entities, repeatedly) before the checks and the redaction see it; any email-shaped
string or sign-in/OAuth URL
left after decoding refuses the write; and the private-name list (a path and its pinned sha256) is REQUIRED. Every
name in these tests is
invented and the list is a temporary file: the real list is never read, committed or quoted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pravrudhi.application import demo_export
from pravrudhi.application.demo_export import (
    CAPTURE_KEYS,
    PrivateNamesError,
    SecretInSnapshot,
    decode_all,
    decoded_hits,
    load_private_names,
    private_markers_left,
    public_view,
    scrub_decoded,
)
from tests._demo_names import TEST_NAMES, names_kwargs
from tests.test_demo_export_pii import _root_with_corpus

NAMES = TEST_NAMES  # ("Zorblat Quux", "Wibble", "sharath_sathish"): invented


def _write(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, data: object, **kw: object) -> Path:
    monkeypatch.setattr(demo_export, "build_demo", lambda root: data)
    monkeypatch.setattr(demo_export, "ASSETS_DIR", tmp_path / "no-assets")
    monkeypatch.setattr(demo_export, "MIN_CORPUS_DOCUMENTS", 1)
    kwargs = {**names_kwargs(tmp_path), **kw}
    return demo_export.write_demo(_root_with_corpus(tmp_path), tmp_path / "out" / "demo.json", **kwargs)  # type: ignore[arg-type]


# -- decoding ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw,decoded", [
    ("admin%40axismeru.com", "admin@axismeru.com"),
    ("admin%2540axismeru.com", "admin@axismeru.com"),  # encoded twice
    ("admin&#64;axismeru.com", "admin@axismeru.com"),
    ("admin&commat;axismeru.com", "admin@axismeru.com"),
    ("sharath%2Esathish", "sharath.sathish"),
    ("a\\u0040b.example", "a@b.example"),
    ("%5Cu0040", "@"),
])
def test_decode_all_undoes_percent_html_and_unicode_escapes(raw: str, decoded: str) -> None:
    assert decode_all(raw) == decoded


# -- R2's probe strings and the captured sign-in URL ------------------------------------------------

PROBES = [
    "https://accounts.example/o/oauth2/auth?client_" + "id=abc123&login_" + "hint=admin%40axismeru.com"
    "&state=xyz&code_" + "challenge=Q",
    "login_hint=admin%40axismeru.com",
    "admin%40AxisMeru.com",
    "admin&#64;axismeru.com",
    "someone%40gmail.com",
    "contact someone&#x40;gmail.com for access",
    "sharath%2Esathish",
    "sharath_sathish",
    "mail\\u0040example.org",
    "https://example.test/signin?next=%2Fhome%2Fss%2Fprojects",
]


@pytest.mark.parametrize("probe", PROBES)
def test_every_probe_is_removed_before_the_file_is_written(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, probe: str) -> None:
    out = _write(monkeypatch, tmp_path, {"status": {"note": probe, "keep": "a constructed matter about a cheque"}})
    text = out.read_text()
    assert json.loads(text)["status"]["keep"] == "a constructed matter about a cheque"
    d = decode_all(text)
    assert "@" not in d.replace("<redacted", "")
    for needle in ("login_hint", "client_id", "oauth", "code_challenge", "sharath", "/home/"):
        assert needle not in d.lower(), (probe, needle)
    assert private_markers_left(text, NAMES) == []


def test_a_sign_in_url_part_is_dropped_whole_not_trimmed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    out = json.loads(_write(monkeypatch, tmp_path, {"status": {"u": PROBES[0], "v": "ordinary text"}}).read_text())
    assert out["status"]["u"] == demo_export.DECODED_MARKER
    assert out["status"]["v"] == "ordinary text"


def test_a_decoded_email_refuses_the_write_if_scrubbing_is_bypassed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The final check decodes too: if the scrub stopped covering a shape, the write is refused, not published."""
    monkeypatch.setattr(demo_export, "scrub_decoded", lambda text, names=(): text)
    with pytest.raises(SecretInSnapshot, match="decoded"):
        _write(monkeypatch, tmp_path, {"status": {"note": "admin%40example.org"}})
    assert not (tmp_path / "out" / "demo.json").exists()


def test_decoded_hits_reports_labels_never_the_matched_text() -> None:
    hits = decoded_hits("login_hint=a%40b.example", NAMES)
    assert "decoded: email-shaped string" in hits and "decoded: sign-in or OAuth url" in hits
    assert all("b.example" not in h for h in hits)


def test_the_markers_check_also_sees_encoded_markers() -> None:
    assert any(m.startswith("decoded:") for m in private_markers_left("path %2Fhome%2Fss%2Fx"))
    assert private_markers_left("a plain sentence about a promise to marry") == []


# -- the private-name list --------------------------------------------------------------------------

SPACING_VARIANTS = [
    "Zorblat  Quux",  # a double space
    "Zorblat\u00a0Quux",  # a no-break space
    "Zorblat\u2009Quux",  # a thin space
    "Zorblat\tQuux",
    "Zorblat\nQuux",
    "Zorblat\u200bQuux",  # a zero-width space
    "Zor\u200bblat Quux",  # a zero-width space inside a word
    "Zorblat\u200d \u200cQuux",  # zero-width joiners around the space
    "Zorblat\ufeffQuux\u00ad",  # a byte-order mark and a soft hyphen
    "Zorblat\u2060 Quux",  # a word joiner
    "ZORBLAT   quux",
]


@pytest.mark.parametrize("variant", SPACING_VARIANTS)
def test_a_multi_word_name_is_redacted_however_it_is_spaced_or_split(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    variant: str,
) -> None:
    out = _write(monkeypatch, tmp_path, {"status": {"a": f"Met {variant} at the hearing", "b": "plain"}})
    text = out.read_text()
    got = json.loads(text)["status"]
    assert got["a"] == "Met <redacted:name> at the hearing"
    assert got["b"] == "plain"
    assert private_markers_left(text, NAMES) == []


@pytest.mark.parametrize("variant", SPACING_VARIANTS)
def test_the_final_check_catches_a_spaced_name_if_the_scrub_is_bypassed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    variant: str,
) -> None:
    monkeypatch.setattr(demo_export, "scrub_decoded", lambda text, names=(): text)
    with pytest.raises(SecretInSnapshot, match="private-name #1"):
        _write(monkeypatch, tmp_path, {"status": {"a": f"Met {variant} at the hearing"}})
    assert decoded_hits(f"x {variant} y", NAMES) == ["private-name #1"]


def test_an_encoded_spaced_name_drops_the_whole_value(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    data = {"status": {"a": "Zorblat%C2%A0Quux was here", "b": "Zorblat&nbsp;&nbsp;Quux", "c": "ok"}}
    got = json.loads(_write(monkeypatch, tmp_path, data).read_text())["status"]
    assert got["a"] == demo_export.DECODED_MARKER and got["b"] == demo_export.DECODED_MARKER and got["c"] == "ok"


def test_normalise_text_collapses_whitespace_and_strips_invisible_characters() -> None:
    from pravrudhi.application.demo_export import normalise_text

    assert normalise_text(" a\u00a0\u00a0b\u200bc \t d\n") == "a bc d"
    assert normalise_text("plain text") == "plain text"


def test_names_in_the_list_are_normalised_too(tmp_path: Path) -> None:
    kw = names_kwargs(tmp_path, ("Alpha   Beta", "Gam\u200bma"))
    assert load_private_names(kw["private_names_path"], kw["private_names_sha256"]) == ("Alpha Beta", "Gamma")


def test_names_are_redacted_whole_word_ignoring_case_and_the_rest_stays(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    plain = "Wibbles and wibbled and Wibblesome stay"
    data = {"status": {"a": "Met zorblat QUUX at the hearing", "b": "wibble said so", "c": plain}}
    got = json.loads(_write(monkeypatch, tmp_path, data).read_text())["status"]
    assert got["a"] == "Met <redacted:name> at the hearing"
    assert got["b"] == "<redacted:name> said so"
    assert got["c"] == "Wibbles and wibbled and Wibblesome stay"


def test_a_name_that_only_shows_after_decoding_drops_the_whole_value(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    got = json.loads(_write(monkeypatch, tmp_path, {"status": {"a": "Zorblat%20Quux was here", "b": "x"}}).read_text())["status"]
    assert got["a"] == demo_export.DECODED_MARKER and got["b"] == "x"


def test_a_surviving_name_is_reported_by_position_not_name_and_refuses(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(demo_export, "scrub_decoded", lambda text, names=(): text)
    with pytest.raises(SecretInSnapshot) as e:
        _write(monkeypatch, tmp_path, {"status": {"a": "Wibble was here"}})
    assert "private-name #2" in str(e.value)
    assert "Wibble" not in str(e.value) and "wibble" not in str(e.value).lower().replace("private-name", "")
    assert private_markers_left("Wibble", NAMES) == ["private-name #2"]


@pytest.mark.parametrize("make", ["missing", "empty", "comments-only", "wrong-sha", "directory"])
def test_the_private_name_list_is_required_and_a_bad_one_refuses_before_anything_is_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, make: str
) -> None:
    good = names_kwargs(tmp_path)
    path = Path(good["private_names_path"])
    sha = good["private_names_sha256"]
    if make == "missing":
        path = tmp_path / "nope.txt"
    elif make == "empty":
        path.write_text("")
        sha = __import__("hashlib").sha256(b"").hexdigest()
    elif make == "comments-only":
        path.write_text("# only a comment\n\n   \n")
        sha = __import__("hashlib").sha256(path.read_bytes()).hexdigest()
    elif make == "wrong-sha":
        sha = "0" * 64
    elif make == "directory":
        path = tmp_path
    with pytest.raises(PrivateNamesError):
        _write(monkeypatch, tmp_path, {"status": {"a": "x"}}, private_names_path=str(path), private_names_sha256=sha)
    assert not (tmp_path / "out" / "demo.json").exists()


def test_load_private_names_reads_one_name_per_line_and_skips_comments(tmp_path: Path) -> None:
    kw = names_kwargs(tmp_path, ("Alpha Beta", "Gamma"))
    assert load_private_names(kw["private_names_path"], kw["private_names_sha256"]) == ("Alpha Beta", "Gamma")
    assert load_private_names(kw["private_names_path"], kw["private_names_sha256"].upper()) == ("Alpha Beta", "Gamma")


def test_write_demo_cannot_be_called_without_the_list(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(demo_export, "build_demo", lambda root: {"status": {}})
    with pytest.raises(TypeError):
        demo_export.write_demo(tmp_path, tmp_path / "out" / "demo.json")  # type: ignore[call-arg]


def test_the_cli_requires_the_list_and_its_sha(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from pravrudhi.cli.app import app

    r = CliRunner().invoke(app, ["demo-export", "--root", str(tmp_path), "--dest", str(tmp_path / "d.json")])
    assert r.exit_code != 0
    r = CliRunner().invoke(app, ["demo-export", "--root", str(tmp_path), "--dest", str(tmp_path / "d.json"),
                                 "--private-names", str(tmp_path / "missing.txt"), "--private-names-sha256", "0" * 64])
    assert r.exit_code == 1
    assert not (tmp_path / "d.json").exists()


# -- captured asks, prompts and transcripts are not published ---------------------------------------

def test_no_captured_ask_prompt_or_transcript_is_in_the_public_view() -> None:
    bundle = {
        "requests": [{"ask": "relay"}], "agent_trace": [{"prompt": "x"}], "candidates": [1], "heartbeat": [1],
        "status": {"ask": "captured ask", "ok": 1, "nested": [{"transcript": "t", "chat": "c", "keep": "product text"}]},
        "featured_run": {"events": [{"messages": ["m"], "kind": "step"}], "id": "n1"},
    }
    out = public_view(bundle)
    assert set(out) == {"status", "featured_run"}
    assert out["status"] == {"ok": 1, "nested": [{"keep": "product text"}]}
    assert out["featured_run"] == {"events": [{"kind": "step"}], "id": "n1"}
    assert {"ask", "transcript", "chat", "messages"} <= CAPTURE_KEYS


def test_scrub_decoded_leaves_a_clean_snapshot_byte_identical() -> None:
    clean = json.dumps({"a": "a promise to marry", "b": [1, 2, {"c": "constructed matter"}]}, indent=2, sort_keys=True) + "\n"
    assert scrub_decoded(clean, NAMES) == clean
