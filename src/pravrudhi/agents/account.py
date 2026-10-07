"""Which account this project's `claude` invocations use.

2026-09-10: the project's CLI calls moved off the operator's personal account onto the project account, everywhere
(studio, product and the rest); the personal account is no longer used for this project.

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
`admin@axismeru.com` onto a second premium seat on the same Axis Meru team plan
(`default_claude_max_5x` tier), logged in at this same default path. It is a personal-domain *address*, not
the operator's personal *account* the CHARTER rule was written against — it is a seat the operator holds on
this project's own team plan, chosen deliberately to spread CLI load, and the operator did the login
themselves in an interactive terminal. Nothing here should treat that email domain as a signal to refuse; the
refusal this module raises is for *no credential provisioned*, not for which mailbox the credential belongs
to. `admin@axismeru.com` remains the desktop-app login only.

**2026-09-11: one account became two, with an order.** The operator's instruction (paraphrased): CLI load goes to the premium seat
that is not the team login; the team login is for the desktop app and is never used for CLI work while that seat can serve.

That is not a different constant, it is a different shape. A seat is now an entry in `configs/seats.yaml`,
the file's order is the precedence, and `select_seat` returns the first entry that can actually serve. A later entry is
reached only when the seat above it holds no credential or is inside a usage-limit cooldown -- never on an ordinary
failure. The team login's directory is NOT such an entry: see `FORBIDDEN_SEAT_DIR_NAMES`.

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

from pravrudhi.agents.seat_identity import is_placeholder, scripted_claude_email, seat_email_override

#: Where this project's Claude credential lives.
#:
#: This first pointed at `~/.config/pravrudhi/claude`, on the assumption that the personal account would stay
#: logged in at the default location and the two had to be kept apart. The operator resolved it differently on
#: 2026-09-10, when the project account was logged in and the personal one removed from this machine, so here the default
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

#: Issue #82 (Tag/Lead-2, 2026-09-26, stopped Track-A's audit run over this): `claude_env()` used to resolve
#: through `select_seat`'s registry (`configs/seats.yaml`, primary = the second seat), which is the right
#: rotation for REAL agentic coding dispatch (`ClaudeCodeAgent.run` calls `select_seat` directly and is
#: unaffected by this change) but the wrong account for a one-shot SCRIPTED `claude -p` call -- TEAM-RULES.md's
#: own Claude usage cost rules require every one of those to bill ONE named seat, never whichever seat the
#: registry happened to pick (the 2026-09-26 rule named seat 0; the operator moved it to seat 2 on 2026-09-27,
#: see `seat_identity.scripted_claude_email`). Configurable, never hardcoded past this default, per the issue's own
#: requirement.
SCRIPTED_CLAUDE_HOME_ENV = "PRAVRUDHI_SCRIPTED_CLAUDE_CONFIG_DIR"
SCRIPTED_CLAUDE_HOME_DEFAULT = Path("~/.config/pravrudhi/claude-loop")
#: Operator 2026-09-27 (TEAM-RULES Claude usage cost rules; issue #210): scripted `claude -p` bills TEAM SEAT 2
#: (`claude-loop`). Seat 0 (`claude-colab`) is R1/R2 only and seat 1
#: (admin@axismeru.com) is never used for scripted load. Mirrors `panel.CLAUDE_CLI_*` (#209).
#: The account that directory must hold is checked, not assumed: its address comes from LOCAL configuration
#: (`seat_identity.scripted_claude_email`: the environment or ~/.config/pravrudhi/seats.local.yaml), never from the
#: repository, and a missing one refuses.


#: The team login's config directory (seat 1, the desktop app's account). It is never a CLI seat: not by name, not by
#: position, not as a "fallback". `seats()` drops any entry that resolves to it and `scripted_claude_home()`
#: refuses it, so no code path can spend it.
FORBIDDEN_SEAT_DIR_NAMES = frozenset({"claude-admin"})


class AdminSeatRefused(RuntimeError):
    """A CLI or scripted call resolved to the team login's config directory (`FORBIDDEN_SEAT_DIR_NAMES`)."""


def is_forbidden_seat_dir(path: str | Path) -> bool:
    return Path(path).expanduser().name in FORBIDDEN_SEAT_DIR_NAMES


class PersonalAccountRefused(RuntimeError):
    """`claude` was asked for, and only the operator's personal login is available.

    Raised rather than falling back. The instruction was to stop using that account for this project, and a
    fallback would satisfy the letter of the change while leaving the behaviour intact.
    """


class ScriptedSeatMismatch(RuntimeError):
    """`claude_env`'s resolved directory holds a login, but its own cached profile (`.claude.json`) names a
    different account than the configured scripted seat (`seat_identity.scripted_claude_email`)
    -- issue #82's second requirement: verify the account,
    don't just check a credential file exists. Raised rather than silently proceeding under whatever account
    is actually logged in there, the same "the refusal is the other half" principle `PersonalAccountRefused`
    already applies one level up (no seat at all vs. the wrong seat)."""


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
    if not raw or is_forbidden_seat_dir(raw):
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
        seat_id = str(entry.get("id") or "unnamed")
        committed = str(entry.get("email") or "")
        # The committed file holds placeholders; the real address comes from the operator's LOCAL file. A placeholder with no
        # override is "unknown" (empty), so `mismatches` has nothing to compare rather than comparing against a fake.
        email = seat_email_override(seat_id) or ("" if is_placeholder(committed) else committed)
        seat = Seat(id=seat_id, email=email, config_dir=Path(str(entry["config_dir"])).expanduser())
        if is_forbidden_seat_dir(seat.config_dir):
            continue  # the team login is never a CLI seat, whatever the registry says
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


def refuse_no_seat_available(root: Path | None = None) -> None:
    """Raises `PersonalAccountRefused` when `select_seat` has genuinely found nothing in the registry that
    can serve -- the documented refusal for REAL agentic coding dispatch (`ClaudeCodeAgent.run`,
    `OrcaAgent`'s claude path), which rotates through `configs/seats.yaml` via `select_seat` directly and was
    never `claude_env`'s concern even before issue #82 repurposed that function for scripted, one-shot calls.
    Split out from `claude_env` (which used to raise this same message) so the two callers that only ever
    reach here after their own `select_seat` loop already came up empty keep raising about THAT registry,
    not about the unrelated scripted seat-2 account `claude_env` now resolves."""
    missing = next((s for s in seats(root) if not s.provisioned), None)
    raise PersonalAccountRefused(
        f"refusing to run `claude` with the operator's personal account. No seat this project declares can "
        f"serve right now. {how_to_provision(missing)}"
    )


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


def scripted_claude_home() -> Path:
    home = Path(os.environ.get(SCRIPTED_CLAUDE_HOME_ENV) or SCRIPTED_CLAUDE_HOME_DEFAULT).expanduser()
    if is_forbidden_seat_dir(home):
        raise AdminSeatRefused(f"{home} is the team login's directory, which is never a scripted-call seat; refusing")
    return home


def claude_env(*, require: bool = True, live: bool = False) -> dict[str, str]:
    """Environment overrides for a SCRIPTED, one-shot `claude` call (issue #82) -- never routes through
    `select_seat`'s registry rotation (`configs/seats.yaml`), which is a SEPARATE mechanism for real agentic
    coding dispatch (`ClaudeCodeAgent.run` calls `select_seat` directly, unaffected by this function).
    Resolves to `scripted_claude_home()` (seat 2, `claude-loop`, by default) and verifies -- doesn't just assume -- that the
    directory actually holds that account's login before returning, per TEAM-RULES.md's Claude usage cost
    rules. `live=True` also asks `claude auth status --json` (the only authoritative answer, one subprocess)
    and refuses unless it reports the expected email AND the directory has a recorded email to cross-check it
    against; the default checks the cached profile only.

    Returns the overrides to merge into the child's environment. With `require=False` a missing credential
    yields the overrides anyway rather than raising -- for callers that only want to know whether the binary
    is reachable, and must not crash a status listing over a credential. A MISMATCHED account (a login IS
    present, for the wrong seat) still raises even with `require=False`: that is a live, silent-billing risk
    `require=False`'s own "don't crash over a missing credential" reasoning was never meant to excuse.
    """
    home = scripted_claude_home()
    provisioned_here = any((home / name).exists() for name in CREDENTIAL_FILES)
    if not provisioned_here:
        if require:
            raise PersonalAccountRefused(
                f"refusing to run `claude` with the operator's personal account. No seat-2 credential at "
                f"{home}. "
                + how_to_provision(Seat(id='scripted', email=scripted_claude_email(required=False) or '', config_dir=home))
            )
        # Always set, even when unprovisioned, so nothing can silently reach the ambient CLAUDE_CONFIG_DIR.
        return {"CLAUDE_CONFIG_DIR": str(home)}
    expected = scripted_claude_email()  # raises SeatIdentityMissing: nothing to verify against is a refusal, not a pass
    assert expected is not None  # narrowing for the type checker: required=True never returns None
    recorded = Seat(id="scripted", email=expected, config_dir=home).recorded_email
    if recorded is not None and recorded != expected:
        raise ScriptedSeatMismatch(
            f"{home} is logged in as {recorded!r}, not the expected scripted seat-2 account {expected!r} "
            f"-- refusing rather than silently spending whoever is actually logged in there"
        )
    if live and recorded is None:
        # A live check is only as good as the profile it is cross-checked against: with no recorded email there
        # is nothing to confirm the directory is the account we think it is, so refuse rather than pass.
        raise ScriptedSeatMismatch(
            f"{home} has no recorded account email, so a live check cannot be cross-checked against its profile "
            f"-- refusing; log the expected scripted seat-2 account {expected!r} in there first"
        )
    if live:
        actual = live_identity(Seat(id="scripted", email=expected, config_dir=home))
        if actual != expected:
            raise ScriptedSeatMismatch(
                f"`claude auth status` for {home} shows {actual!r}, not the expected scripted seat-2 account "
                f"{expected!r} -- refusing; this never switches a login"
            )
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
