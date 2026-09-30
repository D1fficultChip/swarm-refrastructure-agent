from math import hypot

from ..models.phase3 import CostBreakdown, FormationMembershipChange, NodeRoleChange, TaskAssignmentChange
from .common import active_tasks
from .formation import profile


def describe_delta(before, after):
    old_tasks = {t.id:t for t in before.tasks}
    old_formations = {f.id:f for f in before.formations}
    nodes = {n.id:n for n in after.nodes}
    task_changes = [TaskAssignmentChange(task_id=t.id,before_formation=old_tasks[t.id].formation_id,
        after_formation=t.formation_id,before_start=old_tasks[t.id].start,after_start=t.start,
        reason_codes=["HIERARCHICAL_REASSIGNMENT"])
        for t in after.tasks if t.formation_id != old_tasks[t.id].formation_id or t.start != old_tasks[t.id].start]
    formations = []
    for f in after.formations:
        old = old_formations.get(f.id)
        if f == old:
            continue
        old_ids = set(old.node_ids) if old else set()
        added,removed = sorted(set(f.node_ids)-old_ids),sorted(old_ids-set(f.node_ids))
        required = dict(f.minimum_capabilities)
        for task in active_tasks(after,f.id):
            for cap,amount in task.requirements.items():
                required[cap] = max(required.get(cap,0),amount)
        current = profile([nodes[n] for n in old_ids])
        formations.append(FormationMembershipChange(formation_id=f.id,before=old,after=f,
            added_nodes=added,removed_nodes=removed,required_capabilities=required,current_capabilities=current,
            capability_gap={cap:max(0,amount-current.get(cap,0)) for cap,amount in required.items()},
            candidate_contributions={n:profile([nodes[n]]) for n in added},
            result_capabilities=profile([nodes[n] for n in f.node_ids]),reason_codes=["LOCAL_COMPOSITION_REPAIR"]))
    old_members = {n:(f.id,f.roles.get(n)) for f in before.formations for n in f.node_ids}
    new_members = {n:(f.id,f.roles.get(n)) for f in after.formations for n in f.node_ids}
    roles = [NodeRoleChange(node_id=n,before_formation=old_members.get(n,(None,None))[0],
        after_formation=new_members.get(n,(None,None))[0],before_role=old_members.get(n,(None,None))[1],
        after_role=new_members.get(n,(None,None))[1])
        for n in sorted(old_members.keys()|new_members.keys()) if old_members.get(n) != new_members.get(n)]
    return task_changes,formations,roles


class DisruptionCostEvaluator:
    def evaluate(self, before, after, route_changes):
        tasks,formations,roles = describe_delta(before,after)
        nodes = {n.id:n for n in before.nodes}
        distance_cost = 0.0
        resource_cost = 0.0
        for change in roles:
            if change.after_formation and change.after_formation != change.before_formation:
                assignments = sorted(active_tasks(after,change.after_formation),key=lambda t:(t.window.start,t.id))
                if assignments:
                    reference = assignments[0].start
                    node = nodes[change.node_id]
                    distance_cost += hypot(node.position.x-reference.x,node.position.y-reference.y)
                    # Remaining capacity is not consumption. No incremental
                    # consumption model exists; both resource cost fields are neutral.
        distance_cost += sum(hypot(t.before_start.x-t.after_start.x,t.before_start.y-t.after_start.y) for t in tasks)
        task_count = sum(t.before_formation != t.after_formation for t in tasks)
        member_count = sum(len(f.added_nodes)+len(f.removed_nodes) for f in formations)
        new_count = sum(f.before is None for f in formations)
        node_count = sum(n.before_formation != n.after_formation for n in roles)
        return CostBreakdown(task_reassignment_count=task_count,formation_membership_changes=member_count,
            formations_changed=len(formations),new_formations=new_count,node_switch_count=node_count,
            route_change_count=len(route_changes),distance_cost=distance_cost,resource_cost=resource_cost,
            resource_usage_cost=0,
            lexicographic_cost=[task_count,new_count,len(formations),member_count,len(route_changes),
                                node_count,distance_cost,0])
