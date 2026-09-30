"""Private plan assembly. Reuses P3 delta/cost/facts; never chooses a strategy."""
from math import hypot
from uuid import uuid4

from ..assessment.facts import distance_to_route
from ..models.domain import TaskStatus
from ..models.phase2 import ConstraintStatus
from ..models.phase3 import (ConstraintOutcome, Phase3ExecutionTrace, Phase3Timing, ReconstructionProposal,
                             RouteChange, SolverStatus, StrategyLevel)
from ..reconstruction.common import digest
from ..reconstruction.cost import DisruptionCostEvaluator, describe_delta
from ..reconstruction.materializer import MaterializationError, ProposalMaterializer
from ..reconstruction.engine import ReconstructionError
from ..reconstruction.primitives import ReconstructionPrimitives
from ..reconstruction.reservations import ReservationLedger
from ..reconstruction.scope import ReconstructionScopeBuilder


def empty_trace():
    return Phase3ExecutionTrace(requirements=[], candidate_sets=[], strategy_attempts=[], reservations=[],
        route_decisions=[], route_results=[], selected_strategies=[], backtracks=0, branches_explored=0, timing=Phase3Timing())


class AgentWorkspace:
    def __init__(self, store, session_id, primitives=None):
        self.store, self.session_id = store, session_id
        self.tools = primitives or ReconstructionPrimitives(store)
        self.load_current()

    def load_current(self):
        self.working_revision = getattr(self,"working_revision",-1)+1
        # State + event evidence are captured together; no mixed-version context.
        with self.store._lock:
            self.base = self.store.get(self.session_id).state
            self.reports = [r.model_copy(deep=True) for r in self.store._sessions[self.session_id].reports.values()]
        self.working = self.base.model_copy(deep=True)
        self.scope = ReconstructionScopeBuilder().build(self.base, self.reports)
        violations = self.tools.get_outstanding_violations(self.base)
        affected = set(self.scope.affected_tasks)
        for task in self.base.tasks:
            if task.status == TaskStatus.ACTIVE and any(v.subject in {task.id, task.formation_id} for v in violations):
                affected.add(task.id)
        self.scope.affected_tasks = sorted(affected)
        self.scope.protected_task_ids = [t for t in self.scope.protected_task_ids if t not in affected]
        self.scope.affected_formations = sorted({t.formation_id for t in self.base.tasks if t.id in affected and t.formation_id})
        self.scope.potentially_affected_routes = sorted({t.route_id for t in self.base.tasks if t.id in affected and t.route_id})
        self.trace = empty_trace()
        self.proposal = None
        self.validation = None
        self.validated_fingerprint = None
        self.last_failed_plan_digest = None
        self.candidate_summaries = {}
        self.route_requests = {}

    def install_initial(self, proposal):
        if proposal.base_state_version != self.base.version or proposal.base_state_digest != digest(self.base):
            raise ReconstructionError("STALE_PROPOSAL", "Initial proposal is stale")
        try:
            self.working = ProposalMaterializer().materialize(self.base, proposal)
        except MaterializationError as exc:
            raise ReconstructionError(exc.code, "Initial proposal cannot be materialized", 422) from None
        self.scope = proposal.reconstruction_scope.model_copy(deep=True)
        self.trace = proposal.trace.model_copy(deep=True)
        self.proposal = proposal.model_copy(deep=True)
        self.working_revision += 1

    def discard(self):
        self.working_revision += 1
        self.working = self.base.model_copy(deep=True)
        self.trace = empty_trace()
        self.proposal = None
        self.validation = None
        self.validated_fingerprint = None
        self.last_failed_plan_digest = None
        self.route_requests = {}
        self.candidate_summaries = {}

    def build_proposal(self):
        """Normalize selected private deltas against the immutable observed base."""
        self.working_revision += 1
        task_changes, formation_changes, node_changes = describe_delta(self.base, self.working)
        old_routes = {r.id: r for r in self.base.routes}
        routes = []
        for route in self.working.routes:
            old = old_routes.get(route.id)
            if route == old:
                continue
            task = next(t for t in self.working.tasks if t.route_id == route.id)
            edges = list(zip(route.waypoints, route.waypoints[1:]))
            routes.append(RouteChange(task_id=task.id, route_id=route.id, before=old, after=route,
                reason_codes=["AGENT_REQUESTED_ROUTE_TOOL"], path_length=sum(hypot(a.x-b.x,a.y-b.y) for a,b in edges),
                deviation_cost=sum(hypot(a.x-b.x,a.y-b.y)*(distance_to_route(a,old)+distance_to_route(b,old)) /
                                   (2*self.base.bounds.grid_resolution) for a,b in edges)))
        scope = self.scope.model_copy(deep=True)
        for fid in sorted({f.formation_id for f in formation_changes} | {t.after_formation for t in task_changes}):
            if fid not in scope.affected_formations:
                scope.affected_formations.append(fid)
                scope.scope_expansion_reason[f"formation:{fid}"] = "DEPENDENCY_OF_AGENT_SELECTED_SOLVER_OPTION"
        original_members = {n for f in self.base.formations if f.id in scope.affected_formations for n in f.node_ids}
        scope.affected_nodes = sorted({n.node_id for n in node_changes})
        for node in scope.affected_nodes:
            if node not in original_members:
                scope.scope_expansion_reason[f"node:{node}"] = "SPARE_FROM_AGENT_SELECTED_SOLVER_OPTION"
        for route in routes:
            if route.route_id not in scope.potentially_affected_routes:
                scope.potentially_affected_routes.append(route.route_id)
                scope.scope_expansion_reason[f"route:{route.route_id}"] = "AGENT_REQUESTED_NEW_ROUTE"
        assessments = self.tools.assess_current_task_state(self.working)
        remaining = [ConstraintOutcome(task_id=a.task_id, check=c) for a in assessments if a.task_id in scope.affected_tasks
                     for c in a.constraints if c.status in {ConstraintStatus.FAIL, ConstraintStatus.DEGRADED}]
        cost = DisruptionCostEvaluator().evaluate(self.base, self.working, routes)
        level = (StrategyLevel.FORMATION_RECONSTRUCTION if cost.new_formations else
                 StrategyLevel.TASK_REASSIGNMENT if cost.task_reassignment_count else
                 StrategyLevel.IN_PLACE_REPAIR if formation_changes else None)
        self.trace.reservations = ReservationLedger(self.working).reservations
        self.proposal = ReconstructionProposal(proposal_id=str(uuid4()), base_state_version=self.base.version,
            base_state_digest=digest(self.base), source_events=scope.source_events, reconstruction_scope=scope,
            strategy_level=level,
            # P4 legacy metadata label describes the P3 delta contract, not the policy producer.
            strategy_name="HIERARCHICAL_BOUNDED_BACKTRACKING" if scope.affected_tasks else "NO_CHANGE_REQUIRED",
            task_changes=task_changes, formation_changes=formation_changes, node_changes=node_changes, route_changes=routes,
            recovered_constraints=[], remaining_constraints=remaining, cost_breakdown=cost,
            solver_status=SolverStatus.PARTIAL if remaining else SolverStatus.FEASIBLE,
            solver_time_ms=self.trace.timing.formation_solver_ms + self.trace.timing.task_solver_ms + self.trace.timing.route_planning_ms,
            search_complete=all(a.search_complete for a in self.trace.strategy_attempts),
            explanations=["AGENT_SELECTED_PRIMITIVE_OPERATIONS", "GLOBAL_VALIDATION_REQUIRED", "NO_GLOBAL_OPTIMALITY_CLAIM"],
            trace=self.trace.model_copy(deep=True))
        self.validation = None
        self.validated_fingerprint = None
        return self.proposal
