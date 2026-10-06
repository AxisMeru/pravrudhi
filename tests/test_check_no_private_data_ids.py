"""Identifier patterns in check_no_private_data: recorded-looking ids, emails, home paths and account fields in
fixture/research files fail; the constructed forms pass. All values below are constructed for these tests."""

import importlib.util
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("cnpd", ROOT / "scripts/check_no_private_data.py")
G = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(G)

REAL_SHAPED_UUID = "3f2b8c1e-5d4a-4b7e-9c10-8a6f2d9e7b34"
UUID7 = "01a0fcb0-974b-7080-a4fe-f8bf6f3a4e23"
CONSTRUCTED = "00000000-0000-4000-8000-000000000001"


def repo(tmp_path, files):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return G.check(tmp_path)


def test_known_positive_real_shaped_session_id_in_fixture_fails(tmp_path):
    v = repo(tmp_path, {"tests/fixtures/x/claude_0.json": json.dumps({"session_id": REAL_SHAPED_UUID})})
    assert len(v) == 1 and "claude_0.json:1" in v[0] and REAL_SHAPED_UUID not in v[0]


def test_uuid7_thread_id_fails(tmp_path):
    assert repo(tmp_path, {"tests/fixtures/x/c.json": f'{{"thread_id":"{UUID7}"}}'})


def test_uuid_in_a_filename_is_not_scanned_but_its_content_is(tmp_path):
    assert repo(tmp_path, {f"tests/fixtures/rollout-{UUID7}.jsonl": '{"a":1}'}) == []


def test_constructed_uuid_passes(tmp_path):
    assert repo(tmp_path, {"tests/fixtures/x/c.json": f'{{"thread_id":"{CONSTRUCTED}"}}'}) == []


def test_real_shaped_response_id_fails_constructed_passes(tmp_path):
    assert repo(tmp_path, {"tests/fixtures/a.json": '{"response_id":"resp_03d7d84f7dc71633016abfaa55a5d887d2b529e520b14679ea"}'})


def test_constructed_response_id_passes(tmp_path):
    assert repo(tmp_path, {"tests/fixtures/a.json": '{"response_id":"resp_constructed_01"}'}) == []


def test_email_fails_but_example_domains_pass(tmp_path):
    assert repo(tmp_path, {"tests/fixtures/a.txt": "someone@gmail.com"})


def test_example_email_passes(tmp_path):
    assert repo(tmp_path, {"tests/fixtures/a.txt": "someone@example.com and x@example.org"}) == []


def test_home_path_fails_constructed_home_passes(tmp_path):
    assert repo(tmp_path, {"research/a.md": "see /home/ss/projects/x"})


def test_constructed_home_path_passes(tmp_path):
    assert repo(tmp_path, {"research/a.md": "see /home/user/projects/x"}) == []


def test_account_fields_fail_unless_constructed(tmp_path):
    assert repo(tmp_path, {"tests/fixtures/a.json": '{"plan_type": "plus"}'})


def test_constructed_account_field_passes(tmp_path):
    assert repo(tmp_path, {"tests/fixtures/a.json": '{"plan_type": "example-plan", "account_id": null, "email": ""}'}) == []


def test_token_shapes_fail_in_fixtures(tmp_path):
    assert repo(tmp_path, {"tests/fixtures/a.txt": "hf_" + "a" * 34})
    assert G.check(tmp_path) == G.check(tmp_path)  # idempotent


def test_same_values_outside_fixture_and_research_paths_are_not_flagged(tmp_path):
    assert repo(tmp_path, {"docs/a.md": f"{REAL_SHAPED_UUID} someone@gmail.com /home/ss/x"}) == []


def test_binary_and_non_text_suffix_skipped(tmp_path):
    assert repo(tmp_path, {"tests/fixtures/a.bin": REAL_SHAPED_UUID}) == []


def test_marker_check_still_works(tmp_path):
    assert repo(tmp_path, {"docs/a.md": G.MARKER})


def test_secret_scan_fixtures_dir_is_exempt(tmp_path):
    assert repo(tmp_path, {"tests/secret_scan_fixtures/a.py": "T = 'hf_" + "a" * 34 + "'"}) == []
