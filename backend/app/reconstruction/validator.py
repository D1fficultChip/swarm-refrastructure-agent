"""Global constraint evaluation independent of local solver feasibility.

Only pure capability/geometry facts are shared with Phase 2. Every ACTIVE task
is checked, including unchanged tasks; proposal feasibility flags are not authority.
"""
from math import hypot, isclose
from time import perf_counter

from ..assessment.facts import distance_to_route, effective_capabilities, eligible, route_intersects
from ..models.domain import EnvironmentKind, NodeStatus, ScenarioState, TaskStatus
from ..models.phase3 import ReconstructionProposal, StrategyLevel
from ..models.phase4 import ValidationCheck, ValidationCoverage, ValidationResult, ValidationStatus
from .common import digest
from .materializer import MaterializationError, ProposalMaterializer


IMPLEMENTED = ("preconditions", "structure", "observed_facts", "membership", "capability", "formation",
               "resource", "time_window", "route", "scope", "metadata", "regression")
LIMITATIONS = ("travel_time", "transfer_time", "dynamic_energy", "formation_geometry", "dynamic_scheduling")


def check(code, category, subject, passed, **details):
    return ValidationCheck(code=code, category=category, subject=subject, passed=bool(passed), details=details)


def mission_checks(state):
    """No solver/assessment calls; recompute from observations and assignments."""
    nodes = {n.id: n for n in state.nodes}
    formations = {f.id: f for f in state.formations}
    routes = {r.id: r for r in state.routes}
    active = [t for t in state.tasks if t.status == TaskStatus.ACTIVE]
    per_task, per_formation, per_route = {}, {}, {}
    for task in active:
        checks = per_task[task.id] = []
        formation = formations.get(task.formation_id)
        checks.append(check("FORMATION_MISSING", "formation", task.id, formation is not None))
        members = [nodes[n] for n in formation.node_ids] if formation else []
        valid = [n for n in members if eligible(n, task)]
        amounts = {}
        for node in valid:
            for cap, amount in effective_capabilities(node).items():
                amounts[cap] = amounts.get(cap, 0) + amount
        checks.append(check("FAILED_NODE_ASSIGNED", "membership", task.id,
                            all(n.status != NodeStatus.FAILED for n in members),
                            failed_nodes=[n.id for n in members if n.status == NodeStatus.FAILED]))
        checks.append(check("MEMBER_UNAVAILABLE", "time_window", task.id, len(valid) == len(members),
                            unavailable_nodes=[n.id for n in members if n not in valid]))
        checks.append(check("CAPABILITY_SHORTAGE", "capability", task.id,
                            all(amounts.get(c, 0) >= v for c, v in task.requirements.items()),
                            required=task.requirements, available=amounts))
        checks.append(check("MINIMUM_NODE_COUNT", "formation", task.id, len(valid) >= task.min_nodes,
                            required=task.min_nodes, available=len(valid)))
        checks.append(check("FORMATION_CAPABILITY_SHORTAGE", "formation", task.id,
                            formation is not None and all(amounts.get(c, 0) >= v for c, v in formation.minimum_capabilities.items()),
                            required=formation.minimum_capabilities if formation else {}, available=amounts))
        available = sum(n.resource_remaining for n in valid)
        checks.append(check("RESOURCE_SHORTAGE", "resource", task.id, available >= task.resource_required,
                            required=task.resource_required, available=available))
        checks.append(check("TASK_DEADLINE_EXPIRED", "time_window", task.id, state.clock < task.window.end,
                            clock=state.clock, deadline=task.window.end))
        conflicts = [t.id for t in active if t.id != task.id and task.formation_id is not None and
                     t.formation_id == task.formation_id and t.window.overlaps(task.window)]
        checks.append(check("TASK_TIME_CONFLICT", "time_window", task.id, not conflicts, conflicts=conflicts))
        route = routes.get(task.route_id)
        route_checks = [check("ROUTE_MISSING", "route", task.id, route is not None)]
        if route:
            route_checks += [
                check("ROUTE_ENDPOINT_MISMATCH", "route", task.id,
                      route.waypoints[0] == task.start and route.waypoints[-1] == task.target),
                check("ROUTE_GEOMETRY_INVALID", "route", task.id,
                      len(route.waypoints) >= 2 and all(state.bounds.contains(p) for p in route.waypoints)),
            ]
            blocked = [r.id for r in state.environment if r.kind == EnvironmentKind.RESTRICTED and route_intersects(route, r)]
            route_checks.append(check("ROUTE_RESTRICTED_AREA_COLLISION", "route", task.id, not blocked, region_ids=blocked))
        per_route[task.id] = route_checks
        checks.extend(route_checks)
    for formation in state.formations:
        assigned = [t for t in active if t.formation_id == formation.id]
        if not assigned:
            continue
        # Resource is a non-replenishing stock in this domain: all ACTIVE
        # commitments count, including disjoint windows (same Phase 2 policy).
        available = sum(nodes[n].resource_remaining for n in formation.node_ids
                        if nodes[n].status != NodeStatus.FAILED and nodes[n].health > 0)
        required = sum(t.resource_required for t in assigned)
        shared = check("SHARED_RESOURCE_OVERCOMMITTED", "resource", formation.id, required <= available,
                       required=required, available=available, task_ids=[t.id for t in assigned])
        per_formation[formation.id] = [shared] + [c for t in assigned for c in per_task[t.id]
                                                if c.category in {"membership", "formation", "time_window"}]
        for task in assigned:
            per_task[task.id].append(shared)
    return per_task, per_formation, per_route


def factual_projection(state):
    data = state.model_dump(mode="json")
    data.pop("routes")
    data["formations"] = {f.id: f.minimum_capabilities for f in state.formations}
    for task in data["tasks"]:
        for field in ("formation_id", "route_id", "start"):
            task.pop(field)
    return data


class GlobalConstraintValidator:
    def validate_proposal(self, observed_state: ScenarioState, proposal: ReconstructionProposal) -> ValidationResult:
        started = perf_counter()
        result = ValidationResult(proposal_id=proposal.proposal_id, base_state_version=proposal.base_state_version)
        checks = result.global_invariants

        def finish():
            all_checks = checks + [c for values in result.per_task_results.values() for c in values]
            # Shared formation checks appear under several tasks: count each once.
            unique = {(c.category, c.code, c.subject): c for c in all_checks}
            result.validated_constraints = list(unique.values())
            result.hard_failures = [c for c in unique.values() if not c.passed]
            categories = {c.category for c in unique.values()}
            result.coverage = [ValidationCoverage(constraint=name,
                status="VALIDATED" if name in categories else "NOT_RUN",
                explanation="Implemented checks executed (see results)" if name in categories else "Stopped before this stage or no applicable entity")
                for name in IMPLEMENTED]
            has_active_tasks = any(t.status == TaskStatus.ACTIVE for t in observed_state.tasks)
            result.not_evaluated = list(LIMITATIONS) if has_active_tasks else []
            result.coverage += [ValidationCoverage(constraint=name,
                                status="NOT_EVALUATED" if has_active_tasks else "NOT_APPLICABLE",
                                explanation="No such physical/scheduling model in the current domain" if has_active_tasks
                                            else "No ACTIVE task") for name in LIMITATIONS]
            result.status = (ValidationStatus.FAIL if result.hard_failures else
                             ValidationStatus.PASS_WITH_LIMITATIONS if result.not_evaluated else ValidationStatus.PASS)
            result.metrics.validated_tasks = len(result.per_task_results)
            result.metrics.validated_formations = len(result.per_formation_results)
            result.metrics.validated_routes = len(result.route_results)
            result.metrics.hard_constraint_count = len(unique)
            result.metrics.failed_constraint_count = len(result.hard_failures)
            result.metrics.warning_count = len(result.warnings)
            result.metrics.not_evaluated_count = len(result.not_evaluated)
            result.validation_time_ms = (perf_counter() - started) * 1000
            return result

        checks.extend([
            check("STATE_VERSION_MISMATCH", "preconditions", proposal.proposal_id, observed_state.version == proposal.base_state_version),
            check("STATE_DIGEST_MISMATCH", "preconditions", proposal.proposal_id, digest(observed_state) == proposal.base_state_digest),
        ])
        if any(not c.passed for c in checks):
            return finish()
        try:
            candidate = ProposalMaterializer().materialize(observed_state, proposal)
        except MaterializationError as exc:
            checks.append(check(exc.code, "structure", proposal.proposal_id, False, message=str(exc)))
            return finish()
        result.candidate_state_digest = digest(candidate)
        checks.append(check("STRUCTURAL_INTEGRITY", "structure", candidate.scenario_id, True))
        members = [n for f in candidate.formations for n in f.node_ids]
        checks.append(check("NODE_MULTIPLE_FORMATIONS", "membership", candidate.scenario_id, len(members) == len(set(members))))
        old_facts, new_facts = factual_projection(observed_state), factual_projection(candidate)
        new_facts["formations"] = {k: v for k, v in new_facts["formations"].items() if k in old_facts["formations"]}
        checks.append(check("OBSERVED_FACT_MUTATION", "observed_facts", candidate.scenario_id, old_facts == new_facts))
        result.per_task_results, result.per_formation_results, result.route_results = mission_checks(candidate)
        baseline, _, _ = mission_checks(observed_state)
        self._scope_and_metadata(observed_state, candidate, proposal, baseline, result)
        for task_id, previous in baseline.items():
            if task_id not in proposal.reconstruction_scope.affected_tasks and all(c.passed for c in previous):
                checks.append(check("UNRELATED_TASK_REGRESSION", "regression", task_id,
                                    all(c.passed for c in result.per_task_results[task_id])))
        if any(n.status == NodeStatus.DEGRADED for n in candidate.nodes):
            result.warnings.append("DEGRADED_CAPABILITY_USES_STATIC_HEALTH_SCALING")
        return finish()

    def _scope_and_metadata(self, before, after, proposal, baseline, result):
        checks = result.global_invariants
        scope = proposal.reconstruction_scope
        tasks = {t.id: t for t in before.tasks}
        old_forms = {f.id: f for f in before.formations}
        new_forms = {f.id: f for f in after.formations}
        nodes = {n.id: n for n in before.nodes}
        old_routes = {r.id: r for r in before.routes}
        actual_formation_ids = {f.id for f in after.formations if old_forms.get(f.id) != f}
        actual_route_ids = {r.id for r in after.routes if old_routes.get(r.id) != r}
        actual_task_ids = {t.id for t in after.tasks if tasks[t.id].formation_id != t.formation_id or tasks[t.id].start != t.start}
        actual_route_assignments = {t.id: t.route_id for t in after.tasks if tasks[t.id].route_id != t.route_id}
        declared_route_assignments = {r.task_id: r.route_id for r in proposal.route_changes if tasks[r.task_id].route_id != r.route_id}
        checks.append(check("DELTA_MISMATCH", "metadata", "actual_delta",
            actual_formation_ids == {f.formation_id for f in proposal.formation_changes} and
            actual_route_ids == {r.route_id for r in proposal.route_changes} and
            actual_task_ids == {t.task_id for t in proposal.task_changes} and
            actual_route_assignments == declared_route_assignments and
            set(old_forms) <= set(new_forms) and set(old_routes) <= {r.id for r in after.routes} and
            all(new_forms[f.formation_id] == f.after for f in proposal.formation_changes)))
        # Scope is not trusted to define its own original impact.
        impacted = {key for key, values in baseline.items() if any(not c.passed for c in values)}
        impacted |= {t.id for t in before.tasks if t.status == TaskStatus.ACTIVE and t.formation_id in old_forms and
                     any(nodes[n].status == NodeStatus.DEGRADED for n in old_forms[t.formation_id].node_ids)}
        original_forms = {tasks[key].formation_id for key in impacted} - {None}
        original_nodes = {n for key in original_forms for n in old_forms[key].node_ids}
        original_routes = {tasks[key].route_id for key in impacted} - {None}
        actual_tasks = {t.task_id for t in proposal.task_changes} | {r.task_id for r in proposal.route_changes}
        actual_forms = {f.formation_id for f in proposal.formation_changes} | {t.after_formation for t in proposal.task_changes}
        actual_nodes = {n.node_id for n in proposal.node_changes}
        actual_routes = {r.route_id for r in proposal.route_changes}
        for kind, actual, declared, original in [
            ("task", actual_tasks, set(scope.affected_tasks), impacted),
            ("formation", actual_forms, set(scope.affected_formations), original_forms),
            ("node", actual_nodes, set(scope.affected_nodes), original_nodes),
            ("route", actual_routes, set(scope.potentially_affected_routes), original_routes),
        ]:
            missing = actual - declared
            unexplained = {key for key in declared - original if not scope.scope_expansion_reason.get(f"{kind}:{key}", "").strip()}
            checks.append(check("OUT_OF_SCOPE_MUTATION", "scope", kind, not missing and not unexplained,
                                undeclared=sorted(missing), unexplained=sorted(unexplained)))
        checks.append(check("PROPOSAL_METADATA_MISMATCH", "metadata", "scope_version",
                            scope.state_version == before.version and scope.source_events == proposal.source_events))
        # Independent cost measurement from actual states, not claimed deltas.
        changed_forms = [f for f in after.formations if old_forms.get(f.id) != f]
        new_count = sum(f.id not in old_forms for f in changed_forms)
        member_count = sum(len(set(f.node_ids) ^ set(old_forms[f.id].node_ids if f.id in old_forms else [])) for f in changed_forms)
        changed_tasks = [t for t in after.tasks if tasks[t.id].formation_id != t.formation_id or tasks[t.id].start != t.start]
        reassigned = sum(tasks[t.id].formation_id != t.formation_id for t in changed_tasks)
        old_owner = {n: f.id for f in before.formations for n in f.node_ids}
        new_owner = {n: f.id for f in after.formations for n in f.node_ids}
        switched = {n for n in old_owner.keys() | new_owner.keys() if old_owner.get(n) != new_owner.get(n)}
        distance = sum(hypot(tasks[t.id].start.x - t.start.x, tasks[t.id].start.y - t.start.y) for t in changed_tasks)
        for n in switched:
            assigned = sorted([t for t in after.tasks if t.status == TaskStatus.ACTIVE and t.formation_id == new_owner.get(n)],
                              key=lambda t: (t.window.start, t.id)) if n in new_owner else []
            if assigned:
                distance += hypot(nodes[n].position.x - assigned[0].start.x, nodes[n].position.y - assigned[0].start.y)
        cost = proposal.cost_breakdown
        expected = [reassigned, new_count, len(changed_forms), member_count, len(actual_route_ids), len(switched), distance, 0]
        declared = [cost.task_reassignment_count, cost.new_formations, cost.formations_changed, cost.formation_membership_changes,
                    cost.route_change_count, cost.node_switch_count, cost.distance_cost, cost.resource_usage_cost]
        checks.append(check("PROPOSAL_METADATA_MISMATCH", "metadata", "cost",
                            len(cost.lexicographic_cost) == len(expected) and
                            all(isclose(a, b, rel_tol=1e-9, abs_tol=1e-9) for a, b in zip(expected, declared)) and
                            all(isclose(a, b, rel_tol=1e-9, abs_tol=1e-9) for a, b in zip(expected, cost.lexicographic_cost)) and cost.resource_cost == 0,
                            expected=expected, declared=declared))
        expected_level = (StrategyLevel.FORMATION_RECONSTRUCTION if new_count else StrategyLevel.TASK_REASSIGNMENT if reassigned
                          else StrategyLevel.IN_PLACE_REPAIR if changed_forms else None)
        checks.append(check("PROPOSAL_METADATA_MISMATCH", "metadata", "strategy", proposal.strategy_level == expected_level and
                            proposal.strategy_name == ("HIERARCHICAL_BOUNDED_BACKTRACKING" if scope.affected_tasks else "NO_CHANGE_REQUIRED")))
        for change in proposal.formation_changes:
            def profile(ids):
                values = {}
                for n in ids:
                    for cap, amount in effective_capabilities(nodes[n]).items():
                        values[cap] = values.get(cap, 0) + amount
                return values
            required = dict(change.after.minimum_capabilities)
            for task in after.tasks:
                if task.status == TaskStatus.ACTIVE and task.formation_id == change.formation_id:
                    for cap, amount in task.requirements.items():
                        required[cap] = max(required.get(cap, 0), amount)
            current = profile(change.before.node_ids if change.before else [])
            checks.append(check("PROPOSAL_METADATA_MISMATCH", "metadata", change.formation_id,
                change.required_capabilities == required and change.current_capabilities == current and
                change.capability_gap == {c: max(0, v - current.get(c, 0)) for c, v in required.items()} and
                change.result_capabilities == profile(change.after.node_ids) and
                change.candidate_contributions == {n: profile([n]) for n in change.added_nodes}))
        for change in proposal.route_changes:
            length = sum(hypot(a.x-b.x, a.y-b.y) for a, b in zip(change.after.waypoints, change.after.waypoints[1:]))
            deviation = sum(hypot(a.x-b.x, a.y-b.y) * (distance_to_route(a, change.before) + distance_to_route(b, change.before))
                            / (2 * before.bounds.grid_resolution) for a, b in zip(change.after.waypoints, change.after.waypoints[1:]))
            checks.append(check("PROPOSAL_METADATA_MISMATCH", "metadata", change.route_id,
                                isclose(length, change.path_length, rel_tol=1e-9, abs_tol=1e-9) and
                                isclose(deviation, change.deviation_cost, rel_tol=1e-9, abs_tol=1e-9)))
        for decision in proposal.trace.route_decisions:
            changed = [r for r in proposal.route_changes if r.task_id == decision.task_id]
            consistent = not changed or (decision.replan and decision.route_id == changed[0].route_id)
            checks.append(check("PROPOSAL_METADATA_MISMATCH", "metadata", f"route_decision:{decision.task_id}", consistent))
        categories = {"priority_timing": "time_window"}
        for outcome in proposal.recovered_constraints:
            category = categories.get(outcome.check.type, outcome.check.type)
            previous = [c for c in baseline.get(outcome.task_id, []) if c.category == category]
            current = [c for c in result.per_task_results.get(outcome.task_id, []) if c.category == category]
            checks.append(check("PROPOSAL_METADATA_MISMATCH", "metadata", f"recovered:{outcome.task_id}:{category}",
                                bool(previous) and any(not c.passed for c in previous) and bool(current) and all(c.passed for c in current)))
