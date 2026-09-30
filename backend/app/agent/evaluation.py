"""Paired small-case evaluation, not a statistical Phase 6 benchmark."""
from time import perf_counter
from unittest.mock import patch

from ..models.domain import TaskStatus
from ..reconstruction.formation import FormationReconstructor
from ..reconstruction.task import TaskReconstructor
from ..reconstruction.routes import RoutePlanner
from ..reconstruction.engine import DeterministicReconstructionEngine
from ..reconstruction.primitives import ReconstructionPrimitives


def violated_tasks(state):
    failures = ReconstructionPrimitives().get_outstanding_violations(state)
    return {t.id for t in state.tasks if t.status == TaskStatus.ACTIVE and
            any(c.subject in {t.id,t.formation_id,t.route_id} for c in failures)}


def recovery(before, after):
    initial, final = violated_tasks(before), violated_tasks(after)
    return {"initial_violated_tasks": sorted(initial), "remaining_violated_tasks": sorted(final),
            "recovered_tasks": sorted(initial-final), "task_recovery": len(initial-final)/len(initial) if initial else 1.0}


def measured_baseline(manager, session):
    """Count actual solver invocations without changing the deterministic policy."""
    counts = {"formation":0,"task":0,"route":0}
    def counted(kind, method):
        def invoke(*args, **kwargs):
            counts[kind] += 1
            return method(*args, **kwargs)
        return invoke
    begin = perf_counter()
    with patch.object(FormationReconstructor,"options",counted("formation",FormationReconstructor.options)), \
         patch.object(TaskReconstructor,"options",counted("task",TaskReconstructor.options)), \
         patch.object(RoutePlanner,"plan",counted("route",RoutePlanner.plan)):
        result = DeterministicReconstructionEngine(manager).reconstruct(session.session_id,session.state.version)
    elapsed = (perf_counter()-begin)*1000
    return result, {**recovery(session.state,result.state), "committed":result.committed,
        "validation_success":result.validation.status != "FAIL", "validation_status":result.validation.status.value,
        "tool_calls":0, "tool_calls_note":"Baseline calls solvers directly, not Agent registry tools.",
        "solver_calls":sum(counts.values()), "solver_calls_by_kind":counts, "validation_retry_count":0,
        "disruption":result.proposal.cost_breakdown.model_dump(mode="json"),
        "route_changes":len(result.proposal.route_changes), "model_total_ms":0, "total_ms":elapsed}


def agent_metrics(result, before, after):
    return {**recovery(before,after), "committed":result.commit_receipt is not None,
        "validation_success":result.validation_status in {"PASS","PASS_WITH_LIMITATIONS"},
        "validation_status":result.validation_status, "termination":result.termination_reason,
        "fallback_used":result.fallback_used, "tool_calls":result.tool_calls, "solver_calls":result.solver_calls,
        "validation_retry_count":max(0,result.validation_attempts-1),
        "disruption":result.proposal.cost_breakdown.model_dump(mode="json") if result.proposal else None,
        "route_changes":len(result.proposal.route_changes) if result.proposal else 0,
        "model_total_ms":result.timing.model_total_ms, "total_ms":result.timing.agent_total_ms}
