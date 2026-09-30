"""Adapter-independent demo use cases, snapshot previews and immutable run replay.

Engines operate on an isolated branch. Only the original P4 transaction publishes
to the live demo session. Step mode can inspect a validated branch before publish.
"""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime,timezone
from pathlib import Path
from threading import RLock
from time import perf_counter
from types import SimpleNamespace
from uuid import uuid4,UUID

from ..agent.config import AgentConfig
from ..agent.optimized_orchestrator import OptimizedAgentOrchestrator
from ..agent.orchestrator import configured_provider
from ..agent.workspace import AgentWorkspace
from ..agent.security import redact
from ..reconstruction.common import apply_option,digest
from ..reconstruction.engine import DeterministicReconstructionEngine,ReconstructionError
from ..reconstruction.materializer import ProposalMaterializer
from ..reconstruction.primitives import ReconstructionPrimitives
from ..scenarios.manager import ScenarioManager
from ..models.events import ScenarioDefinition
from .models import DemoRunResult
from .projection import state_metrics,compare,agent_steps,deterministic_steps
from .instrumentation import SolverInvocationCounter

ROOT=Path(__file__).resolve().parents[3]


class DemoError(ValueError):
    def __init__(self,code,message,status=409):
        self.code,self.status=code,status
        super().__init__(message)


class DemoApplicationService:
    def __init__(self,directory=None,output_dir=None,config=None,provider_factory=None):
        directory=Path(directory or ROOT/'scenarios/demo')
        self.manager=ScenarioManager(directory)
        self.catalog={s['scenario_id']:s for s in json.loads((directory/'catalog/manifest.json').read_text())}
        self.output_dir=Path(output_dir or ROOT/'artifacts/phase6/demo/runs')
        self.config=config or AgentConfig.load()
        self.provider_factory=provider_factory or configured_provider
        self.sessions={};self.runs={};self.jobs={};self._lock=RLock()
        self.pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='demo')

    def close(self): self.pool.shutdown(wait=True)

    def scenarios(self):
        return [{**s.model_dump(mode='json'),**self.catalog[s.scenario_id]} for s in self.manager.list_scenarios()]

    def load(self,scenario_id):
        if scenario_id not in self.catalog: raise DemoError('SCENARIO_NOT_FOUND','场景不存在',404)
        snapshot=self.manager.load(scenario_id)
        with self._lock:
            self.sessions[snapshot.session_id]=dict(initial=snapshot.state,reports=[],busy=False)
        return self.state(snapshot.session_id)

    def attach_dynamic(self,definition:ScenarioDefinition,metadata):
        """Attach a constraint-validated generated scenario to the existing core."""
        snapshot=self.manager.create(definition)
        self.catalog[definition.initial_state.scenario_id]=dict(metadata)
        with self._lock:
            self.sessions[snapshot.session_id]=dict(initial=snapshot.state,reports=[],busy=False)
        return self.state(snapshot.session_id)

    def inject_dynamic(self,sid,event):
        """Apply a runtime-generated event through the original EventInjector/P2 path."""
        with self._lock:
            record=self._session(sid)
            if record['busy']:raise DemoError('SESSION_BUSY','重构运行中，动态事件应进入等待队列')
            report=self.manager.inject(sid,self.manager.get(sid).state.version,event)
            record['reports'].append(report)
        return self.state(sid),report

    def _session(self,sid):
        if sid not in self.sessions: raise DemoError('SESSION_NOT_FOUND','会话不存在，请重新加载场景',404)
        return self.sessions[sid]

    def state(self,sid):
        with self._lock:
            record=self._session(sid);snapshot=self.manager.get(sid)
            applied={r.trace.event.event_id for r in record['reports']}
            return dict(session_id=sid,scenario=self.catalog[snapshot.state.scenario_id],
                initial_state=record['initial'].model_dump(mode='json'),state=snapshot.state.model_dump(mode='json'),
                planned_events=[e.model_dump(mode='json') for e in snapshot.events],
                remaining_events=[e.event_id for e in snapshot.events if e.event_id not in applied],
                impact_analysis=[r.impact_analysis.model_dump(mode='json') for r in record['reports']],
                task_assessment=[a.model_dump(mode='json') for a in ReconstructionPrimitives().assess_current_task_state(snapshot.state)],
                metrics=state_metrics(snapshot.state),busy=record['busy'])

    def inject(self,sid,expected_version,all_remaining=True):
        with self._lock:
            record=self._session(sid)
            if record['busy']: raise DemoError('SESSION_BUSY','重构运行中，暂不可注入事件')
            snapshot=self.manager.get(sid)
            if snapshot.state.version!=expected_version:raise DemoError('VERSION_CONFLICT','请刷新当前会话版本')
            used={r.trace.event.event_id for r in record['reports']}
            pending=[e for e in snapshot.events if e.event_id not in used]
            for event in pending if all_remaining else pending[:1]:
                report=self.manager.inject(sid,self.manager.get(sid).state.version,event)
                record['reports'].append(report)
        return self.state(sid)

    def start(self,sid,expected_version,mode,auto_commit=True):
        with self._lock:
            record=self._session(sid)
            if record['busy']:raise DemoError('SESSION_BUSY','已有重构正在运行')
            current=self.manager.get(sid).state
            if current.version!=expected_version:raise DemoError('VERSION_CONFLICT','请刷新当前会话版本')
            if mode not in {'adaptive_agent','bounded_agent','deterministic_realtime'}:raise DemoError('UNKNOWN_MODE','未知执行模式',422)
            run_id=str(uuid4());record['busy']=True
            self.jobs[run_id]=dict(run_id=run_id,session_id=sid,status='RUNNING',execution_mode=mode)
        self.pool.submit(self._execute,run_id,sid,mode,auto_commit)
        return dict(self.jobs[run_id])

    def run_sync(self,sid,expected_version,mode,auto_commit=True):
        # Same use case for evaluation/CLI without an HTTP worker or polling.
        with self._lock:
            record=self._session(sid)
            if record['busy']:raise DemoError('SESSION_BUSY','已有重构正在运行')
            if self.manager.get(sid).state.version!=expected_version:raise DemoError('VERSION_CONFLICT','版本已变化')
            if mode not in {'adaptive_agent','bounded_agent','deterministic_realtime'}:raise DemoError('UNKNOWN_MODE','未知执行模式',422)
            record['busy']=True;run_id=str(uuid4())
        self._execute(run_id,sid,mode,auto_commit)
        return self.get_run(run_id)

    def _branch(self,sid):
        branch=ScenarioManager(ROOT/'scenarios/demo')
        with self.manager._lock:
            original=self.manager._sessions[sid]
            branch._sessions[sid]=replace(original,snapshot=original.snapshot.model_copy(deep=True),
                reports={k:v.model_copy(deep=True) for k,v in original.reports.items()},commits={},reconstructions={})
        return branch

    def _execute(self,run_id,sid,mode,auto_commit):
        begin=perf_counter();record=self.sessions[sid]
        before=self.manager.get(sid).state
        result=DemoRunResult(run_id=run_id,session_id=sid,scenario=self.catalog[before.scenario_id],execution_mode=mode,
            status='RUNNING',initial_state=record['initial'],before_state=before,after_state=before,
            event=[r.trace.event for r in record['reports']],impact_analysis=[r.impact_analysis for r in record['reports']],
            task_assessment=ReconstructionPrimitives().assess_current_task_state(before),created_at=datetime.now(timezone.utc).isoformat())
        result.timing={k:sum(getattr(r.metrics,k) for r in record['reports']) for k in
            ['event_application_ms','graph_build_ms','impact_analysis_ms','assessment_ms','total_phase2_ms']}
        result.timing.update(option_generation_ms=0,model_ms=0,tool_ms=0,validation_ms=0,commit_ms=0,fallback_ms=0)
        counter=SolverInvocationCounter();counter.start()
        try:
            branch=self._branch(sid)
            seed=None
            if result.scenario['diagnostic_seed'] and mode!='deterministic_realtime':
                w=AgentWorkspace(branch,sid)
                seed_task=result.scenario.get('diagnostic_seed_task_id','T03')
                local=w.tools.try_in_place_repair(w.base,seed_task)
                if not local.options:raise DemoError('DIAGNOSTIC_SEED_UNAVAILABLE','当前状态不能生成局部诊断方案')
                w.working=apply_option(w.base,local.options[0]);w.trace=local.trace;seed=w.build_proposal()
                result.seed_origin='P3 locally feasible diagnostic proposal; not authored by model'
            if mode=='deterministic_realtime':
                # No-op is decided from P4 mission facts, never P2 KEEP alone.
                if not ReconstructionPrimitives().get_outstanding_violations(before):
                    result.status='NO_RECONSTRUCTION_REQUIRED'
                    result.policy_metrics=dict(model_calls=0,tool_calls=0,solver_calls=0,pure_agent_success=False,
                        fallback_used=False,fallback_result=None,overall_success=True,policy_mode=mode)
                    result.trace_steps=[dict(step=1,kind='DETERMINISTIC_BASELINE',tool='Inspect',result='NO_RECONSTRUCTION_REQUIRED')]
                else:
                    outcome=DeterministicReconstructionEngine(branch).reconstruct(sid,before.version)
                    result.reconstruction_proposal=outcome.proposal;result.validation=outcome.validation
                    result.deterministic_trace=outcome.proposal.trace.model_dump(mode='json')
                    result.trace_steps=deterministic_steps(outcome.proposal,outcome.validation)
                    result.timing.update(option_generation_ms=outcome.metrics.proposal_ms,
                        validation_ms=outcome.metrics.validation_ms,commit_ms=outcome.metrics.commit_ms,
                        deterministic_core_ms=outcome.metrics.deterministic_total_ms)
                    result.policy_metrics=dict(model_calls=0,tool_calls=0,solver_calls=None,
                        solver_calls_note='See solver attempts and route results in deterministic trace; no fabricated invocation count',
                        validation_retries=0,scope_expansions=0,invalid_actions=0,dominated_action_rejections=0,
                        fallback_used=False,fallback_result=None,pure_agent_success=False,overall_success=False,policy_mode=mode)
                    result.status='READY' if outcome.committed else 'FAILED'
            else:
                config=self.config.model_copy(update={'policy_profile':'optimized','auto_commit_after_validation':True})
                fast=self.provider_factory(config.model_copy(update={'model_name':config.fast_model_name or config.model_name}))
                strong=self.provider_factory(config.model_copy(update={'model_name':config.strong_model_name or config.model_name}))
                objective='Restore all ACTIVE tasks with minimum lexicographic disruption. Use actual current options and route tools; finalize when all violations are resolved.'
                if seed:objective+=' Diagnostic: first validate/finalize the supplied proposal, then use true feedback to expand scope and replace the rejected plan.'
                outcome=OptimizedAgentOrchestrator(branch,fast,config,strong_provider=strong).run(sid,before.version,mode,objective,seed)
                result.reconstruction_proposal=outcome.proposal
                result.agent_trace=outcome.trace.model_dump(mode='json');result.trace_steps=agent_steps(outcome)
                extra_validation=perf_counter()
                if outcome.proposal:result.validation=branch.validate(sid,outcome.proposal) if not outcome.commit_receipt else branch.reconstruction(outcome.commit_receipt.reconstruction_id).validation
                extra_validation_ms=(perf_counter()-extra_validation)*1000
                timing=outcome.timing
                result.timing.update(option_generation_ms=timing.option_generation_ms,model_ms=timing.model_total_ms,
                    tool_ms=timing.tool_total_ms,validation_ms=timing.validation_total_ms+extra_validation_ms,commit_ms=timing.commit_ms,
                    fallback_ms=timing.fallback_ms,policy_total_ms=timing.pure_agent_ms)
                result.policy_metrics=dict(model_calls=outcome.model_calls,tool_calls=outcome.tool_calls,solver_calls=outcome.solver_calls,
                    validation_retries=max(0,outcome.validation_attempts-1),scope_expansions=len(outcome.trace.scope_expansions),
                    invalid_actions=sum(s.observation.status=='ACTION_REJECTED' for s in outcome.trace.steps),
                    dominated_action_rejections=sum('DOMINATED_OPTION' in s.observation.reason_codes for s in outcome.trace.steps),
                    fallback_used=outcome.fallback_used,fallback_result=outcome.termination_reason if outcome.fallback_used else None,
                    pure_agent_success=False,branch_policy_success=outcome.pure_agent_success,overall_success=False,
                    termination=outcome.termination_reason,policy_mode=mode,
                    input_tokens=sum(s.token_usage.get('prompt_tokens',0) for s in outcome.trace.steps),
                    output_tokens=sum(s.token_usage.get('completion_tokens',0) for s in outcome.trace.steps))
                result.status='READY' if outcome.commit_receipt else 'NO_RECONSTRUCTION_REQUIRED' if outcome.termination_reason=='NO_RECONSTRUCTION_REQUIRED' else 'FAILED'
                if result.status=='FAILED':
                    codes=[c for s in outcome.trace.steps for c in s.observation.reason_codes]
                    result.error=dict(code='MODEL_NOT_CONFIGURED' if 'MODEL_API_KEY_MISSING' in codes else outcome.termination_reason,
                        message='Agent Policy unavailable / 未形成合法可提交方案',reason_codes=codes)
            if result.reconstruction_proposal and result.status=='READY':
                result.candidate_state=ProposalMaterializer().materialize(before,result.reconstruction_proposal)
                if auto_commit:self._publish(result)
            if result.status=='NO_RECONSTRUCTION_REQUIRED':
                result.policy_metrics.update(overall_success=True,pure_agent_success=mode!='deterministic_realtime')
        except Exception as exc:
            result.status='FAILED';result.error=dict(code=getattr(exc,'code','DEMO_EXECUTION_ERROR'),message=type(exc).__name__)
        finally:
            counter.stop()
            result.policy_metrics['solver_calls']=sum(counter.counts.values())
            result.policy_metrics['solver_calls_by_kind']=counter.counts
            result.policy_metrics['solver_calls_note']='Thread-local actual invocations, including diagnostic preparation and fallback; instrumentation overhead included.'
            result.timing['reconstruction_total_ms']=(perf_counter()-begin)*1000
            result.timing['total_ms']=result.timing['reconstruction_total_ms']+result.timing['total_phase2_ms']
            result.before_after_metrics=compare(before,result.after_state,result.reconstruction_proposal,result.commit is not None)
            with self._lock:
                self.runs[run_id]=result;self.jobs.pop(run_id,None);record['busy']=False
                self._save(result)

    def _publish(self,result):
        start=perf_counter()
        receipt=self.manager.commit(result.session_id,result.reconstruction_proposal)
        result.commit=receipt;result.after_state=self.manager.get(result.session_id).state
        result.status='COMMITTED'
        elapsed=(perf_counter()-start)*1000
        result.timing['commit_ms']+=elapsed
        result.timing['live_publish_ms']=elapsed
        result.policy_metrics.update(overall_success=True,pure_agent_success=bool(result.policy_metrics.get('branch_policy_success')))
        result.trace_steps.append(dict(step=len(result.trace_steps)+1,kind='LIVE_TRANSACTION',tool='Validate / Commit',
            result=receipt.model_dump(mode='json')))

    def commit(self,run_id):
        with self._lock:
            result=self.runs.get(run_id)
            if result is None:raise DemoError('LIVE_RUN_NOT_FOUND','回放记录不能提交到新的会话',404)
            if result.commit:return result.model_copy(deep=True)
            if result.status!='READY':raise DemoError('RUN_NOT_READY','没有可提交的已验证方案')
            if self._session(result.session_id)['busy']:raise DemoError('SESSION_BUSY','会话正在运行')
            self._publish(result)
            result.timing['reconstruction_total_ms']+=result.timing['live_publish_ms']
            result.timing['total_ms']+=result.timing['live_publish_ms']
            result.before_after_metrics=compare(result.before_state,result.after_state,result.reconstruction_proposal,True)
            self._save(result)
            return result.model_copy(deep=True)

    def _save(self,result):
        self.output_dir.mkdir(parents=True,exist_ok=True)
        target=self.output_dir/(result.run_id+'.json');temp=target.with_suffix('.tmp')
        temp.write_text(json.dumps(redact(result.model_dump(mode='json')),ensure_ascii=False,separators=(',',':'))+'\n')
        temp.replace(target)

    def get_run(self,run_id):
        with self._lock:
            if run_id in self.jobs:return dict(self.jobs[run_id])
            if run_id in self.runs:return self.runs[run_id].model_copy(deep=True)
            try:UUID(run_id)
            except ValueError:raise DemoError('RUN_NOT_FOUND','执行记录不存在',404) from None
            path=self.output_dir/(run_id+'.json')
            if not path.is_file():raise DemoError('RUN_NOT_FOUND','执行记录不存在',404)
            return DemoRunResult.model_validate_json(path.read_text())

    def list_runs(self):
        with self._lock:
            result=[]
            for path in sorted(self.output_dir.glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:50]:
                r=DemoRunResult.model_validate_json(path.read_text())
                result.append(dict(run_id=r.run_id,scenario_id=r.scenario['scenario_id'],execution_mode=r.execution_mode,status=r.status,created_at=r.created_at))
            return result
