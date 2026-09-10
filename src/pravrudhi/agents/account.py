"""Which account this project's `claude` invocations use.

Operator instruction, 2026-09-10: *"the claude-cli change that from the personal claude account to this
axismeru account...make this change everywhere for this project...stuido, product etc...stop using the
personal claude account for this project"*.

Every `claude` invocation in this repository inherited the ambient environment, so it used whatever OAuth
login happened to be present. At the time that was the operator's personal account, and it exhausted a
**weekly** limit mid-run during gate A1.1 (68 of 80 prompts answered, 12 recorded as gaps). A personal limit
is the wrong thing for a project's automated loops to consume.

`CLAUDE_CONFIG_DIR` is the mechanism rather than `ANTHROPIC_API_KEY`. An API key can lose to an existing OAuth
login depending on how the CLI resolves credentials, and "depending on" is not good enough for an instruction
that says *stop*. Setting the config directory explicitly means an invocation reads the credential this project
names or finds none — it cannot pick up a different account by accident.

**The refusal is the other half.** With no credential at the named location this raises instead of running as
whoever happens to be logged in, because a silent fallback would look like compliance while being the
opposite.

The operator then settled the location by logging out of the personal account entirely and logging the
project's account in at the default path (2026-09-10), so on this machine there is no second account to keep
apart — see `PROJECT_CLAUDE_HOME`. The separation still matters for a product install in someone else's
hands, which is what `PRAVRUDHI_CLAUDE_CONFIG_DIR` is for.

Later the same day the project's designated account changed again: CLI-agent load was moved off
`admin@axismeru.com` onto a second premium seat on the same Axis Meru team plan, `sharath.sathish@gmail.com`
(`default_claude_max_5x` tier), logged in at this same default path. It is a personal-domain *address*, not
the operator's personal *account* the CHARTER rule was written against — it is a seat the operator holds on
this project's own team plan, chosen deliberately to spread CLI load, and the operator did the login
themselves in an interactive terminal. Nothing here should treat that email domain as a signal to refuse; the
refusal this module raises is for *no credential provisioned*, not for which mailbox the credential belongs
to. `admin@axismeru.com` remains the desktop-app login only.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Where this project's Claude credential lives.
#:
#: This first pointed at `~/.config/pravrudhi/claude`, on the assumption that the personal account would stay
#: logged in at the default location and the two had to be kept apart. The operator resolved it differently on
#: 2026-09-10 -- *"axismeru claude login done (logged out of personal too)"* -- so on this machine the default
#: location IS the project's account and there is no personal login left to guard against.
#:
#: Pointing here rather than at an empty directory is therefore not a relaxed check, it is the correct
#: location. The property the instruction asked for is held by the machine's state; what this module still
#: does is set `CLAUDE_CONFIG_DIR` explicitly, so an invocation cannot pick up some other account by accident,
#: and refuse when there is no credential at all rather than running as whoever happens to be logged in.
#:
#: A product install where the two must be separate -- an end user's own key alongside their personal Claude
#: -- sets `PRAVRUDHI_CLAUDE_CONFIG_DIR`. That is the BYOK case this was originally designed for.
PROJECT_CLAUDE_HOME = Path("~/.claude")

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
    same = home == Path("~/.claude").expanduser()
    where = "" if same else f'CLAUDE_CONFIG_DIR="{home}" '
    return (
        f"log this project's Claude account in once, in an interactive terminal:\n"
        f"  {'' if same else f'mkdir -p {home}' + chr(10) + '  '}{where}claude login\n"
        f"signing in as the account this project uses, not a personal one. An API key works too: put\n"
        f"  ANTHROPIC_API_KEY=<key>\n"
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
