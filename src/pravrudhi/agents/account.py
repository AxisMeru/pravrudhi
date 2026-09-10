"""Which account this project's `claude` invocations use.

Operator instruction, 2026-09-10: *"the claude-cli change that from the personal claude account to this
axismeru account...make this change everywhere for this project...stuido, product etc...stop using the
personal claude account for this project"*.

Every `claude` invocation in this repository inherited the ambient environment, which means it used whatever
OAuth login sits in `~/.claude` — the operator's personal account. That is what exhausted a **weekly** limit
mid-run during gate A1.1 (68 of 80 prompts answered, 12 recorded as gaps), and a personal limit is the wrong
thing for a project's automated loops to consume.

`CLAUDE_CONFIG_DIR` is the mechanism rather than `ANTHROPIC_API_KEY`. An API key can lose to an existing OAuth
login depending on how the CLI resolves credentials, and "depending on" is not good enough for an instruction
that says *stop*. Pointing the config directory somewhere else makes the personal login unreachable: the CLI
reads credentials from that directory or finds none.

**The refusal is the point.** With no project credential, this raises instead of falling back — because a
fallback to the personal account is exactly the behaviour being removed, and a silent one would look like
compliance while being the opposite.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Where this project's own Claude credentials live. Under `~/.config/pravrudhi`, which already exists, so the
#: personal `~/.claude` is untouched and either account can be re-authenticated without disturbing the other.
PROJECT_CLAUDE_HOME = Path("~/.config/pravrudhi/claude")

#: Set this to override the location (a product install on another machine, or a second org).
HOME_ENV = "PRAVRUDHI_CLAUDE_CONFIG_DIR"

#: Files the CLI writes when it holds a credential. Either is enough to call the directory provisioned.
CREDENTIAL_FILES = (".credentials.json", "credentials.json")


class PersonalAccountRefused(RuntimeError):
    """`claude` was asked for, and only the operator's personal login is available.

    Raised rather than falling back. The instruction was to stop using that account for this project, and a
    fallback would satisfy the letter of the change while leaving the behaviour intact.
    """


def project_claude_home() -> Path:
    return Path(os.environ.get(HOME_ENV) or PROJECT_CLAUDE_HOME).expanduser()


def provisioned() -> bool:
    """Whether this project's own Claude account is logged in."""
    home = project_claude_home()
    return any((home / name).exists() for name in CREDENTIAL_FILES)


def how_to_provision() -> str:
    """The exact thing a person has to do, since no agent session can complete an OAuth flow."""
    home = project_claude_home()
    return (
        f"log this project's Claude account in once, in an interactive terminal:\n"
        f"  mkdir -p {home}\n"
        f'  CLAUDE_CONFIG_DIR="{home}" claude login\n'
        f"and sign in as the AxisMeru account, not the personal one. An API key works too: put\n"
        f'  ANTHROPIC_API_KEY=<axismeru key>\n'
        f"in a 0600 file and export it before running, or set {HOME_ENV} to a directory that already holds a\n"
        f"credential."
    )


def claude_env(*, require: bool = True) -> dict[str, str]:
    """Environment overrides that make `claude` run as this project's account.

    Returns the overrides to merge into the child's environment. With `require=False` a missing credential
    yields the overrides anyway rather than raising — for callers that only want to know whether the binary
    is reachable, and must not crash a status listing over a credential.
    """
    home = project_claude_home()
    if require and not provisioned():
        raise PersonalAccountRefused(
            f"refusing to run `claude` with the operator's personal account. This project's own credential is "
            f"not present at {home}. {how_to_provision()}"
        )
    # Always set, even when unprovisioned, so nothing can silently reach `~/.claude`.
    return {"CLAUDE_CONFIG_DIR": str(home)}


def account_status() -> tuple[bool, str]:
    """Whether a `claude` invocation can proceed, and what to do if not. For status surfaces."""
    home = project_claude_home()
    if provisioned():
        return True, f"project Claude account at {home}"
    return False, f"no project Claude credential at {home} (the personal account is deliberately not used)"
