"""Independent validation and transactional, auditable commit contracts."""
from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import uuid4

from pydantic import Field, JsonValue

from .domain import DomainModel, ScenarioState
from .phase2 import Phase2Result
from .phase3 import (FormationMembershipChange, NodeRoleChange, ReconstructionProposal,
                     RouteChange, TaskAssignmentChange)


class ValidationStatus(str, Enum):
    PASS = "PASS"
    PASS_WITH_LIMITATIONS = "PASS_WITH_LIMITATIONS"
    FAIL = "FAIL"


class ValidationCheck(DomainModel):
    code: str
    category: str
    subject: str
    passed: bool
    details: dict[str, JsonValue] = Field(default_factory=dict)


class ValidationCoverage(DomainModel):
    constraint: str
    status: Literal["VALIDATED", "NOT_EVALUATED", "NOT_APPLICABLE", "NOT_RUN"]
    explanation: str


class ValidationMetrics(DomainModel):
    validated_tasks: int = 0
    validated_formations: int = 0
    validated_routes: int = 0
    hard_constraint_count: int = 0
    failed_constraint_count: int = 0
    warning_count: int = 0
    not_evaluated_count: int = 0


class ValidationResult(DomainModel):
    validation_id: str = Field(default_factory=lambda: str(uuid4()))
    proposal_id: str
    base_state_version: int
    candidate_state_digest: str | None = None
    status: ValidationStatus = ValidationStatus.FAIL
    hard_failures: list[ValidationCheck] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    not_evaluated: list[str] = Field(default_factory=list)
    validated_constraints: list[ValidationCheck] = Field(default_factory=list)
    per_task_results: dict[str, list[ValidationCheck]] = Field(default_factory=dict)
    per_formation_results: dict[str, list[ValidationCheck]] = Field(default_factory=dict)
    route_results: dict[str, list[ValidationCheck]] = Field(default_factory=dict)
    global_invariants: list[ValidationCheck] = Field(default_factory=list)
    coverage: list[ValidationCoverage] = Field(default_factory=list)
    metrics: ValidationMetrics = Field(default_factory=ValidationMetrics)
    validation_time_ms: float = 0


class AppliedDeltas(DomainModel):
    task_changes: list[TaskAssignmentChange]
    formation_changes: list[FormationMembershipChange]
    node_changes: list[NodeRoleChange]
    route_changes: list[RouteChange]


class ReconstructionCommitReceipt(DomainModel):
    commit_id: str = Field(default_factory=lambda: str(uuid4()))
    reconstruction_id: str
    proposal_id: str
    validation_id: str
    base_version: int
    committed_version: int
    candidate_state_digest: str
    committed_state_digest: str
    applied_deltas: AppliedDeltas
    validation_status: ValidationStatus
    validation_summary: ValidationMetrics
    committed_at: datetime
    replayed: bool = False
    commit_ms: float = 0


class ReconstructionTiming(DomainModel):
    proposal_ms: float = 0
    validation_ms: float = 0
    commit_ms: float = 0
    deterministic_total_ms: float = 0


class ReconstructionResult(DomainModel):
    reconstruction_id: str
    session_id: str | None
    committed: bool
    proposal: ReconstructionProposal
    validation: ValidationResult
    receipt: ReconstructionCommitReceipt | None = None
    state: ScenarioState
    event_traces: list[Phase2Result] = Field(default_factory=list)
    metrics: ReconstructionTiming


class ProposalActionRequest(DomainModel):
    session_id: str = Field(min_length=1)
    proposal: ReconstructionProposal


class SessionReconstructRequest(DomainModel):
    session_id: str = Field(min_length=1)
    expected_version: int = Field(ge=0, strict=True)
    commit: bool = True
