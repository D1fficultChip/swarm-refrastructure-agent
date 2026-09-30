"""Small explicit diagnostic fixtures, not model-control policies."""
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.app.main import DEFAULT_SCENARIO_DIR
from backend.app.models.domain import ScenarioState
from backend.app.models.events import ScenarioDefinition
from backend.app.scenarios.manager import ScenarioManager
from scripts.phase3_scenarios import small_scenario


def manager_for_state(state):
    definition = ScenarioDefinition(name="Phase 5 diagnostic", description="Explicit synthetic fixture", initial_state=state, events=[])
    with TemporaryDirectory() as directory:
        Path(directory, "scenario.json").write_text(definition.model_dump_json(), encoding="utf-8")
        manager = ScenarioManager(Path(directory))
    return manager, manager.load(state.scenario_id)


def sc01(count=1):
    manager = ScenarioManager(DEFAULT_SCENARIO_DIR)
    session = manager.load("SC01")
    for index in range(count):
        manager.inject(session.session_id, index, session.events[index])
    return manager, manager.get(session.session_id)


def route_only():
    state = small_scenario().model_dump(mode="json")
    state["nodes"][0]["status"] = "NORMAL"
    state["environment"] = [dict(id="Z1", lower=dict(x=15,y=0), upper=dict(x=25,y=10))]
    return ScenarioState.model_validate(state)


def validator_feedback():
    # P2 can cover T06 with U0 alone. P4 requires EVERY assigned member to
    # cover T06's window. Thus U1 repairs T03 locally but causes a real P4
    # MEMBER_UNAVAILABLE / UNRELATED_TASK_REGRESSION on the shared formation.
    state = small_scenario().model_dump(mode="json")
    state["nodes"] = [
        dict(id="U0",position=dict(x=0,y=0),capabilities={"navigation":1},
             availability=dict(start=0,end=400),resource_remaining=10),
        dict(id="U1",position=dict(x=1,y=0),capabilities={"relay":1},
             availability=dict(start=0,end=100),resource_remaining=10),
        dict(id="U2",position=dict(x=50,y=0),capabilities={"relay":1},
             availability=dict(start=0,end=400),resource_remaining=10),
    ]
    base_task = state["tasks"][0]
    state["tasks"] = [dict(base_task,id="T03",route_id="R03",requirements={"navigation":1,"relay":1}),
        dict(base_task,id="T06",route_id="R06",requirements={"navigation":1},window=dict(start=200,end=300))]
    state["routes"] = [dict(id=rid,waypoints=[dict(x=0,y=0),dict(x=40,y=0)]) for rid in ["R03","R06"]]
    return ScenarioState.model_validate(state)


def action(tool, **arguments):
    return {"action_type": "CALL_TOOL", "tool_name": tool, "arguments": arguments,
            "decision_reason": "Explicit offline test action; verify actual tool observation."}


def feedback_actions():
    return [action("try_in_place_repair", task_id="T03"), action("validate_proposal"),
        action("inspect_task_state", task_id="T06"),
        action("request_scope_expansion", entity="task:T06", reason="Inspect and repair the validator-reported shared-formation regression.", source_validation_id="latest"),
        action("discard_working_proposal"), action("generate_candidates", task_id="T03"),
        action("try_in_place_repair", task_id="T03", option_index=1),
        action("validate_proposal"), action("commit_validated_proposal")]
