from ..models.domain import NodeStatus, TaskStatus
from ..models.phase2 import ConstraintStatus
from ..models.phase3 import ReconstructionScope
from .common import failures, local_assessments
from .reservations import ReservationLedger


class ReconstructionScopeBuilder:
    def build(self, state, receipts=()):
        assessments = local_assessments(state)
        tasks = {task.id: task for task in state.tasks}
        # Evidence cannot introduce an old gap, but current-state reconciliation
        # catches outstanding failures even if history is missing or incomplete.
        current = [a for a in assessments if tasks[a.task_id].status == TaskStatus.ACTIVE
                   and (failures(a) or any(c.status == ConstraintStatus.DEGRADED for c in a.constraints))]
        current.sort(key=lambda a: (-len(failures(a)), -tasks[a.task_id].priority,
                                   tasks[a.task_id].window.end, a.task_id))
        ids = {a.task_id for a in current}
        events = []
        for receipt in receipts:
            if (receipt.impact_analysis.scenario_id != state.scenario_id or
                    receipt.application.state_version_after > state.version):
                raise ValueError("Receipt is not evidence for the current scenario version")
            if ids.intersection(receipt.impact_analysis.affected_tasks):
                events.append(receipt.application.event_id)
        return ReconstructionScope(
            state_version=state.version, source_events=list(dict.fromkeys(events)),
            affected_tasks=[a.task_id for a in current],
            affected_formations=sorted({tasks[key].formation_id for key in ids if tasks[key].formation_id}),
            unavailable_nodes=sorted(n.id for n in state.nodes if n.status == NodeStatus.FAILED or
                                     (n.status == NodeStatus.DEGRADED and n.health == 0)),
            potentially_affected_routes=sorted({tasks[key].route_id for key in ids if tasks[key].route_id}),
            protected_task_ids=sorted(t.id for t in state.tasks if t.status == TaskStatus.ACTIVE and t.id not in ids),
            constraint_gaps=current, current_assessments=assessments,
            reserved_resources=ReservationLedger(state).reservations,
            order_reasons={a.task_id: [f"hard_failures={len(failures(a))}", f"priority={tasks[a.task_id].priority}",
                                      f"deadline={tasks[a.task_id].window.end}", "stable_task_id_tiebreak"] for a in current},
        )
