import random
from collections import Counter

import pytest

from backend.app.scenarios.manager import (
    ScenarioManager, ScenarioNotFoundError, SessionNotFoundError,
)
from scripts.build_scenario import build_sc01


def test_seed_reproduces_checked_in_fixture(definition):
    before = random.getstate()
    assert build_sc01(42) == definition
    assert random.getstate() == before
    assert build_sc01(43).initial_state.nodes[36].position != definition.initial_state.nodes[36].position


def test_initial_fixture_is_a_consistent_baseline(definition):
    state = definition.initial_state
    assert (len(state.nodes), len(state.tasks), len(state.formations)) == (60, 8, 6)
    nodes = {node.id: node for node in state.nodes}
    formations = {formation.id: formation for formation in state.formations}
    routes = {route.id: route for route in state.routes}
    for task in state.tasks:
        members = [nodes[node_id] for node_id in formations[task.formation_id].node_ids]
        profile = Counter()
        for node in members:
            profile.update(node.capabilities)
            assert node.status.value == "NORMAL"
            assert node.availability.start <= task.window.start
            assert node.availability.end >= task.window.end
        assert len(members) >= task.min_nodes
        assert all(profile[cap] >= amount for cap, amount in task.requirements.items())
        assert all(profile[cap] >= amount for cap, amount in formations[task.formation_id].minimum_capabilities.items())
        route = routes[task.route_id]
        assert route.waypoints[0] == task.start
        assert route.waypoints[-1] == task.target
    for formation in state.formations:
        tasks = sorted((task for task in state.tasks if task.formation_id == formation.id), key=lambda task: task.window.start)
        assert sum(task.resource_required for task in tasks) <= sum(nodes[n].resource_remaining for n in formation.node_ids)
        for first, second in zip(tasks, tasks[1:]):
            assert not first.window.overlaps(second.window)
            assert first.target == second.start
    for node_id, formation_id in [("U17", "F03"), ("U21", "F04")]:
        relays = [n for n in formations[formation_id].node_ids if nodes[n].capabilities.get("relay", 0)]
        assert relays == [node_id]


def test_sessions_and_return_values_are_isolated(scenario_dir):
    manager = ScenarioManager(scenario_dir)
    first = manager.load("SC01")
    second = manager.load("SC01")
    assert first.session_id != second.session_id
    assert first.state == second.state
    first.state.nodes[0].position.x = 999
    first.state.formations[0].node_ids.clear()
    first.events.clear()
    original = manager.get(first.session_id)
    assert original.state.nodes[0].position.x == 100
    assert len(original.state.formations[0].node_ids) == 6
    assert len(original.events) == 2
    assert manager.get(second.session_id).state == original.state
    third = manager.load("SC01")
    assert third.state == original.state


def test_manager_errors(scenario_dir):
    manager = ScenarioManager(scenario_dir)
    with pytest.raises(ScenarioNotFoundError):
        manager.load("../../etc/passwd")
    with pytest.raises(SessionNotFoundError):
        manager.get("not-a-session")


def test_empty_directory_fails_fast(tmp_path):
    with pytest.raises(ValueError, match="no JSON"):
        ScenarioManager(tmp_path)


def test_duplicate_scenario_ids_fail_fast(tmp_path, definition):
    for name in ["one.json", "two.json"]:
        (tmp_path / name).write_text(definition.model_dump_json(), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate scenario"):
        ScenarioManager(tmp_path)
