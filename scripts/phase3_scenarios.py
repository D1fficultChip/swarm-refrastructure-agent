"""Small explicit evaluation fixtures, independent of solver choices."""
from backend.app.models.domain import ScenarioState


def small_scenario(kind="repair"):
    nodes = [dict(id="U0",position=dict(x=0,y=0),status="FAILED",capabilities={"relay":1},
                  availability=dict(start=0,end=100),resource_remaining=10)]
    formations = [dict(id="F0",node_ids=["U0"],minimum_capabilities={})]
    tasks = [dict(id="T0",name="repair",requirements={"relay":1},resource_required=1,min_nodes=1,
                  priority=5,window=dict(start=0,end=100),start=dict(x=0,y=0),target=dict(x=40,y=0),
                  formation_id="F0",route_id="R0")]
    routes = [dict(id="R0",waypoints=[dict(x=0,y=0),dict(x=40,y=0)])]
    if kind != "impossible":
        nodes.append(dict(id="U1",position=dict(x=10,y=10),capabilities={"relay":1},
                          availability=dict(start=0,end=100),resource_remaining=10))
    if kind in {"reassignment","new_formation"}:
        formations[0]["minimum_capabilities"]={"navigation":1}
    if kind == "reassignment":
        formations.append(dict(id="F1",node_ids=["U1"]))
    if kind == "new_formation":
        nodes.append(dict(id="U2",position=dict(x=20,y=10),capabilities={"sensor":1},
                          availability=dict(start=0,end=100),resource_remaining=10))
        tasks[0]["requirements"]["sensor"]=1
        tasks[0]["min_nodes"]=2
    return ScenarioState.model_validate(dict(scenario_id="SMALL",seed=42,bounds=dict(width=100,height=100,grid_resolution=5),
        capabilities=[dict(id=cap,description=cap) for cap in ["relay","sensor","navigation"]],
        nodes=nodes,formations=formations,tasks=tasks,routes=routes))
