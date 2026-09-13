"""The published snapshot must not carry personal data, not just credentials.

`demo.json` is committed and pushed to a public repository by the hourly publish timer. On
2026-09-13 the committed copy held 120 absolute paths under the operator's home directory and
three of their personal email addresses, because `redact_secrets` only ever looked for
credential shapes. The same fail-closed discipline the bot-token incident produced applies here:
a snapshot that still carries personal data after redaction must not be written at all.

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


def test_still_carries_names_what_is_left() -> None:
    assert still_carries("clean text") == []
    assert "home-path" in still_carries("/home/ss/x")


def test_write_demo_refuses_a_snapshot_that_still_carries_personal_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail closed: if redaction ever stops covering a shape, nothing is written."""
    from pravrudhi.application import demo_export

    monkeypatch.setattr(demo_export, "redact_secrets", lambda text: text)
    monkeypatch.setattr(demo_export, "build_demo", lambda root: {"note": "/home/ss/leak"})
    with pytest.raises(SecretInSnapshot, match="home-path"):
        demo_export.write_demo(root=None, dest=None)  # type: ignore[arg-type]
