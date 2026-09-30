"""P5.1 contracts; stable options and policy records, separate from world state."""
from typing import Literal

from pydantic import ConfigDict, Field, JsonValue

from ..models.domain import DomainModel


class PolicyExpansion(DomainModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    entity: str = Field(pattern=r"^(task|formation|node|route):[A-Za-z][A-Za-z0-9_-]{0,63}$")
    reason: str = Field(min_length=1, max_length=300)
    source_validation_id: str | None = None


class ReconstructionOption(DomainModel):
    option_id: str
    option_set_id: str
    base_state_version: int
    working_state_version: int  # private proposal revision, NOT ScenarioState.version
    base_state_digest: str
    working_state_digest: str
    strategy_type: str
    target_tasks: list[str]
    target_formations: list[str]
    added_nodes: list[str]
    removed_nodes: list[str]
    target_formation: str | None
    capability_effect: dict[str, JsonValue]
    resource_effect: dict[str, JsonValue]
    route_effect: dict[str, JsonValue]
    predicted_delta_summary: dict[str, JsonValue]
    disruption_cost_vector: list[float]
    eligibility: dict[str, JsonValue]
    explanation_summary: str
    replaces_working_proposal: bool = False
    hard_goal_deficits: dict[str, float] = Field(default_factory=dict)
    future_support: dict[str, float] = Field(default_factory=dict)
    dominated_by: list[str] = Field(default_factory=list)


class PolicyAction(DomainModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    action_type: Literal["SELECT_OPTION", "CALL_TOOL", "FINALIZE_PROPOSAL", "STOP"]
    option_id: str | None = None
    tool_name: str | None = None
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    scope_expansions: list[PolicyExpansion] = Field(default_factory=list, max_length=8)
    finalize: bool = False
    decision_reason: str = Field(min_length=1, max_length=300)


class PromptSizeMetrics(DomainModel):
    system_chars: int = 0
    tool_schema_chars: int = 0
    action_schema_chars: int = 0
    context_chars: int = 0  # excludes separately counted observation history
    history_chars: int = 0
    request_chars: int = 0
    estimated_input_tokens: int = 0


class PolicyDecisionRecord(DomainModel):
    decision_id: str
    model: str
    model_tier: str
    router_reasons: list[str]
    decision_snapshot_hash: str
    selected_option: str | None = None
    selected_tool: str | None = None
    selected_targets: list[str] = Field(default_factory=list)
    disruption_cost: list[float] | None = None
    non_dominated_options: list[str] = Field(default_factory=list)
    validator_feedback: dict[str, JsonValue] | None = None
    reason_argument_consistency: bool | None = None
    warnings: list[str] = Field(default_factory=list)
    prompt_size: PromptSizeMetrics = Field(default_factory=PromptSizeMetrics)
    automatic_steps: list[dict[str, JsonValue]] = Field(default_factory=list)
    available_options: list[dict[str, JsonValue]] = Field(default_factory=list)
    preparation_solver_calls: int = 0
    preparation_ms: float = 0


class DecisionSnapshot(DomainModel):
    policy_version: Literal["5.1"] = "5.1"
    snapshot_hash: str
    base_state_version: int
    working_revision: int
    option_set_id: str
    violations: list[dict[str, JsonValue]]
    affected_tasks: list[dict[str, JsonValue]]
    route_conflicts: list[str]
    candidate_summary: dict[str, list[str]]
    options: list[ReconstructionOption]
    options_omitted: int
    shared_candidate_conflicts: dict[str, list[str]]
    working_proposal: dict[str, JsonValue] | None
    validator_feedback: dict[str, JsonValue] | None
    scope: list[str]
    scope_expansion_reasons: dict[str, str]
    option_generation: dict[str, JsonValue]
