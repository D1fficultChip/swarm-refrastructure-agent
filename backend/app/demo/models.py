from typing import Literal
from pydantic import Field, JsonValue
from ..models.domain import DomainModel, ScenarioState
from ..models.events import Event
from ..models.phase2 import ImpactAnalysisResult, TaskAssessmentResult
from ..models.phase3 import ReconstructionProposal
from ..models.phase4 import ReconstructionCommitReceipt, ValidationResult

ExecutionMode = Literal['adaptive_agent','bounded_agent','deterministic_realtime']


class DemoRunResult(DomainModel):
    run_id: str
    session_id: str
    scenario: dict[str, JsonValue]
    execution_mode: ExecutionMode
    status: str
    initial_state: ScenarioState
    before_state: ScenarioState
    event: list[Event]
    impact_analysis: list[ImpactAnalysisResult]
    task_assessment: list[TaskAssessmentResult]
    agent_trace: dict[str, JsonValue] | None = None
    deterministic_trace: dict[str, JsonValue] | None = None
    reconstruction_proposal: ReconstructionProposal | None = None
    validation: ValidationResult | None = None
    commit: ReconstructionCommitReceipt | None = None
    after_state: ScenarioState
    candidate_state: ScenarioState | None = None
    before_after_metrics: dict[str, JsonValue] = Field(default_factory=dict)
    timing: dict[str, float] = Field(default_factory=dict)
    policy_metrics: dict[str, JsonValue] = Field(default_factory=dict)
    trace_steps: list[dict[str, JsonValue]] = Field(default_factory=list)
    error: dict[str, JsonValue] | None = None
    created_at: str
    trace_execution_scope: Literal['PRIVATE_BRANCH'] = 'PRIVATE_BRANCH'
    seed_origin: str | None = None


class LoadDemoRequest(DomainModel):
    scenario_id: str


class EventDemoRequest(DomainModel):
    session_id: str
    expected_version: int = Field(ge=0,strict=True)
    all_remaining: bool = True


class RunDemoRequest(DomainModel):
    session_id: str
    expected_version: int = Field(ge=0,strict=True)
    mode: ExecutionMode = 'deterministic_realtime'
    auto_commit: bool = True


class CommitDemoRequest(DomainModel):
    run_id: str
