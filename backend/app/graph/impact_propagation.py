"""Finite, relation-specific walks. Membership/reachability is not invalidity."""

from time import perf_counter

from ..assessment.facts import effective_capabilities
from ..models.domain import ScenarioState, TaskStatus
from ..models.events import (
    Event, NodeDegradation, NodeFailure, RestrictedAreaAdd, RestrictedAreaRemove,
    TargetMove, TaskAdd, TaskCancel, TaskPriorityChange,
)
from ..models.phase2 import ImpactAnalysisResult, ImpactLevel, ImpactRecord, PropagationStep, RelationType as R
from .dependency_graph import TaskReconstructionGraph


def analyze_impact(before_state: ScenarioState, after_state: ScenarioState, event: Event,
                   graph: TaskReconstructionGraph, before_graph: TaskReconstructionGraph | None = None) -> ImpactAnalysisResult:
    start = perf_counter()
    if before_state.scenario_id != after_state.scenario_id or after_state.version != before_state.version + 1:
        raise ValueError("Impact requires consecutive versions of the same scenario")
    if not graph.is_for(after_state):
        raise ValueError("Impact graph does not match after_state")
    previous = before_graph or TaskReconstructionGraph.from_state(before_state)
    if not previous.is_for(before_state):
        raise ValueError("Impact graph does not match before_state")
    tasks = {task.id: task for task in after_state.tasks}
    records: list[ImpactRecord] = []
    seen = set()
    direct = set()

    def add(path, code, reason, level=ImpactLevel.AFFECTED, steps=None):
        if len(path) - 1 > 4 or len(set(path)) != len(path):
            return  # safety cap in addition to the domain-specific finite rules
        key = (tuple(path), code)
        if key in seen:
            return
        seen.add(key)
        records.append(ImpactRecord(entity=path[-1], impact_level=level, reason_code=code,
                                    reason=reason, path=path, steps=steps or [], depth=len(path) - 1))

    def step(source, target, relation, reverse=False):
        return PropagationStep(source=source, target=target, relation=relation,
                               direction="REVERSE" if reverse else "FORWARD")

    def root(key, code="DIRECT_STATE_CHANGE", reason="Entity changed directly in this state transition"):
        direct.add(key)
        add([key], code, reason, ImpactLevel.CRITICAL)

    def active(key):
        return tasks[key.split(":", 1)[1]].status == TaskStatus.ACTIVE

    def sharing_tasks(task_key, source_graph, code):
        for relation in source_graph.query_relations(task_key, R.TASK_ASSIGNED_TO_FORMATION):
            formation = relation.target
            s1 = step(task_key, formation, relation.relation)
            add([task_key, formation], code, "Assignment affects this formation's resource commitments", steps=[s1])
            for neighbor in graph.query_neighbors(formation, R.TASK_ASSIGNED_TO_FORMATION, "in"):
                if neighbor.id != task_key and active(neighbor.id):
                    add([task_key, formation, neighbor.id], code,
                        "Task shares the formation's finite resource budget or time allocation",
                        steps=[s1, step(formation, neighbor.id, R.TASK_ASSIGNED_TO_FORMATION, True)])

    if isinstance(event, (NodeFailure, NodeDegradation)):
        key = f"node:{event.node_id}"
        old = next(node for node in before_state.nodes if node.id == event.node_id)
        new = next(node for node in after_state.nodes if node.id == event.node_id)
        if old != new:
            root(key)
            changed_caps = {cap for cap, amount in effective_capabilities(old).items()
                            if effective_capabilities(new).get(cap, 0) != amount}
            for edge in graph.query_relations(key, R.NODE_PROVIDES_CAPABILITY):
                if graph.query_node(edge.target).capability in changed_caps:
                    add([key, edge.target], "NODE_CONTRIBUTION_CHANGED", "Effective node supply changed",
                        steps=[step(key, edge.target, edge.relation)])
            for formation in graph.query_neighbors(key, R.FORMATION_CONTAINS_NODE, "in"):
                s1 = step(key, formation.id, R.FORMATION_CONTAINS_NODE, True)
                add([key, formation.id], "MEMBER_STATE_CHANGED", "Formation member changed; recompute capacity", steps=[s1])
                for task in graph.query_neighbors(formation.id, R.TASK_ASSIGNED_TO_FORMATION, "in"):
                    if active(task.id):
                        add([key, formation.id, task.id], "ASSIGNED_FORMATION_CHANGED",
                            "Recheck member count, resources and formation minimums",
                            steps=[s1, step(formation.id, task.id, R.TASK_ASSIGNED_TO_FORMATION, True)])
                for cap in graph.query_neighbors(formation.id, R.FORMATION_PROVIDES_CAPABILITY):
                    if cap.capability not in changed_caps:
                        continue
                    s2 = step(formation.id, cap.id, R.FORMATION_PROVIDES_CAPABILITY)
                    path = [key, formation.id, cap.id]
                    add(path, "FORMATION_SUPPLY_CHANGED", "Scoped formation supply changed", steps=[s1, s2])
                    for task in graph.query_neighbors(cap.id, R.TASK_REQUIRES_CAPABILITY, "in"):
                        if active(task.id):
                            add(path + [task.id], "REQUIRED_CAPABILITY_MAY_CHANGE",
                                "Task requires the changed supply; redundancy must be assessed",
                                steps=[s1, s2, step(cap.id, task.id, R.TASK_REQUIRES_CAPABILITY, True)])
        # No propagation from an affected task to its route or other formations.
    elif isinstance(event, (RestrictedAreaAdd, RestrictedAreaRemove)):
        key = f"environment:{event.region.id if isinstance(event, RestrictedAreaAdd) else event.region_id}"
        root(key)
        source = graph if isinstance(event, RestrictedAreaAdd) else previous
        for route in source.query_neighbors(key, R.ROUTE_INTERSECTS_CONSTRAINT, "in"):
            s1 = step(key, route.id, R.ROUTE_INTERSECTS_CONSTRAINT, True)
            add([key, route.id], "ROUTE_ENVIRONMENT_CHANGED", "Continuous route segment intersects changed region", steps=[s1])
            for task in source.query_neighbors(route.id, R.TASK_USES_ROUTE, "in"):
                if active(task.id):
                    add([key, route.id, task.id], "TASK_ROUTE_ENVIRONMENT_CHANGED", "Task uses the affected route",
                        steps=[s1, step(route.id, task.id, R.TASK_USES_ROUTE, True)])
    elif isinstance(event, (TaskAdd, TaskCancel, TaskPriorityChange, TargetMove)):
        key = f"task:{event.task.id if isinstance(event, TaskAdd) else event.task_id}"
        old = next((task for task in before_state.tasks if f"task:{task.id}" == key), None)
        if old != tasks[key.split(":", 1)[1]]:
            root(key)
            if isinstance(event, (TaskAdd, TaskCancel)):
                sharing_tasks(key, previous if isinstance(event, TaskCancel) else graph, "SHARED_COMMITMENT_CHANGED")
            if isinstance(event, TargetMove):
                for route in graph.query_neighbors(key, R.TASK_USES_ROUTE):
                    add([key, route.id], "ROUTE_ENDPOINT_CHANGED", "Task target changed; route is not replanned",
                        steps=[step(key, route.id, R.TASK_USES_ROUTE)])
    # Clock advance can have real nonlocal effects. Include only crossed deadlines,
    # not every task. Resource commitments are not automatically consumed/released.
    for task in after_state.tasks:
        if task.status == TaskStatus.ACTIVE and before_state.clock < task.window.end <= after_state.clock:
            key = f"task:{task.id}"
            root(key, "TASK_DEADLINE_CROSSED", "Scenario clock crossed the task deadline; task lifecycle is unchanged")
    entities = sorted({record.entity for record in records})
    return ImpactAnalysisResult(
        scenario_id=after_state.scenario_id, event_id=event.event_id,
        state_version_before=before_state.version, state_version_after=after_state.version,
        directly_affected=sorted(direct), affected_entities=entities,
        affected_tasks=[key.split(":", 1)[1] for key in entities if key.startswith("task:")],
        affected_formations=[key.split(":", 1)[1] for key in entities if key.startswith("formation:")],
        affected_nodes=[key.split(":", 1)[1] for key in entities if key.startswith("node:")],
        affected_routes=[key.split(":", 1)[1] for key in entities if key.startswith("route:")],
        impact_records=records, propagation_paths=[record.path for record in records],
        affected_subgraph=graph.find_affected_subgraph(entities, previous),
        analysis_time_ms=(perf_counter() - start) * 1000,
    )
