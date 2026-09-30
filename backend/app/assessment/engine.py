from collections import defaultdict

from ..models.domain import EnvironmentKind, NodeStatus, ScenarioState, Task, TaskStatus
from ..models.phase2 import (
    CapabilityCheck, CapabilityDetails, ConstraintCheck, ConstraintStatus as S,
    FormationDetails, ImpactAnalysisResult, ImpactLevel, InactiveDetails,
    ResourceDetails, RouteDetails, TaskAssessmentResult, TaskDecision, TaskEntityStates, TimingDetails,
)
from .facts import effective_capabilities, eligible, route_intersects

EPSILON = 1e-9


def capability_checks(requirements, profile):
    return {cap: CapabilityCheck(required=amount, available=profile.get(cap, 0),
                                 gap=max(0, amount - profile.get(cap, 0)))
            for cap, amount in sorted(requirements.items())}


class TaskAssessmentEngine:
    def assess(self, state: ScenarioState, impact: ImpactAnalysisResult | None = None,
               *, full_check: bool = False) -> list[TaskAssessmentResult]:
        if not full_check and impact is None:
            raise ValueError("Incremental assessment requires an impact result; full_check must be explicit")
        if impact is not None and (impact.scenario_id != state.scenario_id or impact.state_version_after != state.version):
            raise ValueError("Impact result is for a different scenario or state version")
        tasks = {task.id: task for task in state.tasks}
        selected = sorted(tasks if full_check else set(impact.affected_tasks))
        if not set(selected).issubset(tasks):
            raise ValueError("Impact references an unknown task")
        # Index all assignments once; only selected tasks receive constraint evaluation.
        by_formation = defaultdict(list)
        for task in state.tasks:
            if task.status == TaskStatus.ACTIVE and task.formation_id:
                by_formation[task.formation_id].append(task)
        nodes = {node.id: node for node in state.nodes}
        formations = {formation.id: formation for formation in state.formations}
        routes = {route.id: route for route in state.routes}
        return [self._assess_task(state, tasks[key], impact, nodes, formations, routes, by_formation) for key in selected]

    def _assess_task(self, state, task: Task, impact, nodes, formations, routes, by_formation):
        relevant = [] if impact is None else [record for record in impact.impact_records if record.entity == f"task:{task.id}"]
        level = None
        if relevant:
            level = min((record.impact_level for record in relevant),
                        key=lambda value: [ImpactLevel.CRITICAL, ImpactLevel.AFFECTED, ImpactLevel.WEAK].index(value))
        related = sorted({key for record in relevant for key in record.path})
        formation = formations.get(task.formation_id)
        members = [] if formation is None else [nodes[key] for key in formation.node_ids]
        available = [node for node in members if eligible(node, task)]
        route = routes.get(task.route_id)
        checks = []
        if task.status != TaskStatus.ACTIVE:
            code = "TASK_CANCELLED" if task.status == TaskStatus.CANCELLED else "TASK_ALREADY_ABORTED"
            checks = [ConstraintCheck(type=kind, status=S.NOT_APPLICABLE, reason_codes=[code],
                                      details=InactiveDetails(task_status=task.status.value))
                      for kind in ["capability", "resource", "formation", "route", "priority_timing"]]
            decision = TaskDecision.KEEP if task.status == TaskStatus.CANCELLED else TaskDecision.ABORT
        else:
            profile = defaultdict(float)
            for node in available:
                for capability, amount in effective_capabilities(node).items():
                    profile[capability] += amount
            supply = capability_checks(task.requirements, profile)
            shortage = any(check.gap > EPSILON for check in supply.values())
            checks.append(ConstraintCheck(
                type="capability", status=S.FAIL if shortage else S.PASS,
                reason_codes=["CAPABILITY_SHORTAGE"] if shortage else [],
                details=CapabilityDetails(capabilities=supply, eligible_node_ids=[node.id for node in available]),
            ))
            peers = by_formation.get(task.formation_id, [])
            resource = sum(node.resource_remaining for node in available)
            pool = sum(node.resource_remaining for node in members
                       if node.status != NodeStatus.FAILED and (node.status != NodeStatus.DEGRADED or node.health > 0))
            # Conservative commitment check across all ACTIVE assigned tasks. No
            # resource consumption is simulated and no completed lifecycle exists yet.
            commitment = sum(peer.resource_required for peer in peers)
            resource_codes = []
            if resource + EPSILON < task.resource_required:
                resource_codes.append("RESOURCE_SHORTAGE")
            if pool + EPSILON < commitment:
                resource_codes.append("SHARED_RESOURCE_OVERCOMMITTED")
            checks.append(ConstraintCheck(
                type="resource", status=S.FAIL if resource_codes else S.PASS, reason_codes=resource_codes,
                details=ResourceDetails(required=task.resource_required, available=resource,
                                        active_node_count=len(available), shared_commitment=commitment,
                                        shared_available=pool, sharing_task_ids=sorted(peer.id for peer in peers)),
            ))
            minimums = capability_checks(formation.minimum_capabilities if formation else {}, profile)
            formation_codes = []
            if formation is None:
                formation_codes.append("TASK_UNASSIGNED")
            if len(available) < task.min_nodes:
                formation_codes.append("INSUFFICIENT_ACTIVE_NODES")
            if any(check.gap > EPSILON for check in minimums.values()):
                formation_codes.append("FORMATION_CAPABILITY_SHORTAGE")
            formation_status = S.FAIL if formation_codes else S.PASS
            if not formation_codes and any(node.status == NodeStatus.DEGRADED for node in available):
                formation_codes.append("FORMATION_DEGRADED")
                formation_status = S.DEGRADED
            checks.append(ConstraintCheck(
                type="formation", status=formation_status, reason_codes=formation_codes,
                details=FormationDetails(formation_id=task.formation_id, exists=formation is not None,
                    minimum_nodes=task.min_nodes, active_node_count=len(available),
                    unavailable_node_ids=sorted(node.id for node in members if not eligible(node, task)),
                    minimum_capabilities=minimums),
            ))
            route_codes = []
            start_valid = route is not None and route.waypoints[0] == task.start
            end_valid = route is not None and route.waypoints[-1] == task.target
            geometry_valid = (route is not None and len(route.waypoints) >= 2
                              and all(state.bounds.contains(point) for point in route.waypoints))
            conflicts = [] if route is None else sorted(region.id for region in state.environment
                if region.kind == EnvironmentKind.RESTRICTED and route_intersects(route, region))
            if route is None:
                route_codes.append("ROUTE_MISSING")
            else:
                if not start_valid or not end_valid:
                    route_codes.append("ROUTE_ENDPOINT_MISMATCH")
                if not geometry_valid:
                    route_codes.append("ROUTE_GEOMETRY_INVALID")
                if conflicts:
                    route_codes.append("ROUTE_RESTRICTED_AREA_CONFLICT")
            checks.append(ConstraintCheck(
                type="route", status=S.FAIL if route_codes else S.PASS, reason_codes=route_codes,
                details=RouteDetails(route_id=task.route_id, exists=route is not None, start_valid=start_valid,
                    end_valid=end_valid, geometry_valid=geometry_valid, conflicting_region_ids=conflicts),
            ))
            timing_conflicts = sorted(peer.id for peer in peers if peer.id != task.id and peer.window.overlaps(task.window))
            expired = state.clock >= task.window.end
            timing_codes = (["TASK_WINDOW_EXPIRED"] if expired else []) + (["ASSIGNMENT_TIME_CONFLICT"] if timing_conflicts else [])
            checks.append(ConstraintCheck(
                type="priority_timing", status=S.FAIL if timing_codes else S.PASS, reason_codes=timing_codes,
                details=TimingDetails(clock=state.clock, window_start=task.window.start, window_end=task.window.end,
                                      priority=task.priority, conflicting_task_ids=timing_conflicts, expired=expired),
            ))
            decision = (TaskDecision.RECONSTRUCT if any(check.status == S.FAIL for check in checks)
                        else TaskDecision.ADJUST if any(check.status == S.DEGRADED for check in checks)
                        else TaskDecision.KEEP)
        return TaskAssessmentResult(
            task_id=task.id, state_version=state.version, impact_level=level, decision=decision,
            constraints=checks, reason_codes=sorted({code for check in checks for code in check.reason_codes}),
            triggered_by=[impact.event_id] if relevant else [], affected_entities=related,
            entity_states=TaskEntityStates(
                task_status=task.status.value, node_states={node.id: node.status.value for node in members},
                formation_state="MISSING" if formation is None else "MEMBERS_UNAVAILABLE" if len(available) < len(members)
                    else "DEGRADED" if any(node.status == NodeStatus.DEGRADED for node in members) else "NORMAL",
                route_state="MISSING" if route is None else next(check.status.value for check in checks if check.type == "route"),
            ),
            feasible_under_evaluated_constraints=task.status == TaskStatus.ACTIVE and not any(check.status == S.FAIL for check in checks),
        )
