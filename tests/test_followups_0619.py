"""0.6.19 follow-ups: the failed-job body is pinned; `intake_timeout_s` overflow names the setting; the objective write keeps the
umask of the process out of other threads' way. Invented data only."""

import os
import threading
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from pravrudhi.api import partner
from pravrudhi.application import objectives
from tests.test_chat import OBJECTIVE
from tests.test_partner_audit import ADMIN, Clock, H, _app, _key, _req


# ---- #499: a crashed analyse-facts job stores the coded body -------------------------------------------------------------------
def test_a_crashed_job_stores_the_coded_internal_error_body(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PRAVRUDHI_ADMINS", ADMIN.id)
    c = TestClient(_app(tmp_path, Clock(), executor=lambda t: t()))
    secret = _key(c, "acme")

    def boom(**_kw: Any) -> Any:
        raise RuntimeError("secret detail /home/someone/x")

    monkeypatch.setattr(partner, "AnalyseFactsResponse", boom)
    job = c.post("/api/v1/analyse-facts/jobs", json=_req(), headers={H: secret}).json()
    got = c.get(f"/api/v1/analyse-facts/jobs/{job['job_id']}", headers={H: secret})
    body = got.json()
    assert got.status_code == 200 and body["status"] == "failed"
    assert body["error"] == {
        "status_code": 500,
        "body": {"error": "internal_error", "detail": "an internal error occurred; try again later"},
    }
    assert "secret" not in got.text and "/home" not in got.text


# ---- #500: an absurd integer in the config file names the setting ----------------------------------------------------
def test_an_integer_too_large_for_a_float_is_refused_with_the_key_named() -> None:
    for huge in (10**309, 10**400, -(10**309)):
        with pytest.raises(ValueError, match="intake_timeout_s"):
            partner._intake_timeout(huge)
    assert partner._intake_timeout(10**308) == float(
        10**308
    )  # the largest that still fits is a number (PartnerApiConfig bounds it)


def test_the_loader_refuses_the_huge_integer_through_the_file(tmp_path: Path) -> None:
    from tests.test_client_ip_diagnostic import REPO

    body = yaml.safe_load((REPO / "configs" / "partner_api.yaml").read_text())
    body["intake_timeout_s"] = 10**309
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs" / "partner_api.yaml").write_text(yaml.safe_dump(body))
    with pytest.raises(ValueError, match="intake_timeout_s"):
        partner.load_partner_api_config(tmp_path)


# ---- #501 L1: the objective write never changes the process umask; the mode follows the umask / the target -------------
def _new_objective(i: int) -> objectives.Objective:
    from dataclasses import replace

    return replace(OBJECTIVE, id=f"conc-{i:04d}")


def test_write_never_touches_the_process_umask(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(_mask: int) -> int:
        raise AssertionError("os.umask must not be called by write()")

    monkeypatch.setattr(os, "umask", forbidden)
    objectives.write(tmp_path, _new_objective(1))


def test_files_created_by_other_threads_during_new_objective_writes_keep_the_umask(tmp_path: Path) -> None:
    """Track-B's L1: write() used to set os.umask(0) for a moment, so a file another thread created in that moment came out 0666.
    A hammer thread creates files while the main thread writes 300 NEW objectives; none may be group/world-writable."""
    old = os.umask(0o022)
    try:
        out = tmp_path / "hammer"
        out.mkdir()
        stop = threading.Event()
        bad: list[str] = []

        def hammer() -> None:
            n = 0
            while not stop.is_set():
                path = out / f"f{n}"
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
                os.close(fd)
                if os.stat(path).st_mode & 0o022:
                    bad.append(path.name)
                n += 1

        h = threading.Thread(target=hammer)
        h.start()
        try:
            for i in range(300):
                objectives.write(tmp_path, _new_objective(i))
        finally:
            stop.set()
            h.join()
        assert bad == []
        written = sorted(objectives.objectives_dir(tmp_path).glob("conc-*.yaml"))
        assert len(written) == 300 and all((p.stat().st_mode & 0o777) == 0o644 for p in written)  # 0666 & ~022
    finally:
        os.umask(old)


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o664])
def test_the_mode_of_an_existing_target_is_kept(tmp_path: Path, mode: int) -> None:
    """Modes that differ from both mkstemp's 0600 and the umask default, so a copy that did not happen cannot pass."""
    objectives.write(tmp_path, OBJECTIVE)
    path = objectives.objectives_dir(tmp_path) / f"{OBJECTIVE.id}.yaml"
    path.chmod(mode)
    objectives.write(tmp_path, OBJECTIVE)
    assert (path.stat().st_mode & 0o777) == mode


# ---- #507: the temporary copy never holds the content at a wider mode than the target ------------------------------------------
def test_the_temporary_file_is_created_0600_and_widened_only_after_the_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    objectives.write(tmp_path, OBJECTIVE)
    path = objectives.objectives_dir(tmp_path) / f"{OBJECTIVE.id}.yaml"
    path.chmod(0o640)
    seen: list[tuple[str, int]] = []
    real_open = os.open

    def spy(p: object, flags: int, mode: int = 0o777, *a: object, **k: object) -> int:
        if str(p).endswith(".tmp"):
            seen.append(("create", mode))
        return real_open(p, flags, mode, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", spy)
    objectives.write(tmp_path, OBJECTIVE)
    assert seen == [("create", 0o600)]  # never 0666: the umask cannot widen it
    assert (path.stat().st_mode & 0o777) == 0o640  # and the target mode is restored by fchmod before the rename


def test_a_0600_objective_is_never_group_readable_through_its_temporary_copy(tmp_path: Path) -> None:
    objectives.write(tmp_path, OBJECTIVE)
    d = objectives.objectives_dir(tmp_path)
    path = d / f"{OBJECTIVE.id}.yaml"
    path.chmod(0o600)
    old = os.umask(0o022)
    try:
        stop = threading.Event()
        leaks: list[str] = []

        def watcher() -> None:
            while not stop.is_set():
                for p in d.glob(".*.tmp"):
                    try:
                        if p.stat().st_mode & 0o077 and p.stat().st_size:
                            leaks.append(p.name)
                    except FileNotFoundError:
                        pass

        w = threading.Thread(target=watcher)
        w.start()
        try:
            for _ in range(400):
                objectives.write(tmp_path, OBJECTIVE)
        finally:
            stop.set()
            w.join()
    finally:
        os.umask(old)
    assert leaks == [] and (path.stat().st_mode & 0o777) == 0o600


def test_a_new_file_gets_the_umask_default_without_a_probe_left_behind(tmp_path: Path) -> None:
    old = os.umask(0o027)
    try:
        objectives.write(tmp_path, _new_objective(7))
    finally:
        os.umask(old)
    d = objectives.objectives_dir(tmp_path)
    p = d / "conc-0007.yaml"
    assert (p.stat().st_mode & 0o777) == 0o640  # 0666 & ~027
    assert sorted(x.name for x in d.iterdir()) == [p.name]
