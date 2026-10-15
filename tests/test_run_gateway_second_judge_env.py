"""#356: deploy/gateway/run_gateway.sh forwards the second judge's base URL, tau, timeout and refer delta
from gateway.env into the engine containers with the same optional `${VAR:+-e ...}` pattern as the existing lines.
The repo carries no real value.
"""

from __future__ import annotations

import re
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "deploy" / "gateway" / "run_gateway.sh"
FORWARDED = ("BASE_URL", "MODEL", "TAU", "TIMEOUT_S", "REFER_LOGIT_DELTA")


def test_each_second_judge_variable_is_forwarded_only_when_set() -> None:
    text = SCRIPT.read_text()
    for name in FORWARDED:
        var = f"NYAYA_SECOND_JUDGE_{name}"
        assert f'${{{var}:+-e "{var}=${var}"}}' in text, f"{var} is not forwarded with the optional -e pattern"


def test_the_script_holds_no_value_for_the_second_judge() -> None:
    """No URL, tau or timeout literal is assigned to a second-judge variable in the repo's script."""
    for line in SCRIPT.read_text().splitlines():
        if re.match(r"\s*(export\s+)?NYAYA_SECOND_JUDGE_\w+=", line):
            raise AssertionError(f"a second-judge value is hard-coded in run_gateway.sh: {line.strip()}")


def test_a_failed_engine_docker_run_fails_the_function() -> None:
    """#269: the docker run that starts an engine container ends with `|| return 1`, so a failed start is not treated as success
    (the health loop below it used to be the only thing that caught it)."""
    lines = SCRIPT.read_text().splitlines()
    runs = [i for i, ln in enumerate(lines) if ln.strip().startswith("docker run -d --name")]
    assert len(runs) == 1, runs
    end = next(i for i in range(runs[0], len(lines)) if "pravrudhi-engine:$PRAVRUDHI_VERSION" in lines[i])
    assert lines[end].rstrip().endswith(">/dev/null || return 1"), lines[end]
