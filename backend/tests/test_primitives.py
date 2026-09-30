import pytest

from backend.app.models.primitives import LocalOptionsResult
from backend.app.reconstruction.common import apply_option, preview
from backend.app.reconstruction.engine import DeterministicReconstructionEngine, ReconstructionError
from backend.app.reconstruction.planner import HierarchicalReconstructionPlanner
from backend.app.reconstruction.primitives import ReconstructionPrimitives
from backend.app.scenarios.manager import ScenarioManager
from backend.tests.test_phase4 import manual_proposal
from scripts.phase3_scenarios import small_scenario


@pytest.fixture
def primitives(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Primitive calls must not invoke the high-level baseline or hierarchical policy")
    monkeypatch.setattr(DeterministicReconstructionEngine, "reconstruct", forbidden)
    monkeypatch.setattr(HierarchicalReconstructionPlanner, "propose", forbidden)
    return ReconstructionPrimitives()


@pytest.mark.parametrize("kind,method,level", [
    ("repair", "try_in_place_repair", "IN_PLACE_REPAIR"),
    ("reassignment", "find_task_reassignment_options", "TASK_REASSIGNMENT"),
    ("new_formation", "try_formation_reconstruction", "FORMATION_RECONSTRUCTION"),
])
def test_independent_local_options_routes_and_validation(primitives, kind, method, level):
    state = small_scenario(kind)
    original = state.model_dump_json()
    assert any(c.code == "FAILED_NODE_ASSIGNED" for c in primitives.get_outstanding_violations(state))
    assert primitives.assess_current_task_state(state)[0].decision == "RECONSTRUCT"
    result = getattr(primitives, method)(state, "T0")
    assert result.options and result.search_complete
    assert result.strategy_level == level
    assert all(a.level == level for a in result.trace.strategy_attempts)  # no automatic escalation
    assert LocalOptionsResult.model_validate_json(result.model_dump_json()) == result
    # The caller selects an option and explicitly asks for route work.
    working = apply_option(state, result.options[0])
    impacts = primitives.detect_route_impacts(working, ["T0"])
    changes = []
    for request in impacts.requests:
        planned = primitives.replan_route(working, request)
        assert planned.status == "FEASIBLE"
        changes.append(planned.change)
    working = preview(working, route_changes=changes)
    # Use the manual test producer, not HierarchicalReconstructionPlanner.
    proposal = manual_proposal(state, working)
    proposal.route_changes = changes
    assert primitives.validate_proposal(state, proposal).status == "PASS_WITH_LIMITATIONS"
    assert not primitives.get_outstanding_violations(working)
    assert state.model_dump_json() == original
    change = result.options[0].formation_change
    if change and change.before:
        change.before.node_ids.clear()
        assert state.model_dump_json() == original


def test_candidates_and_failed_local_attempt_do_not_escalate(primitives):
    state = small_scenario()
    candidates = primitives.generate_candidates(state, "T0", {"relay": 1})
    assert [c.node_id for c in candidates.candidates] == ["U1"]
    state = small_scenario("reassignment")
    attempt = primitives.try_in_place_repair(state, "T0")
    assert not attempt.options
    assert all(a.level == "IN_PLACE_REPAIR" for a in attempt.trace.strategy_attempts)
    assert primitives.find_task_reassignment_options(state, "T0").options
    with pytest.raises(ValueError, match="Unknown task"):
        primitives.try_in_place_repair(state, "UNKNOWN")
    with pytest.raises(ValueError, match="Unknown protected"):
        primitives.try_in_place_repair(state, "T0", ("UNKNOWN",))


def test_commit_without_baseline_preserves_guards(scenario_dir, primitives):
    store = ScenarioManager(scenario_dir)
    session = store.load("SC01")
    store.inject(session.session_id, 0, session.events[0])
    state = store.get(session.session_id).state
    options = primitives.try_in_place_repair(state, "T03")
    candidate = apply_option(state, options.options[0])
    proposal = manual_proposal(state, candidate)
    assert primitives.validate_proposal(state, proposal).status == "PASS_WITH_LIMITATIONS"
    assert store.get(session.session_id).state == state
    with pytest.raises(ValueError, match="store is required"):
        primitives.commit_validated_proposal(session.session_id, proposal)
    tools = ReconstructionPrimitives(store)
    altered = proposal.model_copy(deep=True)
    altered.cost_breakdown.route_change_count = 99
    with pytest.raises(ReconstructionError) as exc:
        tools.commit_validated_proposal(session.session_id, altered)
    assert exc.value.code == "VALIDATION_FAILED"
    assert store.get(session.session_id).state == state
    receipt = tools.commit_validated_proposal(session.session_id, proposal)
    assert receipt.committed_version == 2
    assert tools.commit_validated_proposal(session.session_id, proposal).replayed
    assert store.get(session.session_id).state.nodes == state.nodes
