from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.app.events.injector import EventApplicationError, apply_event
from backend.app.models.domain import NodeStatus
from backend.app.models.events import event_adapter, NodeFailure
from backend.app.models.phase2 import EventErrorCode
from backend.app.scenarios.manager import ScenarioManager


def event(kind, **payload):
    return event_adapter.validate_python({"type": kind, "event_id": "EX", "occurred_at": 10, **payload})


def test_failure_only_changes_fact_and_returns_detached_snapshot(definition):
    state = definition.initial_state
    saved = state.model_dump_json()
    result = apply_event(state, definition.events[0])
    assert state.model_dump_json() == saved
    assert result.state.version == state.version + 1
    assert result.state.clock == 10
    assert result.changed_entities == ["node:U17"]
    assert result.changes[0].changed_fields == ["status"]
    assert result.changes[0].before.status == NodeStatus.NORMAL
    assert result.changes[0].after.status == NodeStatus.FAILED
    assert result.state.tasks == state.tasks
    assert result.state.formations == state.formations
    result.state.nodes[0].position.x = 999
    assert state.model_dump_json() == saved


def test_all_other_handler_transforms(definition):
    state = definition.initial_state
    degraded = apply_event(state, event("NodeDegradation", node_id="U17", health=0.5)).state
    assert degraded.nodes[16].health == 0.5
    assert degraded.nodes[16].status == NodeStatus.DEGRADED
    task = state.tasks[0].model_copy(deep=True)
    task.id, task.formation_id, task.route_id = "T09", None, None
    added = apply_event(state, event("TaskAdd", task=task.model_dump())).state
    assert len(added.tasks) == 9
    assert apply_event(state, event("TaskCancel", task_id="T01")).state.tasks[0].status.value == "CANCELLED"
    assert apply_event(state, event("TaskPriorityChange", task_id="T01", priority=5)).state.tasks[0].priority == 5
    moved = apply_event(state, event("TargetMove", task_id="T01", target={"x": 900, "y": 100})).state
    assert moved.tasks[0].target.x == 900
    assert moved.routes == state.routes  # no hidden replanning
    region = {"id": "Z01", "lower": {"x": 400, "y": 90}, "upper": {"x": 500, "y": 110}}
    restricted = apply_event(state, event("RestrictedAreaAdd", region=region)).state
    assert len(restricted.environment) == 1
    removed = apply_event(restricted, event("RestrictedAreaRemove", region_id="Z01"))
    assert removed.state.environment == []
    assert removed.state.version == 2
    assert removed.changes[0].after is None


@pytest.mark.parametrize("kind,payload,code", [
    ("NodeFailure", {"node_id": "U999"}, EventErrorCode.TARGET_NOT_FOUND),
    ("TaskCancel", {"task_id": "T99"}, EventErrorCode.TARGET_NOT_FOUND),
    ("RestrictedAreaRemove", {"region_id": "Z99"}, EventErrorCode.TARGET_NOT_FOUND),
    ("TargetMove", {"task_id": "T01", "target": {"x": 2000, "y": 100}}, EventErrorCode.INVALID_EVENT_STATE),
    ("TaskPriorityChange", {"task_id": "T01", "priority": 3}, EventErrorCode.INVALID_TRANSITION),
])
def test_invalid_events_are_atomic(definition, kind, payload, code):
    state = definition.initial_state
    saved = state.model_dump_json()
    with pytest.raises(EventApplicationError) as error:
        apply_event(state, event(kind, **payload))
    assert error.value.code == code
    assert state.model_dump_json() == saved


def test_task_add_duplicate_or_invalid_reference_is_atomic(definition):
    state = definition.initial_state
    task = state.tasks[0].model_copy(deep=True)
    with pytest.raises(EventApplicationError) as error:
        apply_event(state, event("TaskAdd", task=task.model_dump()))
    assert error.value.code == EventErrorCode.ENTITY_ALREADY_EXISTS
    task.id, task.formation_id, task.route_id = "T09", "F99", None
    with pytest.raises(EventApplicationError) as error:
        apply_event(state, event("TaskAdd", task=task.model_dump()))
    assert error.value.code == EventErrorCode.INVALID_EVENT_STATE
    assert state.version == 0 and len(state.tasks) == 8


def test_repeated_failure_and_degradation_of_failed_node_are_rejected(definition):
    state = apply_event(definition.initial_state, definition.events[0]).state
    for repeated in [event("NodeFailure", node_id="U17"), event("NodeDegradation", node_id="U17", health=0.5)]:
        with pytest.raises(EventApplicationError) as error:
            apply_event(state, repeated)
        assert error.value.code == EventErrorCode.INVALID_TRANSITION
    assert state.version == 1


def test_session_sequential_idempotency_and_version_rules(scenario_dir, definition):
    manager = ScenarioManager(scenario_dir)
    session = manager.load("SC01")
    first = manager.inject(session.session_id, 0, definition.events[0])
    second = manager.inject(session.session_id, 1, definition.events[1])
    assert second.application.state_version_before == 1
    assert second.application.state_version_after == 2
    replay = manager.inject(session.session_id, 0, definition.events[0])
    assert replay.replayed
    assert replay.application.state.version == 1  # historical receipt, not current state
    assert replay.impact_analysis == first.impact_analysis
    assert manager.get(session.session_id).state.version == 2
    assert manager.assessment(session.session_id).application.event_id == definition.events[1].event_id
    replay.application.state.nodes.clear()
    assert len(manager.assessment(session.session_id, definition.events[0].event_id).application.state.nodes) == 60
    assert manager.load("SC01").state.version == 0


@pytest.mark.parametrize("case", ["id_conflict", "stale_version", "out_of_order", "repeat_failure"])
def test_session_rejections_preserve_state(scenario_dir, definition, case):
    manager = ScenarioManager(scenario_dir)
    session = manager.load("SC01")
    manager.inject(session.session_id, 0, definition.events[0])
    incoming = NodeFailure(event_id="NEXT", occurred_at=11, node_id="U21")
    expected_version = 1
    code = EventErrorCode.INVALID_TRANSITION
    if case == "id_conflict":
        incoming.event_id = definition.events[0].event_id
        code = EventErrorCode.EVENT_ID_CONFLICT
    elif case == "stale_version":
        expected_version = 0
        code = EventErrorCode.VERSION_CONFLICT
    elif case == "out_of_order":
        incoming.occurred_at = 9
        code = EventErrorCode.EVENT_OUT_OF_ORDER
    else:
        incoming.node_id = "U17"
    before = manager.get(session.session_id)
    with pytest.raises(EventApplicationError) as error:
        manager.inject(session.session_id, expected_version, incoming)
    assert error.value.code == code
    assert manager.get(session.session_id) == before


def test_concurrent_same_version_only_one_event_commits(scenario_dir, definition):
    manager = ScenarioManager(scenario_dir)
    session = manager.load("SC01")
    def inject(incoming):
        try:
            return manager.inject(session.session_id, 0, incoming).application.event_id
        except EventApplicationError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(inject, definition.events))
    assert outcomes.count(EventErrorCode.VERSION_CONFLICT) == 1
    assert manager.get(session.session_id).state.version == 1
