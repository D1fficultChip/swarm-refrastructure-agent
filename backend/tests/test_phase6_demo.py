import json
from pathlib import Path
from time import sleep,monotonic

import pytest
from fastapi.testclient import TestClient
from backend.app.main import create_app
from backend.app.demo.service import DemoApplicationService,DemoError
from backend.app.reconstruction.engine import ReconstructionError
from backend.app.agent.providers.mock import MockModelProvider
from backend.app.agent.config import AgentConfig
from scripts.benchmark_phase51 import policy_action


def policy(context):
    """Explicit offline policy double; no production use and no fixed node IDs."""
    if context['context_kind']=='base' and context['proposal']:
        return policy_action('FINALIZE_PROPOSAL')
    options=[o for o in context['options'] if not o.get('dominated_by')]
    if options:
        option=min(options,key=lambda o:(len(o['remaining']),o['cost'],o['option_id']))
        expansions=[]
        if context['feedback']:
            expansions=[dict(entity='task:'+f['subject'],reason='Global regression',source_validation_id='latest')
                for f in context['feedback']['hard_failures'] if f['subject'].startswith('T') and f['subject'] not in context['scope']]
            expansions=list({e['entity']:e for e in expansions}.values())
        return policy_action('SELECT_OPTION',option_id=option['option_id'],scope_expansions=expansions,
            finalize=not option['remaining'] and not option['route_tasks'])
    if context['route_tasks']:
        return policy_action('CALL_TOOL',tool_name='replan_route',arguments={'task_id':context['route_tasks'][0]},
            finalize=len(context['route_tasks'])==1)
    if context['proposal']:return policy_action('FINALIZE_PROPOSAL')
    return policy_action('STOP',arguments={'reason':'NO_RECONSTRUCTION_REQUIRED'})


def factory(config):return MockModelProvider([policy]*16)


@pytest.fixture
def service(tmp_path):
    s=DemoApplicationService(output_dir=tmp_path,provider_factory=factory)
    yield s
    s.close()


def injected(service,ident):
    d=service.load(ident)
    return service.inject(d['session_id'],0)


@pytest.mark.parametrize('ident',['SC01','SC03','SC04','SC05','SC06','SC08'])
def test_real_scenario_chain_semantics(service,ident):
    d=injected(service,ident)
    result=service.run_sync(d['session_id'],d['state']['version'],'deterministic_realtime')
    assert result.status=='COMMITTED' and result.validation.status=='PASS_WITH_LIMITATIONS'
    assert result.policy_metrics['model_calls']==0 and result.policy_metrics['solver_calls']>0
    assert result.before_after_metrics['task_recovery']==1
    assert result.before_after_metrics['after']['violations']==[]
    assert {n.id for n in result.before_state.nodes if n.status=='FAILED'}=={n.id for n in result.after_state.nodes if n.status=='FAILED'}
    if ident in {'SC04','SC05'}:
        assert result.before_state.formations==result.after_state.formations
        assert result.before_state.nodes==result.after_state.nodes
        assert result.policy_metrics['solver_calls_by_kind']=={'formation':0,'task':0,'route':1}
    if ident in {'SC06','SC08'}:
        assert result.reconstruction_proposal.formation_changes and result.reconstruction_proposal.route_changes
    changed_tasks={c.task_id for c in result.reconstruction_proposal.task_changes}
    for before,after in zip(result.before_state.tasks,result.after_state.tasks):
        if before.id not in changed_tasks:assert before==after


def test_redundancy_honestly_partial_not_false_global_keep(service):
    d=injected(service,'SC02')
    assert d['scenario']['support']=='PARTIALLY_SUPPORTED' and not d['scenario']['default_visible']
    assert next(a for a in d['task_assessment'] if a['task_id']=='T03')['decision']=='KEEP'
    r=service.run_sync(d['session_id'],d['state']['version'],'deterministic_realtime')
    assert r.status=='FAILED' and r.commit is None and r.after_state==r.before_state
    assert 'FAILED_NODE_ASSIGNED' in [c.code for c in r.validation.hard_failures]


def test_feedback_replanning_uses_true_validator(service):
    d=injected(service,'SC07')
    r=service.run_sync(d['session_id'],d['state']['version'],'adaptive_agent')
    assert r.status=='COMMITTED',r.error
    assert r.agent_trace['steps'][0]['observation']['status']=='FAIL'
    assert 'UNRELATED_TASK_REGRESSION' in r.agent_trace['steps'][0]['observation']['reason_codes']
    assert r.policy_metrics['scope_expansions']>=1
    assert r.validation.status=='PASS_WITH_LIMITATIONS' and r.policy_metrics['pure_agent_success']


def test_route_only_agent_does_not_invoke_unrelated_solvers(service):
    d=injected(service,'SC04')
    r=service.run_sync(d['session_id'],d['state']['version'],'adaptive_agent')
    assert r.status=='COMMITTED'
    assert r.policy_metrics['solver_calls_by_kind']=={'formation':0,'task':0,'route':1}
    assert r.before_state.formations==r.after_state.formations
    assert r.before_state.nodes==r.after_state.nodes
    assert r.before_after_metrics['changed_entities']['routes']==['R05']


@pytest.mark.parametrize('mode',['deterministic_realtime','adaptive_agent','bounded_agent'])
def test_preview_does_not_publish_and_commit_is_idempotent(service,mode):
    d=injected(service,'SC08')
    r=service.run_sync(d['session_id'],d['state']['version'],mode,auto_commit=False)
    assert r.status=='READY' and r.commit is None
    assert r.after_state==r.before_state
    assert not r.policy_metrics['overall_success']
    assert service.state(d['session_id'])['state']==d['state']
    committed=service.commit(r.run_id)
    assert committed.status=='COMMITTED' and committed.after_state.version==r.before_state.version+1
    assert service.commit(r.run_id).commit==committed.commit
    stored=service.get_run(r.run_id)
    stored.status='TAMPERED'
    assert service.get_run(r.run_id).status=='COMMITTED'


def test_preview_stale_on_new_event(service):
    d=service.load('SC03');d=service.inject(d['session_id'],0,False)
    r=service.run_sync(d['session_id'],1,'deterministic_realtime',False)
    service.inject(d['session_id'],1,False)
    with pytest.raises(ReconstructionError,match='Version or digest'):service.commit(r.run_id)
    assert service.state(d['session_id'])['state']['version']==2


def test_replay_survives_restart_without_mutating_live_session(tmp_path):
    s=DemoApplicationService(output_dir=tmp_path);d=injected(s,'SC08')
    r=s.run_sync(d['session_id'],d['state']['version'],'deterministic_realtime');s.close()
    s=DemoApplicationService(output_dir=tmp_path)
    assert s.get_run(r.run_id)==r
    assert len(s.list_runs())==1
    with pytest.raises(DemoError):s.commit(r.run_id)
    with pytest.raises(DemoError):s.get_run('../../API')
    s.close()


def test_missing_model_key_does_not_break_baseline(tmp_path,monkeypatch):
    monkeypatch.delenv('MODEL_API_KEY',raising=False)
    s=DemoApplicationService(output_dir=tmp_path)
    d=injected(s,'SC01');r=s.run_sync(d['session_id'],1,'adaptive_agent')
    assert r.error['code']=='MODEL_NOT_CONFIGURED' and r.after_state==r.before_state
    r=s.run_sync(d['session_id'],1,'bounded_agent')
    assert r.status=='COMMITTED' and r.policy_metrics['fallback_used'] and not r.policy_metrics['pure_agent_success']
    s.close()


def test_http_sc08_end_to_end_preview_commit_and_replay(tmp_path):
    with TestClient(create_app(demo_output_dir=tmp_path,agent_provider_factory=factory)) as c:
        assert len(c.get('/api/v1/demo/scenarios').json())==8
        d=c.post('/api/v1/demo/session',json={'scenario_id':'SC08'}).json()
        d=c.post('/api/v1/demo/event',json={'session_id':d['session_id'],'expected_version':0}).json()
        assert len(d['impact_analysis'])==4 and d['metrics']['plan_status']=='INVALID'
        job=c.post('/api/v1/demo/reconstruct',json={'session_id':d['session_id'],'expected_version':4,'mode':'adaptive_agent','auto_commit':False})
        assert job.status_code==202
        rid=job.json()['run_id'];deadline=monotonic()+15
        while monotonic()<deadline:
            result=c.get('/api/v1/demo/run/'+rid).json()
            if result['status']!='RUNNING':break
            sleep(.01)
        assert result['status']=='READY',result
        result=c.post('/api/v1/demo/commit',json={'run_id':rid}).json()
        assert result['status']=='COMMITTED' and result['after_state']['version']==5
        assert result['before_after_metrics']['after']['feasible_tasks']==8
        assert c.get('/api/v1/demo/run/'+rid).json()==result
        assert c.get('/api/v1/demo/state/unknown').status_code==404
        assert c.post('/api/v1/demo/event',json={'session_id':d['session_id'],'expected_version':0}).status_code==409
