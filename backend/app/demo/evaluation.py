"""Scenario × execution mode × repeat count. No single combined quality score."""
import json
from collections import Counter
from datetime import datetime,timezone
from hashlib import sha256
from math import ceil
from pathlib import Path
from shutil import copytree
from statistics import median

from .service import DemoApplicationService,ROOT
from ..reconstruction.common import digest


def distribution(values):
    values=sorted(values)
    if not values:return dict(n=0,p50=None,p95=None,p99=None,max=None)
    return dict(n=len(values),p50=median(values),p95=values[ceil(.95*len(values))-1],
                p99=values[ceil(.99*len(values))-1],max=values[-1])


def summarize(rows):
    groups={}
    for scenario,mode in sorted({(r['scenario_id'],r['mode']) for r in rows}):
        trials=[r for r in rows if r['scenario_id']==scenario and r['mode']==mode]
        required=[r for r in trials if r['requires_reconstruction']]
        task_total=sum(r['initial_violated_tasks'] for r in trials)
        n=len(trials)
        policy_keys=['model_calls','tool_calls','solver_calls','validation_retries','scope_expansions','invalid_actions','dominated_action_rejections']
        timing_keys=sorted({k for r in trials for k in r['timing']})
        groups[scenario+'/'+mode]=dict(n=n,requires_reconstruction_trials=len(required),
            reconstruction_success_rate=sum(r['committed'] and r['validation_status'] in {'PASS','PASS_WITH_LIMITATIONS'} for r in required)/len(required) if required else None,
            overall_success_rate=sum(r['overall_success'] for r in trials)/n,
            task_recovery_rate=sum(r['recovered_tasks'] for r in trials)/task_total if task_total else None,
            fallback_rate=sum(r['fallback_used'] for r in trials)/n,
            pure_agent_success_rate=sum(r['pure_agent_success'] for r in trials)/n,
            fallback_success_rate=sum(r['fallback_success'] for r in trials)/n,
            no_reconstruction_required=sum(r['status']=='NO_RECONSTRUCTION_REQUIRED' for r in trials),
            under_5s_rate=sum(r['overall_success'] and r['timing']['total_ms']<5000 for r in trials)/n,
            scope_ratio=distribution([r['scope_ratio'] for r in trials]),
            timing={k:distribution([r['timing'][k] for r in trials if k in r['timing']]) for k in timing_keys},
            policy={k:distribution([r['policy_metrics'].get(k,0) or 0 for r in trials]) for k in policy_keys},
            input_tokens=sum(r['policy_metrics'].get('input_tokens',0) or 0 for r in trials),
            output_tokens=sum(r['policy_metrics'].get('output_tokens',0) or 0 for r in trials),
            constraint_counts={k:distribution([r['constraints'].get(k,0) for r in trials]) for k in ['hard_constraint_count','failed_constraint_count','warning_count','not_evaluated_count']},
            status_counts=dict(Counter(r['status'] for r in trials)),
            disruption_comparison=dict(Counter(r['disruption_comparison'] for r in trials)),
            not_evaluated=sorted({v for r in trials for v in r['not_evaluated']}))
    return groups


class Phase6EvaluationHarness:
    def __init__(self,output_dir,config=None,provider_factory=None):
        self.output_dir=Path(output_dir)
        self.service=DemoApplicationService(output_dir=self.output_dir/'runs',config=config,provider_factory=provider_factory)

    def _run(self,scenario,mode):
        loaded=self.service.load(scenario)
        event_state=self.service.inject(loaded['session_id'],0)
        result=self.service.run_sync(loaded['session_id'],event_state['state']['version'],mode)
        # Raw run already persisted. Bound the benchmark's live-session memory.
        with self.service._lock,self.service.manager._lock:
            self.service.sessions.pop(loaded['session_id'])
            self.service.manager._sessions.pop(loaded['session_id'])
            self.service.runs.pop(result.run_id)
        return result

    def run(self,scenarios,modes,trials):
        self.output_dir.mkdir(parents=True,exist_ok=True)
        path=self.output_dir/'trials.jsonl'
        if path.exists():raise ValueError('Refusing to overwrite existing trials; use a fresh output directory')
        if not set(scenarios)<=self.service.catalog.keys():raise ValueError('Unknown scenario')
        if not set(modes)<={'adaptive_agent','bounded_agent','deterministic_realtime'} or trials<1:raise ValueError('Invalid evaluation configuration')
        copytree(ROOT/'scenarios/demo',self.output_dir/'scenario_definitions')
        sources=[*sorted((ROOT/'backend/app').rglob('*.py')),*sorted((ROOT/'scenarios/demo').rglob('*.json')),ROOT/'config/agent.json',ROOT/'config/planner.json']
        manifest=dict(phase=6,scenarios=scenarios,modes=modes,trials_per_cell=trials,
            started_at=datetime.now(timezone.utc).isoformat(),config=self.service.config.model_dump(mode='json'),
            source_hashes={str(p.relative_to(ROOT)):sha256(p.read_bytes()).hexdigest() for p in sources if p.exists()},
            timing_boundary='P2 event pipeline + demo reconstruction/branch/publish; excludes UI pauses, queue, network response serialization and persistence',
            counter='Per-thread profiling of actual solver calls; overhead included',selection='all consecutive trials, including failures')
        manifest_path=self.output_dir/'manifest.json'
        manifest_path.write_text(json.dumps(manifest,indent=2)+'\n')
        rows=[]
        try:
            for scenario in scenarios:
                for mode in modes:
                    for trial in range(1,trials+1):
                        result=self._run(scenario,mode)
                        baseline=self._run(scenario,'deterministic_realtime') if mode!='deterministic_realtime' else result
                        assert digest(result.before_state)==digest(baseline.before_state)
                        compared='not_comparable'
                        if result.commit and baseline.commit:
                            a=result.reconstruction_proposal.cost_breakdown.lexicographic_cost
                            b=baseline.reconstruction_proposal.cost_breakdown.lexicographic_cost
                            compared='equal' if a==b else 'agent_better' if a<b else 'agent_worse'
                        metrics=result.before_after_metrics;policy=result.policy_metrics
                        row=dict(scenario_id=scenario,mode=mode,trial=trial,run_id=result.run_id,baseline_run_id=baseline.run_id,
                            raw_file='runs/'+result.run_id+'.json',before_digest=digest(result.before_state),
                            status=result.status,committed=result.commit is not None,requires_reconstruction=bool(metrics['initial_violated_tasks']),
                            initial_violated_tasks=len(metrics['initial_violated_tasks']),recovered_tasks=len(metrics['recovered_tasks']),
                            overall_success=bool(policy.get('overall_success')),pure_agent_success=bool(policy.get('pure_agent_success')),
                            fallback_used=bool(policy.get('fallback_used')),fallback_success=bool(policy.get('fallback_used') and result.commit),
                            scope_ratio=metrics['scope_ratio'],timing=result.timing,policy_metrics=policy,
                            constraints=result.validation.metrics.model_dump(mode='json') if result.validation else {},
                            validation_status=result.validation.status.value if result.validation else None,
                            not_evaluated=result.validation.not_evaluated if result.validation else [],
                            disruption=result.reconstruction_proposal.cost_breakdown.lexicographic_cost if result.commit else None,
                            baseline_disruption=baseline.reconstruction_proposal.cost_breakdown.lexicographic_cost if baseline.commit else None,
                            disruption_comparison=compared,error=result.error)
                        with path.open('a') as f:f.write(json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n')
                        rows.append(row)
                        (self.output_dir/'summary.json').write_text(json.dumps(summarize(rows),ensure_ascii=False,indent=2)+'\n')
                        print(f'{scenario} {mode} {trial}/{trials}: {result.status}; calls={policy.get("model_calls")}; total={result.timing["total_ms"]:.1f} ms',flush=True)
            manifest['finished_at']=datetime.now(timezone.utc).isoformat()
            manifest_path.write_text(json.dumps(manifest,indent=2)+'\n')
        finally:self.service.close()
        return summarize(rows)
