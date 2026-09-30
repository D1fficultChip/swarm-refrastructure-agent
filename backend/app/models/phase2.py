"""Structured event, graph, impact and assessment contracts (no solver results)."""

from enum import Enum
from typing import Annotated, Literal

from pydantic import Field

from .domain import DomainModel, EnvironmentConstraint, Node, NonNegative, ScenarioState, Task
from .events import Event

GraphId = Annotated[str, Field(pattern=r"^(task|formation|node|capability|route|environment):.+$")]


class EventErrorCode(str, Enum):
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    ENTITY_ALREADY_EXISTS = "ENTITY_ALREADY_EXISTS"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    EVENT_OUT_OF_ORDER = "EVENT_OUT_OF_ORDER"
    INVALID_EVENT_STATE = "INVALID_EVENT_STATE"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    EVENT_ID_CONFLICT = "EVENT_ID_CONFLICT"
    ASSESSMENT_NOT_FOUND = "ASSESSMENT_NOT_FOUND"


class EntityChange(DomainModel):
    entity: GraphId
    changed_fields: list[str]
    before: Node | Task | EnvironmentConstraint | None
    after: Node | Task | EnvironmentConstraint | None


class EventApplicationResult(DomainModel):
    event_id: str
    status: Literal["APPLIED"] = "APPLIED"
    state_version_before: int
    state_version_after: int
    clock_before: NonNegative
    clock_after: NonNegative
    changed_entities: list[GraphId]
    changes: list[EntityChange]
    state: ScenarioState
    event_application_ms: NonNegative


class EntityType(str, Enum):
    TASK = "Task"
    FORMATION = "Formation"
    NODE = "Node"
    CAPABILITY = "Capability"
    ROUTE = "Route"
    ENVIRONMENT = "EnvironmentConstraint"


class RelationType(str, Enum):
    TASK_REQUIRES_CAPABILITY = "TASK_REQUIRES_CAPABILITY"
    TASK_ASSIGNED_TO_FORMATION = "TASK_ASSIGNED_TO_FORMATION"
    FORMATION_CONTAINS_NODE = "FORMATION_CONTAINS_NODE"
    NODE_PROVIDES_CAPABILITY = "NODE_PROVIDES_CAPABILITY"
    FORMATION_PROVIDES_CAPABILITY = "FORMATION_PROVIDES_CAPABILITY"
    CAPABILITY_CONTRIBUTES_TO = "CAPABILITY_CONTRIBUTES_TO"
    CAPABILITY_HAS_TYPE = "CAPABILITY_HAS_TYPE"
    TASK_USES_ROUTE = "TASK_USES_ROUTE"
    ROUTE_INTERSECTS_CONSTRAINT = "ROUTE_INTERSECTS_CONSTRAINT"


class GraphEntity(DomainModel):
    id: GraphId
    entity_id: str
    type: EntityType
    capability: str | None = None
    owner: GraphId | None = None


class DependencyEdge(DomainModel):
    source: GraphId
    target: GraphId
    relation: RelationType
    amount: NonNegative | None = None
    role: str | None = None


class GraphSnapshot(DomainModel):
    scenario_id: str
    state_version: int
    entities: list[GraphEntity]
    edges: list[DependencyEdge]


class GraphStats(DomainModel):
    entities: int
    edges: int


class ImpactLevel(str, Enum):
    # CRITICAL is a direct factual change, never a feasibility verdict.
    CRITICAL = "CRITICAL"
    AFFECTED = "AFFECTED"
    WEAK = "WEAK"


class PropagationStep(DomainModel):
    source: GraphId
    target: GraphId
    relation: RelationType
    direction: Literal["FORWARD", "REVERSE"]


class ImpactRecord(DomainModel):
    entity: GraphId
    impact_level: ImpactLevel
    reason_code: str
    reason: str
    path: list[GraphId]
    steps: list[PropagationStep] = Field(default_factory=list)
    depth: int


class ImpactAnalysisResult(DomainModel):
    scenario_id: str
    event_id: str
    state_version_before: int
    state_version_after: int
    directly_affected: list[GraphId]
    affected_entities: list[GraphId]
    # These subsets use domain IDs, while entities/paths use namespaced graph IDs.
    affected_tasks: list[str]
    affected_formations: list[str]
    affected_nodes: list[str]
    affected_routes: list[str]
    impact_records: list[ImpactRecord]
    propagation_paths: list[list[GraphId]]
    affected_subgraph: GraphSnapshot
    analysis_time_ms: NonNegative


class ConstraintStatus(str, Enum):
    PASS = "PASS"
    DEGRADED = "DEGRADED"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NOT_EVALUATED = "NOT_EVALUATED"


class TaskDecision(str, Enum):
    KEEP = "KEEP"
    ADJUST = "ADJUST"
    RECONSTRUCT = "RECONSTRUCT"
    ABORT = "ABORT"


class CapabilityCheck(DomainModel):
    required: NonNegative
    available: NonNegative
    gap: NonNegative


class CapabilityDetails(DomainModel):
    capabilities: dict[str, CapabilityCheck]
    eligible_node_ids: list[str]


class ResourceDetails(DomainModel):
    required: NonNegative
    available: NonNegative
    active_node_count: int
    shared_commitment: NonNegative
    shared_available: NonNegative
    sharing_task_ids: list[str]


class FormationDetails(DomainModel):
    formation_id: str | None
    exists: bool
    minimum_nodes: int
    active_node_count: int
    unavailable_node_ids: list[str]
    minimum_capabilities: dict[str, CapabilityCheck]


class RouteDetails(DomainModel):
    route_id: str | None
    exists: bool
    start_valid: bool
    end_valid: bool
    geometry_valid: bool
    conflicting_region_ids: list[str]


class TimingDetails(DomainModel):
    clock: NonNegative
    window_start: NonNegative
    window_end: NonNegative
    priority: int
    conflicting_task_ids: list[str]
    expired: bool
    priority_policy: ConstraintStatus = ConstraintStatus.NOT_EVALUATED
    travel_time: ConstraintStatus = ConstraintStatus.NOT_EVALUATED


class InactiveDetails(DomainModel):
    task_status: str


class ConstraintCheck(DomainModel):
    type: Literal["capability", "resource", "formation", "route", "priority_timing"]
    status: ConstraintStatus
    reason_codes: list[str] = Field(default_factory=list)
    details: CapabilityDetails | ResourceDetails | FormationDetails | RouteDetails | TimingDetails | InactiveDetails


class TaskEntityStates(DomainModel):
    task_status: str
    node_states: dict[str, str]
    formation_state: str
    route_state: str


class TaskAssessmentResult(DomainModel):
    task_id: str
    state_version: int
    impact_level: ImpactLevel | None
    decision: TaskDecision
    constraints: list[ConstraintCheck]
    reason_codes: list[str]
    triggered_by: list[str]
    affected_entities: list[GraphId]
    entity_states: TaskEntityStates
    # Feasible only within the explicitly implemented checks, not a global validation.
    feasible_under_evaluated_constraints: bool
    evaluation_complete: Literal[False] = False


class Phase2Timing(DomainModel):
    event_application_ms: NonNegative
    graph_build_ms: NonNegative
    impact_analysis_ms: NonNegative
    assessment_ms: NonNegative
    total_phase2_ms: NonNegative


class Phase2ExecutionTrace(DomainModel):
    event: Event
    changes: list[EntityChange]
    state_version_before: int
    state_version_after: int
    clock_before: NonNegative
    clock_after: NonNegative
    graph_stats_before: GraphStats
    graph_stats_after: GraphStats
    propagation_records: list[ImpactRecord]
    task_assessments: list[TaskAssessmentResult]
    timing: Phase2Timing


class Phase2Result(DomainModel):
    session_id: str | None = None
    replayed: bool = False
    application: EventApplicationResult
    impact_analysis: ImpactAnalysisResult
    task_assessment: list[TaskAssessmentResult]
    reconstruction_required: bool
    adjustment_required: bool
    trace: Phase2ExecutionTrace
    metrics: Phase2Timing
