"""Hierarchical proposal search with private snapshots and bounded backtracking."""
from time import perf_counter
from uuid import uuid4

from ..models.domain import ScenarioState
from ..models.phase2 import ConstraintStatus
from ..models.phase3 import (ConstraintOutcome, Phase3ExecutionTrace, Phase3Timing, ReconstructionProposal,
    SolverStatus, StrategyAttempt, StrategyLevel, TaskRequirement)
from .common import apply_option, digest, failures, local_assessments, preview
from .config import PlannerConfig
from .cost import DisruptionCostEvaluator, describe_delta
from .formation import FormationReconstructor
from .reservations import ReservationLedger
from .routes import RouteImpactDetector, RoutePlanner
from .scope import ReconstructionScopeBuilder
from .task import TaskReconstructor


class HierarchicalReconstructionPlanner:
    def __init__(self, config=None):
        self.config = config or PlannerConfig.load()

    def propose(self, state, receipts=()):
        started = perf_counter()
        state = ScenarioState.model_validate(state.model_dump(mode="json"))
        scope = ReconstructionScopeBuilder().build(state,receipts)
        timing = Phase3Timing(scope_build_ms=(perf_counter()-started)*1000)
        task_index = {t.id:t for t in state.tasks}
        trace = Phase3ExecutionTrace(requirements=[TaskRequirement.from_task(task_index[key]) for key in scope.affected_tasks],
            candidate_sets=[],strategy_attempts=[],reservations=[],route_decisions=[],route_results=[],selected_strategies=[],
            backtracks=0,branches_explored=0,timing=timing)
        complete = True
        best = None
        best_partial = None
        costs = DisruptionCostEvaluator()
        formation_solver = FormationReconstructor(self.config)
        route_cache = {}

        def finish(working, selected):
            nonlocal best,best_partial,complete
            detect_start = perf_counter()
            requests,decisions = RouteImpactDetector().detect(working,set(scope.affected_tasks))
            timing.route_impact_ms += (perf_counter()-detect_start)*1000
            route_results,route_changes = [],[]
            for request in requests:
                key = request.model_dump_json()
                if key not in route_cache:
                    route_cache[key] = RoutePlanner(self.config).plan(working,request)
                    timing.route_planning_ms += route_cache[key].elapsed_ms
                result = route_cache[key]
                route_results.append(result)
                if result.change:
                    route_changes.append(result.change)
                if result.status == SolverStatus.PARTIAL:
                    complete = False
            candidate = preview(working,route_changes=route_changes)
            assessments = local_assessments(candidate)
            remaining = [a for a in assessments if a.task_id in scope.affected_tasks and failures(a)]
            protected_failed = any(failures(a) for a in assessments if a.task_id in scope.protected_task_ids)
            if protected_failed:
                return False
            cost = costs.evaluate(state,candidate,route_changes)
            solution = (candidate,selected,route_changes,decisions,route_results,cost,assessments)
            if not remaining:
                if best is None or cost.lexicographic_cost < best[5].lexicographic_cost:
                    best = solution
                return True
            if best_partial is None or len(remaining) < best_partial[0]:
                best_partial = (len(remaining),solution)
            return False

        def search(index,working,selected):
            nonlocal complete
            if trace.branches_explored >= self.config.max_branches:
                complete = False
                return False
            trace.branches_explored += 1
            if index == len(scope.affected_tasks):
                return finish(working,selected)
            task_id = scope.affected_tasks[index]
            task = next(t for t in working.tasks if t.id == task_id)
            current = next(a for a in local_assessments(working) if a.task_id == task_id)
            protected = set(scope.protected_task_ids) | {s.task_id for s in selected}
            # Route-only violations and non-fatal degradation need no composition change.
            if not failures(current,include_route=False):
                return search(index+1,working,selected)
            found = False
            for level in StrategyLevel:
                if level == StrategyLevel.TASK_REASSIGNMENT:
                    options = TaskReconstructor().options(working,task,protected,trace)
                    exhaustive = True
                else:
                    options,exhaustive = formation_solver.options(working,task,scope,level,protected,trace)
                complete &= exhaustive
                for option in options:
                    if trace.branches_explored >= self.config.max_branches:
                        complete = False
                        break
                    next_state = apply_option(working,option)
                    chosen = StrategyAttempt(task_id=task_id,level=level,status=SolverStatus.FEASIBLE,
                        reason_codes=["LOCAL_CONSTRAINTS_SATISFIED"],selected_candidates=option.selected_nodes,
                        formation_id=next(t.formation_id for t in next_state.tasks if t.id==task_id),search_complete=exhaustive)
                    success = search(index+1,next_state,selected+[chosen])
                    found |= success
                    if not success:
                        trace.backtracks += 1
                if found:
                    break  # escalate only if no complete continuation exists at this level
                trace.strategy_attempts.append(StrategyAttempt(task_id=task_id,level=level,
                    status=SolverStatus.INFEASIBLE if exhaustive else SolverStatus.PARTIAL,
                    reason_codes=["NO_COMPLETE_CONTINUATION_ESCALATE" if exhaustive else "SEARCH_LIMIT_ESCALATE_NOT_PROVEN_INFEASIBLE"],
                    search_complete=exhaustive))
            return found

        search_started = perf_counter()
        search(0,state,[])
        search_ms = (perf_counter()-search_started)*1000
        proposal_started = perf_counter()
        solution = best or (best_partial[1] if best_partial else None)
        if solution:
            candidate,selected,route_changes,decisions,route_results,cost,assessments = solution
            status = SolverStatus.FEASIBLE if best else SolverStatus.PARTIAL
        else:
            candidate,selected,route_changes,decisions,route_results = state,[],[],[],[]
            assessments = scope.current_assessments
            cost = costs.evaluate(state,state,[])
            status = SolverStatus.INFEASIBLE if complete else SolverStatus.PARTIAL
        task_changes,formation_changes,node_changes = describe_delta(state,candidate)
        for formation_id in sorted({f.formation_id for f in formation_changes} |
                                   {t.after_formation for t in task_changes}):
            if formation_id not in scope.affected_formations:
                scope.affected_formations.append(formation_id)
                scope.scope_expansion_reason[f"formation:{formation_id}"] = "REPAIR_REQUIRES_DESTINATION_FORMATION"
        scope.affected_nodes = sorted({n.node_id for n in node_changes})
        original_members = {n for f in state.formations if f.id in scope.affected_formations for n in f.node_ids}
        for node_id in scope.affected_nodes:
            if node_id not in original_members:
                scope.scope_expansion_reason[f"node:{node_id}"] = "ELIGIBLE_SPARE_SELECTED_FOR_REPAIR"
        for change in route_changes:
            if change.route_id not in scope.potentially_affected_routes:
                scope.potentially_affected_routes.append(change.route_id)
                scope.scope_expansion_reason[f"route:{change.route_id}"] = "REPAIR_REQUIRES_NEW_ROUTE"
        trace.selected_strategies=selected
        trace.route_decisions=decisions
        trace.route_results=route_results
        trace.reservations=ReservationLedger(candidate).reservations
        before_checks = {(a.task_id,c.type):c for a in scope.current_assessments for c in a.constraints}
        remaining,recovered=[],[]
        for assessment in assessments:
            if assessment.task_id not in scope.affected_tasks:
                continue
            for check in assessment.constraints:
                if check.status in {ConstraintStatus.FAIL,ConstraintStatus.DEGRADED}:
                    remaining.append(ConstraintOutcome(task_id=assessment.task_id,check=check))
                if before_checks[(assessment.task_id,check.type)].status == ConstraintStatus.FAIL and check.status != ConstraintStatus.FAIL:
                    recovered.append(ConstraintOutcome(task_id=assessment.task_id,check=check))
        if status == SolverStatus.FEASIBLE and remaining:
            status=SolverStatus.PARTIAL
        level = max((s.level for s in selected),key=lambda x:list(StrategyLevel).index(x),default=None)
        proposal = ReconstructionProposal(proposal_id=str(uuid4()),base_state_version=state.version,base_state_digest=digest(state),
            source_events=scope.source_events,reconstruction_scope=scope,strategy_level=level,
            strategy_name="HIERARCHICAL_BOUNDED_BACKTRACKING" if scope.affected_tasks else "NO_CHANGE_REQUIRED",
            task_changes=task_changes,formation_changes=formation_changes,node_changes=node_changes,route_changes=route_changes,
            recovered_constraints=recovered,remaining_constraints=remaining,cost_breakdown=cost,solver_status=status,
            solver_time_ms=0,search_complete=complete,
            explanations=["CURRENT_STATE_IS_SOURCE_OF_TRUTH","PROTECTED_ASSIGNMENTS_NOT_STOLEN",
                          "LEXICOGRAPHIC_COST_NO_WEIGHTED_SCALAR","NO_GLOBAL_OPTIMALITY_CLAIM",
                          "RENDEZVOUS_TRAVEL_TIME_NOT_MODELED",
                          "PHASE4_VALIDATION_AND_COMMIT_REQUIRED"] + ([] if complete else ["BOUNDED_SEARCH_LIMIT_REACHED"]),trace=trace)
        timing.proposal_build_ms=(perf_counter()-proposal_started)*1000
        timing.total_phase3_ms=(perf_counter()-started)*1000
        timing.coordination_ms=max(0,timing.total_phase3_ms-sum(getattr(timing,name) for name in type(timing).model_fields
                                                            if name not in {"total_phase3_ms","coordination_ms"}))
        proposal.solver_time_ms=search_ms
        return proposal
