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

import pytest

from pravrudhi.application.demo_export import SecretInSnapshot, redact_secrets, still_carries


def test_an_absolute_home_path_does_not_survive_redaction() -> None:
    out = redact_secrets("the directory `/home/ss/projects/pravrudhi/research` is empty")
    assert "/home/ss" not in out
    assert "projects/pravrudhi/research" in out, "only the home prefix goes; the rest stays readable"


def test_a_macos_home_path_does_not_survive_either() -> None:
    assert "/Users/sharath" not in redact_secrets("ran from /Users/sharath/pravrudhi on the Mac mini")


def test_a_personal_email_does_not_survive_redaction() -> None:
    out = redact_secrets("author qbz506@york.ac.uk and sharath.sathish@gmail.com")
    assert "york.ac.uk" not in out
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
    assert "home-path" in still_carries("/home/ss/x")
    assert "cross-session-relay" in still_carries("<cross-session-message>x</cross-session-message>")
    assert "session-socket-path" in still_carries("uds:/tmp/cc-socks/123.sock")


def test_write_demo_refuses_a_snapshot_that_still_carries_personal_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail closed: if redaction ever stops covering a shape, nothing is written."""
    from pravrudhi.application import demo_export

    monkeypatch.setattr(demo_export, "redact_secrets", lambda text: text)
    monkeypatch.setattr(demo_export, "build_demo", lambda root: {"note": "/home/ss/leak"})
    with pytest.raises(SecretInSnapshot, match="home-path"):
        demo_export.write_demo(root=None, dest=None)  # type: ignore[arg-type]
