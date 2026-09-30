"""Canonical units: metres, seconds from scenario start, abstract resource units.

These validators enforce structural integrity, not mission feasibility. An
observed state containing a failed assigned node must remain representable.
"""

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")]
NonNegative = Annotated[float, Field(ge=0)]
Positive = Annotated[float, Field(gt=0)]
CapabilityProfile = dict[Identifier, Positive]


class DomainModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, validate_assignment=True,
        str_strip_whitespace=True,
    )


class Position(DomainModel):
    x: float
    y: float


class TimeWindow(DomainModel):
    start: NonNegative
    end: Positive

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("time window must satisfy start < end")
        return self

    def overlaps(self, other: "TimeWindow") -> bool:
        """Half-open [start, end): adjacent assignments do not conflict."""
        return self.start < other.end and other.start < self.end


class MapBounds(DomainModel):
    width: Positive = 1000
    height: Positive = 1000
    grid_resolution: Positive = 10

    @model_validator(mode="after")
    def valid_resolution(self):
        if self.grid_resolution > min(self.width, self.height):
            raise ValueError("grid resolution exceeds map dimensions")
        return self

    def contains(self, point: Position) -> bool:
        return 0 <= point.x <= self.width and 0 <= point.y <= self.height


class Capability(DomainModel):
    id: Identifier
    description: str
    aggregation: Literal["sum"] = "sum"


class NodeStatus(str, Enum):
    NORMAL = "NORMAL"
    DEGRADED = "DEGRADED"
    FAILED = "FAILED"


class Node(DomainModel):
    id: Identifier
    position: Position
    status: NodeStatus = NodeStatus.NORMAL
    capabilities: CapabilityProfile
    availability: TimeWindow
    resource_remaining: NonNegative
    health: Annotated[float, Field(ge=0, le=1)] = 1
    risk: Annotated[float, Field(ge=0, le=1)] = 0


class Formation(DomainModel):
    id: Identifier
    node_ids: list[Identifier]
    minimum_capabilities: CapabilityProfile = Field(default_factory=dict)
    roles: dict[Identifier, Identifier] = Field(default_factory=dict)

    @model_validator(mode="after")
    def members_and_roles(self):
        if len(self.node_ids) != len(set(self.node_ids)):
            raise ValueError("duplicate formation member")
        if not set(self.roles).issubset(self.node_ids):
            raise ValueError("role assigned to a non-member")
        return self


class TaskStatus(str, Enum):
    ACTIVE = "ACTIVE"
    CANCELLED = "CANCELLED"
    ABORTED = "ABORTED"


class Task(DomainModel):
    id: Identifier
    name: str = Field(min_length=1)
    requirements: CapabilityProfile
    resource_required: NonNegative = 0
    min_nodes: int = Field(default=1, ge=1, strict=True)
    priority: int = Field(default=3, ge=1, le=5, strict=True)
    window: TimeWindow
    start: Position
    target: Position
    formation_id: Identifier | None = None
    route_id: Identifier | None = None
    status: TaskStatus = TaskStatus.ACTIVE


class Route(DomainModel):
    id: Identifier
    waypoints: list[Position] = Field(min_length=2)


class EnvironmentKind(str, Enum):
    RESTRICTED = "RESTRICTED"
    RISK = "RISK"


class EnvironmentConstraint(DomainModel):
    """Axis-aligned rectangle; the boundary counts as inside the region."""

    id: Identifier
    kind: EnvironmentKind = EnvironmentKind.RESTRICTED
    lower: Position
    upper: Position
    risk: Annotated[float, Field(ge=0, le=1)] = 1

    @model_validator(mode="after")
    def rectangle(self):
        if self.lower.x >= self.upper.x or self.lower.y >= self.upper.y:
            raise ValueError("region must have positive width and height")
        return self


class ScenarioState(DomainModel):
    schema_version: Literal["1.0"] = "1.0"
    scenario_id: Identifier
    seed: int = Field(ge=0, strict=True)
    version: int = Field(default=0, ge=0, strict=True)
    clock: NonNegative = 0
    bounds: MapBounds = Field(default_factory=MapBounds)
    capabilities: list[Capability]
    nodes: list[Node]
    tasks: list[Task]
    formations: list[Formation]
    routes: list[Route]
    environment: list[EnvironmentConstraint] = Field(default_factory=list)

    @model_validator(mode="after")
    def references_and_geometry(self):
        groups = [self.capabilities, self.nodes, self.tasks, self.formations,
                  self.routes, self.environment]
        ids = [entity.id for group in groups for entity in group]
        if len(ids) != len(set(ids)):
            raise ValueError("entity identifiers must be globally unique")
        nodes = {node.id: node for node in self.nodes}
        capabilities = {cap.id for cap in self.capabilities}
        formations = {formation.id for formation in self.formations}
        routes = {route.id for route in self.routes}
        memberships: set[str] = set()
        profiles = [node.capabilities for node in self.nodes]
        profiles += [task.requirements for task in self.tasks]
        profiles += [formation.minimum_capabilities for formation in self.formations]
        if any(not set(profile).issubset(capabilities) for profile in profiles):
            raise ValueError("unknown capability reference")
        for formation in self.formations:
            members = set(formation.node_ids)
            if not members.issubset(nodes):
                raise ValueError("unknown formation member")
            if memberships & members:
                raise ValueError("node belongs to multiple formations")
            memberships |= members
            for node_id, role in formation.roles.items():
                if role not in nodes[node_id].capabilities:
                    raise ValueError("role must reference a member's nominal capability")
        claimed_routes: set[str] = set()
        for task in self.tasks:
            if task.formation_id is not None and task.formation_id not in formations:
                raise ValueError("unknown task formation")
            if task.route_id is not None:
                if task.route_id not in routes:
                    raise ValueError("unknown task route")
                if task.route_id in claimed_routes:
                    raise ValueError("route assigned to multiple tasks")
                claimed_routes.add(task.route_id)
        points = [node.position for node in self.nodes]
        points += [point for task in self.tasks for point in (task.start, task.target)]
        points += [point for route in self.routes for point in route.waypoints]
        points += [point for region in self.environment for point in (region.lower, region.upper)]
        if any(not self.bounds.contains(point) for point in points):
            raise ValueError("position outside scenario map")
        return self
