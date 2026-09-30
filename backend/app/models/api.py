from typing import Literal

from pydantic import Field

from .domain import DomainModel, Identifier, ScenarioState
from .events import Event


class LoadScenarioRequest(DomainModel):
    scenario_id: Identifier


class ScenarioSummary(DomainModel):
    scenario_id: Identifier
    name: str
    node_count: int
    task_count: int
    formation_count: int
    event_count: int


class ScenarioSnapshot(DomainModel):
    session_id: str
    state: ScenarioState
    events: list[Event]


class ApiError(DomainModel):
    code: str
    message: str


class ErrorResponse(DomainModel):
    detail: ApiError


class HealthResponse(DomainModel):
    status: Literal["ok"] = "ok"
    implemented_phase: Literal[5] = 5
    implemented_stage: Literal["5.1"] = "5.1"
    reconstruction_available: Literal[True] = True


class InjectEventRequest(DomainModel):
    session_id: str = Field(min_length=1)
    expected_version: int = Field(ge=0, strict=True)
    event: Event


class ReconstructRequest(DomainModel):
    """Stateless boundary: state is the baseline BEFORE this event."""

    state: ScenarioState
    event: Event
    commit: bool = True


class AssessmentRequest(DomainModel):
    session_id: str = Field(min_length=1)
    # Omit to retrieve the latest completed event assessment; never reapply it.
    event_id: Identifier | None = None


class ProposeReconstructionRequest(DomainModel):
    session_id: str = Field(min_length=1)
    expected_version: int = Field(ge=0, strict=True)
