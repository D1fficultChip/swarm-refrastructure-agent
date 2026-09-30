from ..assessment.facts import eligible
from ..models.phase3 import Reservation
from .common import active_tasks


class ReservationLedger:
    """Proposal snapshot ledger. Resource amounts are formation-pool commitments."""
    def __init__(self, state):
        nodes = {node.id: node for node in state.nodes}
        self.owners = {n: f.id for f in state.formations for n in f.node_ids}
        self.reservations = [Reservation(
            task_id=task.id, formation_id=f.id, node_ids=[n for n in f.node_ids if eligible(nodes[n], task)],
            window=task.window.model_copy(deep=True), resource_amount=task.resource_required,
        ) for f in state.formations for task in active_tasks(state, f.id)]

    def conflicts(self, node_id, window, task_id):
        return [r.task_id for r in self.reservations if node_id in r.node_ids
                and r.task_id != task_id and r.window.overlaps(window)]
