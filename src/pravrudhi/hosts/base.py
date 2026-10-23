"""Machines Pravrudhi can place work on.

Pravrudhi is the recursive self-improvement engine; a fleet of machines is something it *uses*, never something it
becomes. So the fleet layer is deliberately small and owned here: a host is a name, a transport and a set of
measured capabilities, and placement is a function from a job's requirements to a host that satisfies them.

Three rules keep this from diluting the engine. A single machine with no configuration is the default, because the
engine must stay a thing a stranger can download and run. Transports are interchangeable, so no orchestrator is
load-bearing: Orca is one transport beside local execution and plain SSH. And capabilities are *measured on the
host*, never declared in a config file, because a config that claims a GPU it does not have would put real
evidence on the wrong machine.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, runtime_checkable

# A hostname, user or orca host id goes onto an argv as a bare token. A value beginning with "-" would be read as a
# flag by ssh, which accepts options that execute commands (ProxyCommand among them), so an enrolled address could
# otherwise smuggle arbitrary execution into every probe. These patterns are deliberately narrow.
_ADDRESS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,253}$")
_USER = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,63}$")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class InvalidHostSpec(ValueError):
    """A host entry that does not describe a usable machine."""
    pass


@dataclass(frozen=True)
class HostSpec:
    """How to reach a machine. `transport` is local, ssh or orca."""

    name: str
    transport: str = "local"
    address: str = ""
    user: str = ""
    workdir: str = "~/pravrudhi"
    orca_host_id: str = ""

    def __post_init__(self) -> None:
        if not _ID.match(self.name):
            raise InvalidHostSpec(f"host name {self.name!r} must start alphanumeric and contain only [A-Za-z0-9._-]")
        if self.transport not in ("local", "ssh", "orca"):
            raise InvalidHostSpec(f"unknown transport {self.transport!r}")
        if self.address and not _ADDRESS.match(self.address):
            raise InvalidHostSpec(f"address {self.address!r} is not a plain hostname or IP")
        if self.user and not _USER.match(self.user):
            raise InvalidHostSpec(f"user {self.user!r} is not a plain user name")
        if self.orca_host_id and not _ID.match(self.orca_host_id):
            raise InvalidHostSpec(f"orca host id {self.orca_host_id!r} is not a plain identifier")
        if self.transport == "ssh" and not self.address:
            raise InvalidHostSpec("the ssh transport needs an address")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HostCapabilities:
    """What a machine can actually do, as measured on it."""

    os: str = ""
    arch: str = ""
    cpu_count: int = 0
    ram_gb: float = 0.0
    gpu_name: str = ""
    gpu_vram_gb: float = 0.0
    accelerator: str = "none"  # cuda | metal | none
    accel_mem_gb: float = 0.0  # measured usable accelerator memory when a runtime could report it
    docker: bool = False
    python: str = ""
    agents: list[str] = field(default_factory=list)
    local_models: list[str] = field(default_factory=list)
    reachable: bool = False
    error: str = ""

    @property
    def can_train(self) -> bool:
        """Weight-level work needs a CUDA GPU and a container runtime: the kernel only admits container-isolated runs."""
        return self.accelerator == "cuda" and self.docker and self.gpu_vram_gb >= 8.0

    @property
    def can_serve_open_models(self) -> bool:
        """Inference on open weights runs on CUDA or on Apple Metal, which needs no container."""
        return (self.accelerator == "cuda" and self.gpu_vram_gb >= 4.0) or (
            self.accelerator == "metal" and self.ram_gb >= 16.0
        )

    @property
    def usable_model_gb(self) -> float:
        """Rough ceiling for a model file on this host.

        On CUDA that is video memory. On Apple Silicon the GPU draws on unified memory, and the conventional
        allocatable fraction is about three quarters, so a 16 GB Mac carries roughly a 12 GB model.
        """
        if self.accel_mem_gb:
            return round(self.accel_mem_gb, 1)  # measured beats estimated
        if self.accelerator == "cuda":
            return round(self.gpu_vram_gb, 1)
        if self.accelerator == "metal":
            return round(self.ram_gb * 0.75, 1)
        return 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.update(
            can_train=self.can_train,
            can_serve_open_models=self.can_serve_open_models,
            usable_model_gb=self.usable_model_gb,
        )
        return d


@dataclass(frozen=True)
class Requirement:
    """What a job needs. Anything left at its default does not constrain placement."""

    needs_cuda: bool = False
    needs_docker: bool = False
    needs_accelerator: bool = False
    min_vram_gb: float = 0.0
    min_ram_gb: float = 0.0
    needs_agent: str = ""

    def unmet(self, cap: HostCapabilities) -> list[str]:
        """Every reason this host cannot take the job, named. An empty list means it can."""
        why: list[str] = []
        if not cap.reachable:
            why.append(f"unreachable: {cap.error or 'no probe'}")
        if self.needs_cuda and cap.accelerator != "cuda":
            why.append(f"needs cuda, has {cap.accelerator}")
        if self.needs_accelerator and cap.accelerator == "none":
            why.append("needs a gpu accelerator, has none")
        if self.needs_docker and not cap.docker:
            why.append("needs docker")
        if self.min_vram_gb and cap.usable_model_gb < self.min_vram_gb:
            why.append(f"needs {self.min_vram_gb}GB usable, has {cap.usable_model_gb}GB")
        if self.min_ram_gb and cap.ram_gb < self.min_ram_gb:
            why.append(f"needs {self.min_ram_gb}GB ram, has {cap.ram_gb}GB")
        if self.needs_agent and self.needs_agent not in cap.agents:
            why.append(f"needs agent {self.needs_agent}")
        return why


@runtime_checkable
class Transport(Protocol):
    """How commands reach a machine: a name, a way to run a command with a timeout, and whether the machine can be
    reached.
    """
    name: str

    def run(self, command: str, timeout_s: int = 300) -> tuple[int, str, str]: ...
    def available(self) -> bool: ...
