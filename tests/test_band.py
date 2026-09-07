from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import combinations

import pytest
import yaml

from pravrudhi.application.band import (
    PACKAGED_CONFIG,
    Artifact,
    Evidence,
    decide,
    load_config,
)
from pravrudhi.application.kshudha import Appetite
from pravrudhi.application.svasthya import Health, HealthState

NOW = datetime(2026, 9, 7, tzinfo=UTC)
APPETITE = Appetite('', '1', (), 'seva', 'seva', {'description': 'external work'}, '', None)
HEALTH = Health(HealthState.READY, '', (), 'ready')


def decision(level='continuous', **kwargs):
    artifact = kwargs.pop('artifact', Artifact('external-app', level))
    args = dict(action='build', cost_cents=1, appetite=APPETITE, health=HEALTH, now=NOW)
    args.update(kwargs)
    return decide(artifact, **args)


def test_every_pair_is_distinguishable_by_permission_fields():
    levels = load_config().levels
    assert len(levels) >= 4
    for left, right in combinations(levels, 2):
        assert any(getattr(left, field) != getattr(right, field) for field in (
            'automatic', 'ask_first', 'spend_ceiling_cents', 'min_interval_seconds', 'max_runs',
        ))


@pytest.mark.parametrize('level', load_config().levels)
def test_ceiling_cannot_be_silently_increased_even_with_approval(level):
    artifact = Artifact('app', level.id, spend_limit_cents=level.spend_ceiling_cents + 1)
    with pytest.raises(ValueError, match='exceeds level ceiling'):
        decision(artifact=artifact, approved=True)
    assert decision(level.id, cost_cents=level.spend_ceiling_cents + 1,
                    approved=True).status == 'denied'
    artifact = Artifact('app', level.id, spent_cents=level.spend_ceiling_cents - 2,
                        reserved_cents=1)
    assert decision(artifact=artifact, cost_cents=1).status == 'allowed'
    assert decision(artifact=artifact, cost_cents=2, approved=True).status == 'denied'


def test_one_time_stops_even_for_free_approved_work():
    artifact = Artifact('app', 'one_time', runs_started=1,
                        last_started_at=NOW - timedelta(days=30))
    assert decision(artifact=artifact, cost_cents=0, approved=True).status == 'denied'
    assert decision('one_time', action='repair_breakage', approved=True).status == 'denied'


def test_critical_only_acts_on_breakage_or_unsafe_dependency():
    for action, evidence in [
        ('repair_breakage', Evidence(broken_report='incident:42')),
        ('patch_unsafe_dependency', Evidence(unsafe_dependency_report='scan:12')),
    ]:
        assert decision('critical', action=action).status == 'denied'
        assert decision('critical', action=action, evidence=evidence).status == 'allowed'
    assert decision('critical', action='propose_improvement').status == 'denied'
    assert decision('critical', action='repair_regression', approved=True).status == 'denied'


def test_self_healing_needs_its_recorded_baseline_and_comparison():
    assert decision('self_healing', action='check_regression').status == 'denied'
    baseline = Evidence(baseline_record='app:baseline:1')
    assert decision('self_healing', action='check_regression', evidence=baseline).status == 'allowed'
    assert decision('self_healing', action='repair_regression', evidence=baseline).status == 'denied'
    assert decision('self_healing', action='repair_regression', evidence=replace(
        baseline, regression_report='app:comparison:2')).status == 'allowed'


def test_continuous_can_propose_and_test_but_deployment_asks():
    for action in ('propose_improvement', 'test_improvement'):
        assert decision(action=action).status == 'allowed'
        assert decision('self_healing', action=action).status == 'denied'
    assert decision(action='deploy_improvement').status == 'ask_first'
    assert decision(action='deploy_improvement', approved=True).status == 'allowed'
    assert decision(action='unknown', approved=True).status == 'denied'


@pytest.mark.parametrize('level', load_config().levels[1:])
def test_run_interval_boundary(level):
    artifact = Artifact('app', level.id, runs_started=1, last_started_at=NOW)
    assert decision(artifact=artifact, now=NOW + timedelta(
        seconds=level.min_interval_seconds - 1), approved=True).status == 'denied'
    assert decision(artifact=artifact, now=NOW + timedelta(
        seconds=level.min_interval_seconds)).status == 'allowed'


@pytest.mark.parametrize('state', list(HealthState))
def test_health_is_delegated_to_existing_guard(state):
    from pravrudhi.application.svasthya import can_dispatch_new_work
    health = replace(HEALTH, state=state)
    assert (decision(health=health, approved=True).status == 'allowed') == can_dispatch_new_work(health)


def test_resting_appetite_and_artifact_isolation():
    assert decision(appetite=replace(APPETITE, selected=None, action=None)).status == 'denied'
    a = Artifact('a', 'continuous', spend_limit_cents=0)
    b = Artifact('b', 'continuous')
    assert decision(artifact=a).status == 'denied'
    assert decision(artifact=b).to_dict()['artifact_id'] == 'b'
    assert decision(artifact=b).status == 'allowed'


@pytest.mark.parametrize('cost', [-1, 1.5, True, float('nan'), float('inf')])
def test_invalid_cost_fails_closed(cost):
    with pytest.raises(ValueError):
        decision(cost_cents=cost)


def test_config_is_authoritative_and_rejects_duplicate_levels(tmp_path):
    raw = yaml.safe_load(PACKAGED_CONFIG.read_text())
    raw['levels'][0]['spend_ceiling_cents'] = 3
    path = tmp_path / 'band.yaml'
    path.write_text(yaml.safe_dump(raw))
    assert decision('one_time', policy=load_config(path), cost_cents=4).status == 'denied'
    raw['levels'].insert(1, dict(raw['levels'][0], id='duplicate', name='Another name'))
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match='indistinguishable'):
        load_config(path)
