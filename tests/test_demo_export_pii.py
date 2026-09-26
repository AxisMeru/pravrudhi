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
    # Quotes are backslash-escaped, matching the real shape this always actually arrives in: `write_demo`
    # only ever calls `redact_secrets` on `json.dumps(...)` output, where the relay's own `from="..."`
    # attribute quotes are JSON-escaped, never bare -- this exercises the real "nice" block match, not the
    # coarser catch-all (see the fallback test below for that path).
    out = redact_secrets(
        'preamble text\\n<cross-session-message from=\\"uds:/run/user/1000/cc-socks/1940473.sock\\" '
        'from-session=\\"local_abc\\" from-name=\\"Lead-2-assistant\\">\\nsome internal instruction\\n'
        "</cross-session-message>\\ntrailer text"
    )
    assert out.count("<redacted:internal-relay>") == 1
    assert "cross-session-message" not in out
    assert "cc-socks" not in out
    assert "preamble text" in out and "trailer text" in out, "text outside the block survives"


def test_back_to_back_relay_blocks_are_each_redacted_separately() -> None:
    out = redact_secrets(
        '<cross-session-message from=\\"a\\">first</cross-session-message>'
        '<cross-session-message from=\\"b\\">second</cross-session-message>'
    )
    assert out.count("<redacted:internal-relay>") == 2


def test_a_relay_with_no_closing_tag_in_the_same_string_falls_back_to_the_bare_token_catch_all() -> None:
    # A captured ask sometimes truncates a relay mid-transcript, with no closing tag anywhere in that same
    # JSON string value -- an earlier version of this fix let an unbounded `.*?` hunt across unrelated later
    # fields for the next literal closing tag and corrupted the JSON. The bounded block regex must not match
    # this at all; the catch-all shape strips the bare token instead, which is coarser but never breaks JSON
    # structure and never leaves the literal marker behind.
    out = redact_secrets('"text": "<cross-session-message from=\\"a\\">never closed')
    assert "<redacted:internal-relay>" not in out  # the bounded block regex correctly did not match
    assert "cross-session-message" not in out


def test_a_bare_socket_path_outside_a_relay_block_does_not_survive_either() -> None:
    out = redact_secrets(
        "Your message to another session was held for approval "
        "(recipient: uds:/tmp/cc-socks/3061626.sock). Not delivered yet."
    )
    assert "cc-socks" not in out
    assert "Not delivered yet" in out


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
