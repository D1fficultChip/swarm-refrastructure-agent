"""Proposal-only contracts. No committed state or global validity claim."""
from enum import Enum
from typing import Literal

from pydantic import Field

from .domain import DomainModel, Formation, Position, Route, Task, TimeWindow
from .phase2 import ConstraintCheck, TaskAssessmentResult


class SolverStatus(str, Enum):
    FEASIBLE = "FEASIBLE"
    INFEASIBLE = "INFEASIBLE"
    PARTIAL = "PARTIAL"
    ERROR = "ERROR"


class StrategyLevel(str, Enum):
    IN_PLACE_REPAIR = "IN_PLACE_REPAIR"
    TASK_REASSIGNMENT = "TASK_REASSIGNMENT"
    FORMATION_RECONSTRUCTION = "FORMATION_RECONSTRUCTION"


class TaskRequirement(DomainModel):
    task_id: str
    required_capabilities: dict[str, float]
    required_resources: float
    minimum_nodes: int
    time_window: TimeWindow
    start: Position
    target: Position
    priority: int

    @classmethod
    def from_task(cls, task: Task):
        return cls(task_id=task.id, required_capabilities=dict(task.requirements),
                   required_resources=task.resource_required, minimum_nodes=task.min_nodes,
                   time_window=task.window.model_copy(deep=True), start=task.start.model_copy(deep=True),
                   target=task.target.model_copy(deep=True), priority=task.priority)


class Reservation(DomainModel):
    task_id: str
    formation_id: str
    node_ids: list[str]
    window: TimeWindow
    resource_amount: float


class ReconstructionScope(DomainModel):
    state_version: int
    source_events: list[str]
    affected_tasks: list[str]
    affected_formations: list[str]
    unavailable_nodes: list[str]
    potentially_affected_routes: list[str]
    protected_task_ids: list[str]
    constraint_gaps: list[TaskAssessmentResult]
    current_assessments: list[TaskAssessmentResult]
    reserved_resources: list[Reservation]
    order_reasons: dict[str, list[str]]
    affected_nodes: list[str] = Field(default_factory=list)
    # Namespaced entity ID -> explanation; required when extending observed impact.
    scope_expansion_reason: dict[str, str] = Field(default_factory=dict)
    reconciliation_mode: Literal["CURRENT_STATE_FULL_CHECK"] = "CURRENT_STATE_FULL_CHECK"


class Candidate(DomainModel):
    node_id: str
    capabilities: dict[str, float]
    available_resources: float
    current_formation: str | None
    current_tasks: list[str]
    distance: float
    switch_cost: int
    capability_contribution: dict[str, float]
    eligibility_reasons: list[str]


class CandidateRejection(DomainModel):
    node_id: str
    reason_code: str


class FilterStage(DomainModel):
    name: str
    remaining: int


class CandidateSet(DomainModel):
    task_id: str
    strategy_level: StrategyLevel
    required_gap: dict[str, float]
    candidates: list[Candidate]
    stages: list[FilterStage]
    rejected_candidates: list[CandidateRejection]


class TaskAssignmentChange(DomainModel):
    task_id: str
    before_formation: str | None
    after_formation: str
    before_start: Position
    after_start: Position
    reason_codes: list[str]


class FormationMembershipChange(DomainModel):
    formation_id: str
    before: Formation | None
    after: Formation
    added_nodes: list[str]
    removed_nodes: list[str]
    required_capabilities: dict[str, float]
    current_capabilities: dict[str, float]
    capability_gap: dict[str, float]
    candidate_contributions: dict[str, dict[str, float]]
    result_capabilities: dict[str, float]
    reason_codes: list[str]


class NodeRoleChange(DomainModel):
    node_id: str
    before_formation: str | None
    after_formation: str | None
    before_role: str | None
    after_role: str | None


class RouteChangeRequest(DomainModel):
    task_id: str
    route_id: str
    start: Position
    target: Position
    previous_route: Route | None
    reason_codes: list[str]


class RouteChange(DomainModel):
    task_id: str
    route_id: str
    before: Route | None
    after: Route
    reason_codes: list[str]
    path_length: float
    deviation_cost: float


class RouteDecision(DomainModel):
    task_id: str
    route_id: str | None
    replan: bool
    reason_codes: list[str]


class RoutePlanResult(DomainModel):
    task_id: str
    status: SolverStatus
    change: RouteChange | None = None
    reason_codes: list[str]
    expanded_cells: int = 0
    elapsed_ms: float


class CostBreakdown(DomainModel):
    task_reassignment_count: int
    formation_membership_changes: int
    formations_changed: int
    new_formations: int
    node_switch_count: int
    route_change_count: int
    distance_cost: float
    resource_cost: float
    resource_usage_cost: float = 0
    # No weighted scalar: comparison is lexicographic, in documented order.
    lexicographic_cost: list[float]


class StrategyAttempt(DomainModel):
    task_id: str
    level: StrategyLevel
    status: SolverStatus
    reason_codes: list[str]
    selected_candidates: list[str] = Field(default_factory=list)
    formation_id: str | None = None
    alternatives_found: int = 0
    search_complete: bool = True


class LocalOption(DomainModel):
    task_id: str
    level: StrategyLevel
    task_change: TaskAssignmentChange | None = None
    formation_change: FormationMembershipChange | None = None
    selected_nodes: list[str] = Field(default_factory=list)
    rank: list[float] = Field(default_factory=list)


class ConstraintOutcome(DomainModel):
    task_id: str
    check: ConstraintCheck


class Phase3Timing(DomainModel):
    scope_build_ms: float = 0
    candidate_generation_ms: float = 0
    task_solver_ms: float = 0
    formation_solver_ms: float = 0
    route_impact_ms: float = 0
    route_planning_ms: float = 0
    proposal_build_ms: float = 0
    coordination_ms: float = 0
    total_phase3_ms: float = 0


class Phase3ExecutionTrace(DomainModel):
    requirements: list[TaskRequirement]
    candidate_sets: list[CandidateSet]
    strategy_attempts: list[StrategyAttempt]
    reservations: list[Reservation]
    route_decisions: list[RouteDecision]
    route_results: list[RoutePlanResult]
    selected_strategies: list[StrategyAttempt]
    backtracks: int
    branches_explored: int
    timing: Phase3Timing


class ReconstructionProposal(DomainModel):
    proposal_id: str
    base_state_version: int
    base_state_digest: str
    source_events: list[str]
    reconstruction_scope: ReconstructionScope
    strategy_level: StrategyLevel | None
    strategy_name: str
    task_changes: list[TaskAssignmentChange]
    formation_changes: list[FormationMembershipChange]
    node_changes: list[NodeRoleChange]
    route_changes: list[RouteChange]
    recovered_constraints: list[ConstraintOutcome]
    remaining_constraints: list[ConstraintOutcome]
    cost_breakdown: CostBreakdown
    solver_status: SolverStatus
    solver_time_ms: float
    search_complete: bool
    optimality_proven: Literal[False] = False
    committed: Literal[False] = False
    global_validation: Literal["NOT_PERFORMED"] = "NOT_PERFORMED"
    explanations: list[str]
    trace: Phase3ExecutionTrace
