"""Which email addresses the operator's Claude seats are, read from LOCAL configuration, never from the repository.

The committed tree is public. A seat is an account the operator holds, so its address does not belong in `configs/`, in
code, in docs or in tests (they use obvious placeholders such as `seat-a@example.invalid`). The real values live in ONE of:

* an environment variable (`PRAVRUDHI_SCRIPTED_CLAUDE_EMAIL`, `PRAVRUDHI_CLAUDE_CLI_EXPECTED_EMAIL`); or
* the local, uncommitted file `~/.config/pravrudhi/seats.local.yaml` (override the path with `PRAVRUDHI_SEATS_LOCAL`):

      scripted_claude_email: seat-2-address@your-domain        # the seat scripted `claude -p` calls must bill
      claude_cli_expected_email: seat-2-address@your-domain    # optional; defaults to scripted_claude_email
      seats:                                                   # optional per-seat overrides of configs/seats.yaml
        primary: seat-2-address@your-domain
        fallback: team-account@your-domain

Where an identity is NEEDED to verify an account, the code refuses with `SeatIdentityMissing` when it is absent: a check that
quietly has nothing to compare against is not a check. A committed placeholder (`*.invalid`) in `configs/seats.yaml`
is never an identity (`is_placeholder`), but a value you set yourself is used as given.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

LOCAL_FILE_ENV = "PRAVRUDHI_SEATS_LOCAL"
LOCAL_FILE_DEFAULT = Path("~/.config/pravrudhi/seats.local.yaml")
SCRIPTED_EMAIL_ENV = "PRAVRUDHI_SCRIPTED_CLAUDE_EMAIL"
CLI_EXPECTED_EMAIL_ENV = "PRAVRUDHI_CLAUDE_CLI_EXPECTED_EMAIL"


class SeatIdentityMissing(RuntimeError):
    """A seat identity is needed to verify an account but the local configuration does not provide one."""


def is_placeholder(email: str | None) -> bool:
    """A committed stand-in (`seat-a@example.invalid`), never a real identity."""
    e = (email or "").strip().lower()
    return not e or e.endswith(".invalid")


def local_config() -> dict[str, Any]:
    """The parsed local seats file, `{}` when it is absent or unreadable (callers decide whether that is fatal)."""
    import yaml

    path = Path(os.environ.get(LOCAL_FILE_ENV) or LOCAL_FILE_DEFAULT).expanduser()
    try:
        raw = yaml.safe_load(path.read_text())
    except (OSError, ValueError, yaml.YAMLError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _clean(value: object) -> str | None:
    """An explicitly configured value (environment or the local file) is used as given, even an obvious test address;
    only empty is absent."""
    s = str(value).strip() if value is not None else ""
    return s or None


def scripted_claude_email(*, required: bool = True) -> str | None:
    """The account a scripted `claude -p` call must bill (seat 2). Environment first, then the local file."""
    found = _clean(os.environ.get(SCRIPTED_EMAIL_ENV)) or _clean(local_config().get("scripted_claude_email"))
    if found is None and required:
        raise SeatIdentityMissing(
            f"the scripted Claude seat's email is not configured: set {SCRIPTED_EMAIL_ENV} or `scripted_claude_email` in "
            f"{os.environ.get(LOCAL_FILE_ENV) or LOCAL_FILE_DEFAULT} (a local, uncommitted file); "
            "refusing to verify against nothing"
        )
    return found


def claude_cli_expected_email(*, required: bool = True) -> str | None:
    """The account `claude auth status` must report for the panel's CLI vendor; defaults to the scripted seat."""
    found = _clean(os.environ.get(CLI_EXPECTED_EMAIL_ENV)) or _clean(local_config().get("claude_cli_expected_email"))
    return found or scripted_claude_email(required=required)


def seat_email_override(seat_id: str) -> str | None:
    """A per-seat address from the local file's `seats:` map, or `None`."""
    seats = local_config().get("seats")
    return _clean(seats.get(seat_id)) if isinstance(seats, dict) else None
