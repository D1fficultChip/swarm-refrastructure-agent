"""The only module allowed to import NetworkX."""

from hashlib import sha256

import networkx as nx

from ..assessment.facts import effective_capabilities, route_intersects
from ..models.domain import ScenarioState
from ..models.phase2 import DependencyEdge, EntityType, GraphEntity, GraphSnapshot, GraphStats, RelationType


class TaskReconstructionGraph:
    def __init__(self, scenario_id: str, state_version: int):
        self.scenario_id = scenario_id
        self.state_version = state_version
        self._graph = nx.MultiDiGraph()
        self._state_digest = None

    @classmethod
    def from_state(cls, state: ScenarioState) -> "TaskReconstructionGraph":
        graph = cls(state.scenario_id, state.version)
        graph._state_digest = sha256(state.model_dump_json().encode()).hexdigest()

        def entity(key, domain_id, kind, capability=None, owner=None):
            graph._graph.add_node(key, value=GraphEntity(
                id=key, entity_id=domain_id, type=kind, capability=capability, owner=owner,
            ))

        def edge(source, target, relation, amount=None, role=None):
            if source not in graph._graph or target not in graph._graph:
                raise ValueError(f"Graph edge references unknown endpoint: {source}, {target}")
            graph._graph.add_edge(source, target, key=relation.value, value=DependencyEdge(
                source=source, target=target, relation=relation, amount=amount, role=role,
            ))

        for capability in state.capabilities:
            entity(f"capability:{capability.id}", capability.id, EntityType.CAPABILITY, capability.id)
        for node in state.nodes:
            owner = f"node:{node.id}"
            entity(owner, node.id, EntityType.NODE)
            for cap, amount in effective_capabilities(node).items():
                key = f"capability:node:{node.id}:{cap}"
                entity(key, cap, EntityType.CAPABILITY, cap, owner)
                edge(owner, key, RelationType.NODE_PROVIDES_CAPABILITY, amount)
                edge(key, f"capability:{cap}", RelationType.CAPABILITY_HAS_TYPE)
        nodes = {node.id: node for node in state.nodes}
        for formation in state.formations:
            owner = f"formation:{formation.id}"
            entity(owner, formation.id, EntityType.FORMATION)
            caps = set(formation.minimum_capabilities)
            for node_id in formation.node_ids:
                caps.update(nodes[node_id].capabilities)
                edge(owner, f"node:{node_id}", RelationType.FORMATION_CONTAINS_NODE,
                     role=formation.roles.get(node_id))
            for task in state.tasks:
                if task.formation_id == formation.id:
                    caps.update(task.requirements)
            for cap in sorted(caps):
                key = f"capability:formation:{formation.id}:{cap}"
                entity(key, cap, EntityType.CAPABILITY, cap, owner)
                amount = sum(effective_capabilities(nodes[n]).get(cap, 0) for n in formation.node_ids)
                edge(owner, key, RelationType.FORMATION_PROVIDES_CAPABILITY, amount)
                edge(key, f"capability:{cap}", RelationType.CAPABILITY_HAS_TYPE)
                for node_id in formation.node_ids:
                    if cap in nodes[node_id].capabilities:
                        edge(f"capability:node:{node_id}:{cap}", key, RelationType.CAPABILITY_CONTRIBUTES_TO,
                             effective_capabilities(nodes[node_id]).get(cap, 0))
        for route in state.routes:
            entity(f"route:{route.id}", route.id, EntityType.ROUTE)
        for region in state.environment:
            entity(f"environment:{region.id}", region.id, EntityType.ENVIRONMENT)
            for route in state.routes:
                if route_intersects(route, region):
                    edge(f"route:{route.id}", f"environment:{region.id}", RelationType.ROUTE_INTERSECTS_CONSTRAINT)
        for task in state.tasks:
            owner = f"task:{task.id}"
            entity(owner, task.id, EntityType.TASK)
            if task.formation_id:
                edge(owner, f"formation:{task.formation_id}", RelationType.TASK_ASSIGNED_TO_FORMATION)
            if task.route_id:
                edge(owner, f"route:{task.route_id}", RelationType.TASK_USES_ROUTE)
            for cap, amount in task.requirements.items():
                target = (f"capability:formation:{task.formation_id}:{cap}" if task.formation_id else f"capability:{cap}")
                edge(owner, target, RelationType.TASK_REQUIRES_CAPABILITY, amount)
        return graph

    def is_for(self, state: ScenarioState) -> bool:
        return self._state_digest == sha256(state.model_dump_json().encode()).hexdigest()

    def query_node(self, entity_id: str) -> GraphEntity | None:
        if entity_id not in self._graph:
            return None
        return self._graph.nodes[entity_id]["value"].model_copy(deep=True)

    def query_relations(self, entity_id: str, relation: RelationType | None = None,
                        direction: str = "out") -> list[DependencyEdge]:
        if direction not in {"out", "in"}:
            raise ValueError("direction must be out or in")
        if entity_id not in self._graph:
            return []
        edges = self._graph.out_edges if direction == "out" else self._graph.in_edges
        values = [data["value"] for _, _, data in edges(entity_id, data=True)]
        return [item.model_copy(deep=True) for item in sorted(values, key=lambda e: (e.source, e.target, e.relation.value))
                if relation is None or item.relation == relation]

    def query_neighbors(self, entity_id: str, relation: RelationType | None = None,
                        direction: str = "out") -> list[GraphEntity]:
        edges = self.query_relations(entity_id, relation, direction)
        ids = {edge.target if direction == "out" else edge.source for edge in edges}
        return [self.query_node(key) for key in sorted(ids)]

    def stats(self) -> GraphStats:
        return GraphStats(entities=self._graph.number_of_nodes(), edges=self._graph.number_of_edges())

    def export_snapshot(self) -> GraphSnapshot:
        return self.find_affected_subgraph(list(self._graph.nodes))

    def find_affected_subgraph(self, entity_ids: list[str], previous: "TaskReconstructionGraph | None" = None) -> GraphSnapshot:
        """Induced selection after semantic propagation, never an unconditional BFS.

        Include previous edges for removed constraints/cancelled dependencies.
        The snapshot is evidence across B/O, labelled with the resulting version.
        """
        selected = set(entity_ids)
        entities, edges = {}, {}
        for graph in ([previous, self] if previous is not None else [self]):
            for key in sorted(selected):
                item = graph.query_node(key)
                if item is not None:
                    entities[key] = item
                    for edge in graph.query_relations(key):
                        if edge.target in selected:
                            edges[(edge.source, edge.target, edge.relation.value)] = edge
        return GraphSnapshot(
            scenario_id=self.scenario_id, state_version=self.state_version,
            entities=[entities[key] for key in sorted(entities)],
            edges=[edges[key] for key in sorted(edges)],
        )
