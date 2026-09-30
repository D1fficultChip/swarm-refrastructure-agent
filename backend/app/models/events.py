"""Each event variant owns its payload; no untyped payload dictionaries."""

from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, model_validator

from .domain import (
    DomainModel, EnvironmentConstraint, Identifier, NonNegative, Position,
    ScenarioState, Task,
)


class EventBase(DomainModel):
    event_id: Identifier
    occurred_at: NonNegative


class NodeFailure(EventBase):
    type: Literal["NodeFailure"] = "NodeFailure"
    node_id: Identifier


class NodeDegradation(EventBase):
    type: Literal["NodeDegradation"] = "NodeDegradation"
    node_id: Identifier
    health: Annotated[float, Field(gt=0, lt=1)]


class TaskAdd(EventBase):
    type: Literal["TaskAdd"] = "TaskAdd"
    task: Task


class TaskCancel(EventBase):
    type: Literal["TaskCancel"] = "TaskCancel"
    task_id: Identifier


class TaskPriorityChange(EventBase):
    type: Literal["TaskPriorityChange"] = "TaskPriorityChange"
    task_id: Identifier
    priority: int = Field(ge=1, le=5, strict=True)


class RestrictedAreaAdd(EventBase):
    type: Literal["RestrictedAreaAdd"] = "RestrictedAreaAdd"
    region: EnvironmentConstraint

    @model_validator(mode="after")
    def restricted_only(self):
        if self.region.kind.value != "RESTRICTED":
            raise ValueError("RestrictedAreaAdd requires a restricted region")
        return self


class RestrictedAreaRemove(EventBase):
    type: Literal["RestrictedAreaRemove"] = "RestrictedAreaRemove"
    region_id: Identifier


class TargetMove(EventBase):
    type: Literal["TargetMove"] = "TargetMove"
    task_id: Identifier
    target: Position


Event = Annotated[
    NodeFailure | NodeDegradation | TaskAdd | TaskCancel | TaskPriorityChange
    | RestrictedAreaAdd | RestrictedAreaRemove | TargetMove,
    Field(discriminator="type"),
]
event_adapter = TypeAdapter(Event)


class ScenarioDefinition(DomainModel):
    name: str = Field(min_length=1)
    description: str
    initial_state: ScenarioState
    events: list[Event] = Field(default_factory=list)

    @model_validator(mode="after")
    def event_sequence(self):
        event_ids = [event.event_id for event in self.events]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("duplicate event identifier")
        previous = self.initial_state.clock
        for event in self.events:
            if event.occurred_at < previous:
                raise ValueError("events must be chronological and not precede state clock")
            previous = event.occurred_at
        return self
