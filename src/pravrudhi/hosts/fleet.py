"""The fleet: enrol machines, measure them, place work on them.

A fresh install has exactly one host, `local`, and needs no configuration file at all. Machines are added only when
a user has them, and the engine keeps working with none. Capabilities are re-measured rather than remembered,
because a machine's GPU, container runtime and installed agents change under it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from pravrudhi.hosts.base import HostCapabilities, HostSpec, Requirement
from pravrudhi.hosts.probe import PROBE_SOURCE, parse_probe
from pravrudhi.hosts.transports import transport_for

FLEET_FILE = Path("configs") / "hosts.yaml"
LOCAL = HostSpec(name="local", transport="local")


def load_fleet(root: Path) -> list[HostSpec]:
    """Every enrolled machine. `local` is always present and never needs declaring."""
    p = Path(root) / FLEET_FILE
    hosts = [LOCAL]
    if p.exists():
        data = yaml.safe_load(p.read_text()) or {}
        for h in data.get("hosts") or []:
            if str(h.get("name")) == "local":
                continue
            hosts.append(
                HostSpec(
                    name=str(h["name"]),
                    transport=str(h.get("transport", "ssh")),
                    address=str(h.get("address", "")),
                    user=str(h.get("user", "")),
                    workdir=str(h.get("workdir", "~/pravrudhi")),
                    orca_host_id=str(h.get("orca_host_id", "")),
                )
            )
    return hosts


def save_host(root: Path, spec: HostSpec) -> Path:
    """Persist an enrolled machine. `local` is implicit and is never written, so the file stays empty of noise
    and a single-machine install keeps needing no configuration at all."""
    p = Path(root) / FLEET_FILE
    if spec.name == "local":
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    data = yaml.safe_load(p.read_text()) if p.exists() else {}
    data = data or {}
    hosts = [h for h in (data.get("hosts") or []) if str(h.get("name")) != spec.name]
    hosts.append({k: v for k, v in spec.to_dict().items() if v not in ("", None)})
    data["hosts"] = hosts
    p.write_text(yaml.safe_dump(data, sort_keys=True))
    return p


def probe_host(spec: HostSpec, timeout_s: int = 120) -> HostCapabilities:
    """Ship the probe over the transport and run it with the host's own python3."""
    t = transport_for(spec)
    if not t.available():
        return HostCapabilities(reachable=False, error=f"{t.name} transport unavailable")
    payload = PROBE_SOURCE.replace("'", "'\\''")
    code, out, err = t.run(f"printf '%s' '{payload}' | python3 -", timeout_s=timeout_s)
    if code != 0 and not out.strip():
        return HostCapabilities(reachable=False, error=(err or f"probe exit {code}").strip()[:300])
    return parse_probe(out)


def survey_fleet(root: Path) -> list[tuple[HostSpec, HostCapabilities]]:
    """Probe every enrolled machine and return each with its measured capabilities."""
    return [(s, probe_host(s)) for s in load_fleet(root)]


def place(
    surveyed: list[tuple[HostSpec, HostCapabilities]], req: Requirement
) -> tuple[HostSpec | None, dict[str, list[str]]]:
    """Pick a host that meets the requirement, and say why each rejected host was rejected.

    Ties break toward the most capable machine, so a training job lands on the largest GPU rather than the first
    host that merely clears the bar. Rejections are returned rather than swallowed: a loop that silently runs
    nowhere is worse than one that stops and says which machine was missing what.
    """
    fits: list[tuple[HostSpec, HostCapabilities]] = []
    why: dict[str, list[str]] = {}
    for spec, cap in surveyed:
        unmet = req.unmet(cap)
        if unmet:
            why[spec.name] = unmet
        else:
            fits.append((spec, cap))
    if not fits:
        return None, why
    fits.sort(key=lambda sc: (sc[1].usable_model_gb, sc[1].ram_gb, sc[1].cpu_count), reverse=True)
    return fits[0][0], why


def fleet_report(root: Path) -> dict[str, Any]:
    """The fleet as a JSON-ready report: each host and what it can do."""
    rows = []
    for spec, cap in survey_fleet(root):
        rows.append({"host": spec.to_dict(), "capabilities": cap.to_dict()})
    return {"hosts": rows}


def render_fleet(root: Path) -> str:
    """The fleet as a text table for the terminal."""
    lines = [
        f"{'host':12} {'transport':10} {'os/arch':18} {'accel':7} {'usable':>8}  {'docker':7} {'train':6} agents",
        "-" * 100,
    ]
    for spec, cap in survey_fleet(root):
        if not cap.reachable:
            lines.append(f"{spec.name:12} {spec.transport:10} unreachable: {cap.error[:60]}")
            continue
        lines.append(
            f"{spec.name:12} {spec.transport:10} {(cap.os + '/' + cap.arch):18} {cap.accelerator:7} "
            f"{cap.usable_model_gb:>7.1f}G  {str(cap.docker):7} {str(cap.can_train):6} {','.join(cap.agents) or '-'}"
        )
    return "\n".join(lines)


__all__ = [
    "FLEET_FILE", "LOCAL", "load_fleet", "save_host", "probe_host", "survey_fleet", "place",
    "fleet_report", "render_fleet", "json",
]
