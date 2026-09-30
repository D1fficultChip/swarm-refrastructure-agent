"""Bounded primitive enumeration + versioned option handles, never a policy planner.

The snapshot builder is read-only. This service explicitly accounts for solver
preparation, caches by working revision, and does not choose/apply any option.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
from time import perf_counter

from ..assessment.facts import effective_capabilities, eligible
from ..models.domain import TaskStatus
from ..models.phase3 import StrategyAttempt, SolverStatus
from ..reconstruction.common import apply_option, digest
from ..reconstruction.cost import DisruptionCostEvaluator, describe_delta
from ..reconstruction.validator import mission_checks
from .action import ActionRejected
from .policy_models import ReconstructionOption


def stable_hash(value):
    return sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",", ":")).encode()).hexdigest()


def goal_deficits(state):
    """Only evaluated hard goals; zero means satisfied, positive means shortfall."""
    tasks, _, _ = mission_checks(state)
    result = {}
    for checks in tasks.values():
        for check in checks:
            key = f"{check.subject}:{check.code}"
            if check.passed:
                continue
            detail = check.details
            required, available = detail.get("required"), detail.get("available")
            if isinstance(required,dict) and isinstance(available,dict):
                for cap, amount in required.items():
                    if amount > available.get(cap,0):
                        result[f"{key}:{cap}"] = amount-available.get(cap,0)
            elif isinstance(required,(int,float)) and isinstance(available,(int,float)):
                result[key] = max(0,required-available)
            else:
                result[key] = float(max(1,len(detail.get("unavailable_nodes",detail.get("failed_nodes",detail.get("conflicts",[]))))))
    return result


def future_support(state, other_tasks):
    """Conservative spare opportunity facts for other currently unresolved tasks.

    Availability-specific quantities matter: consuming the ONLY spare for another
    task is worse even when current-task disruption is lower. No future-policy
    optimality claim is made from these aggregate bounds.
    """
    occupied = {n for f in state.formations for n in f.node_ids}
    values = {}
    for task in state.tasks:
        if task.id not in other_tasks:
            continue
        spare = [n for n in state.nodes if n.id not in occupied and eligible(n,task)]
        values[f"{task.id}:node_count"] = len(spare)
        values[f"{task.id}:resource"] = sum(n.resource_remaining for n in spare)
        for cap in task.requirements:
            values[f"{task.id}:cap:{cap}"] = sum(effective_capabilities(n).get(cap,0) for n in spare)
        # Preserve identities of uniquely useful spares: aggregate supply alone
        # must not conceal a window-specific single point of failure.
        for cap in task.requirements:
            useful = [n.id for n in spare if effective_capabilities(n).get(cap,0)>0]
            if len(useful) == 1:
                values[f"{task.id}:unique:{cap}:{useful[0]}"] = 1
    return values


class DominanceGuard:
    def dominates(self, a, b):
        if a.target_tasks != b.target_tasks or a.option_set_id != b.option_set_id:
            return False
        if not a.eligibility.get("local_feasible") or tuple(a.disruption_cost_vector) >= tuple(b.disruption_cost_vector):
            return False
        goals = a.hard_goal_deficits.keys() | b.hard_goal_deficits.keys()
        if any(a.hard_goal_deficits.get(k,0) > b.hard_goal_deficits.get(k,0) for k in goals):
            return False
        opportunities = a.future_support.keys() | b.future_support.keys()
        return all(a.future_support.get(k,0) >= b.future_support.get(k,0) for k in opportunities)

    def annotate(self, options):
        for option in options:
            option.dominated_by = sorted(other.option_id for other in options if self.dominates(other,option))

    def check(self, option):
        if option.dominated_by:
            raise ActionRejected("DOMINATED_OPTION",{"better_options":option.dominated_by})


@dataclass
class BoundOption:
    public: ReconstructionOption
    local: object
    trace: object
    origin: object
    scope_digest: str


class OptionCatalog:
    METHODS = {"IN_PLACE_REPAIR":"try_in_place_repair", "TASK_REASSIGNMENT":"find_task_reassignment_options",
               "FORMATION_RECONSTRUCTION":"try_formation_reconstruction"}

    def __init__(self, config):
        self.config = config
        self.revision = 0
        self.fingerprint = None
        self.option_set_id = ""
        self.current = []
        self.handles = {}
        self.metrics = {}

    def prepare(self, w, feedback=None):
        # Scope-only metadata changes issue a new proposal ID but do NOT repair
        # the rejected plan. Track its working content until changed/discarded.
        replace = bool(w.proposal and w.last_failed_plan_digest==digest(w.working))
        fingerprint = stable_hash([digest(w.base),digest(w.working),w.working_revision,w.scope.model_dump(mode="json"),replace])
        if fingerprint == self.fingerprint:
            return 0,0
        self.revision = w.working_revision
        self.fingerprint = fingerprint
        self.option_set_id = "OS_"+stable_hash([fingerprint,self.revision])[:16]
        self.current = []
        origin = w.base if replace else w.working
        failures = w.tools.get_outstanding_violations(origin)
        task_ids = {t.id for t in origin.tasks if t.status == TaskStatus.ACTIVE and
                    any(c.subject in {t.id,t.formation_id} and c.category != "route" for c in failures)}
        tasks = sorted([t for t in origin.tasks if t.id in task_ids],key=lambda t:(-t.priority,t.window.end,t.id))
        started, calls = perf_counter(), 0
        searches = []
        for task in tasks[:self.config.max_option_tasks]:
            for strategy, name in self.METHODS.items():
                result = getattr(w.tools,name)(origin,task.id)
                calls += 1
                searches.append({"task":task.id,"strategy":strategy,"found":len(result.options),
                    "shown":min(len(result.options),self.config.max_options_per_strategy),"search_complete":result.search_complete})
                for local in result.options[:self.config.max_options_per_strategy]:
                    candidate = apply_option(origin,local)
                    delta_tasks,delta_forms,delta_nodes = describe_delta(w.base,candidate)
                    local_tasks,local_forms,local_nodes = describe_delta(origin,candidate)
                    if not local_tasks and not local_forms and not local_nodes:
                        continue
                    candidate_failures = w.tools.get_outstanding_violations(candidate)
                    candidate_checks,_,_ = mission_checks(candidate)
                    impacts = w.tools.detect_route_impacts(candidate)
                    # Cost is the ACTUAL currently materialized delta. Required
                    # but unplanned route work is reported separately, never guessed.
                    changed_routes = [r for r in candidate.routes if r not in w.base.routes]
                    cost = DisruptionCostEvaluator().evaluate(w.base,candidate,changed_routes)
                    form = next(t.formation_id for t in candidate.tasks if t.id==task.id)
                    checks = candidate_checks.get(task.id,[])
                    public = ReconstructionOption(option_id="O_"+task.id+"_"+stable_hash([
                        self.option_set_id,strategy,local.model_dump(mode="json")])[:12],
                        option_set_id=self.option_set_id,base_state_version=w.base.version,working_state_version=self.revision,
                        base_state_digest=digest(w.base),working_state_digest=digest(w.working),strategy_type=strategy,
                        target_tasks=[task.id],target_formations=sorted({f.formation_id for f in local_forms}|{form}),
                        added_nodes=sorted({n for f in local_forms for n in f.added_nodes}),
                        removed_nodes=sorted({n for f in local_forms for n in f.removed_nodes}),target_formation=form,
                        capability_effect={c.code:c.details for c in checks if c.category=="capability"},
                        resource_effect={c.code:c.details for c in checks if c.category=="resource"},
                        route_effect={"requires_replan":[r.task_id for r in impacts.requests],"planning_not_included":True},
                        predicted_delta_summary={"tasks":[t.task_id for t in delta_tasks],"formations":[f.formation_id for f in delta_forms],
                            "nodes":[n.node_id for n in delta_nodes],"cost_basis":"actual_delta_before_pending_route_planning"},
                        disruption_cost_vector=cost.lexicographic_cost,
                        eligibility={"local_feasible":True,"mission_hard_constraints_pass":not candidate_failures,
                            "remaining_failures":[{"subject":c.subject,"code":c.code} for c in candidate_failures],
                            "search_complete":result.search_complete},
                        explanation_summary=f"{strategy} for {task.id}; selected members {','.join(local.selected_nodes)}",
                        replaces_working_proposal=replace,hard_goal_deficits=goal_deficits(candidate),
                        future_support=future_support(candidate,task_ids-{task.id}))
                    self.current.append(public)
                    self.handles[public.option_id] = BoundOption(public,local.model_copy(deep=True),result.trace.model_copy(deep=True),
                        origin.model_copy(deep=True),stable_hash(w.scope.model_dump(mode="json")))
        DominanceGuard().annotate(self.current)
        ms = (perf_counter()-started)*1000
        self.metrics = {"solver_calls":calls,"generation_ms":ms,"tasks_omitted":max(0,len(tasks)-self.config.max_option_tasks),"searches":searches}
        return calls,ms

    def resolve(self, option_id, w):
        bound = self.handles.get(option_id)
        if bound is None:
            raise ActionRejected("UNKNOWN_OPTION",{"refresh":"get_reconstruction_options"})
        option = bound.public
        current = w.store.get(w.session_id).state
        if (option.option_set_id != self.option_set_id or option.working_state_version != w.working_revision or
            option.working_state_digest != digest(w.working) or option.base_state_digest != digest(w.base) or
            bound.scope_digest != stable_hash(w.scope.model_dump(mode="json")) or
            digest(current) != digest(w.base)):
            raise ActionRejected("STALE_OPTION",{"refresh":"get_current_state_summary","option_set_id":self.option_set_id})
        return bound

    def apply(self, bound, w):
        # Caller preflights the version/scope/guard before a bundled scope update.
        if bound.public.replaces_working_proposal:
            w.discard()
        w.working = apply_option(bound.origin,bound.local)
        w.trace.requirements.extend(bound.trace.requirements)
        w.trace.candidate_sets.extend(bound.trace.candidate_sets)
        w.trace.strategy_attempts.extend(bound.trace.strategy_attempts)
        w.trace.selected_strategies.append(StrategyAttempt(task_id=bound.local.task_id,level=bound.local.level,
            status=SolverStatus.FEASIBLE,reason_codes=["AGENT_SELECTED_STABLE_OPTION"],selected_candidates=bound.local.selected_nodes))
        w.build_proposal()
