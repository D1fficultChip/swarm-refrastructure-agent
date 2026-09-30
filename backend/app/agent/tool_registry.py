from dataclasses import dataclass

from ..models.domain import TaskStatus
from ..models.phase3 import SolverStatus, StrategyAttempt, StrategyLevel
from ..reconstruction.common import apply_option, digest, preview
from ..reconstruction.engine import proposal_digest
from .action import (ActionRejected, CandidateArgs, DetectArgs, EmptyArgs, ExpansionArgs, RepairArgs, TaskArgs)
from .models import ScopeExpansion
from .observation import ObservationAdapter


@dataclass(frozen=True)
class ToolSpec:
    description: str
    arguments: type


def tool_registry():
    return {
        "get_current_state_summary": ToolSpec("Read current session facts; refresh and discard stale working plan if version changed.", EmptyArgs),
        "get_outstanding_violations": ToolSpec("Inspect current private plan's independently computed mission violations.", EmptyArgs),
        "inspect_task_state": ToolSpec("Inspect one task's actual capability/resource/window/route checks and shared tasks.", TaskArgs),
        "generate_candidates": ToolSpec("Query legal spare candidates for this task; does not apply a plan or call a solver.", CandidateArgs),
        "try_in_place_repair": ToolSpec("Run ONLY L1; apply requested option_index (default 0) privately; return other options. No escalation.", RepairArgs),
        "find_task_reassignment": ToolSpec("Run ONLY L2 on existing formations; apply selected option privately. Allowed without trying L1.", RepairArgs),
        "try_formation_reconstruction": ToolSpec("Run ONLY L3; build a new formation privately using a solver option.", RepairArgs),
        "detect_route_impacts": ToolSpec("Detect missing, endpoint-changed or blocked routes; no route planning.", DetectArgs),
        "replan_route": ToolSpec("Plan route for task_id from actual geometry, apply privately. You cannot provide arbitrary waypoints.", TaskArgs),
        "request_scope_expansion": ToolSpec("Explicitly extend scope with entity, reason, and optional source_validation_id ('latest' supported).", ExpansionArgs),
        "discard_working_proposal": ToolSpec("Discard all private deltas to choose different options; retain approved scope expansion and feedback history.", EmptyArgs),
        "validate_proposal": ToolSpec("Independently validate current proposal against actual current session. Does not commit.", EmptyArgs),
        "commit_validated_proposal": ToolSpec("Only after matching authoritative PASS; original transaction revalidates under lock and commits atomically.", EmptyArgs),
    }


def tool_schemas(registry):
    return [{"name": name, "description": spec.description, "parameters": spec.arguments.model_json_schema()}
            for name, spec in registry.items()]


class ToolExecutor:
    def __init__(self, workspace, state, config):
        self.w, self.state, self.config = workspace, state, config
        self.adapter = ObservationAdapter()
        self.receipt = None

    def task_summary(self, task_id):
        w = self.w
        task = next(t for t in w.working.tasks if t.id == task_id)
        assessment = next(a for a in w.tools.assess_current_task_state(w.working) if a.task_id == task_id)
        return {"task_id": task.id, "formation_id": task.formation_id, "route_id": task.route_id,
            "priority": task.priority, "window": task.window.model_dump(), "requirements": task.requirements,
            "checks": [c.model_dump(mode="json") for c in assessment.constraints],
            "sharing_tasks": [{"task_id": t.id, "window": t.window.model_dump(), "requirements": t.requirements}
                              for t in w.working.tasks if t.status == TaskStatus.ACTIVE and t.formation_id == task.formation_id]}

    def _sync(self):
        self.state.current_scope = self.w.scope.model_copy(deep=True)
        self.state.working_proposal = self.w.proposal.model_copy(deep=True) if self.w.proposal else None
        self.state.outstanding_violations = self.w.tools.get_outstanding_violations(self.w.working)

    def execute(self, name, args):
        w, state = self.w, self.state
        obs = self.adapter
        if name == "get_current_state_summary":
            current = w.store.get(w.session_id).state
            changed = digest(current) != digest(w.base)
            discarded = w.proposal.proposal_id if changed and w.proposal else None
            if changed:
                if not state.needs_refresh:
                    state.stale_restarts += 1
                if state.stale_restarts > self.config.max_stale_restarts:
                    raise ActionRejected("STALE_RESTART_LIMIT")
                w.load_current()
                state.validation_feedback = None
            state.base_state_version = w.base.version
            state.current_state_version = current.version
            state.needs_refresh = False
            data = {"scenario_id": current.scenario_id, "version": current.version, "digest": digest(current),
                    "refreshed": changed, "discarded_proposal_id": discarded}
        elif name == "get_outstanding_violations":
            data = {"violations": [c.model_dump(mode="json") for c in w.tools.get_outstanding_violations(w.working)]}
        elif name == "inspect_task_state":
            data = self.task_summary(args.task_id)
        elif name == "generate_candidates":
            task = next(t for t in w.working.tasks if t.id == args.task_id)
            assessment = next(a for a in w.tools.assess_current_task_state(w.working) if a.task_id == args.task_id)
            capability, resource, formation = assessment.constraints[:3]
            gap = {c: v.gap for c, v in capability.details.capabilities.items()}
            for c, v in formation.details.minimum_capabilities.items():
                gap[c] = max(gap.get(c, 0), v.gap)
            if args.level == "FORMATION_RECONSTRUCTION":
                gap = dict(task.requirements)
            result = w.tools.generate_candidates(w.working, args.task_id, gap, StrategyLevel(args.level),
                count_or_resource_gap=args.level == "FORMATION_RECONSTRUCTION" or resource.status == "FAIL" or
                    formation.details.active_node_count < task.min_nodes)
            limit = self.config.context_budget.max_candidate_summaries
            data = {"task_id": args.task_id, "required_gap": result.required_gap, "count": len(result.candidates),
                    "candidates": [c.model_dump(mode="json") for c in result.candidates[:limit]],
                    "truncated": len(result.candidates) > limit, "computed_on_digest": digest(w.working),
                    "rejection_codes": sorted({r.reason_code for r in result.rejected_candidates})}
            w.candidate_summaries[args.task_id] = data
        elif name in {"try_in_place_repair", "find_task_reassignment", "try_formation_reconstruction"}:
            return self._solver(name, args)
        elif name == "detect_route_impacts":
            result = w.tools.detect_route_impacts(w.working, args.task_ids)
            w.route_requests.update({r.task_id: r for r in result.requests})
            data = {"decisions": [d.model_dump(mode="json") for d in result.decisions]}
            decisions = {d.task_id: d for d in w.trace.route_decisions}
            decisions.update({d.task_id: d for d in result.decisions})
            w.trace.route_decisions = list(decisions.values())
        elif name == "replan_route":
            # Resolve geometry from the working plan; no model-provided coordinates.
            impacts = w.tools.detect_route_impacts(w.working, [args.task_id])
            if not impacts.requests:
                return obs.make(name, "NO_CHANGE_REQUIRED", w, {"task_id": args.task_id})
            result = w.tools.replan_route(w.working, impacts.requests[0])
            state.solver_calls += 1
            if result.change:
                w.working = preview(w.working, route_changes=[result.change])
                decisions = {d.task_id: d for d in w.trace.route_decisions}
                decisions.update({d.task_id: d for d in impacts.decisions})
                w.trace.route_decisions = list(decisions.values())
                w.trace.route_results.append(result)
                w.trace.timing.route_planning_ms += result.elapsed_ms
                w.build_proposal()
            self._sync()
            return obs.make(name, result.status.value, w,
                {"task_id": args.task_id, "route_id": result.change.route_id if result.change else None,
                 "path_length": result.change.path_length if result.change else None}, result.reason_codes)
        elif name == "request_scope_expansion":
            source = state.validation_feedback.validation_id if args.source_validation_id == "latest" else args.source_validation_id
            state.scope_expansions.append(ScopeExpansion(entity=args.entity, reason=args.reason, source_validation_id=source))
            kind, entity_id = args.entity.split(":", 1)
            field = {"task": "affected_tasks", "formation": "affected_formations", "node": "affected_nodes", "route": "potentially_affected_routes"}[kind]
            values = getattr(w.scope, field)
            if entity_id not in values:
                values.append(entity_id)
            w.scope.scope_expansion_reason[args.entity] = args.reason + (f" [validation:{source}]" if source else "")
            if kind == "task":
                w.scope.protected_task_ids = [t for t in w.scope.protected_task_ids if t != entity_id]
            if w.proposal:
                w.build_proposal()
            else:
                w.working_revision += 1
            data = {"entity": args.entity, "reason": args.reason, "source_validation_id": source}
        elif name == "discard_working_proposal":
            previous = w.proposal.proposal_id if w.proposal else None
            w.discard()
            data = {"discarded_proposal_id": previous, "scope_retained": True}
        elif name == "validate_proposal":
            result = w.tools.validate_proposal(w.store.get(w.session_id).state, w.proposal)
            state.validation_attempts += 1
            state.validation_feedback = result
            w.validation = result
            w.last_failed_plan_digest = digest(w.working) if result.status == "FAIL" else None
            w.validated_fingerprint = proposal_digest(w.proposal) if result.status != "FAIL" else None
            self._sync()
            return obs.validation(w, result)
        elif name == "commit_validated_proposal":
            self.receipt = w.tools.commit_validated_proposal(w.session_id, w.proposal)
            state.reconstruction_id = self.receipt.reconstruction_id
            state.current_state_version = self.receipt.committed_version
            observation = obs.make(name, "SUCCESS", w, self.receipt.model_dump(mode="json"))
            observation.state_version = self.receipt.committed_version
            observation.validation_id = self.receipt.validation_id
            return observation
        else:
            raise ActionRejected("UNKNOWN_TOOL")
        self._sync()
        return obs.make(name, "OK", w, data)

    def _solver(self, name, args):
        w, state = self.w, self.state
        method = {"try_in_place_repair": w.tools.try_in_place_repair,
                  "find_task_reassignment": w.tools.find_task_reassignment_options,
                  "try_formation_reconstruction": w.tools.try_formation_reconstruction}[name]
        result = method(w.working, args.task_id)
        state.solver_calls += 1
        nodes = {n.id: n for n in w.working.nodes}
        alternatives = [{"option_index": index, "formation_id": o.task_change.after_formation if o.task_change else
                         o.formation_change.formation_id if o.formation_change else None,
                         "selected_nodes": o.selected_nodes,
                         "availability": {n: nodes[n].availability.model_dump() for n in o.selected_nodes}, "rank": o.rank}
                        for index, o in enumerate(result.options)]
        data = {"task_id": args.task_id, "option_count": len(result.options), "options": alternatives,
                "search_complete": result.search_complete, "selected_option_index": None}
        reasons = sorted({c for a in result.trace.strategy_attempts for c in a.reason_codes})
        if not result.options:
            return self.adapter.make(name, "INFEASIBLE" if result.search_complete else "PARTIAL", w, data, reasons)
        if args.option_index >= len(result.options):
            raise ActionRejected("OPTION_INDEX_OUT_OF_RANGE", {"option_count": len(result.options)})
        option = result.options[args.option_index]
        w.working = apply_option(w.working, option)
        w.trace.requirements.extend(result.trace.requirements)
        w.trace.candidate_sets.extend(result.trace.candidate_sets)
        w.trace.strategy_attempts.extend(result.trace.strategy_attempts)
        w.trace.selected_strategies.append(StrategyAttempt(task_id=args.task_id, level=result.strategy_level,
            status=SolverStatus.FEASIBLE, reason_codes=["AGENT_SELECTED_OPTION"], selected_candidates=option.selected_nodes,
            search_complete=result.search_complete))
        for key in type(w.trace.timing).model_fields:
            setattr(w.trace.timing, key, getattr(w.trace.timing, key) + getattr(result.trace.timing, key))
        w.build_proposal()
        self._sync()
        data["selected_option_index"] = args.option_index
        return self.adapter.make(name, "FEASIBLE", w, data, reasons)
