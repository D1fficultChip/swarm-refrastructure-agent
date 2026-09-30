"""Fixed inputs and semantic expectations, never precomputed reconstruction answers."""
import json
from copy import deepcopy
from pathlib import Path

from backend.app.models.events import ScenarioDefinition
from scripts.phase5_scenarios import validator_feedback

ROOT = Path(__file__).resolve().parents[1]


def definitions():
    base = json.loads((ROOT / 'scenarios/sc01_node_failure.json').read_text())
    failure = base['events'][0]
    second = base['events'][1]
    area = dict(type='RestrictedAreaAdd', event_id='E_AREA', occurred_at=12,
                region=dict(id='Z_DEMO', lower=dict(x=450,y=560), upper=dict(x=500,y=600)))
    move = dict(type='TargetMove',event_id='E_TARGET',occurred_at=10,task_id='T05',target=dict(x=850,y=620))
    priority = dict(type='TaskPriorityChange',event_id='E_PRIORITY',occurred_at=13,task_id='T05',priority=5)
    specs = [
        ('SC01','关键能力失效','单节点失效 → 影响传播 → 原位修复',[failure],['capability_restored','unrelated_tasks_unchanged']),
        ('SC02','冗余能力保持','影响不等于不可行；剩余 provider 覆盖任务',[failure],['affected_but_keep','no_reconstruction']),
        ('SC03','双故障与共享候选','两个任务竞争备用 relay，检查占用与最小扰动',[failure,second],['capability_restored','unique_membership']),
        ('SC04','环境禁区 / 航迹修复','只改变受影响航迹，编队与节点归属保持不变',[area],['route_only','unrelated_routes_unchanged']),
        ('SC05','任务目标变化','TargetMove 后旧航迹终点失配，重新规划至新目标',[move],['route_endpoint_matches_target','route_only']),
        ('SC06','混合异常','T03 能力缺口 + T05 航迹冲突，使用两类技能',[failure,area],['formation_and_route_changes','all_hard_constraints_pass']),
        ('SC07','全局反馈重规划','诊断方案局部可行但窗口回归；模型依据真实反馈替换',[],['validator_fail_then_pass','explicit_scope_expansion']),
        ('SC08','综合演示','60 节点、双故障、禁区、任务优先级变化',[failure,second,area,priority],['formation_and_route_changes','all_hard_constraints_pass','failed_nodes_remain_failed']),
    ]
    for ident,name,description,events,expected in specs:
        d = deepcopy(base)
        d.update(name=name,description=description,events=deepcopy(events))
        d['initial_state']['scenario_id']=ident
        if ident=='SC02':
            node=next(n for n in d['initial_state']['nodes'] if n['id']=='U18')
            node['capabilities']['relay']=1
        if ident=='SC07':
            state=validator_feedback().model_dump(mode='json')
            state['scenario_id']=ident
            # Task arrival creates the shortage from a globally valid state.
            # The later shared task is initially protected, making the seeded
            # local repair's global window regression observable.
            new_task=state['tasks'].pop(0)
            d['initial_state']=state
            d['events']=[dict(type='TaskAdd',event_id='E_TASK',occurred_at=10,task=new_task)]
        definition=ScenarioDefinition.model_validate(d)
        yield definition,dict(scenario_id=ident,name=name,description=description,expected_properties=expected,
            diagnostic_seed=ident=='SC07',support='PARTIALLY_SUPPORTED' if ident=='SC02' else 'SUPPORTED',default_visible=ident!='SC02',
            support_note=('P2 KEEP under redundancy, but P4 rejects failed assigned members; full no-reconstruction claim unsupported.' if ident=='SC02' else
                'Priority is an input/order attribute; dynamic priority scheduling remains NOT_EVALUATED.' if ident=='SC08' else ''))


def main():
    directory=ROOT/'scenarios/demo'
    (directory/'catalog').mkdir(parents=True,exist_ok=True)
    catalog=[]
    for definition,meta in definitions():
        (directory/(meta['scenario_id']+'.json')).write_text(definition.model_dump_json(indent=2)+'\n')
        catalog.append(meta)
    (directory/'catalog/manifest.json').write_text(json.dumps(catalog,ensure_ascii=False,indent=2)+'\n')
    print('Wrote 8 deterministic scenario definitions')


if __name__=='__main__': main()
