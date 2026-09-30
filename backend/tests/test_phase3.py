import pytest

from backend.app.assessment.service import process_event
from backend.app.events.injector import apply_event
from backend.app.models.domain import EnvironmentConstraint, Position, Route, Formation
from backend.app.models.events import RestrictedAreaAdd, RestrictedAreaRemove
from backend.app.models.phase3 import ReconstructionProposal, SolverStatus, StrategyLevel, TaskRequirement
from backend.app.reconstruction.candidates import CandidateGenerator
from backend.app.reconstruction.common import failures, local_assessments, preview
from backend.app.reconstruction.config import PlannerConfig
from backend.app.reconstruction.planner import HierarchicalReconstructionPlanner
from backend.app.reconstruction.reservations import ReservationLedger
from backend.app.reconstruction.routes import RouteImpactDetector, RoutePlanner
from backend.app.reconstruction.scope import ReconstructionScopeBuilder
from scripts.phase3_scenarios import small_scenario


def materialize(state, proposal):
    return preview(state,proposal.task_changes,proposal.formation_changes,proposal.route_changes)


def assert_local_checks(state):
    assert all(not failures(a) for a in local_assessments(state))


def test_single_gap_in_place_no_commit_no_route_call(monkeypatch):
    state=small_scenario()
    saved=state.model_dump_json()
    def forbidden(*args):
        raise AssertionError("RoutePlanner must not run for an unchanged legal route")
    monkeypatch.setattr(RoutePlanner,"plan",forbidden)
    proposal=HierarchicalReconstructionPlanner().propose(state)
    assert proposal.solver_status==SolverStatus.FEASIBLE
    assert proposal.strategy_level==StrategyLevel.IN_PLACE_REPAIR
    assert proposal.task_changes==proposal.route_changes==[]
    assert proposal.formation_changes[0].added_nodes==["U1"]
    assert proposal.formation_changes[0].removed_nodes==["U0"]
    assert proposal.trace.route_decisions[0].reason_codes==["EXISTING_ROUTE_STILL_VALID"]
    assert state.model_dump_json()==saved
    assert not proposal.committed and proposal.global_validation=="NOT_PERFORMED"
    assert_local_checks(materialize(state,proposal))
    assert ReconstructionProposal.model_validate_json(proposal.model_dump_json())==proposal


def test_candidate_choice_uses_cost_not_node_id():
    state=small_scenario()
    state.nodes[1].position=Position(x=90,y=90)
    nearer=state.nodes[1].model_copy(deep=True)
    nearer.id="Z_LAST"
    nearer.position=Position(x=1,y=0)
    state.nodes.append(nearer)
    p=HierarchicalReconstructionPlanner().propose(state)
    assert p.formation_changes[0].added_nodes==["Z_LAST"]
    assert p.cost_breakdown.distance_cost==1
    assert any(c.node_id=="U1" for c in p.trace.candidate_sets[0].candidates)


def test_conflicting_candidate_rejected_and_protected_plan_preserved():
    state=small_scenario()
    state.formations.append(Formation(id="F_OTHER",node_ids=["U1"]))
    other=state.tasks[0].model_copy(deep=True)
    other.id,other.formation_id,other.route_id="OTHER","F_OTHER","OTHER_ROUTE"
    state.tasks.append(other)
    state.routes.append(Route(id="OTHER_ROUTE",waypoints=state.routes[0].waypoints))
    p=HierarchicalReconstructionPlanner().propose(state)
    assert p.solver_status==SolverStatus.INFEASIBLE
    assert p.task_changes==p.formation_changes==[]
    assert any(r.node_id=="U1" and r.reason_code=="SCHEDULE_CONFLICT"
               for cs in p.trace.candidate_sets for r in cs.rejected_candidates)


@pytest.mark.parametrize("kind,expected",[("reassignment",StrategyLevel.TASK_REASSIGNMENT),
                                         ("new_formation",StrategyLevel.FORMATION_RECONSTRUCTION)])
def test_strategy_escalation(kind,expected):
    state=small_scenario(kind)
    p=HierarchicalReconstructionPlanner().propose(state)
    assert p.solver_status==SolverStatus.FEASIBLE
    assert p.strategy_level==expected
    assert p.task_changes[0].before_formation=="F0"
    assert p.task_changes[0].after_formation!="F0"
    assert any("NO_COMPLETE_CONTINUATION_ESCALATE" in a.reason_codes for a in p.trace.strategy_attempts)
    assert p.route_changes  # reference point really moved
    assert_local_checks(materialize(state,p))


def test_infeasible_does_not_invent_nodes_or_relax_requirements():
    state=small_scenario("impossible")
    saved=state.model_dump_json()
    p=HierarchicalReconstructionPlanner().propose(state)
    assert p.solver_status==SolverStatus.INFEASIBLE
    assert p.remaining_constraints
    assert not p.formation_changes and not p.task_changes
    assert state.model_dump_json()==saved


def test_sc01_outstanding_scope_joint_reservations_and_locality(definition):
    receipts=[]
    state=definition.initial_state
    for event in definition.events:
        receipt=process_event(state,event)
        receipts.append(receipt)
        state=receipt.application.state
    saved=state.model_dump_json()
    p=HierarchicalReconstructionPlanner().propose(state,receipts)
    assert set(p.reconstruction_scope.affected_tasks)=={"T03","T04"}
    assert set(p.source_events)=={"E001","E002"}
    assert p.solver_status==SolverStatus.FEASIBLE
    additions=[n for f in p.formation_changes for n in f.added_nodes]
    assert len(additions)==len(set(additions))==2
    assert not p.task_changes and not p.route_changes
    candidate=materialize(state,p)
    assert candidate.tasks==state.tasks
    assert candidate.routes==state.routes
    for before in state.formations:
        if before.id not in p.reconstruction_scope.affected_formations:
            assert next(f for f in candidate.formations if f.id==before.id)==before
    assert state.model_dump_json()==saved
    assert_local_checks(candidate)
    ledger=ReservationLedger(candidate)
    assert len({ledger.owners[n] for n in additions})==2


def test_joint_backtracking_escapes_greedy_resource_trap():
    state=small_scenario()
    state.nodes[1].capabilities={"relay":1,"sensor":1}
    state.nodes[1].position=Position(x=1,y=0)
    spare=state.nodes[1].model_copy(deep=True)
    spare.id="U2"
    spare.capabilities={"relay":1}
    spare.position=Position(x=30,y=0)
    state.nodes.append(spare)
    failed=state.nodes[0].model_copy(deep=True)
    failed.id="U9"
    failed.capabilities={"sensor":1}
    state.nodes.append(failed)
    state.formations.append(Formation(id="F9",node_ids=["U9"]))
    task=state.tasks[0].model_copy(deep=True)
    task.id,task.formation_id,task.route_id,task.priority="T9","F9","R9",3
    task.requirements={"sensor":1}
    state.tasks.append(task)
    state.routes.append(Route(id="R9",waypoints=state.routes[0].waypoints))
    p=HierarchicalReconstructionPlanner().propose(state)
    assert p.solver_status==SolverStatus.FEASIBLE
    changes={f.formation_id:f for f in p.formation_changes}
    assert changes["F0"].added_nodes==["U2"]
    assert changes["F9"].added_nodes==["U1"]
    assert p.trace.backtracks>0
    assert_local_checks(materialize(state,p))


def test_in_place_preferred_to_available_reassignment():
    state=small_scenario()
    node=state.nodes[1].model_copy(deep=True)
    node.id="U_ALT"
    node.position=Position(x=0,y=0)
    state.nodes.append(node)
    state.formations.append(Formation(id="F_ALT",node_ids=["U_ALT"]))
    p=HierarchicalReconstructionPlanner().propose(state)
    assert p.strategy_level==StrategyLevel.IN_PLACE_REPAIR
    assert p.cost_breakdown.task_reassignment_count==0


def test_stale_assessment_does_not_preserve_removed_region_gap(definition):
    region=EnvironmentConstraint(id="Z",lower=Position(x=400,y=339),upper=Position(x=500,y=341))
    add=process_event(definition.initial_state,RestrictedAreaAdd(event_id="ADD",occurred_at=10,region=region))
    remove=process_event(add.application.state,RestrictedAreaRemove(event_id="REMOVE",occurred_at=11,region_id="Z"))
    p=HierarchicalReconstructionPlanner().propose(remove.application.state,[add,remove])
    assert p.reconstruction_scope.affected_tasks==[]
    assert p.solver_status==SolverStatus.FEASIBLE
    assert not p.task_changes and not p.route_changes and not p.formation_changes


def test_restricted_region_replans_only_conflicting_route(definition):
    region=EnvironmentConstraint(id="Z",lower=Position(x=400,y=330),upper=Position(x=500,y=350))
    state=apply_event(definition.initial_state,RestrictedAreaAdd(event_id="ADD",occurred_at=10,region=region)).state
    p=HierarchicalReconstructionPlanner().propose(state)
    assert p.solver_status==SolverStatus.FEASIBLE
    assert [r.route_id for r in p.route_changes]==["R03"]
    assert p.task_changes==p.formation_changes==[]
    assert p.trace.route_results[0].expanded_cells>0
    candidate=materialize(state,p)
    assert_local_checks(candidate)
    for route in candidate.routes:
        if route.id!="R03":
            assert route==next(r for r in state.routes if r.id==route.id)


@pytest.mark.parametrize("region",[
    EnvironmentConstraint(id="Z",lower=Position(x=0,y=0),upper=Position(x=1,y=1)),
    EnvironmentConstraint(id="Z",lower=Position(x=19,y=0),upper=Position(x=21,y=100)),
])
def test_blocked_endpoint_or_unreachable_route_is_not_feasible(region):
    state=small_scenario()
    state.nodes[0].status="NORMAL"
    state.environment.append(region)
    p=HierarchicalReconstructionPlanner().propose(state)
    assert p.solver_status in {SolverStatus.INFEASIBLE,SolverStatus.PARTIAL}
    assert p.remaining_constraints
    assert not p.route_changes


def test_budget_exhaustion_not_claimed_infeasible():
    state=small_scenario()
    p=HierarchicalReconstructionPlanner(PlannerConfig(max_subsets=1,max_branches=1)).propose(state)
    assert not p.search_complete
    assert p.solver_status==SolverStatus.PARTIAL
    assert not p.optimality_proven


def test_requirement_is_derived_independently_of_formation():
    task=small_scenario().tasks[0]
    requirement=TaskRequirement.from_task(task)
    task.formation_id="CHANGED"
    task.requirements["relay"]=9
    assert requirement.required_capabilities=={"relay":1}
    assert not hasattr(requirement,"formation_id")


def test_candidate_availability_and_resource_shortage_are_hard_constraints():
    state=small_scenario()
    state.nodes[1].availability.end=50
    p=HierarchicalReconstructionPlanner().propose(state)
    assert p.solver_status==SolverStatus.INFEASIBLE
    assert any(r.reason_code=="AVAILABILITY_WINDOW" for cs in p.trace.candidate_sets for r in cs.rejected_candidates)
    state=small_scenario()
    state.nodes[1].resource_remaining=0.5
    p=HierarchicalReconstructionPlanner().propose(state)
    assert p.solver_status==SolverStatus.INFEASIBLE
    assert p.remaining_constraints


def test_scope_uses_actual_priority_and_deadline_not_id(definition):
    state=definition.initial_state
    for event in definition.events:
        state=apply_event(state,event).state
    next(t for t in state.tasks if t.id=="T04").priority=5
    assert ReconstructionScopeBuilder().build(state).affected_tasks==["T04","T03"]


def test_route_non_grid_endpoints_are_exact_and_continuously_valid():
    from backend.app.assessment.facts import route_intersects
    state=small_scenario()
    state.nodes[0].status="NORMAL"
    state.tasks[0].start=Position(x=1.2,y=1.3)
    state.tasks[0].target=Position(x=39.2,y=1.3)
    region=EnvironmentConstraint(id="Z",lower=Position(x=19,y=0),upper=Position(x=21,y=10))
    state.environment.append(region)
    p=HierarchicalReconstructionPlanner().propose(state)
    assert p.solver_status==SolverStatus.FEASIBLE
    route=p.route_changes[0].after
    assert route.waypoints[0]==state.tasks[0].start and route.waypoints[-1]==state.tasks[0].target
    assert all(state.bounds.contains(point) for point in route.waypoints)
    assert not route_intersects(route,region)


def test_timing_components_account_for_total():
    p=HierarchicalReconstructionPlanner().propose(small_scenario())
    timing=p.trace.timing
    assert sum(getattr(timing,name) for name in type(timing).model_fields if name!="total_phase3_ms")==pytest.approx(timing.total_phase3_ms)


def test_candidate_can_supply_gap_not_whole_task(definition):
    state=apply_event(definition.initial_state,definition.events[0]).state
    scope=ReconstructionScopeBuilder().build(state)
    task=next(t for t in state.tasks if t.id=="T03")
    candidates=CandidateGenerator().generate(state,TaskRequirement.from_task(task),scope,ReservationLedger(state),
                                             {"relay":1},StrategyLevel.IN_PLACE_REPAIR)
    assert candidates.candidates
    assert all(c.capabilities.get("relay",0)>0 for c in candidates.candidates)
    assert all(not c.capabilities.get("sensor") for c in candidates.candidates)
    assert candidates.stages[0].remaining==60
