"""Shared deterministic facts; neither propagation nor evaluation is encoded here."""

from math import hypot

from ..models.domain import EnvironmentConstraint, Node, NodeStatus, Position, Route, Task


def effective_capabilities(node: Node) -> dict[str, float]:
    factor = 0 if node.status == NodeStatus.FAILED else node.health if node.status == NodeStatus.DEGRADED else 1
    return {capability: amount * factor for capability, amount in node.capabilities.items()}


def eligible(node: Node, task: Task) -> bool:
    return (node.status != NodeStatus.FAILED and
            (node.status != NodeStatus.DEGRADED or node.health > 0) and
            node.availability.start <= task.window.start and node.availability.end >= task.window.end)


def segment_intersects_rectangle(start: Position, end: Position, region: EnvironmentConstraint) -> bool:
    """Closed slab intersection: touching an edge/corner also conflicts."""
    enter, leave = 0.0, 1.0
    for origin, delta, lower, upper in [
        (start.x, end.x - start.x, region.lower.x, region.upper.x),
        (start.y, end.y - start.y, region.lower.y, region.upper.y),
    ]:
        if delta == 0:
            if origin < lower or origin > upper:
                return False
        else:
            first, last = sorted(((lower - origin) / delta, (upper - origin) / delta))
            enter, leave = max(enter, first), min(leave, last)
            if enter > leave:
                return False
    return True


def route_intersects(route: Route, region: EnvironmentConstraint) -> bool:
    return any(segment_intersects_rectangle(a, b, region) for a, b in zip(route.waypoints, route.waypoints[1:]))


def distance_to_route(point: Position, route: Route | None) -> float:
    """Euclidean distance to a continuous polyline, including zero-length edges."""
    if route is None:
        return 0.0
    values = []
    for a, b in zip(route.waypoints, route.waypoints[1:]):
        dx, dy = b.x - a.x, b.y - a.y
        fraction = max(0, min(1, ((point.x-a.x)*dx + (point.y-a.y)*dy)/(dx*dx + dy*dy))) if dx or dy else 0
        values.append(hypot(point.x-a.x-fraction*dx, point.y-a.y-fraction*dy))
    return min(values)
