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

**2026-09-11: one account became two, with an order.** *"admin@axismeru.com will be always available the
other seat may drop on and off....always use the other seat (sharath.sathish@gmail.com) for the cli to
balance the usage load..if it drops off the premium and cli returns error/something else enable the
router/config to automatically switch to admin account (but it should not use admin cli account if the other
seat is active)"*.

That is not a different constant, it is a different shape. A seat is now an entry in `configs/seats.yaml`,
the file's order is the precedence, and `select_seat` returns the first entry that can actually serve. The
reserve is reached only when the seat above it holds no credential or is inside a usage-limit cooldown --
never on an ordinary failure, because a prompt the model botched will be botched by the reserve too and
spending the always-available account on it is the opposite of balancing load.

The refusal is unchanged in spirit and wider in reach: `claude_env` raises when NO declared seat can serve,
rather than when one named directory is empty. And `mismatches` was added for the failure neither the CLI nor
`provisioned()` can see -- `CLAUDE_CONFIG_DIR` relocates a credential file and a profile cache with different
lifetimes, so a directory can hold a live token for one account beside a profile naming another, and two
directories can hold the *same* credential and look like a failover pair while being one account.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
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


def how_to_provision(seat: Seat | None = None) -> str:
    """The exact thing a person has to do, since no agent session can complete an OAuth flow.

    Names `claude auth login`, NOT `claude login`. The bare form is not a subcommand on CLI 2.x -- it falls
    through to the default and starts a session with the word "login" as the prompt, which looks like the
    instruction working right up until the credential is not there.

    Takes the seat, so the instruction names the directory and the account that are actually missing. With two
    seats and one of them fine, "log this project's Claude account in" does not say which.
    """
    home = seat.config_dir if seat is not None else project_claude_home()
    email = seat.email if seat is not None else ""
    same = home == Path("~/.claude").expanduser()
    make = "" if same else f"mkdir -p {home}\n  "
    where = "" if same else f'CLAUDE_CONFIG_DIR="{home}" '
    pick = f" --email {email}" if email else ""
    who = f" as {email}" if email else " as the account this project uses, not a personal one"
    return (
        f"log this seat in once, in an interactive terminal:\n"
        f"  {make}{where}claude auth login{pick}\n"
        f"signing in{who}. The credential is written to {home} and nothing outside it is touched, so a login "
        f"in another directory stays signed in -- `claude auth status --json` reports each directory "
        f"separately. An API key works too: put\n"
        f"  ANTHROPIC_API_KEY=<key>\n"
        f"in a 0600 file and export it before running, or set {HOME_ENV} to a directory that already holds a\n"
        f"credential."
    )


#: Where the seat registry lives, relative to the project root. Absent means one seat at the default
#: location, which is exactly how this module behaved before seats existed.
SEATS_CONFIG = Path("configs") / "seats.yaml"

#: The router's name for this vendor. A seat cools under `claude-code:<seat id>` so that one spent seat does
#: not take the whole agent out of rotation -- which is what happened while there was only one.
AGENT_ID = "claude-code"


@dataclass(frozen=True)
class Seat:
    """One Claude login this project may spend, and where its credential lives."""

    id: str
    email: str
    config_dir: Path

    @property
    def cooldown_key(self) -> str:
        return f"{AGENT_ID}:{self.id}"

    @property
    def provisioned(self) -> bool:
        return any((self.config_dir / name).exists() for name in CREDENTIAL_FILES)

    @property
    def recorded_email(self) -> str | None:
        """Whose profile this directory has cached, which is NOT necessarily whose credential it holds.

        `CLAUDE_CONFIG_DIR` relocates two files with different lifetimes: `.credentials.json` is rewritten on
        every token refresh, `.claude.json` only when the CLI re-fetches the profile. A directory can
        therefore hold a live token for one account beside a profile block naming another, and nothing in the
        CLI complains. `mismatches` is what notices.
        """
        path = self.config_dir / ".claude.json"
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        account = data.get("oauthAccount") if isinstance(data, dict) else None
        email = account.get("emailAddress") if isinstance(account, dict) else None
        return str(email) if email else None

    @property
    def credential_fingerprint(self) -> str | None:
        """A short digest of the refresh token, so two seats can be compared without reading a secret aloud.

        The refresh token rather than the access token: access tokens rotate, and two directories that shared
        one login would stop looking identical the moment either refreshed.
        """
        for name in CREDENTIAL_FILES:
            path = self.config_dir / name
            try:
                data = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            oauth = data.get("claudeAiOauth") if isinstance(data, dict) else None
            token = oauth.get("refreshToken") if isinstance(oauth, dict) else None
            if token:
                return hashlib.sha256(str(token).encode()).hexdigest()[:16]
        return None


def _pinned_seat() -> Seat | None:
    """The directory an automated unit pinned for itself, if it pinned one.

    `PRAVRUDHI_CLAUDE_CONFIG_DIR` exists so a human re-authenticating in a terminal cannot blank a running
    loop's credential. That is an instruction from the process that is running, and it outranks a file
    describing which seats the machine has -- so it is returned as the first seat rather than replacing the
    registry, and the registry is still there to fall back to when the pinned seat is spent.
    """
    raw = os.environ.get(HOME_ENV)
    if not raw:
        return None
    return Seat(id="pinned", email="", config_dir=Path(raw).expanduser())


def seats(root: Path | None = None) -> list[Seat]:
    """Every seat this project may spend, in the precedence the registry declares.

    Order in the file IS the precedence; there is deliberately no `priority` integer to tie-break.
    """
    import yaml

    declared: list[Seat] = []
    pinned = _pinned_seat()
    if pinned is not None:
        declared.append(pinned)

    path = Path(root or Path.cwd()) / SEATS_CONFIG
    try:
        raw = yaml.safe_load(path.read_text())
    except (OSError, ValueError):
        raw = None
    entries = (raw or {}).get("seats") if isinstance(raw, dict) else None
    if not entries:
        # No registry: one seat at the default location, which is what this module meant before seats existed.
        if pinned is None:
            declared.append(Seat(id="default", email="", config_dir=project_claude_home()))
        return declared

    for entry in entries:
        if not isinstance(entry, dict) or not entry.get("config_dir"):
            continue
        seat = Seat(
            id=str(entry.get("id") or "unnamed"),
            email=str(entry.get("email") or ""),
            config_dir=Path(str(entry["config_dir"])).expanduser(),
        )
        if pinned is not None and seat.config_dir == pinned.config_dir:
            continue  # the pinned directory is already first; do not offer it twice
        declared.append(seat)
    return declared


def select_seat(root: Path | None = None, *, now: datetime | None = None) -> Seat | None:
    """The highest-precedence seat that can actually serve, or `None` when none can.

    A seat is passed over for exactly two reasons: it holds no credential, or it is inside a usage-limit
    cooldown. An ordinary failure is not one of them -- a prompt the model botched will be botched by the
    reserve seat too, and spending the always-available account on it inverts the instruction this implements.
    """
    from pravrudhi.application import availability

    base = Path(root or Path.cwd())
    cool = availability.cooling(base, now=now)
    for seat in seats(base):
        if seat.provisioned and seat.cooldown_key not in cool:
            return seat
    return None


def _auth_status(config_dir: Path) -> dict[str, object] | None:
    """`claude auth status --json` for one directory, or `None` when it cannot be asked.

    A subprocess per seat, so this belongs to diagnostics rather than the dispatch path. It is also the only
    authoritative answer available: everything else on disk is a cache that can disagree with the token beside
    it, which is the whole reason this file needs a mismatch check at all.
    """
    import subprocess

    try:
        done = subprocess.run(
            ["claude", "auth", "status", "--json"],
            env={**os.environ, "CLAUDE_CONFIG_DIR": str(config_dir)},
            capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        parsed = json.loads(done.stdout)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def live_identity(seat: Seat) -> str | None:
    """Who a seat's directory is ACTUALLY logged in as, or `None` when the CLI could not be asked."""
    status = _auth_status(seat.config_dir)
    if not status or not status.get("loggedIn"):
        return None
    email = status.get("email")
    return str(email) if email else None


def mismatches(root: Path | None = None, *, live: bool = False) -> list[str]:
    """Everything about the seats on this machine that does not add up, in plain sentences.

    Two failures are worth naming because neither the CLI nor `provisioned()` can see them:

    * a directory whose identity is not the account the registry declares for it -- the token may still be
      right, but every surface that reads the email off the config reports the wrong seat;

    `live=True` asks the CLI who each directory is actually logged in as, which is the only authoritative
    answer and costs a subprocess per seat. It is OFF by default because `account_status` is polled by status
    surfaces and a listing must not shell out; the default reads the profile cache, and says so when it
    reports something, so nobody mistakes a stale cache for a wrong seat.
    * two seats holding the SAME credential. That is not two seats. A limit on one is a limit on the other,
      so the failover this module exists for would move to a directory that is already spent.
    """
    declared = [s for s in seats(root) if s.id != "pinned"]
    problems: list[str] = []

    for seat in declared:
        if not seat.provisioned or not seat.email:
            continue
        actual = live_identity(seat) if live else None
        if actual is not None:
            # Authoritative. A cache naming someone else is then merely stale rather than a wrong seat,
            # and saying so would send the operator to re-login a directory that is already correct.
            if actual != seat.email:
                problems.append(
                    f"seat {seat.id!r} at {seat.config_dir} declares {seat.email} but is logged in as "
                    f"{actual}"
                )
            continue
        recorded = seat.recorded_email
        if recorded and recorded != seat.email:
            problems.append(
                f"seat {seat.id!r} at {seat.config_dir} declares {seat.email} but its cached profile says "
                f"{recorded} (the CLI could not be asked, so this is the cache, not the live token)"
            )

    by_fingerprint: dict[str, list[str]] = {}
    for seat in declared:
        fingerprint = seat.credential_fingerprint
        if fingerprint:
            by_fingerprint.setdefault(fingerprint, []).append(seat.id)
    for shared in by_fingerprint.values():
        if len(shared) > 1:
            problems.append(
                f"seats {', '.join(sorted(shared))} hold the same credential, so they are one account and "
                f"not a failover pair; a usage limit on one is a limit on all of them"
            )
    return problems


def claude_env(*, require: bool = True, root: Path | None = None, now: datetime | None = None) -> dict[str, str]:
    """Environment overrides that make `claude` run as the seat this project should be spending.

    Returns the overrides to merge into the child's environment. With `require=False` a missing credential
    yields the overrides anyway rather than raising -- for callers that only want to know whether the binary
    is reachable, and must not crash a status listing over a credential.
    """
    seat = select_seat(root, now=now)
    if seat is not None:
        return {"CLAUDE_CONFIG_DIR": str(seat.config_dir)}

    home = project_claude_home()
    if require:
        missing = next((s for s in seats(root) if not s.provisioned), None)
        raise PersonalAccountRefused(
            f"refusing to run `claude` with the operator's personal account. No seat this project declares can "
            f"serve right now. {how_to_provision(missing)}"
        )
    # Always set, even when unprovisioned, so nothing can silently reach `~/.claude`.
    return {"CLAUDE_CONFIG_DIR": str(home)}


def account_status(root: Path | None = None) -> tuple[bool, str]:
    """Whether a `claude` invocation can proceed, and what to do if not. For status surfaces.

    A mismatch does not make the seat unusable, so it does not flip the verdict -- it is appended to the
    detail, because a status line that says "fine" while two directories disagree about who is paying is how
    the disagreement survived five days unnoticed.
    """
    seat = select_seat(root)
    problems = mismatches(root)
    suffix = ("; " + "; ".join(problems)) if problems else ""
    if seat is not None:
        return True, f"project Claude account at {seat.config_dir} (seat {seat.id!r}){suffix}"
    return False, (
        f"no usable project Claude seat (the personal account is deliberately not used){suffix}"
    )
