"""Generate the checked-in SC01 fixture without changing global random state."""

import argparse
import random
from pathlib import Path

from backend.app.models.domain import (
    Capability, Formation, Node, Position, Route, ScenarioState, Task, TimeWindow,
)
from backend.app.models.events import NodeFailure, ScenarioDefinition


def build_sc01(seed: int = 42) -> ScenarioDefinition:
    rng = random.Random(seed)
    nodes = []
    roles = ["sensor", "sensor", "navigation", "sensor", "relay", "navigation"]
    for index in range(1, 61):
        role = roles[(index - 1) % 6]
        if index == 21:
            role = "relay"
        elif index == 23:
            role = "navigation"
        position = (
            Position(x=100, y=100 + ((index - 1) // 6) * 120)
            if index <= 36 else Position(x=rng.randint(50, 950), y=rng.randint(50, 950))
        )
        nodes.append(Node(
            id=f"U{index:02d}", position=position, capabilities={role: 1},
            availability=TimeWindow(start=0, end=1000), resource_remaining=100,
        ))
    formations = []
    for index in range(1, 7):
        members = nodes[(index - 1) * 6:index * 6]
        formations.append(Formation(
            id=f"F{index:02d}", node_ids=[node.id for node in members],
            minimum_capabilities={"navigation": 1},
            roles={node.id: next(iter(node.capabilities)) for node in members},
        ))
    tasks = []
    routes = []
    for index in range(1, 9):
        formation_index = index if index <= 6 else index - 6
        y = 100 + (formation_index - 1) * 120
        outbound = index <= 6
        start = Position(x=100 if outbound else 850, y=y)
        target = Position(x=850 if outbound else 100, y=y)
        route = Route(id=f"R{index:02d}", waypoints=[start, target])
        routes.append(route)
        tasks.append(Task(
            id=f"T{index:02d}", name=f"区域巡检 {index}",
            requirements={"sensor": 2, "relay": 1, "navigation": 1},
            resource_required=20, min_nodes=4,
            window=TimeWindow(start=0 if outbound else 400, end=300 if outbound else 700),
            start=start, target=target, formation_id=f"F{formation_index:02d}", route_id=route.id,
        ))
    state = ScenarioState(
        scenario_id="SC01", seed=seed,
        capabilities=[Capability(id=role, description=description) for role, description in
                      [("sensor", "感知能力"), ("relay", "中继能力"), ("navigation", "导航能力")]],
        nodes=nodes, formations=formations, tasks=tasks, routes=routes,
    )
    return ScenarioDefinition(
        name="SC01 节点失效",
        description="60 节点 / 8 任务 / 6 编队；U17 与 U21 是 F03/F04 的唯一中继。事件在 P2 实施。",
        initial_state=state,
        events=[NodeFailure(event_id="E001", occurred_at=10, node_id="U17"),
                NodeFailure(event_id="E002", occurred_at=11, node_id="U21")],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, default=Path("scenarios/sc01_node_failure.json"))
    args = parser.parse_args()
    scenario = build_sc01(args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(scenario.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.output}: 60 nodes, 8 tasks, 6 formations, seed={args.seed}")


if __name__ == "__main__":
    main()
