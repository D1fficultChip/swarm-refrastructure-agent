from datetime import datetime
from typing import Literal
from uuid import uuid4

from pydantic import ConfigDict, Field, JsonValue

from ..models.domain import DomainModel
from ..models.phase3 import ReconstructionProposal, ReconstructionScope
from ..models.phase4 import ReconstructionCommitReceipt, ValidationCheck, ValidationResult
from .policy_models import PolicyAction, PolicyDecisionRecord, PromptSizeMetrics

Termination = Literal["SUCCESS", "NO_RECONSTRUCTION_REQUIRED", "INFEASIBLE", "MAX_STEPS_REACHED",
    "MAX_MODEL_CALLS_REACHED", "VALIDATION_RETRY_LIMIT", "TIME_BUDGET_EXCEEDED", "MODEL_ERROR", "TOOL_ERROR",
    "STALE_RESTART_LIMIT", "FALLBACK_SUCCESS", "FALLBACK_FAILED", "DETERMINISTIC_SUCCESS", "DETERMINISTIC_FAILED",
    "VALIDATED_NOT_COMMITTED", "DECISION_TIME_BUDGET_EXCEEDED"]


class AgentAction(DomainModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    action_type: Literal["CALL_TOOL", "VALIDATE", "COMMIT", "STOP", "USE_FALLBACK"]
    tool_name: str | None = Field(default=None, max_length=80)
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    target_entities: list[str] = Field(default_factory=list, max_length=32)
    decision_reason: str = Field(min_length=1, max_length=400)
    expected_effect: str = Field(default="", max_length=300)
    evidence_refs: list[str] = Field(default_factory=list, max_length=32)


class ToolObservation(DomainModel):
    observation_id: str = Field(default_factory=lambda: str(uuid4()))
    tool: str
    status: str
    reason_codes: list[str] = Field(default_factory=list)
    data: dict[str, JsonValue] = Field(default_factory=dict)
    state_version: int
    proposal_id: str | None = None
    validation_id: str | None = None


class ScopeExpansion(DomainModel):
    entity: str
    reason: str
    source_validation_id: str | None


class AgentTiming(DomainModel):
    context_compile_ms: float = 0
    model_total_ms: float = 0
    tool_total_ms: float = 0  # excludes standalone validation and commit
    validation_total_ms: float = 0
    commit_ms: float = 0  # includes transaction's mandatory revalidation
    fallback_ms: float = 0
    agent_total_ms: float = 0
    deterministic_baseline_ms: float | None = None  # populated only by a measured comparison
    option_generation_ms: float = 0
    pure_agent_ms: float = 0


class AgentState(DomainModel):
    agent_run_id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    reconstruction_id: str | None = None
    base_state_version: int
    current_state_version: int
    objective: str
    outstanding_violations: list[ValidationCheck] = Field(default_factory=list)
    current_scope: ReconstructionScope
    working_proposal: ReconstructionProposal | None = None
    observations: list[ToolObservation] = Field(default_factory=list)
    tool_history: list[str] = Field(default_factory=list)
    validation_feedback: ValidationResult | None = None
    scope_expansions: list[ScopeExpansion] = Field(default_factory=list)
    step_index: int = 0
    max_steps: int
    model_calls: int = 0
    tool_calls: int = 0
    solver_calls: int = 0
    validation_attempts: int = 0
    stale_restarts: int = 0
    needs_refresh: bool = False
    start_time: datetime
    deadline: datetime
    status: Literal["RUNNING", "TERMINATED"] = "RUNNING"
    termination_reason: Termination | None = None


class AgentTraceStep(DomainModel):
    step: int
    timestamp: datetime
    context_summary: dict[str, JsonValue]
    model_name: str
    model_latency_ms: float = 0
    token_usage: dict[str, int] = Field(default_factory=dict)
    action: AgentAction | PolicyAction | None = None
    tool_name: str | None = None
    tool_arguments: dict[str, JsonValue] = Field(default_factory=dict)
    observation: ToolObservation
    validation_feedback: ValidationResult | None = None
    state_version: int
    working_proposal_id: str | None = None
    decision_record: PolicyDecisionRecord | None = None
    prompt_size: PromptSizeMetrics | None = None


class AgentExecutionTrace(DomainModel):
    trace_id: str
    agent_run_id: str
    session_id: str
    policy_mode: Literal["agent", "mock", "deterministic"]
    steps: list[AgentTraceStep] = Field(default_factory=list)
    scope_expansions: list[ScopeExpansion] = Field(default_factory=list)
    termination: Termination | None = None
    fallback_reason: str | None = None


class AgentReconstructionResult(DomainModel):
    agent_run_id: str
    reconstruction_id: str | None
    status: Literal["COMMITTED", "NO_CHANGE", "STOPPED"]
    termination_reason: Termination
    initial_state_version: int
    final_state_version: int
    steps: int
    model_calls: int
    tool_calls: int
    solver_calls: int
    validation_attempts: int
    proposal_id: str | None
    validation_status: str | None
    commit_receipt: ReconstructionCommitReceipt | None
    fallback_used: bool
    timing: AgentTiming
    trace_id: str
    trace: AgentExecutionTrace
    proposal: ReconstructionProposal | None = None
    final_outstanding_violations: list[ValidationCheck] = Field(default_factory=list)
    execution_mode: str = "adaptive_agent"
    policy_profile: str = "original"
    pure_agent_success: bool = False
    fallback_success: bool = False
    overall_success: bool = False


class AgentReconstructRequest(DomainModel):
    session_id: str = Field(min_length=1)
    expected_version: int = Field(ge=0, strict=True)
    mode: Literal["agent", "deterministic", "adaptive_agent", "bounded_agent", "deterministic_realtime"] = "agent"
    objective: str = Field(default="Restore all ACTIVE tasks with minimal plan disruption; validate and commit.", max_length=1000)
    initial_proposal: ReconstructionProposal | None = None
