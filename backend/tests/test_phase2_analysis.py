import pytest

from backend.app.assessment.engine import TaskAssessmentEngine
from backend.app.assessment.facts import segment_intersects_rectangle
from backend.app.assessment.service import process_event
from backend.app.graph.dependency_graph import TaskReconstructionGraph
from backend.app.graph.impact_propagation import analyze_impact
from backend.app.models.domain import EnvironmentConstraint, Position
from backend.app.models.events import event_adapter, NodeFailure
from backend.app.models.phase2 import ConstraintStatus, Phase2Result, RelationType, TaskDecision
from scripts.demo_phase2 import redundancy_example


def check(result, name):
    return next(item for item in result.constraints if item.type == name)


def event(kind, **payload):
    return event_adapter.validate_python({"event_id": "EX", "occurred_at": 10, "type": kind, **payload})


def redundant_state():
    return redundancy_example()


def test_sc01_initial_all_keep_with_honest_timing_limits(definition):
    results = TaskAssessmentEngine().assess(definition.initial_state, full_check=True)
    assert len(results) == 8
    for result in results:
        assert result.decision == TaskDecision.KEEP
        assert all(item.status == ConstraintStatus.PASS for item in result.constraints)
        assert check(result, "priority_timing").details.travel_time == ConstraintStatus.NOT_EVALUATED
        assert not result.evaluation_complete


@pytest.mark.parametrize("node_id", ["U17", "U21"])
def test_sc01_real_dependencies_locality_and_trace(definition, node_id):
    state = definition.initial_state
    incoming = next(item for item in definition.events if item.node_id == node_id)
    formation = next(item for item in state.formations if node_id in item.node_ids)
    tasks = {task.id for task in state.tasks if task.formation_id == formation.id}
    result = process_event(state, incoming)
    assert set(result.impact_analysis.affected_tasks) == tasks
    assert result.impact_analysis.affected_formations == [formation.id]
    assert result.impact_analysis.affected_nodes == [node_id]
    assert result.impact_analysis.affected_routes == []
    assert {task.task_id for task in result.task_assessment} == tasks
    for task in result.task_assessment:
        assert check(task, "capability").details.capabilities["relay"].gap == 1
        assert check(task, "route").status == ConstraintStatus.PASS
        assert task.decision == TaskDecision.RECONSTRUCT
        expected_path = [f"node:{node_id}", f"formation:{formation.id}",
                         f"capability:formation:{formation.id}:relay", f"task:{task.task_id}"]
        assert expected_path in result.impact_analysis.propagation_paths
    full_after = TaskAssessmentEngine().assess(result.application.state, full_check=True)
    assert all(task.decision == TaskDecision.KEEP for task in full_after if task.task_id not in tasks)
    assert Phase2Result.model_validate_json(result.model_dump_json()) == result


def test_redundancy_is_affected_but_keep():
    result = process_event(redundant_state(), event("NodeFailure", node_id="U1"))
    assert result.impact_analysis.affected_tasks == ["T1"]
    task = result.task_assessment[0]
    relay = check(task, "capability").details.capabilities["relay"]
    assert (relay.required, relay.available, relay.gap) == (1, 1, 0)
    assert all(item.status == ConstraintStatus.PASS for item in task.constraints)
    assert task.decision == TaskDecision.KEEP
    assert not result.reconstruction_required
    assert task.entity_states.node_states["U1"] == "FAILED"


def test_completeness_for_all_60_possible_single_node_failures(definition):
    state = definition.initial_state
    baseline = {result.task_id: result.constraints for result in TaskAssessmentEngine().assess(state, full_check=True)}
    for node in state.nodes:
        result = process_event(state, event("NodeFailure", node_id=node.id))
        expected_formations = {formation.id for formation in state.formations if node.id in formation.node_ids}
        expected_tasks = {task.id for task in state.tasks if task.formation_id in expected_formations}
        assert set(result.impact_analysis.affected_tasks) == expected_tasks
        full = TaskAssessmentEngine().assess(result.application.state, full_check=True)
        # Ignore clock-only descriptive details, compare actual constraint outcomes.
        changed = {task.task_id for task in full if [(c.type, c.status, c.reason_codes) for c in task.constraints]
                   != [(c.type, c.status, c.reason_codes) for c in baseline[task.task_id]]}
        assert changed.issubset(expected_tasks)
        assert len(result.task_assessment) == len(expected_tasks)


def test_incremental_engine_does_not_evaluate_unaffected_tasks(definition, monkeypatch):
    original = TaskAssessmentEngine._assess_task
    evaluated = []
    def tracked(self, state, task, *args):
        evaluated.append(task.id)
        return original(self, state, task, *args)
    monkeypatch.setattr(TaskAssessmentEngine, "_assess_task", tracked)
    result = process_event(definition.initial_state, definition.events[0])
    assert evaluated == result.impact_analysis.affected_tasks
    assert len(evaluated) < len(definition.initial_state.tasks)


def test_graph_encapsulation_namespaces_relations_and_defensive_queries(definition):
    state = definition.initial_state
    graph = TaskReconstructionGraph.from_state(state)
    assert graph.query_node("U17") is None
    node = graph.query_node("node:U17")
    node.entity_id = "changed"
    assert graph.query_node("node:U17").entity_id == "U17"
    snapshot = graph.export_snapshot()
    assert len({item.id for item in snapshot.entities}) == len(snapshot.entities)
    assert graph.stats().entities == len(snapshot.entities)
    assert {item.type.value for item in snapshot.entities} == {"Task", "Node", "Formation", "Capability", "Route"}
    assert any(edge.relation == RelationType.CAPABILITY_CONTRIBUTES_TO for edge in snapshot.edges)
    result = process_event(state, definition.events[0])
    assert "capability:relay" not in result.impact_analysis.affected_entities
    edge_keys = {(edge.source, edge.target, edge.relation) for edge in result.impact_analysis.affected_subgraph.edges}
    for record in result.impact_analysis.impact_records:
        assert len(record.steps) == record.depth == len(record.path) - 1
        for step in record.steps:
            pair = (step.target, step.source) if step.direction == "REVERSE" else (step.source, step.target)
            assert (*pair, step.relation) in edge_keys
    with pytest.raises(ValueError, match="after_state"):
        analyze_impact(state, result.application.state, definition.events[0], graph)


def test_restricted_area_add_and_remove_use_new_and_old_edges(definition):
    state = definition.initial_state
    region = {"id": "Z01", "lower": {"x": 400, "y": 339}, "upper": {"x": 500, "y": 341}}
    added = process_event(state, event("RestrictedAreaAdd", region=region))
    assert added.impact_analysis.affected_routes == ["R03"]
    assert added.impact_analysis.affected_tasks == ["T03"]
    assert check(added.task_assessment[0], "route").details.conflicting_region_ids == ["Z01"]
    assert added.task_assessment[0].decision == TaskDecision.RECONSTRUCT
    removed = process_event(added.application.state, event("RestrictedAreaRemove", region_id="Z01"))
    assert removed.impact_analysis.affected_routes == ["R03"]
    assert removed.impact_analysis.affected_tasks == ["T03"]
    assert removed.task_assessment[0].decision == TaskDecision.KEEP
    assert "environment:Z01" in {item.id for item in removed.impact_analysis.affected_subgraph.entities}
    assert removed.application.state.routes == state.routes


@pytest.mark.parametrize("a,b,expected", [
    ((0, 5), (10, 5), True), ((0, 4), (10, 4), True),
    ((0, 0), (4, 4), True), ((0, 3), (10, 3), False),
    ((5, 5), (5, 5), True), ((0, 0), (0, 0), False),
])
def test_continuous_segments_and_closed_region_boundary(a, b, expected):
    region = EnvironmentConstraint(id="Z1", lower=Position(x=4, y=4), upper=Position(x=6, y=6))
    assert segment_intersects_rectangle(Position(x=a[0], y=a[1]), Position(x=b[0], y=b[1]), region) is expected


def test_target_move_priority_cancel_and_add_assessment(definition):
    state = definition.initial_state
    moved = process_event(state, event("TargetMove", task_id="T03", target={"x": 900, "y": 340}))
    assert moved.impact_analysis.affected_routes == ["R03"]
    assert "ROUTE_ENDPOINT_MISMATCH" in moved.task_assessment[0].reason_codes
    priority = process_event(state, event("TaskPriorityChange", task_id="T03", priority=5))
    assert priority.impact_analysis.affected_tasks == ["T03"]
    assert priority.impact_analysis.affected_routes == []
    assert priority.task_assessment[0].decision == TaskDecision.KEEP
    cancelled = process_event(state, event("TaskCancel", task_id="T01"))
    assert set(cancelled.impact_analysis.affected_tasks) == {"T01", "T07"}
    inactive = next(task for task in cancelled.task_assessment if task.task_id == "T01")
    assert all(item.status == ConstraintStatus.NOT_APPLICABLE for item in inactive.constraints)
    new = state.tasks[0].model_copy(deep=True)
    new.id, new.formation_id, new.route_id = "T09", None, None
    added = process_event(state, event("TaskAdd", task=new.model_dump()))
    assert added.impact_analysis.affected_tasks == ["T09"]
    assert {"TASK_UNASSIGNED", "ROUTE_MISSING"}.issubset(added.task_assessment[0].reason_codes)


def test_task_add_reassesses_shared_budget_and_time_conflicts(definition):
    state = definition.initial_state
    new = state.tasks[0].model_copy(deep=True)
    new.id, new.route_id, new.resource_required = "T09", None, 1000
    result = process_event(state, event("TaskAdd", task=new.model_dump()))
    assert set(result.impact_analysis.affected_tasks) == {"T01", "T07", "T09"}
    for task in result.task_assessment:
        assert "SHARED_RESOURCE_OVERCOMMITTED" in task.reason_codes
    existing = next(task for task in result.task_assessment if task.task_id == "T01")
    assert "ASSIGNMENT_TIME_CONFLICT" in existing.reason_codes


def test_degradation_can_need_adjustment_or_reconstruction():
    state = redundant_state()
    result = process_event(state, event("NodeDegradation", node_id="U1", health=0.5))
    assert check(result.task_assessment[0], "capability").details.capabilities["relay"].available == 1.5
    assert result.task_assessment[0].decision == TaskDecision.ADJUST
    state.tasks[0].requirements["relay"] = 2
    result = process_event(state, event("NodeDegradation", node_id="U1", health=0.5))
    assert result.task_assessment[0].decision == TaskDecision.RECONSTRUCT


def test_time_advance_includes_crossed_deadlines_even_for_spare_failure(definition):
    result = process_event(definition.initial_state, NodeFailure(event_id="LATE", occurred_at=350, node_id="U60"))
    expected = {task.id for task in definition.initial_state.tasks if task.window.end <= 350}
    assert set(result.impact_analysis.affected_tasks) == expected
    assert all("TASK_WINDOW_EXPIRED" in task.reason_codes for task in result.task_assessment)
    assert all(task.decision == TaskDecision.RECONSTRUCT for task in result.task_assessment)


def test_formation_minimum_count_resource_and_availability_are_real_checks():
    state = redundant_state()
    state.tasks[0].min_nodes = 2
    state.tasks[0].resource_required = 15
    result = process_event(state, event("NodeFailure", node_id="U1"))
    assert {"INSUFFICIENT_ACTIVE_NODES", "RESOURCE_SHORTAGE"}.issubset(result.task_assessment[0].reason_codes)
    state = redundant_state()
    state.nodes[1].availability.end = 50
    result = process_event(state, event("NodeFailure", node_id="U1"))
    assert check(result.task_assessment[0], "formation").details.active_node_count == 0
    assert "FORMATION_CAPABILITY_SHORTAGE" in result.task_assessment[0].reason_codes


def test_full_assessment_must_be_explicit(definition):
    with pytest.raises(ValueError, match="full_check"):
        TaskAssessmentEngine().assess(definition.initial_state)
