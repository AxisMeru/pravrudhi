"""Ways to reach a machine. Interchangeable by construction, so none is load-bearing."""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pravrudhi.hosts.base import HostSpec


class LocalTransport:
    """This machine. The default, and the only one a single-machine install needs."""

    name = "local"

    def __init__(self, spec: HostSpec | None = None) -> None:
        self.spec = spec or HostSpec(name="local")

    def available(self) -> bool:
        return True

    def run(self, command: str, timeout_s: int = 300) -> tuple[int, str, str]:
        try:
            p = subprocess.run(["bash", "-lc", command], capture_output=True, text=True, timeout=timeout_s)
            return p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired as e:
            return 124, (e.stdout or "") if isinstance(e.stdout, str) else "", f"timeout after {timeout_s}s"


class SshTransport:
    """Another machine over plain SSH: no agent, no daemon, nothing to install first."""

    name = "ssh"

    def __init__(self, spec: HostSpec) -> None:
        self.spec = spec

    @property
    def target(self) -> str:
        return f"{self.spec.user}@{self.spec.address}" if self.spec.user else self.spec.address

    def available(self) -> bool:
        if shutil.which("ssh") is None or not self.spec.address:
            return False
        code, _, _ = self.run("true", timeout_s=25)
        return code == 0

    def run(self, command: str, timeout_s: int = 300) -> tuple[int, str, str]:
        cmd = [
            "ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
            "-o", f"ConnectTimeout={min(20, timeout_s)}",
            # the target is validated on HostSpec construction, and "--" stops option parsing regardless
            "--", self.target,
            # a login shell, so PATH matches what a person would see: version managers put agent CLIs there and a
            # non-login shell would report a host as lacking tools it has
            f"bash -lc {shlex.quote(command)}",
        ]
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
            return p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired:
            return 124, "", f"ssh timeout after {timeout_s}s"


class OrcaTransport:
    """A machine Orca already manages. Orca supplies the session machinery; placement stays Pravrudhi's."""

    name = "orca"

    def __init__(self, spec: HostSpec) -> None:
        self.spec = spec

    def available(self) -> bool:
        if shutil.which("orca-ide") is None:
            return False
        p = subprocess.run(["orca-ide", "status"], capture_output=True, text=True, timeout=60)
        return "runtimeReachable: true" in p.stdout

    def run(self, command: str, timeout_s: int = 300) -> tuple[int, str, str]:
        args = ["orca-ide", "host", "run", "--host", self.spec.orca_host_id or "local", "--command", command, "--json"]
        try:
            p = subprocess.run(args, capture_output=True, text=True, timeout=timeout_s)
            return p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired:
            return 124, "", f"orca timeout after {timeout_s}s"


def transport_for(spec: HostSpec) -> LocalTransport | SshTransport | OrcaTransport:
    if spec.transport == "local":
        return LocalTransport(spec)
    if spec.transport == "ssh":
        return SshTransport(spec)
    return OrcaTransport(spec)


# --------------------------------------------------------------------------------------------------------------
# RunPod: pod lifecycle for Track B's T6 (docs/decisions/RUNPOD-HOUSE-RULES.md, binding for every agent).
#
# This is deliberately NOT a `Transport` (no `run`/`available`): a pod is not a machine this engine places jobs
# on transparently, it is an expensive, singular, house-rule-governed resource that must be created, watched and
# torn down as a deliberate act. Folding it into the `Transport` protocol would let ordinary placement logic
# reach for it the way it reaches for `local`/`ssh`/`orca`, which is exactly the accident the house rules exist
# to prevent (rule 6: "nobody starts training or a pod until the operator says kick off"). So this section is
# scaffolding a caller drives explicitly, never something `transport_for` can hand back.
#
# Every method here that touches the network goes through the RunPod REST v2 API directly
# (https://api.runpod.io/v2), not the MCP tool surface: MCP tools are agent-facing infrastructure for an
# interactive session, not something engine source can call at runtime. `http_call` is injectable so every test
# in `tests/test_runpod_transport.py` runs with a fake and never touches the real API or spends real money.
# --------------------------------------------------------------------------------------------------------------

RUNPOD_API_BASE = "https://api.runpod.io/v2"

#: House rule 3: "L40S (48 GB) or cheaper" -- the VRAM ceiling is the simple, mechanically enforceable half of
#: that rule (the cost-per-hour comparison across GPU types changes as RunPod's catalog does, and is not this
#: module's job to track; a caller still names the actual `gpu_type_id` and `cost_per_hour_usd` it found).
ALLOWED_GPU_VRAM_GB_MAX = 48

#: House rule 1: "$200 loaded, meant to last ~10 days."
MAX_TOTAL_SPEND_USD = 200.0

#: House rule 5: "pause everything and tell the lead at $100 spent."
PAUSE_AT_SPEND_USD = 100.0

#: House rule 5: "nothing new above $180; the last $20 is reserved for pulling checkpoints off the volume."
HARD_STOP_SPEND_USD = 180.0

#: House rule 3a (operator amendment, 2026-09-15): one L40S running 24h ~= $24/day (~$1.01/h). Spending
#: faster than this on any day, on any card mix, needs the lead's written go and the operator's knowledge --
#: this module surfaces the number, it does not block on it (the rule says "make it visible", not "refuse").
DAILY_CAP_USD = 24.0

#: House rule 16: the RTX 5090 is the durable local checkpoint store for this line (training there stays
#: forbidden by rule 8 -- this path is a sync destination, never a compute target).
#:
#: 2026-09-15, reviewer's 5090 preflight: checkpoints AND venvs go here, NEVER `/tmp` -- `/tmp` on this box is
#: a 16 GiB tmpfs and a spike checkpoint already hit ENOSPC there. `rsync_checkpoint_command` below is
#: structurally incapable of writing anywhere else (the destination is always built from this constant, never
#: from a caller-supplied path), and any future pod-start/staging helper added to this module must target a
#: subdirectory of this same root, not `/tmp`.
CHECKPOINT_SYNC_ROOT = "/home/ss/fusion-project/prabhasa-nyaya/checkpoints"

_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class RunpodError(RuntimeError):
    """A RunPod API call failed, or a house-rule guard refused the request."""


class OnePodGuardViolation(RunpodError):
    """`create_pod` refused: `list_pods` already shows a pod, running or stopped (house rule 2)."""


#: (method, path, json body) -> (HTTP status, parsed JSON response). The default implementation makes a real
#: call; every test supplies its own fake instead.
HttpCall = Callable[[str, str, dict[str, Any] | None], tuple[int, dict[str, Any]]]


def _default_http_call(
    method: str, path: str, json_body: dict[str, Any] | None, *, api_key: str, base_url: str = RUNPOD_API_BASE,
) -> tuple[int, dict[str, Any]]:
    import httpx

    with httpx.Client(timeout=30.0) as client:
        resp = client.request(method, f"{base_url}{path}", json=json_body,
                               headers={"Authorization": f"Bearer {api_key}"})
    try:
        data = resp.json()
    except ValueError:
        data = {}
    return resp.status_code, data if isinstance(data, dict) else {"data": data}


@dataclass(frozen=True)
class PodSpec:
    """What `create_pod` needs. The VRAM ceiling is enforced at construction, not at call time, so a spec that
    should never exist cannot be built in the first place, let alone passed around."""

    name: str
    gpu_type_id: str
    image: str
    gpu_vram_gb: float
    cost_per_hour_usd: float
    container_disk_gb: int = 50
    network_volume_id: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    estimated_build_minutes: float = 0.0
    """2026-09-15, reviewer's arm-N preflight: `nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16` (a Mamba hybrid)
    hard-requires `mamba_ssm`/`causal_conv1d` at import, and this host cannot build that wheel (no nvcc, no
    prebuilt wheel for the relevant torch/CUDA combo). A pod image for that arm must either ship both
    preinstalled, or the pod-start includes a CUDA-toolkit build step costing roughly 20-40 real minutes --
    real cost, not zero, and easy to leave off a proposal by assuming pod-start is instant. Zero for an image
    that already carries what the job needs (e.g. the Qwen arm, no such kernel). Whoever assembles a per-job
    go proposal reads this field to state the extra minutes/$ honestly rather than from arithmetic that
    forgot pod-start exists."""

    def __post_init__(self) -> None:
        if self.gpu_vram_gb > ALLOWED_GPU_VRAM_GB_MAX:
            raise RunpodError(
                f"{self.gpu_type_id}: {self.gpu_vram_gb}GB exceeds the house-rule ceiling of "
                f"{ALLOWED_GPU_VRAM_GB_MAX}GB (L40S or cheaper 48GB only)"
            )

    @property
    def estimated_build_cost_usd(self) -> float:
        """`estimated_build_minutes` translated into the currency a per-job go actually has to state -- so an
        image requiring a CUDA-toolkit build step (arm N) shows up in the same cost figure as the run itself,
        not as a separately-remembered caveat."""
        return round((self.estimated_build_minutes / 60.0) * self.cost_per_hour_usd, 4)


@dataclass(frozen=True)
class GpuCandidate:
    """One card RunPod has in stock, as read off the catalog -- not a recommendation, a fact to choose among."""

    gpu_type_id: str
    vram_gb: float
    cost_per_hour_usd: float


def choose_gpu_type(candidates: list[GpuCandidate], *, measured_peak_vram_gb: float) -> GpuCandidate:
    """House rule 3 (operator amendment, 2026-09-15): 'do well and do more with the least spend' -- pick the
    cheapest in-stock card whose VRAM is within the house-rule ceiling (`ALLOWED_GPU_VRAM_GB_MAX`) and whose
    headroom over the measured preflight peak is at least the required margin (measured peak must be no more
    than 80% of the card's VRAM, i.e. the card must be at least 1.25x the measured peak). A 24 GB card is a
    legitimate answer now, not just the 48 GB tier -- this function does not prefer one tier over the other,
    only cost among whatever satisfies the constraint.

    Refuses rather than guesses when nothing fits: an oversized card silently chosen "to be safe" is exactly
    the spend the house rules exist to prevent.
    """
    fits = [
        c for c in candidates
        if c.vram_gb <= ALLOWED_GPU_VRAM_GB_MAX and measured_peak_vram_gb <= 0.8 * c.vram_gb
    ]
    if not fits:
        raise RunpodError(
            f"no candidate card has >=80% headroom over the measured peak {measured_peak_vram_gb}GB within "
            f"the {ALLOWED_GPU_VRAM_GB_MAX}GB house-rule ceiling; checked: "
            f"{[(c.gpu_type_id, c.vram_gb) for c in candidates]}"
        )
    return min(fits, key=lambda c: c.cost_per_hour_usd)


class RunpodPodManager:
    """Pod lifecycle under the RunPod house rules. Every method that could spend money or leave a pod running
    enforces the relevant house rule itself, so a caller cannot violate one by skipping a step: `create_pod`
    refuses without a written `go` and re-checks `list_pods` first every single time (house rules 2 and 7),
    `spend_status` reads the pause/hard-stop thresholds directly rather than leaving them to be remembered.

    Nothing here provisions a real pod merely by being imported or constructed. `create_pod` is the only method
    that spends money, and it requires an explicit, non-empty `go` string on every call -- this class does not
    decide what counts as a valid authorization (that is a person's judgement, per house rule 7), only refuses
    the mechanical case of nothing having been supplied at all.
    """

    def __init__(self, api_key: str, *, http_call: HttpCall | None = None) -> None:
        self._api_key = api_key
        self._http_call = http_call or (
            lambda method, path, body: _default_http_call(method, path, body, api_key=api_key)
        )

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        status, data = self._http_call(method, path, body)
        if status not in (200, 201, 204):
            raise RunpodError(f"{method} {path} failed: HTTP {status} {data}")
        return data

    def list_pods(self) -> list[dict[str, Any]]:
        data = self._call("GET", "/pods")
        pods = data.get("pods", data if isinstance(data, list) else [])
        return list(pods) if isinstance(pods, list) else []

    def _assert_one_pod_guard(self) -> None:
        """House rule 2: before `create-pod`, run `list-pods`; if anything is listed -- running or stopped,
        stopped pods still bill disk -- do not create another."""
        existing = self.list_pods()
        if existing:
            ids = ", ".join(str(p.get("id", "?")) for p in existing)
            raise OnePodGuardViolation(
                f"one-pod-account-wide guard: {len(existing)} pod(s) already listed ({ids}); stopped pods "
                "still bill disk and still count -- delete or resume the existing pod before creating another"
            )

    def create_pod(self, spec: PodSpec, *, go: str) -> dict[str, Any]:
        if not go or not go.strip():
            raise RunpodError(
                "create_pod refuses without an explicit go (a written per-job authorization naming the GPU, "
                "image, volume, expected hours/$, and kill condition -- house rule 7); nothing is provisioned"
            )
        self._assert_one_pod_guard()
        body: dict[str, Any] = {
            "name": spec.name,
            "gpu": {"gpuTypeId": spec.gpu_type_id},
            "image": spec.image,
            "containerDiskInGb": spec.container_disk_gb,
            "env": dict(spec.env),
        }
        if spec.network_volume_id:
            body["networkVolumeId"] = spec.network_volume_id
        return self._call("POST", "/pods", body)

    def stop_pod(self, pod_id: str) -> dict[str, Any]:
        """House rule: stop, never delete, when a pod is only paused -- the checkpoint stays reachable for a
        resume within the same pod, which the durability rules (15) require."""
        return self._call("POST", f"/pods/{pod_id}/action", {"action": "stop"})

    def terminate_pod(self, pod_id: str) -> dict[str, Any]:
        """House rule 18: call this only after the final checkpoint's sha256 is confirmed on the volume, the
        5090, and (for milestones) HF -- this method does not check that itself, it only performs the delete
        the caller has already confirmed is safe."""
        return self._call("DELETE", f"/pods/{pod_id}")

    def get_billing(self, *, bucket_size: str | None = None) -> dict[str, Any]:
        path = "/billing" if bucket_size is None else f"/billing?bucketSize={bucket_size}"
        return self._call("GET", path)

    def daily_spend_status(self) -> dict[str, Any]:
        """House rule 3a: a day spending faster than ~$24 (one L40S at 24h) needs the lead's written go and
        the operator's knowledge. `GET /billing` buckets by day by default; the most recent bucket is today's
        (partial) spend. This surfaces the number -- it never blocks, per the rule's own wording ("make it
        visible, don't auto-block")."""
        billing = self.get_billing(bucket_size="day")
        records = billing.get("records", [])
        today = float(records[-1].get("totalAmount", 0.0)) if records else 0.0
        return {
            "today_spend_usd": today,
            "daily_cap_usd": DAILY_CAP_USD,
            "approaching_cap": today >= 0.8 * DAILY_CAP_USD,
            "over_cap": today > DAILY_CAP_USD,
        }

    def spend_status(self) -> dict[str, Any]:
        """Current spend read against the house-rule ceilings (rule 1, 5), so a caller never has to remember
        the numbers or recompute them by hand."""
        billing = self.get_billing()
        spent = float(billing.get("totalSpend", billing.get("total", 0.0)) or 0.0)
        return {
            "spent_usd": spent,
            "must_pause": spent >= PAUSE_AT_SPEND_USD,
            "hard_stop": spent >= HARD_STOP_SPEND_USD,
            "remaining_usd": max(0.0, MAX_TOTAL_SPEND_USD - spent),
        }


def append_ledger_entry(
    path: Path, *, event: str, pod_id: str, gpu_type_id: str, cost_per_hour_usd: float, spent_usd: float,
    why: str, actor: str,
) -> None:
    """House rule 4: every pod creation, stop and delete is recorded (who, why, gpuTypeId, $/h, measured cost
    from `get-billing`) in the repo's own `docs/decisions/runpod-ledger.md`. Appends one markdown table row;
    writes the header first if the file is new. `path` is the caller's to choose -- this function does not
    assume which repo owns the run."""
    path = Path(path)
    header = "| pod | event | gpu_type_id | $/h | spent_usd | why | actor | at |\n|---|---|---|---|---|---|---|---|\n"
    row = (
        f"| {pod_id} | {event} | {gpu_type_id} | {cost_per_hour_usd} | {spent_usd} | {why} | {actor} | "
        f"{datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')} |\n"
    )
    existing = path.read_text() if path.exists() else ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(existing + (header if not existing else "") + row)


def rsync_checkpoint_command(
    *, pod_ssh_target: str, pod_ssh_port: int, remote_checkpoint_dir: str, run_id: str,
) -> list[str]:
    """House rule 16: ship each checkpoint off the pod as it is written, to the RTX 5090 via `rsync` over SSH
    into `CHECKPOINT_SYNC_ROOT/<run_id>/`. Returns the argv; this function never runs it -- the caller decides
    when, exactly as every other command-building helper in this module leaves execution to `Transport.run` or
    its own subprocess call, so a test can assert on the command without touching a network or a real pod."""
    if not _RUN_ID.match(run_id):
        raise RunpodError(
            f"run_id {run_id!r} must be a plain directory-name component (no '/', no '..'); a run id is not "
            "a path, so the destination stays pinned under CHECKPOINT_SYNC_ROOT regardless of what is passed"
        )
    destination = f"{CHECKPOINT_SYNC_ROOT}/{run_id}/"
    return [
        "rsync", "-avz", "--partial",
        "-e", f"ssh -p {pod_ssh_port} -o BatchMode=yes -o StrictHostKeyChecking=accept-new",
        f"{pod_ssh_target}:{remote_checkpoint_dir}/", destination,
    ]
