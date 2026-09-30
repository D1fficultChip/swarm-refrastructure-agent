import random
from math import hypot

from ..models.domain import (Capability, Formation, MapBounds, Node, Position,
                             Route, ScenarioState, Task, TimeWindow)
from ..models.events import ScenarioDefinition
from ..reconstruction.primitives import ReconstructionPrimitives
from .timing import (DYNAMIC_NODE_AVAILABILITY_END,
                     TASK_TIME_WINDOW_SECONDS)


class DynamicScenarioGenerator:
    """Constraint-guided generator: requirements are created before providers."""

    def __init__(self, max_attempts: int = 20):
        self.max_attempts = max_attempts

    def generate(self, seed: int, node_count: int = 60, task_count: int = 8) -> ScenarioDefinition:
        if node_count not in {30, 60} or task_count not in {4, 8}:
            raise ValueError("dynamic demo supports 30/60 nodes and 4/8 tasks")
        for attempt in range(self.max_attempts):
            definition = self._candidate(seed, node_count, task_count, attempt)
            if not ReconstructionPrimitives().get_outstanding_violations(definition.initial_state):
                return definition
        raise RuntimeError("unable to generate an initially feasible dynamic scenario")

    def _candidate(self, seed: int, node_count: int, task_count: int, attempt: int) -> ScenarioDefinition:
        rng = random.Random((seed << 8) + attempt)
        # The compact 30-node setting uses four formations so it still has a
        # real reserve pool; consuming all 30 nodes as six fixed formations
        # would make every post-failure challenge unsolvable.
        formation_count = min(6, task_count, 4 if node_count == 30 else 6)
        bounds = MapBounds(width=1000, height=1000, grid_resolution=10)
        capabilities = [Capability(id="sensor", description="感知能力"),
                        Capability(id="relay", description="中继能力"),
                        Capability(id="navigation", description="导航能力")]
        centers = []
        for i in range(formation_count):
            band = (i + 1) * bounds.height / (formation_count + 1)
            centers.append(Position(x=rng.uniform(90, 240), y=min(930, max(70, band+rng.uniform(-45, 45)))))

        nodes, formations = [], []
        roles = ["sensor", "sensor", "relay", "relay", "navigation"]
        next_node = 1
        for index, center in enumerate(centers, 1):
            member_ids, role_map = [], {}
            for role_index, role in enumerate(roles):
                node_id = f"U{next_node:02d}"; next_node += 1
                angle_offset = [(-18,-12),(16,-10),(-14,14),(15,15),(0,0)][role_index]
                position = Position(x=center.x+angle_offset[0]+rng.uniform(-3,3),
                                    y=center.y+angle_offset[1]+rng.uniform(-3,3))
                nodes.append(Node(id=node_id, position=position, capabilities={role:1},
                                  availability=TimeWindow(start=0,end=DYNAMIC_NODE_AVAILABILITY_END),resource_remaining=100))
                member_ids.append(node_id);role_map[node_id]=role
            formations.append(Formation(id=f"F{index:02d}",node_ids=member_ids,roles=role_map,
                                        minimum_capabilities={"navigation":1}))

        # Two nearby short-window navigation candidates create a genuine L4
        # validator-feedback opportunity for either shared formation.
        shared_count = max(0, task_count-formation_count)
        for shared_index in range(min(shared_count, 2)):
            center = centers[shared_index]
            node_id=f"U{next_node:02d}";next_node+=1
            nodes.append(Node(id=node_id,position=Position(x=center.x+25,y=center.y+8),
                              capabilities={"navigation":1},availability=TimeWindow(start=0,end=TASK_TIME_WINDOW_SECONDS+50),
                              resource_remaining=100))
        free_roles = ["navigation","relay","sensor"]
        while next_node <= node_count:
            role = free_roles[rng.randrange(len(free_roles))]
            node_id=f"U{next_node:02d}";next_node+=1
            nodes.append(Node(id=node_id,position=Position(x=rng.uniform(280,930),y=rng.uniform(60,940)),
                              capabilities={role:1},availability=TimeWindow(start=0,end=DYNAMIC_NODE_AVAILABILITY_END),
                              resource_remaining=rng.uniform(60,120)))

        tasks, routes = [], []
        for index in range(1, task_count+1):
            extra_index=index-formation_count-1
            formation_index = index if index <= formation_count else (extra_index % min(2,formation_count))+1
            center = centers[formation_index-1]
            later = index > formation_count
            wave=extra_index//min(2,formation_count) if later else 0
            window_start=0 if not later else TASK_TIME_WINDOW_SECONDS*(wave+1)
            window=TimeWindow(start=window_start,end=window_start+TASK_TIME_WINDOW_SECONDS)
            target = Position(x=rng.uniform(700,920),y=min(950,max(50,center.y+rng.uniform(-110,110))))
            # Avoid very short routes and keep exact route endpoints auditable.
            if hypot(target.x-center.x,target.y-center.y)<350:
                target.x=min(950,center.x+500)
            route=Route(id=f"R{index:02d}",waypoints=[center.model_copy(deep=True),target])
            routes.append(route)
            tasks.append(Task(id=f"T{index:02d}",name=f"动态巡检 {index}",
                requirements={"sensor":2,"relay":1,"navigation":1},resource_required=20,min_nodes=4,
                priority=rng.randint(2,5),window=window,start=center.model_copy(deep=True),target=target,
                formation_id=f"F{formation_index:02d}",route_id=route.id))
        state=ScenarioState(scenario_id=f"DYN{seed}",seed=seed,bounds=bounds,capabilities=capabilities,
                            nodes=nodes,tasks=tasks,formations=formations,routes=routes)
        return ScenarioDefinition(name=f"Dynamic Mission #{seed}",
            description="约束引导生成的二维动态任务环境",initial_state=state,events=[])
