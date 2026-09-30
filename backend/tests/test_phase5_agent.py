"""Policy decisions are supplied explicitly; real P2–P4 tools/validator/commit run."""
from datetime import datetime, timedelta, timezone
from time import sleep

import pytest

from backend.app.agent.config import AgentConfig, AgentContextBudget
from backend.app.agent.context_compiler import ContextCompiler, serialized
from backend.app.agent.models import AgentState
from backend.app.agent.orchestrator import AgentReconstructionOrchestrator
from backend.app.agent.providers.base import ModelError
from backend.app.agent.providers.mock import MockModelProvider
from backend.app.agent.tool_registry import ToolExecutor, tool_registry
from backend.app.agent.workspace import AgentWorkspace
from backend.app.reconstruction.common import digest
from backend.app.reconstruction.primitives import ReconstructionPrimitives
from scripts.phase5_scenarios import (action, feedback_actions, manager_for_state, route_only, sc01,
                                      small_scenario, validator_feedback)


def run(manager, session, actions, **config):
    provider = MockModelProvider(actions)
    result = AgentReconstructionOrchestrator(manager, provider, AgentConfig(**config)).run(
        session.session_id, session.state.version)
    return result, provider


def tools(result):
    return [s.tool_name for s in result.trace.steps]


def committed(result):
    assert result.termination_reason == "SUCCESS", [(s.tool_name,s.observation.reason_codes) for s in result.trace.steps]
    assert result.status == "COMMITTED" and result.commit_receipt
    assert not result.fallback_used and not result.final_outstanding_violations
    assert result.final_state_version == result.initial_state_version+1
    assert result.validation_status == "PASS_WITH_LIMITATIONS"


def test_a_sc01_single_primitive_repair():
    manager, session = sc01()
    before = session.state.model_copy(deep=True)
    result, provider = run(manager, session, [action("inspect_task_state",task_id="T03"),
        action("generate_candidates",task_id="T03"), action("try_in_place_repair",task_id="T03"),
        action("validate_proposal"), action("commit_validated_proposal")])
    committed(result)
    after = manager.get(session.session_id).state
    assert after.nodes == before.nodes and after.environment == before.environment and after.tasks == before.tasks
    assert result.solver_calls == 1 and result.model_calls == 5
    assert "reconstruct" not in tool_registry()
    assert provider.contexts[1]["recent_observations"][-1]["tool"] == "inspect_task_state"


def test_double_failure_reserves_distinct_spares():
    manager, session = sc01(2)
    result, _ = run(manager, session, [action("try_in_place_repair",task_id=t) for t in ["T03","T04"]]+
                    [action("validate_proposal"),action("commit_validated_proposal")])
    committed(result)
    added = [n for f in result.proposal.formation_changes for n in f.added_nodes]
    assert len(set(added)) == len(added) == 2


@pytest.mark.parametrize("initial_failure", [True, False])
def test_b_c_policy_changes_strategy_or_skips_l1(initial_failure):
    manager, session = manager_for_state(small_scenario("reassignment"))
    def choose_l2(context):
        if initial_failure:
            assert context["recent_observations"][-1]["status"] == "INFEASIBLE"
        else:
            assert context["free_node_capabilities"] == []
            assert any(f["formation_id"] == "F1" and f["effective_capabilities"]["relay"] == 1
                       for f in context["other_formation_facts"])
        return action("find_task_reassignment",task_id="T0")
    script = ([action("try_in_place_repair",task_id="T0")] if initial_failure else []) + [choose_l2,
        action("replan_route",task_id="T0"),action("validate_proposal"),action("commit_validated_proposal")]
    result, _ = run(manager, session, script)
    committed(result)
    assert tools(result).count("try_in_place_repair") == int(initial_failure)
    assert tools(result).count("try_formation_reconstruction") == 0


def test_l3_is_independently_callable():
    manager, session = manager_for_state(small_scenario("new_formation"))
    result, _ = run(manager, session, [action("try_formation_reconstruction",task_id="T0"),
        action("replan_route",task_id="T0"),action("validate_proposal"),action("commit_validated_proposal")])
    committed(result)
    assert result.proposal.cost_breakdown.new_formations == 1


def test_d_route_only_does_not_call_composition_or_candidate_tools(monkeypatch):
    manager, session = manager_for_state(route_only())
    def forbidden(*args, **kwargs):
        pytest.fail("Unrelated solver was called")
    for method in ["generate_candidates", "try_in_place_repair", "find_task_reassignment_options", "try_formation_reconstruction"]:
        monkeypatch.setattr(ReconstructionPrimitives, method, forbidden)
    result, _ = run(manager, session, [action("detect_route_impacts"),action("replan_route",task_id="T0"),
        action("validate_proposal"),action("commit_validated_proposal")])
    committed(result)
    assert result.solver_calls == 1 and len(result.proposal.route_changes) == 1
    assert not result.proposal.formation_changes


def test_e_real_validator_failure_expansion_and_replan():
    manager, session = manager_for_state(validator_feedback())
    script = feedback_actions()
    original = script[2]
    def inspect_feedback(context):
        assert context["validation_feedback"]["status"] == "FAIL"
        assert "UNRELATED_TASK_REGRESSION" in {c["code"] for c in context["validation_feedback"]["hard_failures"]}
        assert "T06" not in context["scope"]["tasks"]
        return original
    script[2] = inspect_feedback
    result, _ = run(manager, session, script)
    committed(result)
    validations = [s for s in result.trace.steps if s.tool_name == "validate_proposal"]
    assert [s.observation.status for s in validations] == ["FAIL","PASS_WITH_LIMITATIONS"]
    assert validations[0].working_proposal_id != validations[1].working_proposal_id
    expansion = result.trace.scope_expansions[0]
    assert expansion.entity == "task:T06" and expansion.source_validation_id == validations[0].validation_feedback.validation_id
    assert "U2" in next(f for f in manager.get(session.session_id).state.formations if f.id == "F0").node_ids


def test_f_event_between_validation_and_commit_requires_model_refresh():
    manager, session = sc01()
    def concurrent_event(context):
        assert context["validation_feedback"]["status"] == "PASS_WITH_LIMITATIONS"
        manager.inject(session.session_id, 1, session.events[1])
        return action("commit_validated_proposal")
    def refresh(context):
        assert context["state"]["needs_refresh"]
        assert context["available_tools"] == ["get_current_state_summary"]
        return action("get_current_state_summary")
    result, _ = run(manager, session, [action("try_in_place_repair",task_id="T03"), action("validate_proposal"),
        concurrent_event, refresh, action("try_in_place_repair",task_id="T03"),
        action("try_in_place_repair",task_id="T04"),action("validate_proposal"),action("commit_validated_proposal")])
    assert result.termination_reason == "SUCCESS" and result.final_state_version == 3
    assert result.trace.steps[2].observation.reason_codes == ["STALE_PROPOSAL"]
    assert result.trace.steps[3].observation.data["discarded_proposal_id"]
    assert {n.id for n in manager.get(session.session_id).state.nodes if n.status == "FAILED"} == {"U17","U21"}


@pytest.mark.parametrize("bad,code", [
    ("{bad json", "ACTION_SCHEMA_INVALID"),
    (action("shell_exec",command="anything"), "UNKNOWN_TOOL"),
    (action("inspect_task_state",task_id="T999"), "UNKNOWN_TASK"),
    (action("try_in_place_repair",task_id="T03",option_index="0"), "INVALID_TOOL_ARGUMENTS"),
    (action("commit_validated_proposal",pass_claim=True), "INVALID_TOOL_ARGUMENTS"),
    (action("commit_validated_proposal"), "AUTHORITATIVE_VALIDATION_REQUIRED"),
    (action("try_in_place_repair",task_id="T01"), "SCOPE_EXPANSION_REQUIRED"),
])
def test_g_malformed_actions_rejected_without_executing(bad, code):
    manager, session = sc01()
    result, _ = run(manager, session, [bad], structured_retry_limit=0)
    assert result.trace.steps[0].observation.reason_codes == [code]
    assert result.termination_reason == "MODEL_ERROR" and result.tool_calls == 0
    assert manager.get(session.session_id).state == session.state


def test_malformed_retry_and_explicit_operational_fallback():
    manager, session = sc01()
    result, _ = run(manager, session, ["bad", action("try_in_place_repair",task_id="T03"),
        action("validate_proposal"),action("commit_validated_proposal")])
    committed(result)
    manager, session = sc01()
    result, _ = run(manager, session, ["bad"]*3, fallback_enabled=True)
    assert result.termination_reason == "FALLBACK_SUCCESS" and result.fallback_used
    assert result.trace.fallback_reason == "MODEL_ERROR"
    assert result.trace.steps[-1].observation.tool == "deterministic_fallback"


@pytest.mark.parametrize("fallback", [False,True])
def test_h_timeout_leaves_no_private_edits_in_observed_state(fallback):
    manager, session = sc01()
    result, _ = run(manager, session, [action("try_in_place_repair",task_id="T03"),ModelError("MODEL_TIMEOUT")],
                    fallback_enabled=fallback)
    if fallback:
        assert result.termination_reason == "FALLBACK_SUCCESS"
        assert result.trace.steps[-1].observation.tool == "deterministic_fallback"
    else:
        assert result.termination_reason == "MODEL_ERROR"
        assert manager.get(session.session_id).state == session.state


def test_i_no_reconstruction_required_uses_zero_solvers():
    manager, session = sc01(0)
    result, _ = run(manager, session, [{"action_type":"STOP", "arguments":{"reason":"NO_RECONSTRUCTION_REQUIRED"},
        "decision_reason":"Current authoritative constraints have no violations."}])
    assert result.termination_reason == "NO_RECONSTRUCTION_REQUIRED"
    assert result.solver_calls == result.tool_calls == 0
    assert manager.get(session.session_id).state == session.state


def test_j_model_claims_never_become_facts():
    manager, session = sc01()
    false_stop = {"action_type":"STOP", "arguments":{"reason":"NO_RECONSTRUCTION_REQUIRED"},
                  "decision_reason":"I repaired relay; all tasks now pass."}
    result, _ = run(manager, session, [false_stop,action("commit_validated_proposal")],structured_retry_limit=1)
    assert result.termination_reason == "MODEL_ERROR"
    assert result.trace.steps[0].observation.reason_codes == ["OUTSTANDING_VIOLATIONS_REMAIN"]
    assert manager.get(session.session_id).state == session.state


@pytest.mark.parametrize("config,reason", [({"max_agent_steps":1},"MAX_STEPS_REACHED"),
                                         ({"max_model_calls":1},"MAX_MODEL_CALLS_REACHED")])
def test_steps_and_calls_bounded(config,reason):
    manager, session = sc01()
    result, _ = run(manager, session, [action("inspect_task_state",task_id="T03")]*3, **config)
    assert result.termination_reason == reason and result.model_calls == 1
    assert manager.get(session.session_id).state == session.state


def test_elapsed_budget_checked_before_tools():
    manager, session = sc01()
    def delayed(context):
        sleep(.035)
        return action("try_in_place_repair",task_id="T03")
    result, _ = run(manager, session, [delayed],max_total_seconds=.03)
    assert result.termination_reason == "TIME_BUDGET_EXCEEDED" and result.tool_calls == 0
    assert manager.get(session.session_id).state == session.state


def test_stale_restart_budget():
    manager, session = sc01()
    def event(context):
        manager.inject(session.session_id,1,session.events[1])
        return action("get_current_state_summary")
    result, _ = run(manager,session,[event],max_stale_restarts=0)
    assert result.termination_reason == "STALE_RESTART_LIMIT" and result.final_state_version == 2


def test_validation_budget_and_changed_proposal_invalidates_pass():
    manager, session = manager_for_state(validator_feedback())
    result, _ = run(manager, session, [action("try_in_place_repair",task_id="T03")]+[action("validate_proposal")]*2,
        max_validation_retries=0)
    assert result.termination_reason == "VALIDATION_RETRY_LIMIT" and result.validation_attempts == 1
    assert manager.get(session.session_id).state == session.state
    manager, session = sc01()
    result, _ = run(manager, session, [action("try_in_place_repair",task_id="T03"),action("validate_proposal"),
        action("request_scope_expansion",entity="task:T01",reason="Audit additional task",source_validation_id="latest"),
        action("commit_validated_proposal")],structured_retry_limit=0)
    assert result.trace.steps[-1].observation.reason_codes == ["AUTHORITATIVE_VALIDATION_REQUIRED"]
    assert manager.get(session.session_id).state == session.state


def test_context_stable_bounded_prioritizes_fail_and_redacts(monkeypatch):
    manager, session = sc01(2)
    workspace = AgentWorkspace(manager,session.session_id)
    now = datetime.now(timezone.utc)
    secret = "sk-test-secret.do-not-expose"
    monkeypatch.setenv("MODEL_API_KEY",secret)
    state = AgentState(session_id=session.session_id,base_state_version=2,current_state_version=2,
        objective="Restore "+secret,current_scope=workspace.scope,max_steps=12,start_time=now,deadline=now+timedelta(seconds=60))
    compiler = ContextCompiler(AgentContextBudget(max_serialized_chars=2500,max_affected_tasks=1))
    first = compiler.compile(workspace,state,list(tool_registry()),elapsed_seconds=0)
    assert first == compiler.compile(workspace,state,list(tool_registry()),elapsed_seconds=0)
    assert len(serialized(first)) <= 2500 and secret not in serialized(first)
    assert first["truncation"]["affected_tasks_omitted"] == 1
    assert "nodes" not in first and "routes" not in first


def test_explicit_deterministic_mode_does_not_use_model():
    manager, session = sc01()
    provider = MockModelProvider([])
    result = AgentReconstructionOrchestrator(manager,provider).run(session.session_id,1,mode="deterministic")
    assert result.termination_reason == "DETERMINISTIC_SUCCESS" and not provider.contexts
    assert not result.fallback_used and result.timing.deterministic_baseline_ms > 0
