from pathlib import Path

import pytest
import yaml
from fastapi.routing import APIRoute
from pydantic import ValidationError
from typer.testing import CliRunner

from pravrudhi.application import parity


def seed(root: Path, *rows: dict[str, object]) -> None:
    path = root / parity.MATRIX
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({'rows': list(rows)}))


REPO_ROOT = Path(__file__).resolve().parents[1]
"""This test file lives in `tests/`, so the repository root is its parent — the root `parity.load` expects."""


def row(id: str = 'feature', ours: str = 'have', rival: str = 'unknown') -> dict[str, object]:
    return {
        'id': id, 'capability': id, 'why_it_matters': 'Operator can complete work.',
        'ours': ours, 'rivals': dict(orca=rival, claude_desktop='unknown', codex='unknown', openclaw='unknown'),
        'evidence': ['proof.txt'], 'notes': 'Test fixture',
    }


def test_failed_have_is_unverified(tmp_path: Path) -> None:
    seed(tmp_path, row())
    assert not parity.verify(tmp_path)[0].verified
    assert parity.coverage(tmp_path).numerator == 0
    assert parity.next_gap(tmp_path) is not None


def test_unknown_is_not_a_rival_advantage(tmp_path: Path) -> None:
    seed(tmp_path, row(ours='none'))
    assert parity.gaps(tmp_path) == []


def test_coverage_counts_verified_full_rows(tmp_path: Path) -> None:
    (tmp_path / 'proof.txt').write_text('proof')
    seed(tmp_path, row(), row('partial', 'partial'))
    coverage = parity.coverage(tmp_path)
    assert (coverage.numerator, coverage.denominator, coverage.fraction) == (1, 2, 0.5)


def test_next_prefers_rival_advantage(tmp_path: Path) -> None:
    seed(tmp_path, row('novel', 'none', 'none'), row('parity', 'none', 'have'))
    selected = parity.next_gap(tmp_path)
    assert selected is not None and selected.id == 'parity'
    assert [r.id for r in parity.gaps(tmp_path)] == ['parity']


def test_have_beats_partial_but_partial_does_not(tmp_path: Path) -> None:
    (tmp_path / 'proof.txt').write_text('proof')
    seed(tmp_path, row('equal', 'partial', 'partial'), row('behind', 'partial', 'have'))
    assert [r.id for r in parity.gaps(tmp_path)] == ['behind']


def test_evidence_commands_are_rerun(tmp_path: Path) -> None:
    entry = row()
    entry['evidence'] = ['command: test -f proof.txt']
    seed(tmp_path, entry)
    assert not parity.verify(tmp_path)[0].verified
    (tmp_path / 'proof.txt').write_text('proof')
    assert parity.verify(tmp_path)[0].verified
    (tmp_path / 'proof.txt').unlink()
    assert not parity.verify(tmp_path)[0].verified


@pytest.mark.parametrize('evidence', [[], ['../outside'], ['command:'], ['command: missing-executable-parity']])
def test_invalid_evidence(tmp_path: Path, evidence: list[str]) -> None:
    entry = row()
    entry['evidence'] = evidence
    seed(tmp_path, entry)
    assert not parity.verify(tmp_path)[0].verified


def test_empty_and_invalid_matrix(tmp_path: Path) -> None:
    assert parity.report(tmp_path).coverage.fraction is None
    assert parity.next_gap(tmp_path) is None
    seed(tmp_path, row(), row())
    with pytest.raises(ValidationError):
        parity.load(tmp_path)


def test_cli_and_typed_api(tmp_path: Path) -> None:
    from pravrudhi.api.server import create_app
    from pravrudhi.cli.app import app

    seed(tmp_path, row('gap', 'none', 'have'))
    result = CliRunner().invoke(app, ['parity', '--root', str(tmp_path), '--json'])
    assert result.exit_code == 0, result.output
    assert '"denominator": 1' in result.output
    result = CliRunner().invoke(app, ['parity', 'gaps', '--root', str(tmp_path)])
    assert result.exit_code == 0 and 'gap:' in result.output
    api = create_app(tmp_path)
    routes = list(api.routes)
    for included in api.routes:
        router = getattr(included, 'original_router', None)
        if router is not None:
            routes.extend(router.routes)
    route = next(route for route in routes if isinstance(route, APIRoute) and route.path == '/api/parity')
    response = route.endpoint()
    assert response.next_gap.id == 'gap'
    schema = api.openapi()['paths']['/api/parity']['get']['responses']['200']
    assert schema['content']['application/json']['schema']['$ref'].endswith('/ParityResponse')


class TestAnAbsentCapabilityNeedsNoEvidence:
    """The most useful row in the table is the one admitting a gap, and it must not be penalised for it.

    Every row used to require evidence, including a row whose whole claim was that the capability is missing.
    There is nothing to cite for something that does not exist, so the rule pushed toward either inventing a
    citation or dropping the row and letting the table quietly overstate the product. An absent capability is
    the backlog; it has to be expressible.
    """

    def test_a_row_claiming_nothing_verifies_without_evidence(self, tmp_path: Path) -> None:
        from pravrudhi.application.parity import Capability, Rivals, _verify

        row = Capability(
            id="terminal", capability="Embedded terminal", why_it_matters="Run a command in place.",
            rivals=Rivals(orca="have", claude_desktop="unknown", codex="unknown", openclaw="unknown"),
            ours="none", evidence=[], notes="Not built.",
        )
        assert _verify(tmp_path, [row])[0].verified

    def test_a_row_claiming_the_capability_still_must_evidence_it(self, tmp_path: Path) -> None:
        from pravrudhi.application.parity import Capability, Rivals, _verify

        row = Capability(
            id="terminal", capability="Embedded terminal", why_it_matters="Run a command in place.",
            rivals=Rivals(orca="have", claude_desktop="unknown", codex="unknown", openclaw="unknown"),
            ours="have", evidence=[], notes="",
        )
        result = _verify(tmp_path, [row])[0]
        assert not result.verified and "no evidence supplied" in result.failures


class TestTheBoardThisRepositoryShips:
    """Every test above builds its own rows in a temporary directory, so nothing checked the board that is
    actually published. That is the gap the board exists to close: `CommandPalette` shipped claiming Ctrl+K and
    was never mounted in the layout, and the claim stood until somebody re-ran its evidence by hand.

    These checks are structural and cost nothing, so they can sit in the ordinary suite. They do not run the
    evidence commands — `pravrudhi parity` does that, and some of them are the test suite itself.
    """

    @staticmethod
    def _rows() -> list[object]:
        from pravrudhi.application.parity import load

        rows = load(REPO_ROOT)
        assert rows, "the shipped parity board did not load"
        return rows  # type: ignore[return-value]

    def test_every_claim_carries_evidence(self) -> None:
        unevidenced = [r.id for r in self._rows() if r.ours != "none" and not r.evidence]  # type: ignore[attr-defined]
        assert not unevidenced, f"claimed without evidence: {unevidenced}"

    def test_a_capability_claimed_as_absent_carries_none(self) -> None:
        """Evidence under a `none` is left over from a claim that was withdrawn, and reads as if it supports it."""
        stale = [r.id for r in self._rows() if r.ours == "none" and r.evidence]  # type: ignore[attr-defined]
        assert not stale, f"withdrawn claims still carrying evidence: {stale}"

    def test_every_file_named_as_evidence_exists(self) -> None:
        """A path that no longer exists is the cheapest way for a claim to rot: the file was renamed, the row was
        not, and the evidence quietly stopped meaning anything."""
        missing = [
            (r.id, e) for r in self._rows() for e in r.evidence  # type: ignore[attr-defined]
            if not e.startswith("command:") and not (REPO_ROOT / e).exists()
        ]
        assert not missing, f"evidence naming files that are gone: {missing}"

    def test_ids_are_unique(self) -> None:
        ids = [r.id for r in self._rows()]  # type: ignore[attr-defined]
        assert len(ids) == len(set(ids))
