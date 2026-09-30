import json
import random
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from time import monotonic
from uuid import UUID, uuid4

from ..demo.projection import state_metrics
from ..models.domain import NodeStatus, Position, ScenarioState, TaskStatus
from .clock import SimulationClock
from .event_sampler import ChallengeNotAvailable, DynamicEventSampler
from .models import (CreateDynamicSessionRequest, DynamicMissionRun, DynamicMissionSession,
                     DynamicRuntimeState, MissionStatus, RuntimeCheckpoint,
                     TaskRuntimeStatus, TimelineEntry)
from .motion import interpolate_route
from .scenario_generator import DynamicScenarioGenerator
from .timing import execution_budget


class DynamicError(ValueError):
    def __init__(self,code,message,status=409):
        self.code,self.status=code,status
        super().__init__(message)


class DynamicEnvironmentService:
    """Authoritative simulation clock and adapter into the existing demo core."""

    def __init__(self,demo_service,output_dir=None):
        self.demo=demo_service
        root=Path(__file__).resolve().parents[3]
        self.output_dir=Path(output_dir or root/'artifacts/phase62/dynamic')
        self.generator=DynamicScenarioGenerator();self.sampler=DynamicEventSampler()
        self.sessions={};self._clocks={};self._rng={};self._config={};self._last_wall={}
        self._recovery_wall={};self._last_event_sim={};self._recovered_until={};self._pending_seed_tasks={}
        self._lock=RLock()

    def _session(self,sid):
        if sid not in self.sessions:raise DynamicError('DYNAMIC_SESSION_NOT_FOUND','动态任务会话不存在',404)
        return self.sessions[sid]

    @staticmethod
    def _metadata(definition,difficulty):
        state=definition.initial_state
        return dict(scenario_id=state.scenario_id,name=definition.name,description=definition.description,
            expected_properties=['dynamic_runtime','multiple_events','planning_runtime_separation'],
            diagnostic_seed=False,support='SUPPORTED',default_visible=True,support_note='二维任务态势动态仿真；非飞行动力学仿真。',
            node_count=len(state.nodes),task_count=len(state.tasks),formation_count=len(state.formations),event_count=0,
            dynamic=True,difficulty=difficulty)

    def create(self,request:CreateDynamicSessionRequest):
        with self._lock:
            system=random.SystemRandom()
            scenario_seed=request.scenario_seed if request.scenario_seed is not None else system.randrange(10000,99999)
            event_seed=request.event_seed if request.event_seed is not None else system.randrange(10000,99999)
            definition=self.generator.generate(scenario_seed,request.node_count,request.task_count)
            planning=self.demo.attach_dynamic(definition,self._metadata(definition,request.difficulty))
            runtime=self._initial_runtime(definition.initial_state,request.simulation_speed)
            sid=str(uuid4());run_id=str(uuid4())
            session=DynamicMissionSession(dynamic_session_id=sid,run_id=run_id,scenario_seed=scenario_seed,
                event_seed=event_seed,planning_session_id=planning['session_id'],planning_state_version=planning['state']['version'],
                initial_definition=definition,runtime_state=runtime,execution_mode=request.execution_mode,
                event_mode=request.event_mode,difficulty=request.difficulty,
                reconstruction_trigger=request.reconstruction_trigger,status=MissionStatus.CREATED)
            clock=SimulationClock(speed=request.simulation_speed)
            rng=random.Random(event_seed);session.next_event_time=rng.uniform(request.event_interval_min,request.event_interval_max)
            self.sessions[sid]=session;self._clocks[sid]=clock;self._rng[sid]=rng
            self._config[sid]=dict(interval_min=request.event_interval_min,interval_max=request.event_interval_max)
            self._last_wall[sid]=monotonic();self._recovered_until[sid]={}
            self._pending_seed_tasks[sid]={}
            self._timeline(session,'MISSION_CREATED','动态任务场景已生成',
                           f'场景种子 {scenario_seed} · 事件种子 {event_seed}')
            self._checkpoint(session);self._save(session)
            return self.state(sid,refresh=False)

    def _initial_runtime(self,state:ScenarioState,speed:float):
        positions={n.id:n.position.model_copy(deep=True) for n in state.nodes}
        formations={};offsets={}
        for formation in state.formations:
            tasks=sorted([t for t in state.tasks if t.formation_id==formation.id],key=lambda t:(t.window.start,t.id))
            reference=tasks[0].start if tasks else Position(x=0,y=0)
            formations[formation.id]=reference.model_copy(deep=True)
            offsets[formation.id]={node_id:Position(x=positions[node_id].x-reference.x,
                y=positions[node_id].y-reference.y) for node_id in formation.node_ids}
        statuses={t.id:(TaskRuntimeStatus.EXECUTING if t.window.start<=state.clock<t.window.end else TaskRuntimeStatus.WAITING)
                  for t in state.tasks if t.status==TaskStatus.ACTIVE}
        return DynamicRuntimeState(simulation_time=state.clock,node_runtime_positions=positions,
            formation_runtime_positions=formations,formation_member_offsets=offsets,
            task_progress={t.id:0 for t in state.tasks if t.status==TaskStatus.ACTIVE},
            task_execution_budget={t.id:execution_budget(t.window.start,t.window.end)
                                   for t in state.tasks if t.status==TaskStatus.ACTIVE},
            task_runtime_status=statuses,current_route_progress={t.id:0 for t in state.tasks if t.status==TaskStatus.ACTIVE},
            simulation_speed=speed)

    def _timeline(self,session,kind,title,detail='',data=None):
        session.timeline.append(TimelineEntry(sequence=len(session.timeline)+1,
            simulation_time=session.runtime_state.simulation_time,kind=kind,title=title,detail=detail,
            planning_version=session.planning_state_version,data=data or {}))

    def _checkpoint(self,session):
        planning=self.demo.manager.get(session.planning_session_id).state
        session.runtime_checkpoints.append(RuntimeCheckpoint(simulation_time=session.runtime_state.simulation_time,
            planning_version=session.planning_state_version,runtime_state=session.runtime_state.model_copy(deep=True),
            planning_state=planning.model_copy(deep=True),planning_metrics=state_metrics(planning)))
        if len(session.runtime_checkpoints)>200:session.runtime_checkpoints=session.runtime_checkpoints[-200:]

    def start(self,sid):
        with self._lock:
            session=self._session(sid);self._reconcile(session)
            self._clocks[sid].start();session.runtime_state.simulation_running=True;session.status=MissionStatus.RUNNING
            self._last_wall[sid]=monotonic();self._timeline(session,'MISSION_STARTED','任务开始执行')
            self._save(session);return self.state(sid,refresh=False)

    def pause(self,sid):
        with self._lock:
            session=self._session(sid);self._refresh(session)
            self._clocks[sid].pause();session.runtime_state.simulation_running=False;session.status=MissionStatus.PAUSED
            self._timeline(session,'MISSION_PAUSED','任务执行已暂停');self._save(session)
            return self.state(sid,refresh=False)

    def resume(self,sid):
        with self._lock:
            session=self._session(sid);self._clocks[sid].resume();session.runtime_state.simulation_running=True
            session.status=MissionStatus.RECONSTRUCTING if session.current_reconstruction_run_id else MissionStatus.RUNNING
            self._last_wall[sid]=monotonic();self._timeline(session,'MISSION_RESUMED','任务继续执行');self._save(session)
            return self.state(sid,refresh=False)

    def set_speed(self,sid,speed):
        with self._lock:
            session=self._session(sid);self._refresh(session);self._clocks[sid].set_speed(speed)
            session.runtime_state.simulation_speed=speed;self._timeline(session,'SPEED_CHANGED','仿真倍率调整',f'{speed}×')
            self._save(session)
            return self.state(sid,refresh=False)

    def step(self,sid,seconds=1):
        with self._lock:
            session=self._session(sid);self._reconcile(session)
            old=self._clocks[sid].time;new=self._clocks[sid].step(seconds)
            self._advance_runtime(session,new-old);self._maybe_automatic_event(session)
            self._checkpoint(session);self._save(session);return self.state(sid,refresh=False)

    def _refresh(self,session):
        self._reconcile(session)
        now=monotonic();elapsed=min(2.0,max(0,now-self._last_wall[session.dynamic_session_id]));self._last_wall[session.dynamic_session_id]=now
        clock=self._clocks[session.dynamic_session_id];old=clock.time;new=clock.advance(elapsed)
        if new>old:
            self._advance_runtime(session,new-old);self._maybe_automatic_event(session)

    def _advance_runtime(self,session,delta):
        runtime=session.runtime_state;runtime.simulation_time=self._clocks[session.dynamic_session_id].time
        planning=self.demo.manager.get(session.planning_session_id).state
        metrics=state_metrics(planning);bad_subjects={v['subject'] for v in metrics['violations']}
        routes={r.id:r for r in planning.routes};nodes={n.id:n for n in planning.nodes}
        formations={f.id:f for f in planning.formations}
        for task in planning.tasks:
            if task.status!=TaskStatus.ACTIVE:continue
            invalid=bool({task.id,task.formation_id,task.route_id}&bad_subjects)
            formation=formations.get(task.formation_id)
            degraded=bool(formation and any(nodes[node_id].status==NodeStatus.DEGRADED for node_id in formation.node_ids))
            if session.current_reconstruction_run_id and invalid:status=TaskRuntimeStatus.RECONSTRUCTING
            elif runtime.simulation_time<task.window.start:status=TaskRuntimeStatus.WAITING
            elif invalid:status=TaskRuntimeStatus.BLOCKED
            elif runtime.task_progress.get(task.id,0)>=1:status=TaskRuntimeStatus.COMPLETED
            elif runtime.simulation_time>=task.window.end:status=TaskRuntimeStatus.BLOCKED
            elif runtime.simulation_time<self._recovered_until[session.dynamic_session_id].get(task.id,-1):status=TaskRuntimeStatus.RECOVERED
            elif degraded:status=TaskRuntimeStatus.DEGRADED
            else:status=TaskRuntimeStatus.EXECUTING
            runtime.task_runtime_status[task.id]=status
            if status in {TaskRuntimeStatus.EXECUTING,TaskRuntimeStatus.DEGRADED,TaskRuntimeStatus.RECOVERED}:
                duration=runtime.task_execution_budget.setdefault(
                    task.id,execution_budget(task.window.start,task.window.end))
                runtime.task_progress[task.id]=min(1,runtime.task_progress.get(task.id,0)+delta/duration)
                if runtime.task_progress[task.id]>=1:
                    status=TaskRuntimeStatus.COMPLETED
                    runtime.task_runtime_status[task.id]=status
            elif status in {TaskRuntimeStatus.BLOCKED,TaskRuntimeStatus.RECONSTRUCTING}:
                session.metrics.task_blocked_duration[task.id]=session.metrics.task_blocked_duration.get(task.id,0)+delta
            runtime.current_route_progress[task.id]=runtime.task_progress.get(task.id,0)
        for formation in planning.formations:
            tasks=sorted([t for t in planning.tasks if t.formation_id==formation.id and t.status==TaskStatus.ACTIVE],
                         key=lambda t:(t.window.start,t.id))
            if not tasks:continue
            task=next((t for t in tasks if runtime.task_runtime_status.get(t.id) in
                       {TaskRuntimeStatus.EXECUTING,TaskRuntimeStatus.DEGRADED,TaskRuntimeStatus.RECOVERED,TaskRuntimeStatus.BLOCKED,TaskRuntimeStatus.RECONSTRUCTING}),tasks[-1])
            route=routes.get(task.route_id);reference=interpolate_route(route,runtime.task_progress.get(task.id,0)) if route else task.start
            runtime.formation_runtime_positions[formation.id]=reference
            formation_offsets=runtime.formation_member_offsets.setdefault(formation.id,{})
            for member_index,node_id in enumerate(formation.node_ids):
                if node_id not in formation_offsets:
                    formation_offsets[node_id]=Position(x=(member_index%3-1)*10,y=(member_index//3-1)*10)
                if nodes[node_id].status==NodeStatus.FAILED:continue
                offset=formation_offsets[node_id]
                runtime.node_runtime_positions[node_id]=Position(
                    x=min(planning.bounds.width,max(0,reference.x+offset.x)),
                    y=min(planning.bounds.height,max(0,reference.y+offset.y)))
        session.metrics.total_mission_runtime=runtime.simulation_time

    def _sync_snapshot(self,session):
        snapshot=self.demo.manager.get(session.planning_session_id)
        updated=self.demo.manager.sync_positions(session.planning_session_id,snapshot.state.version,
            session.runtime_state.node_runtime_positions,session.runtime_state.simulation_time)
        session.planning_state_version=updated.state.version
        session.runtime_state.last_planning_sync_time=session.runtime_state.simulation_time
        self._timeline(session,'PLANNING_SYNC','运行位置同步到规划快照',
                       f'Planning State v{updated.state.version}')
        return updated.state

    def inject(self,sid,mode='semi_random',event_type=None,difficulty=None,target_id=None):
        with self._lock:
            session=self._session(sid);self._refresh(session)
            state=self.demo.manager.get(session.planning_session_id).state
            chosen=difficulty or session.difficulty
            event_index=len(session.event_history)+len(session.runtime_state.pending_dynamic_events)+1
            try:events,seed_task=self.sampler.sample(state,chosen,self._rng[sid],event_index,
                session.runtime_state.simulation_time,event_type,target_id)
            except ChallengeNotAvailable as exc:raise DynamicError('CHALLENGE_NOT_AVAILABLE',str(exc),422) from exc
            if session.current_reconstruction_run_id or self.demo._session(session.planning_session_id)['busy']:
                session.runtime_state.pending_dynamic_events.extend(events)
                if seed_task:self._pending_seed_tasks[sid][events[0].event_id]=seed_task
                self._timeline(session,'EVENT_QUEUED','动态事件进入等待队列','当前重构完成后应用',{'event_count':len(events)})
                self._save(session);return self.state(sid,refresh=False)
            self._apply_events(session,events,seed_task)
            return self.state(sid,refresh=False)

    def _apply_events(self,session,events,seed_task=None):
        self._sync_snapshot(session)
        if seed_task:
            catalog=self.demo.catalog[self.demo.manager.get(session.planning_session_id).state.scenario_id]
            catalog['diagnostic_seed']=True;catalog['diagnostic_seed_task_id']=seed_task
        for event in events:
            _,report=self.demo.inject_dynamic(session.planning_session_id,event)
            session.event_history.append(event);session.impact_results.append(report.model_dump(mode='json'))
            session.planning_state_version=report.application.state_version_after;session.metrics.runtime_events+=1
            self._timeline(session,'DYNAMIC_EVENT',self._event_title(event),'本次事件由动态环境生成',
                           {'event_id':event.event_id,'event_type':event.type})
            affected='、'.join(report.impact_analysis.affected_tasks) or '无任务'
            self._timeline(session,'IMPACT_ASSESSED','影响传播与状态评估完成',f'受影响任务：{affected}')
        planning=self.demo.manager.get(session.planning_session_id).state
        violations=state_metrics(planning)['violations'];self._advance_runtime(session,0)
        if not violations:
            session.metrics.noop_events+=1
            self._timeline(session,'NO_RECONSTRUCTION_REQUIRED','当前任务方案仍可继续','无需重构，动态任务继续执行')
            self._schedule_next(session)
        else:
            self._last_event_sim[session.dynamic_session_id]=session.runtime_state.simulation_time
            self._timeline(session,'RECONSTRUCTION_REQUIRED','检测到任务约束失效',
                           f'{len(violations)} 项未解决约束')
            if session.reconstruction_trigger=='automatic':self._start_reconstruction(session,None)
        self._checkpoint(session);self._save(session)

    @staticmethod
    def _event_title(event):
        if event.type=='NodeFailure':return f'节点 {event.node_id} 失效'
        if event.type=='NodeDegradation':return f'节点 {event.node_id} 性能下降'
        if event.type=='RestrictedAreaAdd':return f'新增限制区域 {event.region.id}'
        if event.type=='RestrictedAreaRemove':return f'移除限制区域 {event.region_id}'
        if event.type=='TargetMove':return f'任务 {event.task_id} 目标移动'
        if event.type=='TaskAdd':return f'新增任务 {event.task.id}'
        if event.type=='TaskCancel':return f'取消任务 {event.task_id}'
        return f'任务 {event.task_id} 优先级变化'

    def _schedule_next(self,session):
        config=self._config[session.dynamic_session_id]
        session.next_event_time=session.runtime_state.simulation_time+self._rng[session.dynamic_session_id].uniform(
            config['interval_min'],config['interval_max'])

    def _maybe_automatic_event(self,session):
        if session.event_mode!='automatic' or session.current_reconstruction_run_id:return
        if state_metrics(self.demo.manager.get(session.planning_session_id).state)['violations']:return
        if session.runtime_state.simulation_time+1e-9<session.next_event_time:return
        try:
            state=self.demo.manager.get(session.planning_session_id).state
            events,seed=self.sampler.sample(state,session.difficulty,self._rng[session.dynamic_session_id],
                len(session.event_history)+1,session.runtime_state.simulation_time)
            self._apply_events(session,events,seed)
        except ChallengeNotAvailable:
            self._timeline(session,'CHALLENGE_NOT_AVAILABLE','当前状态无法构造指定难度事件','可继续运行或重新生成场景')
            self._schedule_next(session)

    def reconstruct(self,sid,mode=None):
        with self._lock:
            session=self._session(sid);self._refresh(session)
            return self._start_reconstruction(session,mode)

    def _start_reconstruction(self,session,mode):
        if session.current_reconstruction_run_id:raise DynamicError('RECONSTRUCTION_RUNNING','重构正在执行')
        if not state_metrics(self.demo.manager.get(session.planning_session_id).state)['violations']:
            raise DynamicError('NO_RECONSTRUCTION_REQUIRED','当前没有需要重构的硬约束',422)
        self._sync_snapshot(session);selected=mode or session.execution_mode
        job=self.demo.start(session.planning_session_id,session.planning_state_version,selected,True)
        session.current_reconstruction_run_id=job['run_id'];session.status=MissionStatus.RECONSTRUCTING
        session.metrics.reconstruction_triggers+=1;self._recovery_wall[session.dynamic_session_id]=monotonic()
        self._advance_runtime(session,0)
        self._timeline(session,'RECONSTRUCTION_STARTED','在线任务重构启动',f'{selected} · 新动态事件暂缓')
        self._save(session);return self.state(session.dynamic_session_id,refresh=False)

    def _reconcile(self,session):
        run_id=session.current_reconstruction_run_id
        if not run_id:return
        result=self.demo.get_run(run_id)
        if isinstance(result,dict) and result.get('status')=='RUNNING':return
        session.current_reconstruction_run_id=None;session.reconstruction_history.append(run_id)
        if result.status=='COMMITTED':
            session.planning_state_version=result.after_state.version
            session.metrics.successful_reconstructions+=1
            session.metrics.model_calls+=int(result.policy_metrics.get('model_calls',0) or 0)
            session.metrics.plan_changes+=result.before_after_metrics.get('scope_numerator',0)
            recovered=result.before_after_metrics.get('recovered_tasks',[])
            until=session.runtime_state.simulation_time+2
            for task_id in recovered:self._recovered_until[session.dynamic_session_id][task_id]=until
            real_ms=(monotonic()-self._recovery_wall.pop(session.dynamic_session_id,monotonic()))*1000
            sim_duration=session.runtime_state.simulation_time-self._last_event_sim.get(session.dynamic_session_id,session.runtime_state.simulation_time)
            session.metrics.recovery_times.append(dict(run_id=run_id,real_recovery_latency_ms=real_ms,
                simulation_recovery_duration=sim_duration))
            self._sync_after_commit(session,result.before_state,result.after_state)
            self._timeline(session,'RECONSTRUCTION_COMMITTED','重构方案通过校核并提交',
                           f'Planning State v{result.after_state.version} · 任务继续执行',{'run_id':run_id})
            self._schedule_next(session)
        else:
            self._timeline(session,'RECONSTRUCTION_FAILED','本次重构未提交',result.error.get('code','') if result.error else '')
        session.status=MissionStatus.RUNNING if self._clocks[session.dynamic_session_id].running else MissionStatus.PAUSED
        self._advance_runtime(session,0);self._checkpoint(session)
        queued=list(session.runtime_state.pending_dynamic_events);session.runtime_state.pending_dynamic_events=[]
        if queued:
            seeds=self._pending_seed_tasks[session.dynamic_session_id]
            queued_seed=next((seeds.pop(event.event_id) for event in queued if event.event_id in seeds),None)
            self._apply_events(session,queued,queued_seed)
        self._save(session)

    def _sync_after_commit(self,session,before,after):
        runtime=session.runtime_state;old_members={n:f.id for f in before.formations for n in f.node_ids}
        for formation in after.formations:
            reference=runtime.formation_runtime_positions.get(formation.id)
            if reference is None:
                task=next((t for t in after.tasks if t.formation_id==formation.id),None);reference=task.start if task else Position(x=0,y=0)
            offsets=runtime.formation_member_offsets.setdefault(formation.id,{})
            for index,node_id in enumerate(formation.node_ids):
                if old_members.get(node_id)!=formation.id:
                    offsets[node_id]=Position(x=(index%3-1)*10,y=(index//3-1)*10)
                    runtime.node_runtime_positions[node_id]=Position(x=reference.x+offsets[node_id].x,y=reference.y+offsets[node_id].y)
                    self._timeline(session,'MEMBERSHIP_SYNC','规划成员已调整',
                                   f'{node_id} 加入 {formation.id}；未模拟真实转场过程')

    def sync(self,sid):
        with self._lock:
            session=self._session(sid);self._refresh(session);self._sync_snapshot(session)
            self._checkpoint(session);self._save(session);return self.state(sid,refresh=False)

    def reset(self,sid):
        with self._lock:
            old=self._session(sid);self._reconcile(old)
            if old.current_reconstruction_run_id:raise DynamicError('RECONSTRUCTION_RUNNING','重构正在执行')
            definition=old.initial_definition.model_copy(deep=True)
            planning=self.demo.attach_dynamic(definition,self._metadata(definition,old.difficulty))
            old.run_id=str(uuid4());old.planning_session_id=planning['session_id'];old.planning_state_version=0
            old.runtime_state=self._initial_runtime(definition.initial_state,old.runtime_state.simulation_speed)
            old.event_history=[];old.impact_results=[];old.reconstruction_history=[];old.runtime_checkpoints=[]
            old.timeline=[];old.metrics=type(old.metrics)();old.status=MissionStatus.CREATED;old.current_reconstruction_run_id=None
            self._clocks[sid]=SimulationClock(speed=old.runtime_state.simulation_speed);self._recovered_until[sid]={}
            self._pending_seed_tasks[sid]={}
            self._schedule_next(old);self._timeline(old,'MISSION_RESET','动态任务已重置');self._checkpoint(old);self._save(old)
            return self.state(sid,refresh=False)

    def state(self,sid,refresh=True):
        with self._lock:
            session=self._session(sid)
            if refresh:self._refresh(session)
            planning=self.demo.state(session.planning_session_id)
            return dict(dynamic_session_id=sid,run_id=session.run_id,scenario_seed=session.scenario_seed,
                event_seed=session.event_seed,status=session.status.value,execution_mode=session.execution_mode,
                event_mode=session.event_mode,difficulty=session.difficulty,reconstruction_trigger=session.reconstruction_trigger,
                planning_session_id=session.planning_session_id,planning_state_version=planning['state']['version'],
                planning_state=planning['state'],planning_metrics=planning['metrics'],runtime_state=session.runtime_state.model_dump(mode='json'),
                impact_analysis=planning['impact_analysis'],task_assessment=planning['task_assessment'],
                event_history=[event.model_dump(mode='json') for event in session.event_history],
                timeline=[e.model_dump(mode='json') for e in session.timeline],metrics=session.metrics.model_dump(mode='json'),
                reconstruction_history=list(session.reconstruction_history),current_reconstruction_run_id=session.current_reconstruction_run_id,
                next_event_time=session.next_event_time)

    def timeline(self,sid):
        with self._lock:
            session=self._session(sid);self._refresh(session)
            return [entry.model_dump(mode='json') for entry in session.timeline]

    def save_scenario(self,sid):
        with self._lock:
            session=self._session(sid);directory=self.output_dir/'scenarios';directory.mkdir(parents=True,exist_ok=True)
            path=directory/f'{session.initial_definition.initial_state.scenario_id}-{session.scenario_seed}.json'
            path.write_text(session.initial_definition.model_dump_json(indent=2)+'\n',encoding='utf-8')
            return dict(path=str(path),scenario_seed=session.scenario_seed,event_seed=session.event_seed,
                        scenario=session.initial_definition.model_dump(mode='json'))

    def _save(self,session):
        self.output_dir.mkdir(parents=True,exist_ok=True)
        planning=self.demo.manager.get(session.planning_session_id).state
        run=DynamicMissionRun(run_id=session.run_id,dynamic_session_id=session.dynamic_session_id,
            scenario_seed=session.scenario_seed,event_seed=session.event_seed,
            initial_state=session.initial_definition.initial_state,timeline=session.timeline,
            runtime_checkpoints=session.runtime_checkpoints,events=session.event_history,
            impact_results=session.impact_results,reconstruction_runs=session.reconstruction_history,
            final_state=planning,metrics=session.metrics)
        target=self.output_dir/(session.run_id+'.json');temp=target.with_suffix('.tmp')
        temp.write_text(run.model_dump_json()+'\n',encoding='utf-8');temp.replace(target)

    def get_run(self,run_id):
        try:UUID(run_id)
        except ValueError:raise DynamicError('DYNAMIC_RUN_NOT_FOUND','动态回放不存在',404) from None
        path=self.output_dir/(run_id+'.json')
        if not path.is_file():raise DynamicError('DYNAMIC_RUN_NOT_FOUND','动态回放不存在',404)
        return DynamicMissionRun.model_validate_json(path.read_text())

    def list_runs(self):
        if not self.output_dir.is_dir():return []
        rows=[]
        for path in sorted(self.output_dir.glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:30]:
            run=DynamicMissionRun.model_validate_json(path.read_text())
            rows.append(dict(run_id=run.run_id,scenario_seed=run.scenario_seed,event_seed=run.event_seed,
                events=len(run.events),reconstructions=len(run.reconstruction_runs),simulation_time=run.metrics.total_mission_runtime))
        return rows
