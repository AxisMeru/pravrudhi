"""The publish unit carries the private-name environment file line, and no list path or name is committed anywhere in the unit."""

from pathlib import Path

UNIT = Path(__file__).resolve().parent.parent / "deploy" / "systemd" / "pravrudhi-publish.service"


def test_the_unit_reads_the_private_name_settings_from_an_environment_file_outside_the_repository() -> None:
    text = UNIT.read_text()
    assert "EnvironmentFile=-%h/.config/pravrudhi/demo_export.env" in text
    assert "PRAVRUDHI_DEMO_PRIVATE_NAMES" in text and "PRAVRUDHI_DEMO_PRIVATE_NAMES_SHA256" in text


def test_the_unit_commits_no_list_path_and_no_sha256_value() -> None:
    import re

    text = UNIT.read_text()
    assert "demo_private_names" not in text
    assert not re.search(r"\b[0-9a-f]{64}\b", text)
    assert "=/home/" not in text
