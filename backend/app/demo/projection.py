"""Presentation facts derived only from P2/P4 checks and actual state deltas."""
from collections import Counter
from ..reconstruction.primitives import ReconstructionPrimitives
from ..agent.evaluation import recovery


def state_metrics(state):
    tools=ReconstructionPrimitives()
    assessments=tools.assess_current_task_state(state)
    violations=tools.get_outstanding_violations(state)
    invalid_tasks={t.id for t in state.tasks if any(v.subject in {t.id,t.formation_id,t.route_id} for v in violations)}
    active=[t for t in state.tasks if t.status=='ACTIVE']
    invalid_routes={t.route_id for t in active if any(v.category=='route' and v.subject==t.id for v in violations)}
    invalid_formations={t.formation_id for t in active if any(v.subject in {t.id,t.formation_id} and v.category!='route' for v in violations)}
    gaps=sum(value.gap for a in assessments for c in a.constraints if c.type=='capability'
             for value in getattr(c.details,'capabilities',{}).values())
    return dict(nodes=dict(Counter(n.status.value for n in state.nodes)),node_count=len(state.nodes),
        task_count=len(active),feasible_tasks=sum(t.id not in invalid_tasks for t in active),
        formation_count=len(state.formations),invalid_formations=sorted(invalid_formations-{None}),
        invalid_routes=sorted(invalid_routes-{None}),capability_gap=gaps,
        task_decisions=dict(Counter(a.decision.value for a in assessments)),
        violations=[v.model_dump(mode='json') for v in violations],
        plan_status='INVALID' if violations else 'VALID_UNDER_IMPLEMENTED_CHECKS')


def compare(before,after,proposal=None,committed=False):
    changed={}
    for kind in ['tasks','formations','nodes','routes']:
        old={x.id:x for x in getattr(before,kind)};new={x.id:x for x in getattr(after,kind)}
        changed[kind]=sorted(k for k in old.keys()|new.keys() if old.get(k)!=new.get(k))
    def assignments(state):return {n:(f.id,f.roles.get(n)) for f in state.formations for n in f.node_ids}
    old_roles,new_roles=assignments(before),assignments(after)
    changed['nodes']=sorted(set(changed['nodes'])|{n for n in old_roles.keys()|new_roles.keys() if old_roles.get(n)!=new_roles.get(n)})
    denominator=sum(len(getattr(before,k)) for k in changed)
    numerator=sum(map(len,changed.values()))
    return dict(before=state_metrics(before),after=state_metrics(after),**recovery(before,after),
        changed_entities=changed,scope_ratio=numerator/denominator if denominator else 0,
        scope_numerator=numerator,scope_denominator=denominator,
        scope_definition='changed task/formation/node(including derived membership/role)/route entities / all pre-reconstruction task/formation/node/route entities',
        plan_disruption=proposal.cost_breakdown.model_dump(mode='json') if proposal and committed else None,
        proposed_disruption=proposal.cost_breakdown.model_dump(mode='json') if proposal else None)


def agent_steps(result):
    output=[]
    for step in result.trace.steps:
        record=step.decision_record
        option=next((o for o in record.available_options if o['option_id']==record.selected_option),None) if record else None
        output.append(dict(step=step.step,kind='AGENT' if step.model_name!='none' else 'FALLBACK',model=step.model_name,
            observed_issue=record.validator_feedback if record and record.validator_feedback else None,
            action=step.action.model_dump(mode='json') if step.action else None,
            option=option,tool=step.tool_name or step.observation.tool,
            result=step.observation.model_dump(mode='json'),feedback=step.validation_feedback.model_dump(mode='json') if step.validation_feedback else None,
            automatic_steps=record.automatic_steps if record else [],model_ms=step.model_latency_ms))
    return output


def deterministic_steps(proposal,validation):
    rows=[('Scope',proposal.reconstruction_scope.model_dump(mode='json')),
          ('Candidate filtering',[c.model_dump(mode='json') for c in proposal.trace.candidate_sets]),
          ('Strategy / Solver',[a.model_dump(mode='json') for a in proposal.trace.strategy_attempts]),
          ('Reservations',[r.model_dump(mode='json') for r in proposal.trace.reservations]),
          ('Route decisions',[r.model_dump(mode='json') for r in proposal.trace.route_decisions]),
          ('Validation',validation.model_dump(mode='json'))]
    return [dict(step=i+1,kind='DETERMINISTIC_BASELINE',tool=name,result=data) for i,(name,data) in enumerate(rows)]
