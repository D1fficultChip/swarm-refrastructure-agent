import random
from time import sleep

from fastapi.testclient import TestClient

from backend.app.demo.service import DemoApplicationService
from backend.app.dynamic.event_sampler import DynamicEventSampler
from backend.app.dynamic.models import CreateDynamicSessionRequest
from backend.app.dynamic.scenario_generator import DynamicScenarioGenerator
from backend.app.dynamic.service import DynamicEnvironmentService
from backend.app.main import create_app
from backend.app.reconstruction.primitives import ReconstructionPrimitives
from backend.app.assessment.service import process_event


def request(**updates):
    values=dict(scenario_seed=58372,event_seed=91420,event_mode='manual',difficulty='L3',
                execution_mode='deterministic_realtime',reconstruction_trigger='manual')
    values.update(updates)
    return CreateDynamicSessionRequest(**values)


def wait_for_reconstruction(service,session_id):
    for _ in range(300):
        session=service.sessions[session_id]
        service._reconcile(session)
        if not session.current_reconstruction_run_id:return service.state(session_id,refresh=False)
        sleep(.01)
    raise AssertionError('reconstruction did not finish')


def test_seed_reproducibility_and_fifty_generated_scenarios():
    generator=DynamicScenarioGenerator()
    first=generator.generate(58372).initial_state
    assert first==generator.generate(58372).initial_state
    other=generator.generate(58373).initial_state
    assert [n.position for n in first.nodes]!=[n.position for n in other.nodes]
    assert [t.target for t in first.tasks]!=[t.target for t in other.tasks]
    for seed in range(50):
        state=generator.generate(seed).initial_state
        assert len(state.nodes)==60 and len(state.tasks)==8 and len(state.formations)==6
        assert not ReconstructionPrimitives().get_outstanding_violations(state)
        assert len({(round(n.position.x,3),round(n.position.y,3)) for n in state.nodes})>40
    for node_count,task_count in [(30,4),(30,8),(60,4)]:
        state=generator.generate(91,node_count,task_count).initial_state
        assert len(state.nodes)==node_count and len(state.tasks)==task_count
        assert not ReconstructionPrimitives().get_outstanding_violations(state)
    compact=generator.generate(92,30,8).initial_state
    assert [(t.window.start,t.window.end) for t in first.tasks[:6]]==[(0,600)]*6
    assert [(t.window.start,t.window.end) for t in first.tasks[6:]]==[(600,1200)]*2
    assert [(t.window.start,t.window.end) for t in compact.tasks]==(
        [(0,600)]*4+[(600,1200)]*2+[(1200,1800)]*2)
    for level in ['L1','L2','L3','L4']:
        assert DynamicEventSampler().sample(compact,level,random.Random(17),1,20)[0]


def test_difficulty_semantics_and_event_targets_are_not_fixed():
    generator=DynamicScenarioGenerator();sampler=DynamicEventSampler();targets=set();l4_success=0
    for seed in range(20):
        state=generator.generate(1000+seed).initial_state
        for level in ['L1','L2','L3','L4']:
            events,seed_task=sampler.sample(state,level,random.Random(7000+seed),1,20)
            changed=state
            for event in events:
                changed=process_event(changed,event).application.state
                if hasattr(event,'node_id'):targets.add(event.node_id)
            failures=ReconstructionPrimitives().get_outstanding_violations(changed)
            if level=='L1':assert not failures
            if level=='L2':assert failures and len({t.formation_id for t in changed.tasks if any(v.subject in {t.id,t.formation_id} for v in failures)})==1
            if level=='L3':assert len({v.category for v in failures})>=2
            if level=='L4':assert seed_task and failures;l4_success+=1
    assert l4_success==20 and len(targets)>=6
    assert targets!={'U17','U21'}


def test_runtime_ticks_do_not_change_planning_version_and_explicit_sync_does(tmp_path):
    demo=DemoApplicationService(output_dir=tmp_path/'demo')
    service=DynamicEnvironmentService(demo,tmp_path/'dynamic')
    created=service.create(request());sid=created['dynamic_session_id'];old_run_id=created['run_id']
    service.start(sid);stepped=service.step(sid,12)
    assert stepped['runtime_state']['simulation_time']>=12
    assert stepped['planning_state_version']==0
    assert stepped['runtime_state']['task_progress']['T01']>0
    synced=service.sync(sid)
    assert synced['planning_state_version']==1
    assert synced['runtime_state']['last_planning_sync_time']>=12
    reset=service.reset(sid)
    assert reset['planning_state_version']==0 and reset['runtime_state']['simulation_time']==0
    assert reset['run_id']!=old_run_id and service.get_run(old_run_id).run_id==old_run_id
    demo.close()


def test_task_execution_budget_leaves_reconstruction_slack(tmp_path):
    demo=DemoApplicationService(output_dir=tmp_path/'demo')
    service=DynamicEnvironmentService(demo,tmp_path/'dynamic')
    created=service.create(request());sid=created['dynamic_session_id']
    assert created['runtime_state']['task_execution_budget']['T01']==300
    assert created['planning_state']['tasks'][0]['window']=={'start':0.0,'end':600.0}
    service.start(sid)
    broken=service.inject(sid,event_type='NodeFailure',target_id='U01')
    progress_when_blocked=broken['runtime_state']['task_progress']['T01']
    assert broken['planning_metrics']['violations']
    still_blocked=service.step(sid,5)
    assert still_blocked['runtime_state']['task_progress']['T01']==progress_when_blocked
    service.reconstruct(sid,'deterministic_realtime')
    restored=wait_for_reconstruction(service,sid)
    assert not restored['planning_metrics']['violations']
    progressed=service.step(sid,300)
    assert progressed['runtime_state']['task_progress']['T01']==1
    assert progressed['runtime_state']['task_runtime_status']['T01']=='COMPLETED'
    assert progressed['runtime_state']['simulation_time']<600
    demo.close()


def test_continuous_mission_three_challenges_two_reconstructions(tmp_path):
    demo=DemoApplicationService(output_dir=tmp_path/'demo')
    service=DynamicEnvironmentService(demo,tmp_path/'dynamic')
    state=service.create(request(difficulty='L2'));sid=state['dynamic_session_id'];service.start(sid)
    service.step(sid,5)
    no_op=service.inject(sid,difficulty='L1')
    assert no_op['metrics']['noop_events']==1 and no_op['current_reconstruction_run_id'] is None
    assert 'DEGRADED' in no_op['runtime_state']['task_runtime_status'].values()
    version_after_motion=no_op['planning_state_version'];service.step(sid,5)
    assert service.state(sid,refresh=False)['planning_state_version']==version_after_motion
    for _ in range(2):
        broken=service.inject(sid,difficulty='L2')
        assert broken['planning_metrics']['feasible_tasks']<broken['planning_metrics']['task_count']
        service.reconstruct(sid,'deterministic_realtime');restored=wait_for_reconstruction(service,sid)
        assert restored['planning_metrics']['feasible_tasks']==restored['planning_metrics']['task_count']
        service.step(sid,5)
    final=service.state(sid,refresh=False)
    assert final['metrics']['runtime_events']==3
    assert final['metrics']['successful_reconstructions']==2
    assert len(final['reconstruction_history'])==2
    planning=demo.manager.get(final['planning_session_id']).state
    members=[n for f in planning.formations for n in f.node_ids]
    assert len(members)==len(set(members))
    assert final['runtime_state']['simulation_time']>=20
    run=service.get_run(final['run_id'])
    assert run.scenario_seed==58372 and len(run.reconstruction_runs)==2
    assert run.runtime_checkpoints[0].planning_state.version==0
    assert all(node.status.value=='NORMAL' for node in run.runtime_checkpoints[0].planning_state.nodes)
    assert run.runtime_checkpoints[-1].planning_metrics['feasible_tasks']==8
    demo.close()


def test_event_during_reconstruction_enters_pending_queue(tmp_path):
    demo=DemoApplicationService(output_dir=tmp_path/'demo')
    service=DynamicEnvironmentService(demo,tmp_path/'dynamic')
    state=service.create(request(difficulty='L2'));sid=state['dynamic_session_id']
    service.inject(sid,difficulty='L2');service.reconstruct(sid,'deterministic_realtime')
    queued=service.inject(sid,event_type='TaskPriorityChange')
    assert len(queued['runtime_state']['pending_dynamic_events'])==1
    final=wait_for_reconstruction(service,sid)
    assert not final['runtime_state']['pending_dynamic_events']
    assert final['metrics']['runtime_events']==2
    demo.close()


def test_automatic_event_interval_and_manual_target(tmp_path):
    demo=DemoApplicationService(output_dir=tmp_path/'demo')
    service=DynamicEnvironmentService(demo,tmp_path/'dynamic')
    state=service.create(request(event_mode='automatic',difficulty='L1',event_interval_min=1,event_interval_max=1))
    sid=state['dynamic_session_id'];service.start(sid)
    automatic=service.step(sid,1.1)
    assert automatic['metrics']['runtime_events']==1 and automatic['metrics']['noop_events']==1
    manual=service.inject(sid,event_type='NodeFailure',target_id='U01')
    assert manual['event_history'][-1]['node_id']=='U01'
    demo.close()


def test_dynamic_http_api_and_saved_scenario(tmp_path):
    with TestClient(create_app(demo_output_dir=tmp_path/'demo',dynamic_output_dir=tmp_path/'dynamic')) as client:
        created=client.post('/api/v1/dynamic/session',json=request().model_dump(mode='json')).json()
        sid=created['dynamic_session_id']
        assert created['scenario_seed']==58372 and created['planning_metrics']['feasible_tasks']==8
        assert client.post('/api/v1/dynamic/start',json={'session_id':sid}).status_code==200
        stepped=client.post('/api/v1/dynamic/step',json={'session_id':sid,'seconds':3}).json()
        assert stepped['planning_state_version']==0 and stepped['runtime_state']['simulation_time']>=3
        event=client.post('/api/v1/dynamic/event',json={'session_id':sid,'difficulty':'L1'}).json()
        assert event['metrics']['noop_events']==1
        saved=client.post('/api/v1/dynamic/save',json={'session_id':sid}).json()
        assert saved['scenario_seed']==58372
        assert client.get('/api/v1/dynamic/timeline/'+sid).status_code==200
        assert client.get('/api/v1/dynamic/run/'+created['run_id']).status_code==200
