from time import perf_counter

from ..assessment.facts import eligible
from ..models.domain import NodeStatus
from ..models.phase3 import LocalOption, SolverStatus, StrategyAttempt, StrategyLevel, TaskAssignmentChange
from .formation import locally_feasible, reference


class TaskReconstructor:
    def options(self, state, task, protected, trace):
        start = perf_counter()
        nodes = {n.id: n for n in state.nodes}
        options = []
        for formation in sorted(state.formations, key=lambda f: f.id):
            if formation.id == task.formation_id:
                continue
            members = [nodes[n] for n in formation.node_ids]
            # Level 2 takes an existing composition unchanged; it does not claim
            # a formation containing failed members is a clean direct assignment.
            if not members or any(n.status == NodeStatus.FAILED for n in members):
                continue
            available = [n for n in members if eligible(n, task)]
            if not available:
                continue
            assignment = TaskAssignmentChange(task_id=task.id, before_formation=task.formation_id,
                after_formation=formation.id, before_start=task.start, after_start=reference(available),
                reason_codes=["CURRENT_FORMATION_REPAIR_UNAVAILABLE"])
            option = LocalOption(task_id=task.id, level=StrategyLevel.TASK_REASSIGNMENT, task_change=assignment)
            if locally_feasible(state, option, protected):
                options.append(option)
        trace.strategy_attempts.append(StrategyAttempt(task_id=task.id, level=StrategyLevel.TASK_REASSIGNMENT,
            status=SolverStatus.FEASIBLE if options else SolverStatus.INFEASIBLE,
            reason_codes=["EXISTING_FORMATION_OPTIONS_FOUND"] if options else ["NO_FEASIBLE_EXISTING_FORMATION"],
            alternatives_found=len(options)))
        trace.timing.task_solver_ms += (perf_counter()-start)*1000
        return options
