"""The published snapshot must not carry personal data, not just credentials.

`demo.json` is committed and pushed to a public repository by the hourly publish timer. On
2026-09-13 the committed copy held 120 absolute paths under the operator's home directory and
three of their personal email addresses, because `redact_secrets` only ever looked for
credential shapes. The same fail-closed discipline the bot-token incident produced applies here:
a snapshot that still carries personal data after redaction must not be written at all.

On 2026-09-14 a second, distinct leak of the same shape: `application.requests` stores an
operator's ask verbatim by design, but an auto-capture hook sometimes recorded a teammate's
relayed `<cross-session-message>` as the ask text itself -- 872 internal agent-to-agent messages
(session socket paths under `/run/user/<uid>/cc-socks/` and `/tmp/cc-socks/`, cross-session
content) published hourly for as long as the exporter has existed.

R1's rejection of the first fix for that leak (liaison-log 22c1451): 6 of the 957 relay fields
were truncated at 300 chars with no closing tag in the same JSON string, and the first version's
catch-all for that case stripped only the bare word "cross-session-message" -- leaving the full
relay BODY (an operator directive, a session id) sitting right there un-redacted. The tests below
assert the body is gone, not just the literal token.

The project's own git identity is deliberately NOT redacted - it is the published authorship of
every commit in the repository, so removing it from the snapshot would hide nothing.
"""

import re
from pathlib import Path

import pytest

from pravrudhi.application.demo_export import SecretInSnapshot, redact_secrets, still_carries
from tests._demo_names import names_kwargs


@pytest.fixture(autouse=True)
def _allow_the_test_payload_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests push arbitrary payloads through `write_demo` to prove the redaction and the refusals. The allowlist
    (`PUBLIC_DEMO_SECTIONS`, tests/test_demo_export_allowlist.py) would drop their ad hoc keys before any check ran, so it is
    widened to them here; the allowlist itself is tested in its own file."""
    monkeypatch.setattr(
        demo_export, "PUBLIC_DEMO_SECTIONS",
        demo_export.PUBLIC_DEMO_SECTIONS | {"note", "x", "y", "a", "keep", "requests"},
    )


def test_an_absolute_home_path_does_not_survive_redaction() -> None:
    out = redact_secrets("the directory `/home/someone/projects/pravrudhi/research` is empty")
    assert "/home/someone" not in out
    assert "projects/pravrudhi/research" in out, "only the home prefix goes; the rest stays readable"


def test_a_macos_home_path_does_not_survive_either() -> None:
    assert "/Users/someone" not in redact_secrets("ran from /Users/someone/pravrudhi on the Mac mini")


def test_a_personal_email_does_not_survive_redaction() -> None:
    out = redact_secrets("author someone@example.ac.uk and other@gmail.com")
    assert "example.ac.uk" not in out
    assert "gmail.com" not in out


def test_the_projects_own_git_identity_survives() -> None:
    assert "admin@axismeru.com" in redact_secrets("Commit as SharathSPhD <admin@axismeru.com>")


def test_a_cross_session_relay_block_does_not_survive_redaction() -> None:
    # A realistic JSON string, quotes backslash-escaped as `write_demo` always actually produces them (it
    # only ever calls `redact_secrets` on `json.dumps(...)` output). The trailer lives in a SEPARATE JSON
    # string (its own real, unescaped quotes) so the match has a genuine boundary to stop at, matching how
    # this always actually looks in the real file: one field's text, then the next field starts.
    out = redact_secrets(
        '"text": "preamble text\\n<cross-session-message from=\\"uds:/run/user/1000/cc-socks/1940473.sock\\" '
        'from-session=\\"local_abc\\" from-name=\\"Lead-2-assistant\\">\\nsome internal instruction\\n'
        '</cross-session-message>", "other_field": "trailer text"'
    )
    assert out.count("<redacted:internal-relay>") == 1
    # The BODY is gone, not just the wrapper tokens -- R1's exact finding on the first version of this fix.
    assert "some internal instruction" not in out
    assert "local_abc" not in out
    assert "cross-session-message" not in out
    assert "cc-socks" not in out
    assert "preamble text" in out and "trailer text" in out, "text outside the block survives"


def test_back_to_back_relay_blocks_in_one_string_are_both_fully_redacted() -> None:
    # Greedy-to-string-boundary (the fix for the truncated-relay case below) means two blocks concatenated
    # in one JSON string collapse into a single redacted span rather than two separate ones -- an accepted
    # trade-off: nothing from either block's body survives, which is what actually matters here.
    out = redact_secrets(
        '<cross-session-message from=\\"a\\">first body</cross-session-message>'
        '<cross-session-message from=\\"b\\">second body</cross-session-message>'
    )
    assert "first body" not in out
    assert "second body" not in out
    assert "cross-session-message" not in out


def test_a_relay_with_no_closing_tag_in_the_same_string_still_loses_its_whole_body() -> None:
    """The exact bug R1 found (liaison-log 22c1451): a captured ask truncates a relay mid-transcript, with
    no closing tag anywhere in that same JSON string value. The first version of this fix matched nothing
    here (a bounded, NON-greedy regex requires the closing tag) and fell back to a bare-token strip that left
    the operator directive and session id sitting right there. Fixed by making the primary match greedy but
    still bounded to the JSON string's own end -- it now consumes everything from the tag start through
    wherever the string actually ends, since there's no unescaped quote before that to stop at either way."""
    out = redact_secrets(
        '"text": "<cross-session-message from=\\"uds:/run/user/1000/cc-socks/1940473.sock\\" '
        'from-session=\\"local_9f8e7d6c\\">\\nDIRECTION FROM THE OPERATOR: do the thing, right now'
    )
    assert out.count("<redacted:internal-relay>") == 1
    assert "DIRECTION FROM THE OPERATOR" not in out
    assert "local_9f8e7d6c" not in out
    assert "cross-session-message" not in out
    assert "cc-socks" not in out
    assert '"text": "' in out, "the JSON key/quote structure before the tag survives"


def test_a_bare_socket_path_outside_a_relay_block_does_not_survive_either() -> None:
    out = redact_secrets(
        "Your message to another session was held for approval "
        "(recipient: uds:/tmp/cc-socks/3061626.sock). Not delivered yet."
    )
    assert "cc-socks" not in out
    assert "Not delivered yet" in out


def test_internal_marker_residue_catch_all_also_consumes_to_the_string_boundary() -> None:
    # Same discipline for the last-resort shape: it must never leave trailing body text after its own
    # marker either, the same defect fixed above for the primary relay shape.
    out = redact_secrets('"text": "some prose mentioning cross-session-message and then a secret continuation')
    assert "<redacted:internal-marker>" in out
    assert "a secret continuation" not in out
    assert "cross-session-message" not in out


def test_still_carries_names_what_is_left() -> None:
    assert still_carries("clean text") == []
    assert "home-path" in still_carries("/home/someone/x")
    assert "cross-session-relay" in still_carries("<cross-session-message>x</cross-session-message>")
    assert "session-socket-path" in still_carries("uds:/tmp/cc-socks/123.sock")


def test_write_demo_refuses_a_snapshot_that_still_carries_personal_data(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Fail closed: if redaction ever stops covering a shape, nothing is written."""
    from pravrudhi.application import demo_export

    monkeypatch.setattr(demo_export, "redact_secrets", lambda text: text)
    monkeypatch.setattr(demo_export, "build_demo", lambda root: {"note": "/home/someone/leak"})
    with pytest.raises(SecretInSnapshot, match="home-path"):
        demo_export.write_demo(root=None, dest=None, **names_kwargs(tmp_path))  # type: ignore[arg-type]


# -- statute text (Lead-2, 2026-10-05; licence memo on s.52(1)(q)(ii)) -----------------------------------------

_PROMPT = (
    "You are answering a question of Indian law.\\n\\nSOURCES (the only authorities you may rely on):\\n"
    "[IPC/Section 320] Indian Penal Code, 1860, Section 320 -- Grievous hurt: The following kinds of hurt "
    "only are designated as \\\"grievous\\\" (First)  -  Emasculation.\\n(Secondly) Permanent privation.\\n"
    "[IPC/Section 326] Indian Penal Code, 1860, Section 326 -- Grievous hurt by weapons: Whoever, except in "
    "the case provided for by section 335, voluntarily causes grievous hurt shall be punished.\\n\\n"
    "QUESTION:\\nA man strikes another."
)


def _snapshot(*texts: str) -> str:
    return '{\n  "text": "' + '",\n  "more": "'.join(texts) + '"\n}\n'


def test_statute_text_in_a_sources_block_is_replaced_but_the_id_and_title_stay() -> None:
    import json

    out = redact_secrets(_snapshot(_PROMPT))
    body = json.loads(out)["text"]  # still valid JSON
    assert "Emasculation" not in body and "Whoever, except" not in body
    kept = "[IPC/Section 320] Indian Penal Code, 1860, Section 320 -- Grievous hurt: "
    assert kept + "[statute text removed; see India Code]" in body
    assert "[IPC/Section 326] Indian Penal Code, 1860, Section 326 -- Grievous hurt by weapons: [statute text removed" in body
    assert "QUESTION:\nA man strikes another." in body  # the rest of the prompt is untouched
    assert still_carries(out) == []  # a second pass finds nothing left, so the exporter does not refuse its own output


def test_a_truncated_300_character_copy_is_scrubbed_too() -> None:
    import json

    cut = _PROMPT[:230]  # ends inside the first provision's text, no QUESTION and no next entry
    body = json.loads(redact_secrets(_snapshot(cut)))["text"]
    assert "Emasculation" not in body
    assert body.endswith("[statute text removed; see India Code]")


def test_a_citation_example_and_ordinary_bracketed_text_are_not_touched() -> None:
    plain = "Cite each one inline, e.g. [IPC/Section 302]. Do not cite anything else. See [1] and [A/B] here."
    assert redact_secrets(_snapshot(plain)) == _snapshot(plain)


def test_redacting_twice_changes_nothing() -> None:
    once = redact_secrets(_snapshot(_PROMPT))
    assert redact_secrets(once) == once


# -- the demo snapshot's own backstop (Lead-2 P0, 2026-10-05) ----------------------------------------------------

import json  # noqa: E402
from pathlib import Path  # noqa: E402

from pravrudhi.application import demo_export  # noqa: E402

_PROVISION = (
    "Whoever takes or entices away any woman who is and whom he knows or has reason to believe to be the wife "
    "of any other man, from that man, with intent that she may have illicit intercourse with any person, shall be punished."
)


_TITLE = "Enticing or taking away or detaining with criminal intent a married woman."


def _root_with_corpus(tmp_path: Path) -> Path:
    d = tmp_path / "research" / "nyaya" / "corpus"
    d.mkdir(parents=True, exist_ok=True)
    doc = {"id": "IPC/Section 498", "act": "Indian Penal Code", "section": "Section 498",
           "title": _TITLE, "text": _PROVISION}
    (d / "ipc.json").write_text(json.dumps({"documents": [doc]}))
    return tmp_path


@pytest.mark.parametrize("raw", [
    "/home/someone/projects/x", "/Users/someone/y", "note to someone@gmail.com", "uds:/run/user/1000/cc-socks/1.sock",
    "<cross-session-message>x</cross-session-message>", "a held cross-session message", "set CLAUDE_CONFIG_DIR=/x",
    "seat 2 account", "Commit as <admin@axismeru.com>",
])
def test_the_demo_redaction_leaves_none_of_the_private_markers(raw: str) -> None:
    out = demo_export.demo_pipeline(json.dumps({"x": raw}))
    assert demo_export.private_markers_left(out) == [], out
    assert demo_export.still_carries(out) == []


def test_only_the_demo_removes_the_project_email_and_the_public_handle_stays() -> None:
    assert "admin@axismeru.com" in redact_secrets("<admin@axismeru.com>")
    assert "axismeru" not in demo_export.redact_for_demo("<admin@axismeru.com>")
    assert "SharathSPhD" in demo_export.redact_for_demo("author SharathSPhD")
    assert demo_export.private_markers_left("author sharathsphd") == []


def _write(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, data: object, redact: bool = True) -> Path:
    monkeypatch.setattr(demo_export, "build_demo", lambda root: data)
    monkeypatch.setattr(demo_export, "ASSETS_DIR", tmp_path / "no-assets")  # only the fixture corpus counts here
    monkeypatch.setattr(demo_export, "MIN_CORPUS_DOCUMENTS", 1)
    if not redact:
        monkeypatch.setattr(demo_export, "demo_pipeline", lambda text: text)
    return demo_export.write_demo(_root_with_corpus(tmp_path), tmp_path / "out" / "demo.json", **names_kwargs(tmp_path))


def test_a_marker_that_survives_redaction_refuses_the_write(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with pytest.raises(SecretInSnapshot, match="/home/"):
        _write(monkeypatch, tmp_path, {"a": "see /home/someone/x"}, redact=False)
    assert not (tmp_path / "out" / "demo.json").exists()
    # a seat/account name is refused by whatever private patterns are configured: shown here with an invented name and pattern
    invented = re.compile(r"quibble\.[a-z]+", re.IGNORECASE)
    monkeypatch.setattr(demo_export, "PRIVATE_PATTERNS", (*demo_export.PRIVATE_PATTERNS, invented))
    with pytest.raises(SecretInSnapshot, match="quibble"):
        _write(monkeypatch, tmp_path, {"a": "seat quibble.trax"}, redact=False)


def test_a_corpus_passage_in_any_layout_refuses_the_write(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    quoted = _PROVISION[20:110]
    layouts = ({"x": quoted}, {"x": ["a", {"deep": "lead-in " + quoted.upper() + " tail"}]},
               {"x": quoted.replace(" ", "\n  ")})
    for data in layouts:
        with pytest.raises(SecretInSnapshot, match="statute text"):
            _write(monkeypatch, tmp_path, data)
    assert not (tmp_path / "out" / "demo.json").exists()


def test_a_kept_heading_and_short_overlap_are_not_statute_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    heading = f"[IPC/Section 498] Indian Penal Code, Section 498 -- {_TITLE}"
    out = _write(monkeypatch, tmp_path, {"x": heading, "y": _PROVISION[:40]})
    assert json.loads(out.read_text())["x"] == heading


# -- internal team vocabulary, any case (Lead-2, 2026-10-05) -------------------------------------------------------

_CHATTER = [
    "[Cross-session idle notice] the session went idle", "ask LEAD-2 to decide", "then SendMessage the owner",
    "Web-on-seat2 reports", "goal-context said so", "see Remote Control", "per CLAUDE.md", "Lead-2-assistant merges",
    "relayed by CROSS-SESSION peers", "an idle NOTICE arrived",
]


@pytest.mark.parametrize("raw", _CHATTER)
def test_a_string_that_mentions_team_vocabulary_is_dropped_whole(raw: str) -> None:
    out = json.loads(demo_export.demo_pipeline(json.dumps({"keep": "a plain row", "x": [raw, {"y": raw}]})))
    assert out == {"keep": "a plain row", "x": [demo_export.INTERNAL_TEXT_MARKER, {"y": demo_export.INTERNAL_TEXT_MARKER}]}


def test_unmarked_text_and_the_layout_are_returned_unchanged() -> None:
    clean = json.dumps({"b": 1, "a": ["Zorblat made this", "plain"]}, indent=2, sort_keys=True) + "\n"
    assert demo_export.drop_internal_text(clean) == clean


@pytest.mark.parametrize(
    "raw",
    _CHATTER + ["Seat 2 ACCOUNT", "/HOME/x", "@GMAIL.com", "UDS:/x", "claude_config_dir"],
)
def test_the_backstop_compares_case_insensitively(raw: str) -> None:
    assert demo_export.private_markers_left(raw), raw


def test_write_demo_cannot_emit_team_chatter_in_any_case(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    out = _write(monkeypatch, tmp_path, {"requests": [{"text": t} for t in _CHATTER] + [{"text": "an ordinary ask"}]})
    body = out.read_text()
    assert demo_export.private_markers_left(body) == []
    assert json.loads(body)["requests"][-1] == {"text": "an ordinary ask"}

def test_the_shipped_corpus_alone_is_enough_for_the_backstop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A clean clone or CI has no research/ corpus: the packaged provisions must still be compared against."""
    windows, documents = demo_export.corpus_windows(tmp_path)  # a root with no research corpus at all
    assert documents >= demo_export.MIN_CORPUS_DOCUMENTS and windows
    shipped = json.loads((demo_export.ASSETS_DIR / "bns_sections.json").read_text())["documents"]
    provision = max((d["text"] for d in shipped), key=len)
    monkeypatch.setattr(demo_export, "build_demo", lambda root: {"x": provision})
    with pytest.raises(SecretInSnapshot, match="statute text"):
        demo_export.write_demo(tmp_path, tmp_path / "out" / "demo.json", **names_kwargs(tmp_path))


def test_a_root_with_no_corpus_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(demo_export, "ASSETS_DIR", tmp_path / "no-assets")
    monkeypatch.setattr(demo_export, "build_demo", lambda root: {"x": "anything"})
    assert demo_export.corpus_windows(tmp_path) == (set(), 0)
    with pytest.raises(SecretInSnapshot, match="cannot vouch"):
        demo_export.write_demo(tmp_path, tmp_path / "out" / "demo.json", **names_kwargs(tmp_path))
    assert not (tmp_path / "out" / "demo.json").exists()


def test_an_empty_or_too_small_corpus_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    d = tmp_path / "research" / "nyaya" / "corpus"
    d.mkdir(parents=True)
    (d / "x.json").write_text(json.dumps({"documents": []}))
    monkeypatch.setattr(demo_export, "ASSETS_DIR", tmp_path / "no-assets")
    monkeypatch.setattr(demo_export, "build_demo", lambda root: {"x": "anything"})
    with pytest.raises(SecretInSnapshot, match="cannot vouch"):
        demo_export.write_demo(tmp_path, tmp_path / "out" / "demo.json", **names_kwargs(tmp_path))
    small = _root_with_corpus(tmp_path / "small")  # one document: below the real minimum
    monkeypatch.setattr(demo_export, "MIN_CORPUS_DOCUMENTS", 2)
    with pytest.raises(SecretInSnapshot, match="cannot vouch"):
        demo_export.write_demo(small, tmp_path / "out" / "demo.json", **names_kwargs(tmp_path))


# -- machine and network identifiers (R2, 2026-10-05) --------------------------------------------------------------

_IDENTIFIERS = [
    "ss@ss-Fusion-99:~/x", "host ss-Fusion-99 is up", "ssh nzorblat@zorblats-Mac-mini", "zorblats-Mac-mini",
    "dvs-builder@U22-I3-B08-02-2",
    "session dir -home-someone-projects-pravrudhi-", "scratch /tmp/claude-1000/x/y", "gateway 192.168.0.250:8080",
    *[f"endpoint {i}" for i in demo_export.PRIVATE_ENDPOINT_IDS], f"endpoint {demo_export.PRIVATE_ENDPOINT_IDS[1].upper()}",
    f"id {demo_export.PRIVATE_ENDPOINT_IDS[2]}",
]


@pytest.mark.parametrize("raw", _IDENTIFIERS)
def test_machine_and_network_identifiers_are_removed_and_the_json_stays_valid(raw: str) -> None:
    out = demo_export.demo_pipeline(json.dumps({"x": raw, "y": "prefix\n" + raw, "z": "a\t" + raw}))
    body = json.loads(out)  # a substitution must never eat the backslash of a neighbouring escape
    assert demo_export.private_markers_left(out) == [], out
    assert body["y"].startswith("prefix\n") and body["z"].startswith("a\t")


def test_a_newline_before_user_at_host_is_kept_not_swallowed() -> None:
    out = demo_export.demo_pipeline(json.dumps({"x": "Login successful.\nss@ss-Fusion-99:~$ claude"}))
    assert json.loads(out)["x"] == "Login successful.\n<redacted:user-at-host>:~$ claude"


def test_ordinary_text_with_at_signs_and_digits_is_untouched() -> None:
    text = "humaneval+ pass@1 0.579; 10.0.0.1; version 1.2.3; at-home-office"
    clean = json.dumps({"x": text}, indent=2, sort_keys=True) + "\n"
    assert demo_export.demo_pipeline(clean) == clean


def test_write_demo_refuses_a_snapshot_the_redaction_left_unparseable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(demo_export, "build_demo", lambda root: {"a": "b"})
    monkeypatch.setattr(demo_export, "demo_pipeline", lambda text: text[:-5])  # a truncated, invalid document
    with pytest.raises(SecretInSnapshot, match="unparseable"):
        demo_export.write_demo(_root_with_corpus(tmp_path), tmp_path / "out" / "demo.json", **names_kwargs(tmp_path))


# -- corpus floor: the shipped corpus, and every shipped asset must load ----------------------------------------------


def test_the_corpus_floor_is_the_shipped_corpus_and_every_asset_loads() -> None:
    _windows, documents = demo_export.corpus_windows(Path("/nonexistent-root"))
    assert demo_export.MIN_CORPUS_DOCUMENTS >= 1609
    assert documents >= demo_export.MIN_CORPUS_DOCUMENTS
    assert demo_export.unloaded_assets() == []


def test_a_shipped_corpus_file_that_does_not_load_refuses_the_write(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    broken = tmp_path / "assets"
    broken.mkdir()
    (broken / "bad.json").write_text("{not json")
    (broken / "worse.json").write_text(json.dumps({"documents": "oops"}))
    monkeypatch.setattr(demo_export, "ASSETS_DIR", broken)
    assert demo_export.unloaded_assets() == ["bad.json", "worse.json"]
    monkeypatch.setattr(demo_export, "MIN_CORPUS_DOCUMENTS", 0)
    monkeypatch.setattr(demo_export, "build_demo", lambda root: {"a": "b"})
    with pytest.raises(SecretInSnapshot, match="failed to load"):
        demo_export.write_demo(_root_with_corpus(tmp_path), tmp_path / "out" / "demo.json", **names_kwargs(tmp_path))
