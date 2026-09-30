"""Choose a model tier, never a reconstruction strategy."""
class PolicyModelRouter:
    def __init__(self, config, fast, strong):
        self.config, self.fast, self.strong = config, fast, strong

    def choose(self, snapshot, consecutive_invalid=0):
        reasons = []
        if snapshot.validator_feedback and snapshot.validator_feedback["status"] == "FAIL":
            reasons.append("VALIDATOR_FEEDBACK")
        if snapshot.shared_candidate_conflicts:
            reasons.append("SHARED_CANDIDATE_COMPETITION")
        if len({v["subject"] for v in snapshot.violations})>1 and snapshot.shared_candidate_conflicts:
            reasons.append("INTERACTING_VIOLATIONS")
        task_ids={task["task_id"] for task in snapshot.affected_tasks}
        if any(v["subject"] in task_ids and v["subject"] not in snapshot.scope for v in snapshot.violations):
            reasons.append("SCOPE_EXPANSION_REQUIRED")
        if consecutive_invalid >= 2:
            reasons.append("REPEATED_INVALID_FAST_ACTION")
        strategies = {o.strategy_type for o in snapshot.options if not o.dominated_by and o.eligibility["local_feasible"]}
        if len(strategies)>1:
            reasons.append("MULTIPLE_NON_DOMINATED_STRATEGIES")
        return (self.strong,"strong",reasons) if reasons else (self.fast,"fast",["SIMPLE_DECISION"])
