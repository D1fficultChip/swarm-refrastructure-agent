from math import hypot

from ..models.domain import Position, Route


def route_length(route: Route) -> float:
    return sum(hypot(b.x-a.x, b.y-a.y) for a,b in zip(route.waypoints, route.waypoints[1:]))


def interpolate_route(route: Route, progress: float) -> Position:
    progress = min(1.0, max(0.0, progress))
    lengths = [hypot(b.x-a.x, b.y-a.y) for a,b in zip(route.waypoints, route.waypoints[1:])]
    total = sum(lengths)
    if total <= 1e-9:
        return route.waypoints[-1].model_copy(deep=True)
    target = progress * total
    travelled = 0.0
    for (a,b),length in zip(zip(route.waypoints, route.waypoints[1:]), lengths):
        if travelled + length >= target:
            ratio = (target-travelled)/length if length else 0
            return Position(x=a.x+(b.x-a.x)*ratio, y=a.y+(b.y-a.y)*ratio)
        travelled += length
    return route.waypoints[-1].model_copy(deep=True)
