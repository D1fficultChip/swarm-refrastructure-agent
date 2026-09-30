import pytest

from backend.app.agent.action import AgentActionValidator
from backend.app.agent.config import AgentConfig
from backend.app.agent.orchestrator import AgentReconstructionOrchestrator
from backend.app.agent.providers.base import ModelError
from backend.app.agent.providers.mock import MockModelProvider
from backend.app.agent.tool_guard import ToolGuard
from backend.app.agent.tool_registry import tool_registry
from backend.app.agent.workspace import AgentWorkspace
from backend.app.models.domain import ScenarioState
from backend.app.reconstruction.common import apply_option
from backend.tests.test_phase5_agent import committed, run
from scripts.phase5_scenarios import action, manager_for_state, sc01, small_scenario, validator_feedback


def test_resource_regression_from_external_partial_proposal_is_real_feedback():
    """A partial external proposal borrows a node; no mock validator or forged check.

    P3 correctly refuses such borrowing itself. This exercises review of a
    structurally valid proposal from another caller through the same P4 boundary.
    """
    data = validator_feedback().model_dump(mode="json")
    for node in data["nodes"]:
        node["availability"] = {"start":0,"end":400}
    data["nodes"][0]["resource_remaining"] = 1
    data["nodes"][1]["resource_remaining"] = 20
    data["nodes"][2]["capabilities"] = {"navigation":1}
    data["nodes"][2]["resource_remaining"] = 1
    data["nodes"].append(dict(id="U3",position=dict(x=10,y=0),capabilities={"relay":1},
                              availability=dict(start=0,end=400),resource_remaining=20))
    data["formations"].append(dict(id="F1",node_ids=["U1","U2"]))
    data["tasks"][1]["formation_id"] = "F1"
    for task in data["tasks"]:
        task["resource_required"] = 10
    manager, session = manager_for_state(ScenarioState.model_validate(data))
    workspace = AgentWorkspace(manager,session.session_id)
    workspace.working.formations[1].node_ids.remove("U1")
    workspace.working.formations[0].node_ids.append("U1")
    seed = workspace.build_proposal()
    def see_feedback(context):
        failures = context["validation_feedback"]["hard_failures"]
        assert {"UNRELATED_TASK_REGRESSION","SHARED_RESOURCE_OVERCOMMITTED","RESOURCE_SHORTAGE"} <= {c["code"] for c in failures}
        return action("inspect_task_state",task_id="T06")
    provider = MockModelProvider([action("validate_proposal"),see_feedback,
        action("request_scope_expansion",entity="task:T06",reason="Resource regression reported by global validator.",source_validation_id="latest"),
        action("discard_working_proposal"),action("generate_candidates",task_id="T03"),
        action("try_in_place_repair",task_id="T03"),action("validate_proposal"),action("commit_validated_proposal")])
    result = AgentReconstructionOrchestrator(manager,provider).run(session.session_id,0,initial_proposal=seed)
    committed(result)
    assert result.validation_attempts == 2
    assert result.proposal.formation_changes[0].added_nodes == ["U3"]


def test_validated_content_hash_detects_in_process_tampering():
    manager, session = sc01()
    workspace = AgentWorkspace(manager,session.session_id)
    workspace.working = apply_option(workspace.base,workspace.tools.try_in_place_repair(workspace.base,"T03").options[0])
    workspace.build_proposal()
    workspace.validation = workspace.tools.validate_proposal(workspace.base,workspace.proposal)
    from backend.app.reconstruction.engine import proposal_digest
    from types import SimpleNamespace
    from backend.app.agent.action import ActionRejected
    workspace.validated_fingerprint = proposal_digest(workspace.proposal)
    workspace.proposal.explanations.append("caller changed validated content")
    parsed,args = AgentActionValidator().validate(action("commit_validated_proposal"),tool_registry(),workspace)
    with pytest.raises(ActionRejected,match="VALIDATED_PROPOSAL_CHANGED"):
        ToolGuard().check(parsed,args,workspace,SimpleNamespace(needs_refresh=False))
    assert manager.get(session.session_id).state == session.state


def test_trace_redacts_model_echoed_key_and_does_not_save_raw_prompt(monkeypatch):
    key = "sk-a-test-key.with-suffix"
    monkeypatch.setenv("MODEL_API_KEY",key)
    manager, session = sc01()
    scripted = action("inspect_task_state",task_id="T03")
    scripted["decision_reason"] = "Credential echo "+key
    result, _ = run(manager,session,[scripted],max_agent_steps=1)
    text = result.model_dump_json()
    assert key not in text and "[REDACTED]" in text
    assert "raw_prompt" not in text and "messages" not in text


def test_infeasible_is_policy_stop_and_failed_fallback_is_distinct():
    manager, session = manager_for_state(small_scenario("impossible"))
    result, _ = run(manager,session,[action("try_in_place_repair",task_id="T0"),
        {"action_type":"STOP","decision_reason":"No feasible option returned in this search.","arguments":{"reason":"INFEASIBLE"}}])
    assert result.termination_reason == "INFEASIBLE" and result.status == "STOPPED"
    assert manager.get(session.session_id).state == session.state
    result, _ = run(manager,session,[ModelError("MODEL_TIMEOUT")],fallback_enabled=True)
    assert result.termination_reason == "FALLBACK_FAILED" and result.fallback_used
    assert manager.get(session.session_id).state == session.state


def test_model_cannot_request_normal_mode_fallback_or_false_success():
    for invalid in [{"action_type":"USE_FALLBACK","decision_reason":"I prefer baseline"},
                    {"action_type":"STOP","decision_reason":"I have a pass","arguments":{"reason":"SUCCESS"}}]:
        manager, session = sc01()
        result, _ = run(manager,session,[invalid],structured_retry_limit=0)
        assert result.termination_reason == "MODEL_ERROR" and not result.fallback_used
        assert result.tool_calls == 0 and manager.get(session.session_id).state == session.state
