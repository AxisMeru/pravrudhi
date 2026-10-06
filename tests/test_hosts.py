"""The fleet layer: capabilities, placement, enrolment and the zero-config default."""

import pytest
import yaml

from pravrudhi.hosts.base import HostCapabilities, HostSpec, Requirement
from pravrudhi.hosts.fleet import load_fleet, place, probe_host, save_host
from pravrudhi.hosts.probe import parse_probe
from pravrudhi.hosts.transports import LocalTransport, transport_for

MAC = HostCapabilities(os="Darwin", arch="arm64", cpu_count=10, ram_gb=16.0, accelerator="metal", reachable=True)
GPU = HostCapabilities(
    os="Linux", arch="x86_64", cpu_count=32, ram_gb=128.0, gpu_vram_gb=31.8, accelerator="cuda",
    docker=True, agents=["claude", "codex"], reachable=True,
)
LAPTOP = HostCapabilities(os="Linux", arch="x86_64", cpu_count=8, ram_gb=32.0, accelerator="none", docker=True, reachable=True)


def test_a_fresh_install_has_one_host_and_no_config(tmp_path):
    hosts = load_fleet(tmp_path)
    assert [h.name for h in hosts] == ["local"] and hosts[0].transport == "local"
    assert not (tmp_path / "configs" / "hosts.yaml").exists(), "the default fleet must not require a file"


def test_apple_silicon_serves_open_models_but_cannot_train():
    assert MAC.can_serve_open_models and not MAC.can_train
    assert MAC.usable_model_gb == 12.0, "unified memory, roughly three quarters allocatable"
    assert GPU.can_train and GPU.usable_model_gb == 31.8
    assert not LAPTOP.can_serve_open_models and not LAPTOP.can_train


def test_placement_picks_the_capable_host_and_explains_rejections():
    fleet = [(HostSpec(name="mac", transport="ssh", address="mac.local"), MAC), (HostSpec(name="gpu"), GPU)]
    chosen, why = place(fleet, Requirement(needs_cuda=True, needs_docker=True, min_vram_gb=8))
    assert chosen is not None and chosen.name == "gpu"
    assert why["mac"] == ["needs cuda, has metal", "needs docker"]

    chosen, why = place(fleet, Requirement(needs_accelerator=True, min_vram_gb=10))
    assert chosen is not None and chosen.name == "gpu", "ties break toward the most capable machine"

    chosen, why = place(fleet, Requirement(min_vram_gb=64))
    assert chosen is None and set(why) == {"mac", "gpu"}


def test_an_unreachable_host_is_never_chosen_and_says_so():
    dead = HostCapabilities(reachable=False, error="ssh timeout")
    chosen, why = place([(HostSpec(name="dead"), dead)], Requirement())
    assert chosen is None and "unreachable: ssh timeout" in why["dead"]


def test_agent_requirement_is_enforced():
    fleet = [(HostSpec(name="mac"), MAC), (HostSpec(name="gpu"), GPU)]
    chosen, why = place(fleet, Requirement(needs_agent="codex"))
    assert chosen is not None and chosen.name == "gpu" and why["mac"] == ["needs agent codex"]


def test_probe_output_is_parsed_and_garbage_is_not_invented():
    cap = parse_probe('some noise\n{"os":"Darwin","arch":"arm64","ram_gb":16.0,"accelerator":"metal","cpu_count":10}')
    assert cap.reachable and cap.os == "Darwin" and cap.usable_model_gb == 12.0
    assert not parse_probe("no json here").reachable
    assert not parse_probe("{not valid json}").reachable


def test_enrolment_round_trips_and_local_is_never_duplicated(tmp_path):
    save_host(tmp_path, HostSpec(name="mac-mini", transport="ssh", address="192.0.2.5", user="me"))
    save_host(tmp_path, HostSpec(name="local", transport="local"))
    names = [h.name for h in load_fleet(tmp_path)]
    assert names.count("local") == 1 and "mac-mini" in names
    data = yaml.safe_load((tmp_path / "configs" / "hosts.yaml").read_text())
    assert all(h["name"] != "local" for h in data["hosts"])


def test_local_transport_really_runs_and_probe_reaches_this_machine(tmp_path):
    assert LocalTransport().run("echo alive")[1].strip() == "alive"
    assert transport_for(HostSpec(name="x", transport="orca")).name == "orca"
    cap = probe_host(HostSpec(name="local", transport="local"))
    assert cap.reachable and cap.os in ("Linux", "Darwin") and cap.cpu_count > 0


def test_a_host_cannot_smuggle_flags_onto_the_ssh_argv():
    """An address beginning with '-' would be read by ssh as an option, and ssh options can execute commands."""
    from pravrudhi.hosts.base import InvalidHostSpec

    for bad in ("-oProxyCommand=touch /tmp/pwned", "-lroot", "host;rm -rf /", "host name", "-"):
        with pytest.raises(InvalidHostSpec):
            HostSpec(name="x", transport="ssh", address=bad)
    for bad_user in ("-oProxyCommand=x", "a b", "-root"):
        with pytest.raises(InvalidHostSpec):
            HostSpec(name="x", transport="ssh", address="192.0.2.5", user=bad_user)
    with pytest.raises(InvalidHostSpec):
        HostSpec(name="-evil", transport="local")
    with pytest.raises(InvalidHostSpec):
        HostSpec(name="x", transport="telnet")
    with pytest.raises(InvalidHostSpec):
        HostSpec(name="x", transport="ssh")  # ssh with no address
    ok = HostSpec(name="mac-mini", transport="ssh", address="192.0.2.6", user="me")
    assert ok.address == "192.0.2.6"
    assert HostSpec(name="mac", transport="ssh", address="mac-mini.local", user="me").user == "me"
