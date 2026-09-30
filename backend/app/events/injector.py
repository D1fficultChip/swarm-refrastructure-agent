from time import perf_counter

from pydantic import ValidationError

from ..models.domain import EnvironmentKind, NodeStatus, ScenarioState, TaskStatus
from ..models.events import (
    Event, NodeDegradation, NodeFailure, RestrictedAreaAdd, RestrictedAreaRemove,
    TargetMove, TaskAdd, TaskCancel, TaskPriorityChange, event_adapter,
)
from ..models.phase2 import EntityChange, EventApplicationResult, EventErrorCode


class EventApplicationError(ValueError):
    def __init__(self, code: EventErrorCode, message: str):
        self.code = code
        super().__init__(message)


def find_entity(entities, entity_id):
    for entity in entities:
        if entity.id == entity_id:
            return entity
    raise EventApplicationError(EventErrorCode.TARGET_NOT_FOUND, f"Entity {entity_id} does not exist")


def require(condition: bool, message: str):
    if not condition:
        raise EventApplicationError(EventErrorCode.INVALID_TRANSITION, message)


def require_new_id(state: ScenarioState, entity_id: str):
    groups = [state.nodes, state.tasks, state.formations, state.capabilities, state.routes, state.environment]
    if any(entity.id == entity_id for group in groups for entity in group):
        raise EventApplicationError(EventErrorCode.ENTITY_ALREADY_EXISTS, f"Entity {entity_id} already exists")


def failure(state: ScenarioState, event: NodeFailure):
    node = find_entity(state.nodes, event.node_id)
    require(node.status != NodeStatus.FAILED, f"Node {node.id} is already FAILED")
    node.status = NodeStatus.FAILED


def degradation(state: ScenarioState, event: NodeDegradation):
    node = find_entity(state.nodes, event.node_id)
    require(node.status != NodeStatus.FAILED, "Cannot degrade a FAILED node")
    current = node.health if node.status == NodeStatus.DEGRADED else 1.0
    require(event.health < current, "Degradation must reduce effective health")
    node.status = NodeStatus.DEGRADED
    node.health = event.health


def task_add(state: ScenarioState, event: TaskAdd):
    require_new_id(state, event.task.id)
    require(event.task.status == TaskStatus.ACTIVE, "New task must be ACTIVE")
    state.tasks.append(event.task.model_copy(deep=True))


def task_cancel(state: ScenarioState, event: TaskCancel):
    task = find_entity(state.tasks, event.task_id)
    require(task.status == TaskStatus.ACTIVE, "Only an ACTIVE task may be cancelled")
    task.status = TaskStatus.CANCELLED


def priority_change(state: ScenarioState, event: TaskPriorityChange):
    task = find_entity(state.tasks, event.task_id)
    require(task.status == TaskStatus.ACTIVE, "Only ACTIVE task priority may change")
    require(task.priority != event.priority, "Priority is unchanged")
    task.priority = event.priority


def region_add(state: ScenarioState, event: RestrictedAreaAdd):
    require_new_id(state, event.region.id)
    state.environment.append(event.region.model_copy(deep=True))


def region_remove(state: ScenarioState, event: RestrictedAreaRemove):
    region = find_entity(state.environment, event.region_id)
    require(region.kind == EnvironmentKind.RESTRICTED, "Only RESTRICTED regions may be removed by this event")
    state.environment.remove(region)


def target_move(state: ScenarioState, event: TargetMove):
    task = find_entity(state.tasks, event.task_id)
    require(task.status == TaskStatus.ACTIVE, "Only ACTIVE task targets may move")
    require(task.target != event.target, "Target is unchanged")
    task.target = event.target.model_copy(deep=True)


HANDLERS = {
    "NodeFailure": failure, "NodeDegradation": degradation, "TaskAdd": task_add,
    "TaskCancel": task_cancel, "TaskPriorityChange": priority_change,
    "RestrictedAreaAdd": region_add, "RestrictedAreaRemove": region_remove, "TargetMove": target_move,
}


def apply_event(state: ScenarioState, event: Event) -> EventApplicationResult:
    """Pure transaction. New IDs failing again are rejected; session replay is upstream."""
    start = perf_counter()
    before = ScenarioState.model_validate(state.model_dump(mode="json"))
    event = event_adapter.validate_python(event.model_dump(mode="json"))
    if event.occurred_at < before.clock:
        raise EventApplicationError(EventErrorCode.EVENT_OUT_OF_ORDER, "Event precedes the current scenario clock")
    candidate = before.model_copy(deep=True)
    try:
        HANDLERS[event.type](candidate, event)
        candidate.version = before.version + 1
        candidate.clock = event.occurred_at
        candidate = ScenarioState.model_validate(candidate.model_dump(mode="json"))
    except ValidationError as exc:
        raise EventApplicationError(EventErrorCode.INVALID_EVENT_STATE, str(exc)) from exc
    changes = []
    for namespace, field in [("node", "nodes"), ("task", "tasks"), ("environment", "environment")]:
        old = {entity.id: entity for entity in getattr(before, field)}
        new = {entity.id: entity for entity in getattr(candidate, field)}
        for key in sorted(old.keys() | new.keys()):
            left, right = old.get(key), new.get(key)
            if left == right:
                continue
            fields = (["__entity__"] if left is None or right is None else
                      [name for name in type(left).model_fields if getattr(left, name) != getattr(right, name)])
            changes.append(EntityChange(entity=f"{namespace}:{key}", changed_fields=fields, before=left, after=right))
    return EventApplicationResult(
        event_id=event.event_id, state_version_before=before.version, state_version_after=candidate.version,
        clock_before=before.clock, clock_after=candidate.clock, changes=changes,
        changed_entities=[change.entity for change in changes], state=candidate,
        event_application_ms=(perf_counter() - start) * 1000,
    )
