"""Strict delta interpreter. No solver, repair, feasibility or store access."""
from pydantic import ValidationError

from ..models.domain import ScenarioState
from ..models.phase3 import ReconstructionProposal


class MaterializationError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(condition, code, message):
    if not condition:
        raise MaterializationError(code, message)


class ProposalMaterializer:
    def materialize(self, observed_state: ScenarioState, proposal: ReconstructionProposal) -> ScenarioState:
        # Revalidate even Python callers using model_copy/model_construct.
        try:
            proposal = ReconstructionProposal.model_validate(proposal.model_dump(mode="json"))
            data = ScenarioState.model_validate(observed_state.model_dump(mode="json")).model_dump(mode="json")
        except ValidationError as exc:
            raise MaterializationError("STRUCTURAL_INVALID", str(exc)) from exc
        formations = {f["id"]: f for f in data["formations"]}
        tasks = {t["id"]: t for t in data["tasks"]}
        routes = {r["id"]: r for r in data["routes"]}
        old_members = {n: (f["id"], f["roles"].get(n)) for f in formations.values() for n in f["node_ids"]}
        for changes, field in [(proposal.task_changes, "task_id"), (proposal.formation_changes, "formation_id"),
                               (proposal.node_changes, "node_id"), (proposal.route_changes, "route_id")]:
            ids = [getattr(c, field) for c in changes]
            require(len(ids) == len(set(ids)), "DELTA_MISMATCH", "Duplicate delta target")
        require(len({c.task_id for c in proposal.route_changes}) == len(proposal.route_changes),
                "DELTA_MISMATCH", "Multiple route changes for one task")
        for change in proposal.formation_changes:
            old = formations.get(change.formation_id)
            require(change.after.id == change.formation_id, "DELTA_MISMATCH", "Formation ID differs from target")
            require(old == (change.before.model_dump(mode="json") if change.before else None),
                    "DELTA_MISMATCH", "Formation before image differs from observation")
            require(old is None or old["minimum_capabilities"] == change.after.minimum_capabilities,
                    "OBSERVED_FACT_MUTATION", "Existing formation requirements cannot be weakened or rewritten")
            old_ids = set(old["node_ids"]) if old else set()
            new_ids = set(change.after.node_ids)
            require(sorted(change.added_nodes) == sorted(new_ids - old_ids) and
                    sorted(change.removed_nodes) == sorted(old_ids - new_ids),
                    "DELTA_MISMATCH", "Membership delta does not match before/after")
            require(old != change.after.model_dump(mode="json"), "DELTA_MISMATCH", "No-op formation delta")
            nodes = {n["id"]: n for n in data["nodes"]}
            require(not any(nodes.get(n, {}).get("status") == "FAILED" for n in change.added_nodes),
                    "FAILED_NODE_ASSIGNED", "Delta explicitly adds a failed node")
            formations[change.formation_id] = change.after.model_dump(mode="json")
        for change in proposal.task_changes:
            require(change.task_id in tasks, "UNKNOWN_TASK", change.task_id)
            task = tasks[change.task_id]
            require(task["formation_id"] == change.before_formation and task["start"] == change.before_start.model_dump(),
                    "DELTA_MISMATCH", "Task before image differs from observation")
            require(task["formation_id"] != change.after_formation or task["start"] != change.after_start.model_dump(),
                    "DELTA_MISMATCH", "No-op task delta")
            task["formation_id"] = change.after_formation
            task["start"] = change.after_start.model_dump()
        for change in proposal.route_changes:
            require(change.task_id in tasks, "UNKNOWN_TASK", change.task_id)
            require(change.route_id == change.after.id, "DELTA_MISMATCH", "Route ID differs from target")
            old = routes.get(change.route_id)
            require(old == (change.before.model_dump(mode="json") if change.before else None),
                    "DELTA_MISMATCH", "Route before image differs from observation")
            require(tasks[change.task_id]["route_id"] == change.route_id or old is None,
                    "DELTA_MISMATCH", "Cannot take another existing route")
            require(old != change.after.model_dump(mode="json") or tasks[change.task_id]["route_id"] != change.route_id,
                    "DELTA_MISMATCH", "No-op route delta")
            routes[change.route_id] = change.after.model_dump(mode="json")
            tasks[change.task_id]["route_id"] = change.route_id
        all_members = [n for f in formations.values() for n in f["node_ids"]]
        require(len(all_members) == len(set(all_members)), "NODE_MULTIPLE_FORMATIONS", "Membership must be globally unique")
        for task in tasks.values():
            require(task["formation_id"] is None or task["formation_id"] in formations,
                    "UNKNOWN_FORMATION", task["id"])
        new_members = {n: (f["id"], f["roles"].get(n)) for f in formations.values() for n in f["node_ids"]}
        actual = {n: (old_members.get(n, (None, None)), new_members.get(n, (None, None)))
                  for n in old_members.keys() | new_members.keys() if old_members.get(n) != new_members.get(n)}
        declared = {n.node_id: ((n.before_formation, n.before_role), (n.after_formation, n.after_role))
                    for n in proposal.node_changes}
        require(actual == declared, "DELTA_MISMATCH", "Node role/ownership deltas must exactly match formation deltas")
        failed_ids = {n["id"] for n in data["nodes"] if n["status"] == "FAILED"}
        require(not any(n.node_id in failed_ids and n.after_role is not None and n.after_role != n.before_role
                        for n in proposal.node_changes),
                "FAILED_NODE_ASSIGNED", "Cannot assign a new role to a failed node, even in an idle formation")
        data.update(formations=list(formations.values()), tasks=list(tasks.values()), routes=list(routes.values()))
        try:
            return ScenarioState.model_validate(data)
        except ValidationError as exc:
            raise MaterializationError("STRUCTURAL_INVALID", str(exc)) from exc
