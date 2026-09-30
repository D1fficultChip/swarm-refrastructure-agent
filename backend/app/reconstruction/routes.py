import heapq
import math
from time import perf_counter

from ..assessment.facts import route_intersects, segment_intersects_rectangle
from ..assessment.facts import distance_to_route as deviation
from ..models.domain import EnvironmentKind, Position, Route
from ..models.phase3 import RouteChange, RouteChangeRequest, RouteDecision, RoutePlanResult, SolverStatus
from .common import unique_id


def distance(a, b):
    return math.hypot(a.x-b.x, a.y-b.y)


class RouteImpactDetector:
    def detect(self, state, task_ids):
        routes = {r.id: r for r in state.routes}
        requests, decisions = [], []
        for task in state.tasks:
            if task.id not in task_ids:
                continue
            route = routes.get(task.route_id)
            codes = []
            if route is None:
                codes.append("ROUTE_MISSING")
            else:
                if route.waypoints[0] != task.start:
                    codes.append("START_REFERENCE_CHANGED")
                if route.waypoints[-1] != task.target:
                    codes.append("TARGET_OR_ENDPOINT_CHANGED")
                if any(c.kind == EnvironmentKind.RESTRICTED and route_intersects(route, c) for c in state.environment):
                    codes.append("RESTRICTED_AREA_INTERSECTION")
            route_id = task.route_id or unique_id(state, f"R_NEW_{task.id}")
            decisions.append(RouteDecision(task_id=task.id, route_id=route_id, replan=bool(codes),
                reason_codes=codes or ["EXISTING_ROUTE_STILL_VALID"]))
            if codes:
                requests.append(RouteChangeRequest(task_id=task.id, route_id=route_id, start=task.start,
                    target=task.target, previous_route=route, reason_codes=codes))
        return requests, decisions


class RoutePlanner:
    def __init__(self, config):
        self.config = config

    def plan(self, state, request):
        started = perf_counter()
        blocked = [r for r in state.environment if r.kind == EnvironmentKind.RESTRICTED]
        expanded = 0
        def legal(a, b):
            return state.bounds.contains(a) and state.bounds.contains(b) and not any(segment_intersects_rectangle(a,b,r) for r in blocked)
        def failed(code, status=SolverStatus.INFEASIBLE):
            return RoutePlanResult(task_id=request.task_id, status=status, reason_codes=[code],
                                   expanded_cells=expanded, elapsed_ms=(perf_counter()-started)*1000)
        if not legal(request.start, request.start) or not legal(request.target, request.target):
            return failed("ROUTE_ENDPOINT_BLOCKED")
        step = state.bounds.grid_resolution
        nx, ny = math.floor(state.bounds.width/step)+1, math.floor(state.bounds.height/step)+1
        if nx*ny > self.config.max_grid_cells:
            return failed("GRID_BUDGET_EXCEEDED", SolverStatus.PARTIAL)
        # Virtual exact endpoints connect to visible grid vertices in surrounding
        # cells. Every connection is checked continuously, including diagonals.
        start_key, end_key = (-1,-1), (-2,-2)
        def point(key):
            return request.start if key == start_key else request.target if key == end_key else Position(x=key[0]*step, y=key[1]*step)
        def nearby(p):
            x,y = math.floor(p.x/step),math.floor(p.y/step)
            return [(i,j) for i in range(max(0,x-1),min(nx,x+3)) for j in range(max(0,y-1),min(ny,y+3))]
        starts, ends = nearby(request.start), set(nearby(request.target))
        def neighbors(key):
            if key == start_key:
                return starts + ([end_key] if legal(request.start,request.target) else [])
            x,y = key
            result = [(x+dx,y+dy) for dx in [-1,0,1] for dy in [-1,0,1]
                      if (dx or dy) and 0<=x+dx<nx and 0<=y+dy<ny]
            if key in ends:
                result.append(end_key)
            return result
        queue = [(distance(request.start,request.target),0.0,start_key)]
        best, parent = {start_key:0.0}, {}
        while queue:
            _, cost, key = heapq.heappop(queue)
            if cost != best.get(key):
                continue
            if key == end_key:
                path = [key]
                while key != start_key:
                    key = parent[key]
                    path.append(key)
                points = [point(k) for k in reversed(path)]
                points = [p for i,p in enumerate(points) if i == 0 or p != points[i-1]]
                if len(points) == 1:
                    points.append(points[0].model_copy(deep=True))
                route = Route(id=request.route_id,waypoints=points)
                if not all(legal(a,b) for a,b in zip(points,points[1:])):
                    return failed("LOCAL_ROUTE_CHECK_FAILED", SolverStatus.ERROR)
                length = sum(distance(a,b) for a,b in zip(points,points[1:]))
                change = RouteChange(task_id=request.task_id,route_id=request.route_id,before=request.previous_route,
                    after=route,reason_codes=request.reason_codes,path_length=length,
                    deviation_cost=sum(distance(a,b)*(deviation(a,request.previous_route)+deviation(b,request.previous_route))/(2*step)
                                       for a,b in zip(points,points[1:])))
                return RoutePlanResult(task_id=request.task_id,status=SolverStatus.FEASIBLE,change=change,
                    reason_codes=["CONTINUOUS_SEGMENT_CHECK_PASSED"],expanded_cells=expanded,elapsed_ms=(perf_counter()-started)*1000)
            expanded += 1
            if expanded > self.config.max_route_expansions:
                return failed("ROUTE_SEARCH_BUDGET_EXCEEDED",SolverStatus.PARTIAL)
            a = point(key)
            for nxt in neighbors(key):
                b = point(nxt)
                if not legal(a,b):
                    continue
                length = distance(a,b)
                penalty = length*(deviation(a,request.previous_route)+deviation(b,request.previous_route))/(2*step)
                new = cost + length + self.config.route_deviation_weight*penalty
                if new < best.get(nxt,float("inf")):
                    best[nxt],parent[nxt] = new,key
                    heapq.heappush(queue,(new+distance(b,request.target),new,nxt))
        return failed("NO_GRID_ROUTE")
