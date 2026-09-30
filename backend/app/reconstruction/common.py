from hashlib import sha256

from ..assessment.engine import TaskAssessmentEngine
from ..models.domain import ScenarioState, TaskStatus
from ..models.phase2 import ConstraintStatus
from ..models.phase3 import LocalOption


def digest(state: ScenarioState) -> str:
    return sha256(state.model_dump_json().encode()).hexdigest()


def unique_id(state, prefix):
    used = {entity.id for field in ["nodes", "tasks", "formations", "routes", "capabilities", "environment"]
            for entity in getattr(state, field)}
    base = prefix[:54]
    suffix = 0
    candidate = base
    while candidate in used:
        suffix += 1
        candidate = f"{base}_{suffix}"
    return candidate


def preview(state, task_changes=(), formation_changes=(), route_changes=()):
    """Materialize typed deltas into a PRIVATE snapshot, retaining version/clock."""
    data = state.model_dump(mode="json")
    for change in formation_changes:
        data["formations"] = [f for f in data["formations"] if f["id"] != change.formation_id]
        data["formations"].append(change.after.model_dump(mode="json"))
    for change in task_changes:
        task = next(t for t in data["tasks"] if t["id"] == change.task_id)
        task["formation_id"] = change.after_formation
        task["start"] = change.after_start.model_dump(mode="json")
    for change in route_changes:
        data["routes"] = [r for r in data["routes"] if r["id"] != change.route_id]
        data["routes"].append(change.after.model_dump(mode="json"))
        next(t for t in data["tasks"] if t["id"] == change.task_id)["route_id"] = change.route_id
    return ScenarioState.model_validate(data)


def apply_option(state, option: LocalOption):
    return preview(state, [option.task_change] if option.task_change else [],
                   [option.formation_change] if option.formation_change else [])


def local_assessments(state):
    # Explicit solver verification, not Phase 2's event propagation flow.
    return TaskAssessmentEngine().assess(state, full_check=True)


def failures(result, include_route=True):
    return [check for check in result.constraints if check.status == ConstraintStatus.FAIL
            and (include_route or check.type != "route")]


def active_tasks(state, formation_id):
    return [task for task in state.tasks if task.status == TaskStatus.ACTIVE and task.formation_id == formation_id]
