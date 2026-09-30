import json

import pytest

from backend.app.demo.evaluation import Phase6EvaluationHarness, distribution, summarize
from backend.app.demo.service import DemoApplicationService
from scripts.probe_phase6_events import probe


def test_harness_retains_failures_and_reproducible_inputs(tmp_path):
    summary = Phase6EvaluationHarness(tmp_path).run(['SC01', 'SC02'], ['deterministic_realtime'], 1)
    assert summary['SC01/deterministic_realtime']['reconstruction_success_rate'] == 1
    assert summary['SC02/deterministic_realtime']['reconstruction_success_rate'] == 0
    rows = [json.loads(line) for line in (tmp_path / 'trials.jsonl').read_text().splitlines()]
    assert all((tmp_path / row['raw_file']).is_file() for row in rows)
    assert rows[1]['initial_violated_tasks'] > 0 and rows[1]['recovered_tasks'] == 0
    assert rows[1]['disruption'] is None
    manifest = json.loads((tmp_path / 'manifest.json').read_text())
    assert manifest['finished_at'] and 'backend/app/demo/service.py' in manifest['source_hashes']
    assert (tmp_path / 'scenario_definitions/SC08.json').is_file()
    # A legitimate no-op contributes to overall success, but not reconstruction's denominator.
    noop = dict(rows[0], requires_reconstruction=False, committed=False, validation_status=None,
                status='NO_RECONSTRUCTION_REQUIRED', initial_violated_tasks=0, recovered_tasks=0)
    failed = dict(rows[1], scenario_id='SC01')
    aggregate = summarize([rows[0], failed, noop])['SC01/deterministic_realtime']
    assert aggregate['requires_reconstruction_trials'] == 2
    assert aggregate['reconstruction_success_rate'] == .5
    assert aggregate['overall_success_rate'] == pytest.approx(2 / 3)
    assert aggregate['task_recovery_rate'] == .5
    assert aggregate['no_reconstruction_required'] == 1


def test_quantiles_use_nearest_rank_without_inventing_tail_samples():
    result = distribution([5, 1, 2, 4, 3])
    assert result == dict(n=5, p50=3, p95=5, p99=5, max=5)
    assert distribution([])['p95'] is None


def test_valid_initial_state_is_a_true_noop(tmp_path):
    service = DemoApplicationService(output_dir=tmp_path)
    try:
        loaded = service.load('SC08')
        result = service.run_sync(loaded['session_id'], 0, 'deterministic_realtime')
        assert result.status == 'NO_RECONSTRUCTION_REQUIRED'
        assert result.before_state == result.after_state and result.commit is None
        assert result.policy_metrics['model_calls'] == 0
        assert result.before_after_metrics['scope_numerator'] == 0
    finally:
        service.close()


def test_task_event_support_through_real_assessment_validation_and_commit():
    results = probe()
    for item in results.values():
        outcome = item['result']
        assert outcome['committed'] and outcome['validation']['status'] == 'PASS_WITH_LIMITATIONS'
        assert outcome['state']['version'] == 2
        assert 'task_assessment' in outcome['event_traces'][0]
        assert 'dynamic_scheduling' in outcome['validation']['not_evaluated']
    cancelled = next(t for t in results['TaskCancel']['result']['state']['tasks'] if t['id'] == 'T01')
    assert cancelled['status'] == 'CANCELLED'
    assert results['TargetMove']['result']['proposal']['route_changes']
