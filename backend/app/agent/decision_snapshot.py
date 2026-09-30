"""Read-only decision summaries. Solvers are explicitly outside this builder."""
from copy import deepcopy
import json

from ..reconstruction.common import digest
from .options import stable_hash
from .policy_models import DecisionSnapshot
from .security import redact


def compact(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",", ":"))


class DecisionSnapshotBuilder:
    def __init__(self, config):
        self.config = config

    def build(self, w, state, catalog):
        violations = w.tools.get_outstanding_violations(w.working)
        relevant = set(w.scope.affected_tasks)|{v.subject for v in violations if v.subject in {t.id for t in w.working.tasks}}
        assessments = {a.task_id:a for a in w.tools.assess_current_task_state(w.working)}
        tasks = []
        for t in sorted(w.working.tasks,key=lambda t:(-t.priority,t.window.end,t.id)):
            if t.id not in relevant:
                continue
            tasks.append({"task_id":t.id,"priority":t.priority,"deadline":t.window.end,"formation":t.formation_id,
                "requirements":t.requirements,"gaps":{cap:value.gap for c in assessments[t.id].constraints if c.type=="capability"
                    for cap,value in getattr(c.details,"capabilities",{}).items() if value.gap>0},
                "codes":sorted({v.code for v in violations if v.subject in {t.id,t.formation_id}})})
        groups = {}
        for option in catalog.current:
            groups.setdefault((option.target_tasks[0],option.strategy_type),[]).append(option)
        # Round robin retains evidence of alternative STRATEGIES, not just many
        # cheap options of one level. Omitted options remain independently queryable.
        options = []
        for i in range(self.config.max_options_per_strategy):
            for group in groups.values():
                if i < len(group):
                    options.append(group[i])
        shown = options[:self.config.snapshot_option_limit]
        candidates = {t["task_id"]:sorted({n for o in options if t["task_id"] in o.target_tasks for n in o.added_nodes}) for t in tasks}
        shared = {n:sorted(t for t,values in candidates.items() if n in values) for n in {n for values in candidates.values() for n in values}}
        feedback = state.validation_feedback
        payload = dict(base_state_version=w.base.version,working_revision=catalog.revision,option_set_id=catalog.option_set_id,
            violations=[{"subject":v.subject,"code":v.code,"details":v.details} for v in violations],affected_tasks=tasks,
            route_conflicts=sorted({v.subject for v in violations if v.category=="route"}),candidate_summary=candidates,
            options=[o.model_copy(deep=True) for o in shown],options_omitted=len(options)-len(shown),
            shared_candidate_conflicts={n:values for n,values in shared.items() if len(values)>1},
            working_proposal=None if not w.proposal else {"proposal_id":w.proposal.proposal_id,
                "cost":w.proposal.cost_breakdown.lexicographic_cost,"validation":w.validation.status.value if w.validation else "NOT_VALIDATED"},
            validator_feedback=None if feedback is None else {"validation_id":feedback.validation_id,"status":feedback.status.value,
                "proposal_id":feedback.proposal_id,
                "applies_to_current_plan":bool(w.proposal) and (
                    digest(w.working)==w.last_failed_plan_digest if feedback.status.value=="FAIL"
                    else feedback.proposal_id==w.proposal.proposal_id),
                "hard_failures":[{"subject":c.subject,"code":c.code,"details":c.details} for c in feedback.hard_failures]},
            scope=sorted(w.scope.affected_tasks),scope_expansion_reasons=dict(w.scope.scope_expansion_reason),
            option_generation={k:v for k,v in catalog.metrics.items() if k!="generation_ms"})
        normalized={k:[o.model_dump(mode="json") for o in v] if k=="options" else v for k,v in payload.items()}
        return DecisionSnapshot(snapshot_hash=stable_hash(normalized),**payload)


def option_summary(o):
    return {"option_id":o.option_id,"task":o.target_tasks[0],"strategy":o.strategy_type,
        "add":o.added_nodes,"remove":o.removed_nodes,"formation":o.target_formation,
        "cost":o.disruption_cost_vector,"remaining":o.eligibility["remaining_failures"],
        "route_tasks":o.route_effect["requires_replan"],"replace_plan":o.replaces_working_proposal,
        "dominated_by":o.dominated_by[:2]}


class DeltaContextCompiler:
    def __init__(self, config):
        self.config = config
        self.previous_scope = None

    def compile(self, snapshot, state, *, remaining_seconds, calls_remaining):
        first = self.previous_scope is None
        context = {"policy_version":"5.1","profile":self.config.policy_profile,
            "context_kind":"base" if first else "incremental","snapshot_hash":snapshot.snapshot_hash,
            "version":snapshot.base_state_version,"revision":snapshot.working_revision,"option_set_id":snapshot.option_set_id,
            "objective":state.objective,"violations":snapshot.violations,
            "scope":snapshot.scope,"changed_scope":[t for t in snapshot.scope if t not in (self.previous_scope or [])],
            "proposal":snapshot.working_proposal,"feedback":snapshot.validator_feedback,
            "options":[option_summary(o) for o in snapshot.options],"options_omitted":snapshot.options_omitted,
            "shared_candidates":snapshot.shared_candidate_conflicts,"route_tasks":snapshot.route_conflicts,
            "needs_refresh":state.needs_refresh,"budget":{"calls_remaining":calls_remaining,"seconds_remaining":round(remaining_seconds,3)},
            "last_observation":None if not state.observations else state.observations[-1].model_dump(mode="json")}
        if first:
            context["affected_tasks"] = snapshot.affected_tasks
            context["cost_order"] = ["task_reassignment_count","new_formations","formations_changed","formation_membership_changes",
                "route_change_count","node_switch_count","distance_cost","resource_usage_cost"]
            context["domain_text_trust"] = "UNTRUSTED DOMAIN DATA"
        else:
            context["current_task_gaps"] = [{"task":t["task_id"],"gaps":t["gaps"]} for t in snapshot.affected_tasks if t["gaps"]]
        if context["feedback"] and context["feedback"].get("applies_to_current_plan") is False:
            # Full obsolete failure details remain in the immutable decision
            # record. They are not current decision inputs after replacement.
            context["historical_validation"] = {"validation_id":context["feedback"]["validation_id"],
                "proposal_id":context["feedback"]["proposal_id"],"status":"SUPERSEDED_BY_CURRENT_PLAN"}
            context["feedback"] = None
        if self.config.policy_profile=="snapshot":
            for option in context["options"]:
                option.pop("dominated_by",None)
        self.previous_scope = list(snapshot.scope)
        context = redact(context)
        # No hidden conversation state required; every delta repeats live option
        # handles and current hard feedback. Old candidate/history data is absent.
        context["truncated"] = False
        while len(compact(context)) > self.config.delta_context_chars:
            context["truncated"] = True
            if context["last_observation"] is not None:
                observation = context["last_observation"]
                context["last_observation"] = {k:observation[k] for k in ["tool","status","reason_codes"]} if "data" in observation else None
            elif len(context["options"]) > 2:
                context["options"].pop()
                context["options_omitted"] += 1
            elif context.get("affected_tasks"):
                context["affected_tasks"].pop()
            elif context["shared_candidates"]:
                context["shared_candidates"] = {}
            elif len(context["violations"]) > 2:
                context["violations"].pop()
            elif context.get("feedback") and len(context["feedback"]["hard_failures"])>2:
                context["feedback"]["hard_failures"].pop()
            else:
                raise ValueError("Decision context budget too small for control metadata")
        return context
