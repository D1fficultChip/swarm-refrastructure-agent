from itertools import combinations
from math import hypot
from time import perf_counter

from ..assessment.facts import effective_capabilities, eligible
from ..models.domain import Formation, NodeStatus, Position
from ..models.phase3 import (FormationMembershipChange, LocalOption, SolverStatus, StrategyAttempt,
                             StrategyLevel, TaskAssignmentChange, TaskRequirement)
from .candidates import CandidateGenerator
from .common import active_tasks, apply_option, failures, local_assessments, unique_id
from .reservations import ReservationLedger


def profile(nodes):
    result = {}
    for node in nodes:
        for cap, amount in effective_capabilities(node).items():
            result[cap] = result.get(cap, 0) + amount
    return result


def reference(nodes):
    return Position(x=sum(n.position.x for n in nodes) / len(nodes),
                    y=sum(n.position.y for n in nodes) / len(nodes))


def locally_feasible(state, option, protected):
    candidate = apply_option(state, option)
    wanted = set(protected) | {option.task_id}
    return all(not failures(a, include_route=False) for a in local_assessments(candidate) if a.task_id in wanted)


class FormationReconstructor:
    def __init__(self, config):
        self.config = config

    def options(self, state, task, scope, level, protected, trace):
        started = perf_counter()
        requirement = TaskRequirement.from_task(task)
        nodes = {n.id: n for n in state.nodes}
        old = next((f for f in state.formations if f.id == task.formation_id), None)
        if level == StrategyLevel.IN_PLACE_REPAIR and old is None:
            trace.strategy_attempts.append(StrategyAttempt(task_id=task.id, level=level, status=SolverStatus.INFEASIBLE,
                                                           reason_codes=["NO_CURRENT_FORMATION"]))
            return [], True
        rebuilding = level == StrategyLevel.FORMATION_RECONSTRUCTION
        retained = [] if rebuilding else [nodes[n] for n in old.node_ids
            if nodes[n].status != NodeStatus.FAILED and (nodes[n].status != NodeStatus.DEGRADED or nodes[n].health > 0)]
        usable = [n for n in retained if eligible(n, task)]
        current = profile(usable)
        required = dict(requirement.required_capabilities)
        if not rebuilding:
            for cap, amount in old.minimum_capabilities.items():
                required[cap] = max(required.get(cap, 0), amount)
            # A shared formation must still serve every existing task after repair.
            for peer in active_tasks(state, old.id):
                for cap, amount in peer.requirements.items():
                    required[cap] = max(required.get(cap, 0), amount)
        gap = {cap: max(0, amount - current.get(cap, 0)) for cap, amount in required.items()}
        resource_gap = sum(n.resource_remaining for n in usable) < task.resource_required
        if old is not None and not rebuilding:
            resource_gap |= sum(n.resource_remaining for n in retained) < sum(t.resource_required for t in active_tasks(state, old.id))
        generation_start = perf_counter()
        candidates = CandidateGenerator().generate(state, requirement, scope, ReservationLedger(state), gap, level,
            count_or_resource_gap=len(usable) < task.min_nodes or resource_gap)
        generation_ms = (perf_counter() - generation_start) * 1000
        trace.timing.candidate_generation_ms += generation_ms
        trace.candidate_sets.append(candidates)
        candidate_ids = [c.node_id for c in candidates.candidates]
        options, feasible_sets = [], []
        examined = 0
        complete = True
        formation_id = unique_id(state, f"F_NEW_{task.id}") if rebuilding else old.id
        for size in range(len(candidate_ids) + 1):
            for ids in combinations(candidate_ids, size):
                if examined >= self.config.max_subsets or len(options) >= self.config.max_options:
                    complete = False
                    break
                examined += 1
                # Extra nodes cannot improve this composition's lexicographic cost.
                if any(chosen.issubset(ids) for chosen in feasible_sets):
                    continue
                members = retained + [nodes[n] for n in ids]
                available = [n for n in members if eligible(n, task)]
                supply = profile(available)
                if len(available) < task.min_nodes or sum(n.resource_remaining for n in available) + 1e-9 < task.resource_required:
                    continue
                if any(supply.get(cap, 0) + 1e-9 < amount for cap, amount in required.items()):
                    continue
                roles = {} if rebuilding else {n: role for n, role in old.roles.items() if n in {m.id for m in members}}
                for node in members:
                    if node.id not in roles:
                        choices = sorted(node.capabilities, key=lambda cap: (-gap.get(cap, 0), cap))
                        if choices:
                            roles[node.id] = choices[0]
                formation = Formation(id=formation_id, node_ids=[n.id for n in members], roles=roles,
                    minimum_capabilities={} if rebuilding else dict(old.minimum_capabilities))
                before = None if rebuilding else old
                change = FormationMembershipChange(
                    formation_id=formation_id, before=before, after=formation,
                    added_nodes=list(ids), removed_nodes=[] if rebuilding else sorted(set(old.node_ids) - set(formation.node_ids)),
                    required_capabilities=required, current_capabilities=current, capability_gap=gap,
                    candidate_contributions={n: effective_capabilities(nodes[n]) for n in ids},
                    result_capabilities=supply, reason_codes=["CAPABILITY_COUNT_RESOURCE_REPAIR"],
                )
                assignment = TaskAssignmentChange(task_id=task.id, before_formation=task.formation_id,
                    after_formation=formation_id, before_start=task.start, after_start=reference(available),
                    reason_codes=["NEW_FORMATION_REQUIRED"]) if rebuilding else None
                option = LocalOption(task_id=task.id, level=level, task_change=assignment,
                    formation_change=change if formation != before else None, selected_nodes=list(ids),
                    rank=[len(ids), sum(hypot(nodes[n].position.x-task.start.x, nodes[n].position.y-task.start.y) for n in ids)])
                if locally_feasible(state, option, protected):
                    options.append(option)
                    feasible_sets.append(set(ids))
            if not complete:
                break
        options.sort(key=lambda o: (o.rank, o.selected_nodes))
        trace.strategy_attempts.append(StrategyAttempt(task_id=task.id, level=level,
            status=SolverStatus.FEASIBLE if options else SolverStatus.INFEASIBLE if complete else SolverStatus.PARTIAL,
            reason_codes=["COMPOSITION_OPTIONS_FOUND"] if options else ["NO_FEASIBLE_COMPOSITION", "NO_CAPABILITY_CANDIDATE" if not candidates.candidates else "RESOURCE_OR_SCHEDULE_OR_CAPABILITY_CONFLICT"],
            alternatives_found=len(options), search_complete=complete))
        trace.timing.formation_solver_ms += (perf_counter()-started)*1000 - generation_ms
        return options, complete
