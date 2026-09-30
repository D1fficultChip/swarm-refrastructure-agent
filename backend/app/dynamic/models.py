from enum import Enum
from typing import Annotated, Literal

from pydantic import Field, JsonValue, model_validator

from ..demo.models import ExecutionMode
from ..models.domain import DomainModel, Position, ScenarioState
from ..models.events import Event, ScenarioDefinition


class TaskRuntimeStatus(str, Enum):
    WAITING = "WAITING"
    EXECUTING = "EXECUTING"
    DEGRADED = "DEGRADED"
    BLOCKED = "BLOCKED"
    RECONSTRUCTING = "RECONSTRUCTING"
    RECOVERED = "RECOVERED"
    COMPLETED = "COMPLETED"


class MissionStatus(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    RECONSTRUCTING = "RECONSTRUCTING"
    COMPLETED = "COMPLETED"


class DynamicRuntimeState(DomainModel):
    simulation_time: float = Field(default=0, ge=0)
    node_runtime_positions: dict[str, Position]
    formation_runtime_positions: dict[str, Position]
    formation_member_offsets: dict[str, dict[str, Position]]
    task_progress: dict[str, Annotated[float, Field(ge=0, le=1)]]
    task_execution_budget: dict[str, Annotated[float, Field(gt=0)]] = Field(default_factory=dict)
    task_runtime_status: dict[str, TaskRuntimeStatus]
    current_route_progress: dict[str, Annotated[float, Field(ge=0, le=1)]]
    simulation_running: bool = False
    simulation_speed: Literal[0.5, 1.0, 2.0, 5.0] = 1.0
    pending_dynamic_events: list[Event] = Field(default_factory=list)
    last_planning_sync_time: float = 0


class TimelineEntry(DomainModel):
    sequence: int = Field(ge=1)
    simulation_time: float = Field(ge=0)
    kind: str
    title: str
    detail: str = ""
    planning_version: int = Field(ge=0)
    data: dict[str, JsonValue] = Field(default_factory=dict)


class RuntimeCheckpoint(DomainModel):
    simulation_time: float
    planning_version: int
    runtime_state: DynamicRuntimeState
    planning_state: ScenarioState | None = None
    planning_metrics: dict[str, JsonValue] = Field(default_factory=dict)


class DynamicMissionMetrics(DomainModel):
    runtime_events: int = 0
    reconstruction_triggers: int = 0
    noop_events: int = 0
    successful_reconstructions: int = 0
    task_blocked_duration: dict[str, float] = Field(default_factory=dict)
    recovery_times: list[dict[str, JsonValue]] = Field(default_factory=list)
    plan_changes: int = 0
    model_calls: int = 0
    total_mission_runtime: float = 0


class DynamicMissionSession(DomainModel):
    dynamic_session_id: str
    run_id: str
    scenario_seed: int
    event_seed: int
    planning_session_id: str
    planning_state_version: int
    initial_definition: ScenarioDefinition
    runtime_state: DynamicRuntimeState
    event_history: list[Event] = Field(default_factory=list)
    impact_results: list[dict[str, JsonValue]] = Field(default_factory=list)
    reconstruction_history: list[str] = Field(default_factory=list)
    timeline: list[TimelineEntry] = Field(default_factory=list)
    runtime_checkpoints: list[RuntimeCheckpoint] = Field(default_factory=list)
    execution_mode: ExecutionMode = "adaptive_agent"
    event_mode: Literal["manual", "semi_random", "automatic"] = "automatic"
    difficulty: Literal["L1", "L2", "L3", "L4"] = "L3"
    reconstruction_trigger: Literal["manual", "automatic"] = "manual"
    status: MissionStatus = MissionStatus.CREATED
    next_event_time: float = 20
    current_reconstruction_run_id: str | None = None
    metrics: DynamicMissionMetrics = Field(default_factory=DynamicMissionMetrics)


class CreateDynamicSessionRequest(DomainModel):
    scenario_seed: int | None = Field(default=None, ge=0)
    event_seed: int | None = Field(default=None, ge=0)
    node_count: Literal[30, 60] = 60
    task_count: Literal[4, 8] = 8
    event_mode: Literal["manual", "semi_random", "automatic"] = "automatic"
    difficulty: Literal["L1", "L2", "L3", "L4"] = "L3"
    execution_mode: ExecutionMode = "adaptive_agent"
    reconstruction_trigger: Literal["manual", "automatic"] = "manual"
    simulation_speed: Literal[0.5, 1.0, 2.0, 5.0] = 2.0
    event_interval_min: float = Field(default=15, ge=1)
    event_interval_max: float = Field(default=30, ge=1)

    @model_validator(mode='after')
    def interval_order(self):
        if self.event_interval_max < self.event_interval_min:
            raise ValueError('event interval max must be >= min')
        return self


class DynamicSessionRequest(DomainModel):
    session_id: str


class DynamicStepRequest(DynamicSessionRequest):
    seconds: float = Field(default=1, gt=0, le=60)


class DynamicSpeedRequest(DynamicSessionRequest):
    speed: Literal[0.5, 1.0, 2.0, 5.0]


class DynamicEventRequest(DynamicSessionRequest):
    mode: Literal["manual", "semi_random"] = "semi_random"
    event_type: Literal["NodeFailure", "NodeDegradation", "RestrictedAreaAdd",
                        "RestrictedAreaRemove", "TargetMove", "TaskAdd",
                        "TaskCancel", "TaskPriorityChange"] | None = None
    difficulty: Literal["L1", "L2", "L3", "L4"] | None = None
    target_id: str | None = None


class DynamicReconstructRequest(DynamicSessionRequest):
    mode: ExecutionMode | None = None


class DynamicMissionRun(DomainModel):
    run_id: str
    dynamic_session_id: str
    scenario_seed: int
    event_seed: int
    initial_state: ScenarioState
    timeline: list[TimelineEntry]
    runtime_checkpoints: list[RuntimeCheckpoint]
    events: list[Event]
    impact_results: list[dict[str, JsonValue]]
    reconstruction_runs: list[str]
    final_state: ScenarioState
    metrics: DynamicMissionMetrics
