from math import hypot

from ..assessment.facts import effective_capabilities
from ..models.domain import NodeStatus
from ..models.phase3 import Candidate, CandidateRejection, CandidateSet, FilterStage
from .common import active_tasks


class CandidateGenerator:
    def generate(self, state, requirement, scope, ledger, gap, level, *, count_or_resource_gap=False):
        remaining = list(state.nodes)
        rejected = []
        stages = [FilterStage(name="total", remaining=len(remaining))]
        owners = ledger.owners

        def keep(name, predicate, code):
            nonlocal remaining
            kept = []
            for node in remaining:
                if predicate(node):
                    kept.append(node)
                else:
                    rejected.append(CandidateRejection(node_id=node.id, reason_code=code))
            remaining = kept
            stages.append(FilterStage(name=name, remaining=len(remaining)))

        keep("status", lambda n: n.status != NodeStatus.FAILED and
             (n.status != NodeStatus.DEGRADED or n.health > 0), "NODE_UNAVAILABLE")
        keep("capability_relevance", lambda n: count_or_resource_gap or
             any(effective_capabilities(n).get(cap, 0) > 0 for cap, amount in gap.items() if amount > 1e-9), "NO_GAP_CONTRIBUTION")
        keep("window", lambda n: n.availability.start <= requirement.time_window.start and
             n.availability.end >= requirement.time_window.end, "AVAILABILITY_WINDOW")
        keep("time_conflict", lambda n: not ledger.conflicts(n.id, requirement.time_window, requirement.task_id), "SCHEDULE_CONFLICT")
        keep("resource", lambda n: n.resource_remaining > 0 or requirement.required_resources == 0, "RESOURCE_UNAVAILABLE")
        keep("critical_assignment", lambda n: not any(t.id in scope.protected_task_ids
             for t in active_tasks(state, owners.get(n.id))) if owners.get(n.id) else True, "PROTECTED_ASSIGNMENT")
        # First version never steals from an existing formation, even an idle one.
        # Whole existing formations are considered separately by Level 2.
        keep("scope_and_ownership", lambda n: n.id not in owners, "EXISTING_FORMATION_OWNERSHIP")
        candidates = [Candidate(node_id=n.id, capabilities=effective_capabilities(n), available_resources=n.resource_remaining,
            current_formation=owners.get(n.id), current_tasks=[r.task_id for r in ledger.reservations if n.id in r.node_ids],
            distance=hypot(n.position.x - requirement.start.x, n.position.y - requirement.start.y), switch_cost=1,
            capability_contribution={cap: min(amount, effective_capabilities(n).get(cap, 0)) for cap, amount in gap.items()},
            eligibility_reasons=["AVAILABLE", "NO_ASSIGNMENT_CONFLICT", "FREE_NODE", "GAP_OR_COUNT_OR_RESOURCE_CONTRIBUTION"])
            for n in remaining]
        candidates.sort(key=lambda c: (c.switch_cost, c.distance, c.node_id))
        return CandidateSet(task_id=requirement.task_id, strategy_level=level, required_gap=gap,
                            candidates=candidates, stages=stages, rejected_candidates=rejected)
