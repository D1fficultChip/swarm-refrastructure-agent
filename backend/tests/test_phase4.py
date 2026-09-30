"""Adversarial proposals are built independently of planner acceptance."""
from concurrent.futures import ThreadPoolExecutor
from math import hypot
from uuid import uuid4

import pytest

from backend.app.assessment.engine import TaskAssessmentEngine
from backend.app.models.domain import EnvironmentConstraint, Formation, NodeStatus, Position, Route, TaskStatus, TimeWindow
from backend.app.models.phase3 import (Phase3ExecutionTrace, Phase3Timing, ReconstructionProposal,
    RouteChange, SolverStatus, StrategyLevel)
from backend.app.models.phase4 import ValidationStatus
from backend.app.reconstruction.common import digest
from backend.app.reconstruction.cost import DisruptionCostEvaluator, describe_delta
from backend.app.reconstruction.engine import ReconstructionError
from backend.app.reconstruction.materializer import ProposalMaterializer
from backend.app.reconstruction.planner import HierarchicalReconstructionPlanner
from backend.app.reconstruction.scope import ReconstructionScopeBuilder
from backend.app.reconstruction.validator import GlobalConstraintValidator
from backend.app.scenarios.manager import ScenarioManager
from scripts.phase3_scenarios import small_scenario


def manual_proposal(before, after):
    """Describe an arbitrary (possibly bad) candidate, without asking a solver."""
    tasks, formations, nodes = describe_delta(before, after)
    old_routes = {r.id: r for r in before.routes}
    routes = []
    for route in after.routes:
        if route == old_routes.get(route.id):
            continue
        task = next(t for t in after.tasks if t.route_id == route.id)
        routes.append(RouteChange(task_id=task.id, route_id=route.id, before=old_routes.get(route.id), after=route,
            reason_codes=["TEST"], path_length=sum(hypot(a.x-b.x, a.y-b.y) for a,b in zip(route.waypoints, route.waypoints[1:])),
            deviation_cost=0))
    scope = ReconstructionScopeBuilder().build(before)
    scope.affected_tasks = sorted(set(scope.affected_tasks) | {t.task_id for t in tasks} | {r.task_id for r in routes})
    scope.affected_formations = sorted(set(scope.affected_formations) | {f.formation_id for f in formations} | {t.after_formation for t in tasks})
    scope.affected_nodes = [n.node_id for n in nodes]
    scope.potentially_affected_routes = sorted(set(scope.potentially_affected_routes) | {r.route_id for r in routes})
    for kind, ids in [("task", scope.affected_tasks), ("formation", scope.affected_formations),
                      ("node", scope.affected_nodes), ("route", scope.potentially_affected_routes)]:
        scope.scope_expansion_reason.update({f"{kind}:{key}": "EXPLICIT_TEST_EXPANSION" for key in ids})
    level = (StrategyLevel.FORMATION_RECONSTRUCTION if any(f.before is None for f in formations) else
             StrategyLevel.TASK_REASSIGNMENT if any(t.before_formation != t.after_formation for t in tasks) else
             StrategyLevel.IN_PLACE_REPAIR if formations else None)
    return ReconstructionProposal(proposal_id=str(uuid4()), base_state_version=before.version, base_state_digest=digest(before),
        source_events=[], reconstruction_scope=scope, strategy_level=level,
        strategy_name="HIERARCHICAL_BOUNDED_BACKTRACKING" if scope.affected_tasks else "NO_CHANGE_REQUIRED",
        task_changes=tasks, formation_changes=formations, node_changes=nodes, route_changes=routes,
        recovered_constraints=[], remaining_constraints=[], cost_breakdown=DisruptionCostEvaluator().evaluate(before,after,routes),
        solver_status=SolverStatus.FEASIBLE, solver_time_ms=0, search_complete=True, explanations=["MANUAL_ADVERSARIAL_PROPOSAL"],
        trace=Phase3ExecutionTrace(requirements=[],candidate_sets=[],strategy_attempts=[],reservations=[],route_decisions=[],
            route_results=[],selected_strategies=[],backtracks=0,branches_explored=0,timing=Phase3Timing()))


def codes(result):
    return {c.code for c in result.hard_failures}


def validate_unchanged(state, proposal, expected):
    original = state.model_dump_json()
    result = GlobalConstraintValidator().validate_proposal(state, proposal)
    assert result.status == ValidationStatus.FAIL
    assert expected in codes(result), result.model_dump_json()
    assert state.model_dump_json() == original
    return result


def healthy():
    state = small_scenario()
    state.nodes[0].status = NodeStatus.NORMAL
    return state


@pytest.mark.parametrize("kind", ["repair", "reassignment", "new_formation"])
def test_all_three_strategy_proposals_independently_validated(kind):
    state = small_scenario(kind)
    proposal = HierarchicalReconstructionPlanner().propose(state)
    result = GlobalConstraintValidator().validate_proposal(state, proposal)
    assert result.status == ValidationStatus.PASS_WITH_LIMITATIONS, result.hard_failures
    assert result.metrics.validated_tasks == 1
    assert result.metrics.not_evaluated_count == 5
    assert {c.constraint for c in result.coverage if c.status == "VALIDATED"} >= {"capability", "resource", "route", "metadata"}


def test_failed_node_reintroduced():
    state = small_scenario()
    state.formations[0].node_ids = []
    candidate = state.model_copy(deep=True)
    candidate.formations[0].node_ids = ["U0"]
    validate_unchanged(state, manual_proposal(state,candidate), "FAILED_NODE_ASSIGNED")


def test_node_in_two_formations():
    state = healthy()
    candidate = state.model_copy(deep=True)
    candidate.formations.append(Formation(id="F_DUP", node_ids=["U0"]))
    validate_unchanged(state, manual_proposal(state,candidate), "NODE_MULTIPLE_FORMATIONS")


@pytest.mark.parametrize("violation,expected", [
    ("capability", "CAPABILITY_SHORTAGE"), ("formation", "FORMATION_CAPABILITY_SHORTAGE"),
    ("count", "MINIMUM_NODE_COUNT"), ("availability", "MEMBER_UNAVAILABLE"),
    ("deadline", "TASK_DEADLINE_EXPIRED"), ("failed", "FAILED_NODE_ASSIGNED"),
])
def test_feasible_label_does_not_bypass_full_mission_checks(violation, expected):
    state = healthy()
    if violation == "capability": state.tasks[0].requirements = {"relay": 2}
    if violation == "formation": state.formations[0].minimum_capabilities = {"sensor": 1}
    if violation == "count": state.tasks[0].min_nodes = 2
    if violation == "availability": state.nodes[0].availability.end = 50
    if violation == "deadline": state.clock = 100
    if violation == "failed": state.nodes[0].status = NodeStatus.FAILED
    proposal = manual_proposal(state, state)
    assert proposal.solver_status == SolverStatus.FEASIBLE
    validate_unchanged(state,proposal,expected)


@pytest.mark.parametrize("overlap", [True, False])
def test_shared_resource_and_half_open_time_windows(overlap):
    state = healthy()
    state.nodes[0].resource_remaining = 10
    state.nodes[0].availability.end = 300
    state.tasks[0].resource_required = 6
    other = state.tasks[0].model_copy(deep=True)
    other.id, other.route_id = "T_OTHER", "R_OTHER"
    other.window = TimeWindow(start=0 if overlap else 100, end=200)
    state.tasks.append(other)
    state.routes.append(Route(id="R_OTHER", waypoints=state.routes[0].waypoints))
    result = validate_unchanged(state, manual_proposal(state,state), "SHARED_RESOURCE_OVERCOMMITTED")
    assert ("TASK_TIME_CONFLICT" in codes(result)) == overlap
    assert result.metrics.validated_tasks == 2


@pytest.mark.parametrize("boundary", [False, True])
def test_continuous_collision_in_unchanged_route(boundary):
    state = healthy()
    state.environment = [EnvironmentConstraint(id="Z", lower=Position(x=19,y=0), upper=Position(x=21,y=10))]
    if not boundary:
        state.tasks[0].start.y = state.tasks[0].target.y = 1
        state.routes[0].waypoints = [state.tasks[0].start, state.tasks[0].target]
    result = validate_unchanged(state, manual_proposal(state,state), "ROUTE_RESTRICTED_AREA_COLLISION")
    assert result.route_results["T0"][-1].details["region_ids"] == ["Z"]


def test_endpoint_mismatch_and_nonexistent_formation():
    state = healthy()
    candidate = state.model_copy(deep=True)
    candidate.routes[0].waypoints[-1] = Position(x=50,y=0)
    validate_unchanged(state, manual_proposal(state,candidate), "ROUTE_ENDPOINT_MISMATCH")
    proposal = manual_proposal(state,state)
    from backend.app.models.phase3 import TaskAssignmentChange
    proposal.task_changes = [TaskAssignmentChange(task_id="T0",before_formation="F0",after_formation="F_UNKNOWN",
        before_start=state.tasks[0].start,after_start=state.tasks[0].start,reason_codes=["TEST"])]
    validate_unchanged(state,proposal,"UNKNOWN_FORMATION")


def test_undeclared_scope_and_unexplained_expansion():
    state = small_scenario()
    proposal = HierarchicalReconstructionPlanner().propose(state)
    proposal.reconstruction_scope.affected_formations = []
    validate_unchanged(state,proposal,"OUT_OF_SCOPE_MUTATION")
    proposal = HierarchicalReconstructionPlanner().propose(state)
    proposal.reconstruction_scope.scope_expansion_reason = {}
    validate_unchanged(state,proposal,"OUT_OF_SCOPE_MUTATION")


def test_out_of_scope_task_and_unrelated_task_regression():
    state = healthy()
    state.formations.append(Formation(id="F_OTHER",node_ids=["U1"]))
    other = state.tasks[0].model_copy(deep=True)
    other.id, other.formation_id, other.route_id = "T_OTHER", "F_OTHER", "R_OTHER"
    state.tasks.append(other)
    state.routes.append(Route(id="R_OTHER",waypoints=state.routes[0].waypoints))
    candidate = state.model_copy(deep=True)
    candidate.formations[0].node_ids.append("U1")
    candidate.formations[1].node_ids = []
    proposal = manual_proposal(state,candidate)
    proposal.reconstruction_scope.affected_tasks = ["T0"]
    result = validate_unchanged(state,proposal,"UNRELATED_TASK_REGRESSION")
    assert "CAPABILITY_SHORTAGE" in codes(result)
    candidate = state.model_copy(deep=True)
    candidate.tasks[1].formation_id = "F0"
    proposal = manual_proposal(state,candidate)
    proposal.reconstruction_scope.affected_tasks = ["T0"]
    validate_unchanged(state,proposal,"OUT_OF_SCOPE_MUTATION")


@pytest.mark.parametrize("field", ["route_count", "lexicographic", "strategy", "profile", "node_delta", "before_image"])
def test_forged_delta_metadata(field):
    state = small_scenario()
    proposal = HierarchicalReconstructionPlanner().propose(state)
    if field == "route_count": proposal.cost_breakdown.route_change_count = 1
    if field == "lexicographic": proposal.cost_breakdown.lexicographic_cost = [0]*8
    if field == "strategy": proposal.strategy_level = StrategyLevel.TASK_REASSIGNMENT
    if field == "profile": proposal.formation_changes[0].result_capabilities = {"relay": 999}
    if field == "node_delta": proposal.node_changes = []
    if field == "before_image": proposal.formation_changes[0].before.node_ids = []
    validate_unchanged(state,proposal,"DELTA_MISMATCH" if field in {"node_delta","before_image"} else "PROPOSAL_METADATA_MISMATCH")


@pytest.mark.parametrize("field,expected", [("base_state_version","STATE_VERSION_MISMATCH"),("base_state_digest","STATE_DIGEST_MISMATCH")])
def test_stale_preconditions_skip_materialization(field,expected,monkeypatch):
    state=small_scenario()
    proposal=HierarchicalReconstructionPlanner().propose(state)
    setattr(proposal,field,42 if field=="base_state_version" else "fake")
    def forbidden(*args): raise AssertionError("stale proposal must not be materialized")
    monkeypatch.setattr(ProposalMaterializer,"materialize",forbidden)
    result=validate_unchanged(state,proposal,expected)
    assert result.candidate_state_digest is None
    assert result.metrics.validated_tasks == 0


def test_validator_does_not_call_solver_or_assessment(monkeypatch):
    state=small_scenario()
    proposal=HierarchicalReconstructionPlanner().propose(state)
    def forbidden(*args,**kwargs): raise AssertionError("Validator must recompute independently")
    monkeypatch.setattr(HierarchicalReconstructionPlanner,"propose",forbidden)
    monkeypatch.setattr(TaskAssessmentEngine,"assess",forbidden)
    assert GlobalConstraintValidator().validate_proposal(state,proposal).status==ValidationStatus.PASS_WITH_LIMITATIONS


@pytest.mark.parametrize("mutation", ["status", "target", "clock", "environment", "hidden_route"])
def test_validator_allowlist_catches_materializer_contract_breach(mutation,monkeypatch):
    state=small_scenario()
    proposal=HierarchicalReconstructionPlanner().propose(state)
    original=ProposalMaterializer.materialize
    def corrupt(self,observed,p):
        candidate=original(self,observed,p)
        if mutation=="status": candidate.nodes[0].status=NodeStatus.NORMAL
        if mutation=="target": candidate.tasks[0].target=Position(x=50,y=0)
        if mutation=="clock": candidate.clock=1
        if mutation=="environment": candidate.environment=[EnvironmentConstraint(id="Z",lower=Position(x=90,y=90),upper=Position(x=91,y=91))]
        if mutation=="hidden_route": candidate.routes[0].waypoints.insert(1,Position(x=20,y=0))
        return candidate
    monkeypatch.setattr(ProposalMaterializer,"materialize",corrupt)
    validate_unchanged(state,proposal,"DELTA_MISMATCH" if mutation=="hidden_route" else "OBSERVED_FACT_MUTATION")


def test_preserves_formation_minimum_requirements():
    state=small_scenario()
    state.formations[0].minimum_capabilities={"navigation":1}
    candidate=state.model_copy(deep=True)
    candidate.formations[0].minimum_capabilities={}
    validate_unchanged(state,manual_proposal(state,candidate),"OBSERVED_FACT_MUTATION")


def test_more_remaining_resource_is_not_a_penalty():
    state=small_scenario()
    state.nodes[1].id="A_RICH"
    state.nodes[1].resource_remaining=1000
    other=state.nodes[1].model_copy(deep=True)
    other.id,other.resource_remaining="Z_POOR",2
    state.nodes.append(other)
    proposal=HierarchicalReconstructionPlanner().propose(state)
    assert proposal.formation_changes[0].added_nodes==["A_RICH"]
    assert proposal.cost_breakdown.resource_cost==proposal.cost_breakdown.resource_usage_cost==0


def test_route_structure_outranks_distance_and_node_switch_cost():
    state=healthy()
    state.nodes[1].position=Position(x=99,y=99)
    far=state.model_copy(deep=True)
    far.formations[0].node_ids=["U1"]
    near=state.model_copy(deep=True)
    near.nodes[1].position=Position(x=0,y=0)
    near.formations[0].node_ids=["U1"]
    far_cost=DisruptionCostEvaluator().evaluate(state,far,[])
    near_baseline=state.model_copy(deep=True)
    near_baseline.nodes[1].position=Position(x=0,y=0)
    near_cost=DisruptionCostEvaluator().evaluate(near_baseline,near,[object()])
    assert far_cost.distance_cost > near_cost.distance_cost
    assert far_cost.lexicographic_cost < near_cost.lexicographic_cost


@pytest.mark.parametrize("count", [1,2])
def test_sc01_atomic_closed_loop(scenario_dir,count):
    manager=ScenarioManager(scenario_dir)
    session=manager.load("SC01")
    for i in range(count): manager.inject(session.session_id,i,session.events[i])
    before=manager.get(session.session_id).state
    history=manager.assessment(session.session_id)
    result=manager.reconstruct(session.session_id,count)
    assert result.committed and result.state.version==count+1
    assert result.validation.status==ValidationStatus.PASS_WITH_LIMITATIONS
    assert result.validation.metrics.validated_tasks==8
    assert result.validation.metrics.validated_formations==6
    assert result.validation.metrics.validated_routes==8
    assert result.state.nodes==before.nodes and result.state.environment==before.environment
    assert result.state.clock==before.clock and result.state.tasks==before.tasks and result.state.routes==before.routes
    assert manager.assessment(session.session_id)==history
    assert all(c.status!="FAIL" for a in TaskAssessmentEngine().assess(result.state,full_check=True) for c in a.constraints)
    for old in before.formations:
        if old.id not in {"F03","F04"}:
            assert next(f for f in result.state.formations if f.id==old.id)==old
    added=[n for f in result.proposal.formation_changes for n in f.added_nodes]
    assert len(set(added))==len(added)==count
    assert result.receipt.validation_id==result.validation.validation_id
    assert result.receipt.committed_state_digest==digest(result.state)
    assert len(result.event_traces)==count
    assert all(getattr(result.metrics,k)>0 for k in type(result.metrics).model_fields)
    stored=manager.reconstruction(result.reconstruction_id)
    assert stored==result
    result.state.nodes[0].health=0
    assert manager.get(session.session_id).state.nodes[0].health==1


def prepared(scenario_dir):
    manager=ScenarioManager(scenario_dir)
    session=manager.load("SC01")
    manager.inject(session.session_id,0,session.events[0])
    return manager,session,manager.propose(session.session_id,1)


def test_formation_valid_route_invalid_rolls_back_entire_transaction(scenario_dir):
    manager,session,proposal=prepared(scenario_dir)
    state=manager.get(session.session_id).state
    candidate=ProposalMaterializer().materialize(state,proposal)
    next(r for r in candidate.routes if r.id=="R03").waypoints[-1]=Position(x=900,y=340)
    malicious=manual_proposal(state,candidate)
    with pytest.raises(ReconstructionError) as error: manager.commit(session.session_id,malicious)
    assert error.value.code=="VALIDATION_FAILED"
    assert "ROUTE_ENDPOINT_MISMATCH" in codes(error.value.validation)
    assert manager.get(session.session_id).state==state
    assert digest(manager.get(session.session_id).state)==digest(state)
    assert manager.commit(session.session_id,proposal).committed_version==2


def test_commit_exception_before_publication_is_atomic(scenario_dir,monkeypatch):
    import backend.app.reconstruction.engine as engine
    manager,session,proposal=prepared(scenario_dir)
    before=manager.get(session.session_id)
    def fail(*args,**kwargs): raise RuntimeError("simulated audit construction failure")
    with monkeypatch.context() as patch:
        patch.setattr(engine,"ReconstructionCommitReceipt",fail)
        with pytest.raises(RuntimeError): manager.commit(session.session_id,proposal)
    assert manager.get(session.session_id)==before
    assert manager.commit(session.session_id,proposal).committed_version==2


def test_idempotent_commit_conflict_and_replay_after_later_event(scenario_dir):
    manager,session,proposal=prepared(scenario_dir)
    receipt=manager.commit(session.session_id,proposal)
    replay=manager.commit(session.session_id,proposal)
    assert replay.replayed and replay.commit_id==receipt.commit_id
    assert replay.model_copy(update={"replayed":False})==receipt
    assert manager.get(session.session_id).state.version==2
    altered=proposal.model_copy(deep=True)
    altered.explanations.append("different content")
    with pytest.raises(ReconstructionError) as error: manager.commit(session.session_id,altered)
    assert error.value.code=="PROPOSAL_ID_CONFLICT"
    manager.inject(session.session_id,2,session.events[1])
    assert manager.commit(session.session_id,proposal).commit_id==receipt.commit_id
    assert manager.get(session.session_id).state.version==3


def test_concurrent_commits_publish_once(scenario_dir):
    manager,session,proposal=prepared(scenario_dir)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(manager.commit,session.session_id,proposal) for _ in range(2)]
        receipts=[f.result(timeout=10) for f in futures]
    assert {r.replayed for r in receipts}=={True,False}
    assert len({r.commit_id for r in receipts})==1
    assert manager.get(session.session_id).state.version==2


def test_event_invalidates_pending_proposal_and_preserves_new_fact(scenario_dir):
    manager,session,proposal=prepared(scenario_dir)
    manager.inject(session.session_id,1,session.events[1])
    before=manager.get(session.session_id)
    with pytest.raises(ReconstructionError) as error: manager.commit(session.session_id,proposal)
    assert error.value.code=="STALE_PROPOSAL"
    assert manager.get(session.session_id)==before


def test_preview_read_only_and_failed_reconstruct_never_commits(scenario_dir,monkeypatch):
    manager,session,proposal=prepared(scenario_dir)
    before=manager.get(session.session_id)
    preview=manager.reconstruct(session.session_id,1,commit=False)
    assert not preview.committed and preview.receipt is None
    assert manager.get(session.session_id)==before
    proposal.cost_breakdown.route_change_count=99
    monkeypatch.setattr(HierarchicalReconstructionPlanner,"propose",lambda *args:proposal)
    rejected=manager.reconstruct(session.session_id,1)
    assert rejected.validation.status==ValidationStatus.FAIL and not rejected.committed
    assert manager.get(session.session_id)==before


def test_no_active_tasks_can_pass_without_physical_limitations():
    state=healthy()
    state.tasks[0].status=TaskStatus.CANCELLED
    result=GlobalConstraintValidator().validate_proposal(state,manual_proposal(state,state))
    assert result.status==ValidationStatus.PASS
    assert result.metrics.validated_tasks==0 and not result.not_evaluated


@pytest.mark.parametrize("field",["length","deviation","declaration"])
def test_route_metadata_checked_independently(field):
    state=small_scenario("reassignment")
    proposal=HierarchicalReconstructionPlanner().propose(state)
    if field=="length": proposal.route_changes[0].path_length+=1
    if field=="deviation": proposal.route_changes[0].deviation_cost+=1
    if field=="declaration": proposal.trace.route_decisions[0].replan=False
    validate_unchanged(state,proposal,"PROPOSAL_METADATA_MISMATCH")


def test_failed_node_cannot_get_new_role_in_idle_formation():
    state=small_scenario()
    state.tasks[0].status=TaskStatus.CANCELLED
    candidate=state.model_copy(deep=True)
    candidate.formations[0].roles={"U0":"relay"}
    validate_unchanged(state,manual_proposal(state,candidate),"FAILED_NODE_ASSIGNED")


def test_event_and_commit_compete_for_same_version(scenario_dir):
    from threading import Barrier
    from backend.app.events.injector import EventApplicationError
    manager,session,proposal=prepared(scenario_dir)
    barrier=Barrier(2)
    def commit():
        barrier.wait(timeout=5)
        try: return manager.commit(session.session_id,proposal)
        except ReconstructionError as exc: return exc.code
    def event():
        barrier.wait(timeout=5)
        try: return manager.inject(session.session_id,1,session.events[1])
        except EventApplicationError as exc: return exc.code.value
    with ThreadPoolExecutor(max_workers=2) as pool:
        commit_future,event_future=pool.submit(commit),pool.submit(event)
        committed,injected=commit_future.result(timeout=10),event_future.result(timeout=10)
    current=manager.get(session.session_id).state
    assert current.version==2
    if isinstance(committed,str):
        assert committed=="STALE_PROPOSAL" and not isinstance(injected,str)
        assert next(n for n in current.nodes if n.id=="U21").status==NodeStatus.FAILED
        assert "U17" in next(f for f in current.formations if f.id=="F03").node_ids
    else:
        assert injected=="VERSION_CONFLICT"
        assert next(n for n in current.nodes if n.id=="U21").status==NodeStatus.NORMAL
        assert "U17" not in next(f for f in current.formations if f.id=="F03").node_ids
