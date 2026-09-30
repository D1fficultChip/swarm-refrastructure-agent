import math

import pytest
from pydantic import ValidationError

from backend.app.adapters.io import InputAdapter
from backend.app.models.domain import (
    EnvironmentConstraint, NodeStatus, Position, ScenarioState, TimeWindow,
)
from backend.app.models.events import ScenarioDefinition, event_adapter


def test_scenario_json_roundtrip(definition):
    assert ScenarioDefinition.model_validate_json(definition.model_dump_json()) == definition


@pytest.mark.parametrize("field,value", [
    ("priority", 6), ("priority", True), ("min_nodes", 0),
    ("resource_required", -1), ("unexpected_solver_field", 1),
])
def test_bad_task_values_rejected(state_payload, field, value):
    state_payload["tasks"][0][field] = value
    with pytest.raises(ValidationError):
        ScenarioState.model_validate(state_payload)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_non_finite_coordinate_rejected(value):
    with pytest.raises(ValidationError):
        Position(x=value, y=0)


@pytest.mark.parametrize("kind", [
    "duplicate_entity", "unknown_member", "duplicate_member", "shared_member",
    "unknown_formation", "unknown_route", "shared_route", "unknown_capability",
    "bad_role", "outside_map",
])
def test_structural_invariants(state_payload, kind):
    if kind == "duplicate_entity":
        state_payload["nodes"][1]["id"] = "U01"
    elif kind == "unknown_member":
        state_payload["formations"][0]["node_ids"].append("U99")
    elif kind == "duplicate_member":
        state_payload["formations"][0]["node_ids"].append("U01")
    elif kind == "shared_member":
        state_payload["formations"][1]["node_ids"].append("U01")
    elif kind == "unknown_formation":
        state_payload["tasks"][0]["formation_id"] = "F99"
    elif kind == "unknown_route":
        state_payload["tasks"][0]["route_id"] = "R99"
    elif kind == "shared_route":
        state_payload["tasks"][1]["route_id"] = "R01"
    elif kind == "unknown_capability":
        state_payload["nodes"][0]["capabilities"]["teleport"] = 1
    elif kind == "bad_role":
        state_payload["formations"][0]["roles"]["U01"] = "relay"
    elif kind == "outside_map":
        state_payload["routes"][0]["waypoints"][0]["x"] = 1001
    with pytest.raises(ValidationError):
        ScenarioState.model_validate(state_payload)


def test_infeasible_observation_is_representable(state_payload):
    state_payload["nodes"][16]["status"] = "FAILED"
    state_payload["tasks"][0]["formation_id"] = None
    state_payload["tasks"][0]["route_id"] = None
    state_payload["routes"][2]["waypoints"][-1] = {"x": 900, "y": 200}
    state = ScenarioState.model_validate(state_payload)
    assert state.nodes[16].status == NodeStatus.FAILED
    assert "U17" in state.formations[2].node_ids
    assert state.tasks[0].formation_id is None


def test_half_open_windows():
    first = TimeWindow(start=0, end=10)
    assert not first.overlaps(TimeWindow(start=10, end=20))
    assert first.overlaps(TimeWindow(start=9, end=20))
    with pytest.raises(ValidationError):
        TimeWindow(start=10, end=10)


def test_invalid_rectangle():
    with pytest.raises(ValidationError):
        EnvironmentConstraint(id="ENV01", lower=Position(x=10, y=10), upper=Position(x=5, y=20))


def test_all_event_variants_roundtrip(definition):
    task = definition.initial_state.tasks[0].model_dump(mode="json")
    payloads = [
        {"type": "NodeFailure", "node_id": "U17"},
        {"type": "NodeDegradation", "node_id": "U17", "health": 0.5},
        {"type": "TaskAdd", "task": task},
        {"type": "TaskCancel", "task_id": "T01"},
        {"type": "TaskPriorityChange", "task_id": "T01", "priority": 5},
        {"type": "RestrictedAreaAdd", "region": {
            "id": "ENV01", "lower": {"x": 1, "y": 1}, "upper": {"x": 2, "y": 2},
        }},
        {"type": "RestrictedAreaRemove", "region_id": "ENV01"},
        {"type": "TargetMove", "task_id": "T01", "target": {"x": 200, "y": 300}},
    ]
    for index, payload in enumerate(payloads):
        event = event_adapter.validate_python({"event_id": f"E{index}", "occurred_at": 10, **payload})
        assert event_adapter.validate_json(event.model_dump_json()) == event


@pytest.mark.parametrize("payload", [
    {"type": "UnknownEvent", "node_id": "U17"},
    {"type": "NodeFailure", "task_id": "T01"},
    {"type": "NodeFailure", "node_id": "U17", "payload": {}},
    {"type": "NodeDegradation", "node_id": "U17", "health": 0},
    {"type": "RestrictedAreaAdd", "region": {
        "id": "ENV01", "kind": "RISK", "lower": {"x": 1, "y": 1}, "upper": {"x": 2, "y": 2},
    }},
])
def test_invalid_event_payload_rejected(payload):
    with pytest.raises(ValidationError):
        event_adapter.validate_python({"event_id": "E01", "occurred_at": 10, **payload})


@pytest.mark.parametrize("kind", ["duplicate", "reverse", "before_clock"])
def test_bad_event_sequence(definition, kind):
    payload = definition.model_dump(mode="json")
    if kind == "duplicate":
        payload["events"][1]["event_id"] = "E001"
    elif kind == "reverse":
        payload["events"].reverse()
    else:
        payload["initial_state"]["clock"] = 20
    with pytest.raises(ValidationError):
        ScenarioDefinition.model_validate(payload)


def test_input_adapter_revalidates_nested_mutation(definition):
    state = definition.initial_state
    state.formations[0].node_ids.append("U99")
    with pytest.raises(ValidationError):
        InputAdapter().normalize(state)
