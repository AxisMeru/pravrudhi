"""Who may close a gate without a person looking, and on what conditions.

ADR-REF: ADR-0040. CHARTER §6's fourth Sākṣī rule made promotion to canonical a human act, and two places
enforced it by refusing any agent identity outright: `gate.sign_gate` and the `/inbox/sign` route. The
operator withdrew that requirement on 2026-09-10.

The withdrawal lives in `configs/delegation.yaml` rather than in a flag on a command, for one reason: an
authority that can be assumed by passing an argument is not auditable. A reader of the ledger has to be able
to answer "who allowed this, and when were they asked" from the repository, and a `--force` would leave that
question unanswerable. Setting `active: false` stops every autonomous close immediately.

**Removing the human does not remove the judgement — it moves it here.** A person signing a pack was
implicitly checking that the gate's own checks passed, that its evidence layers were green, and that the
badge was not equivocal. Those were never written down because a person did them by looking. They are now
`conditions`, and `permits()` refuses with the specific reason when one fails, so a refusal names what to fix
rather than reading as "not allowed".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

CONFIG = Path("configs") / "delegation.yaml"

#: Identities that are never a person. Kept as the check on the *other* side: a delegation lets an agent sign
#: AS `agent-for-operator`, and must never let one sign as a human name.
AGENT_IDENTITIES = frozenset({"pravrudhi-agent", "agent", "claude", "agent-for-operator"})


@dataclass(frozen=True)
class Delegation:
    """The operator's standing grant, as recorded."""

    active: bool
    granted: str
    instruction: str
    identity: str
    scope: dict[str, bool]
    #: Either `{condition: bool}` or `{act: {condition: bool}}`. Nested is the current shape (conditions are
    #: per act); flat is honoured so an older config keeps working.
    conditions: dict[str, Any]
    source: Path

    def permits(self, act: str) -> tuple[bool, str]:
        """Whether this act is delegated, and why not if it is not."""
        if not self.active:
            return False, f"{self.source} sets active: false, so nothing closes autonomously"
        if act not in self.scope:
            return False, f"{self.source} says nothing about {act!r}; add it to `scope` to delegate it"
        if not self.scope[act]:
            return False, f"{self.source} sets scope.{act}: false"
        return True, f"delegated {self.granted} by the operator"

    def requires(self, condition: str, act: str) -> bool:
        """Whether `condition` applies to `act`.

        Scoped per act because the two acts have different artefacts. A gate JSON has closure layers; an inbox
        pack does not, and applying the gate's conditions to a pack refused every autonomous approval for "no
        gate check was run" -- demanding a check that cannot exist for that artefact rather than one that was
        skipped. A flat block is still honoured, so an older config keeps working.
        """
        scoped = self.conditions.get(act)
        if isinstance(scoped, dict):
            return bool(scoped.get(condition, False))
        return bool(self.conditions.get(condition, False))

    @property
    def citation(self) -> str:
        """What an autonomous signature records about its own authority.

        The instruction is quoted rather than referenced, because a gate JSON outlives the config file and a
        reader should not have to go and find out what was actually said.
        """
        return f"autonomous under the operator's delegation of {self.granted}: {self.instruction.strip()}"


def load_delegation(root: Path = Path(".")) -> Delegation | None:
    """The recorded delegation, or `None` when there is none.

    `None` means the pre-2026-09-10 rule stands and sign-off is a human act. That is the safe default and the
    behaviour of any workspace that does not carry this file -- an end-user install, for instance, whose
    operator has delegated nothing.
    """
    path = Path(root) / CONFIG
    if not path.exists():
        return None
    body: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
    return Delegation(
        active=bool(body.get("active", False)),
        granted=str(body.get("granted") or "unknown"),
        instruction=str(body.get("instruction") or ""),
        identity=str(body.get("signature_identity") or "agent-for-operator"),
        scope=dict(body.get("scope") or {}),
        conditions=dict(body.get("conditions") or {}),
        source=path,
    )


def unmet_conditions(
    delegation: Delegation, *, act: str, gate_problems: list[str] | None = None,
    failing_layers: list[str] | None = None, badge: str | None = None,
) -> list[str]:
    """The delegation's conditions that this subject fails, as reasons a reader can act on.

    Each argument is evidence the caller already has; a caller that cannot supply one passes `None` and that
    condition is not evaluated. Deliberately not "assume it passed": a condition nobody checked must not read
    as a condition that held.
    """
    reasons: list[str] = []
    if delegation.requires("require_gate_check_clean", act):
        if gate_problems is None:
            reasons.append("require_gate_check_clean is set but no gate check was run")
        elif gate_problems:
            reasons.append(f"gate check found {len(gate_problems)} problem(s): {'; '.join(gate_problems[:3])}")
    if delegation.requires("require_closure_layers_pass", act):
        if failing_layers is None:
            reasons.append("require_closure_layers_pass is set but the closure layers were not read")
        elif failing_layers:
            reasons.append(f"closure layers not passing: {', '.join(failing_layers)}")
    if delegation.requires("require_green_badge", act):
        if badge is None:
            reasons.append("require_green_badge is set but no badge was resolved")
        elif badge != "green":
            # Named as equivocal rather than as forbidden, and with the next step, because CHARTER §6 says to
            # propose the experiment that finds what is true rather than to stop.
            reasons.append(
                f"badge is {badge!r}, not green: the evidence is equivocal, so the autonomous action is to run "
                f"the experiment that resolves it, not to approve it"
            )
    return reasons
