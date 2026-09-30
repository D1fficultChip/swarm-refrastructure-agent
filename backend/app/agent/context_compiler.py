"""Deterministic, bounded, decision-relevant context. No LLM summarization."""
import hashlib
import json
from math import ceil

from ..reconstruction.common import digest
from ..assessment.facts import effective_capabilities
from .security import redact


def serialized(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ContextCompiler:
    def __init__(self, budget):
        self.budget = budget

    def compile(self, workspace, state, available_tools, *, elapsed_seconds):
        w, budget = workspace, self.budget
        assessments = {a.task_id: a for a in w.tools.assess_current_task_state(w.working)}
        violations = w.tools.get_outstanding_violations(w.working)
        tasks = {t.id: t for t in w.working.tasks}
        def rank(task_id):
            task = tasks[task_id]
            severity = 0 if any(v.subject in {task.id, task.formation_id} for v in violations) else 1 if assessments[task_id].decision == "ADJUST" else 2
            return (severity, -task.priority, task.window.end, task.id)
        relevant = sorted(set(w.scope.affected_tasks) | {v.subject for v in violations if v.subject in tasks}, key=rank)
        summaries = []
        for task_id in relevant[:budget.max_affected_tasks]:
            task, assessment = tasks[task_id], assessments[task_id]
            summaries.append({"task_id": task_id, "formation_id": task.formation_id, "route_id": task.route_id,
                "priority": task.priority, "deadline": task.window.end, "decision": assessment.decision.value,
                "capability_gaps": {c: v.gap for check in assessment.constraints if check.type == "capability"
                                    for c, v in check.details.capabilities.items() if v.gap > 0},
                "reason_codes": sorted({v.code for v in violations if v.subject in {task.id, task.formation_id}}),
                "sharing_tasks": sorted(t.id for t in tasks.values() if t.formation_id == task.formation_id and t.id != task_id)})
        def violation_rank(v):
            linked = [t.id for t in tasks.values() if v.subject in {t.id, t.formation_id}]
            return (min((rank(key) for key in linked), default=(0,0,0,"")), v.code, v.subject)
        ordered = sorted(violations, key=violation_rank)
        candidates = []
        for task_id, data in sorted(w.candidate_summaries.items(), key=lambda pair: rank(pair[0])):
            for c in data["candidates"]:
                candidates.append({"task_id": task_id, "node_id": c["node_id"], "capabilities": c["capabilities"],
                                   "distance": c["distance"], "computed_on_digest": data["computed_on_digest"]})
        counts = {c["node_id"]: sum(v["node_id"] == c["node_id"] for v in candidates) for c in candidates}
        candidates.sort(key=lambda c: (-counts[c["node_id"]], rank(c["task_id"]), c["distance"], c["node_id"]))
        feedback = state.validation_feedback
        proposal = w.proposal
        nodes = {n.id: n for n in w.working.nodes}
        memberships = {n for f in w.working.formations for n in f.node_ids}
        formation_facts = []
        for formation in sorted(w.working.formations, key=lambda f: (f.id in w.scope.affected_formations, f.id)):
            profile = {}
            for node_id in formation.node_ids:
                for cap, value in effective_capabilities(nodes[node_id]).items():
                    profile[cap] = profile.get(cap, 0)+value
            formation_facts.append({"formation_id": formation.id, "effective_capabilities": profile,
                "minimum_capabilities": formation.minimum_capabilities,
                "assigned_tasks": sorted(t.id for t in tasks.values() if t.formation_id == formation.id),
                "note": "Capacity facts only; solver must check task windows, resource and sharing legality."})
        context = {
            "objective": state.objective, "domain_text_trust": "UNTRUSTED DOMAIN DATA",
            "state": {"base_version": w.base.version, "current_version": state.current_state_version,
                      "base_digest": digest(w.base),
                      "working_digest": digest(w.working),
                      "needs_refresh": state.needs_refresh},
            "events": [{"event_id": r.application.event_id, "type": r.trace.event.type,
                        "version": r.application.state_version_after, "affected_tasks": r.impact_analysis.affected_tasks}
                       for r in w.reports[-budget.max_history_steps:]],
            "affected_tasks": summaries,
            "scope": {"tasks": w.scope.affected_tasks, "formations": w.scope.affected_formations,
                      "expansion_reasons": w.scope.scope_expansion_reason},
            "outstanding_violations": [v.model_dump(mode="json") for v in ordered[:budget.max_validation_violations]],
            "candidate_summaries": candidates[:budget.max_candidate_summaries],
            "other_formation_facts": formation_facts[:budget.max_candidate_summaries],
            "free_node_capabilities": sorted({c for n in nodes.values() if n.id not in memberships
                                               for c, amount in effective_capabilities(n).items() if amount > 0}),
            "proposal": None if proposal is None else {"proposal_id": proposal.proposal_id,
                "cost": proposal.cost_breakdown.model_dump(), "locally_feasible": proposal.solver_status.value,
                "changed_formations": [f.formation_id for f in proposal.formation_changes],
                "changed_routes": [r.route_id for r in proposal.route_changes]},
            "recent_observations": [o.model_dump(mode="json") for o in state.observations[-budget.max_history_steps:]],
            "validation_feedback": None if feedback is None else {"validation_id": feedback.validation_id,
                "proposal_id": feedback.proposal_id, "status": feedback.status.value,
                "hard_failures": [c.model_dump(mode="json") for c in feedback.hard_failures[:budget.max_validation_violations]],
                "not_evaluated": feedback.not_evaluated},
            "available_tools": list(available_tools),
            "budget": {"step": state.step_index, "steps_remaining": max(0,state.max_steps-state.step_index),
                       "elapsed_seconds": round(elapsed_seconds,3), "model_calls_used": state.model_calls,
                       "validation_attempts": state.validation_attempts, "stale_restarts": state.stale_restarts,
                       "total_seconds": (state.deadline-state.start_time).total_seconds()},
            "truncation": {"affected_tasks_omitted": max(0,len(relevant)-len(summaries)),
                           "violations_omitted": max(0,len(ordered)-budget.max_validation_violations),
                           "candidates_omitted": max(0,len(candidates)-budget.max_candidate_summaries), "char_budget_applied": False},
        }
        context = redact(context)
        # Trim whole structured items, never slice serialized JSON. Keep latest
        # authoritative feedback and the most severe/high-priority task longest.
        trim_order = [context["recent_observations"], context["candidate_summaries"], context["other_formation_facts"], context["events"],
                      context["outstanding_violations"], context["affected_tasks"]]
        while len(serialized(context)) > budget.max_serialized_chars:
            context["truncation"]["char_budget_applied"] = True
            target = next((items for items in trim_order if len(items) > (1 if items is context["affected_tasks"] else 0)), None)
            if target is not None:
                target.pop(0 if target is context["recent_observations"] else -1)
                continue
            if context["validation_feedback"] and context["validation_feedback"]["hard_failures"]:
                context["validation_feedback"]["hard_failures"].pop()
                continue
            # Extremely large scope/metadata: retain identifiers and explicit truncation.
            context["scope"] = {"tasks": context["scope"]["tasks"][:1], "truncated": True}
            context["proposal"] = {"proposal_id": proposal.proposal_id} if proposal else None
            context["affected_tasks"] = []
            context["objective"] = context["objective"][:200]
            context["free_node_capabilities"] = []
            if len(serialized(context)) > budget.max_serialized_chars:
                context["available_tools"] = []
            if len(serialized(context)) > budget.max_serialized_chars:
                raise ValueError("Context budget cannot hold required control metadata")
        return context

    @staticmethod
    def trace_summary(context):
        text = serialized(context)
        return {"sha256": hashlib.sha256(text.encode()).hexdigest(), "serialized_chars": len(text),
                "token_estimate": ceil(len(text)/3), "task_ids": [t["task_id"] for t in context["affected_tasks"]],
                "truncation": context["truncation"]}
