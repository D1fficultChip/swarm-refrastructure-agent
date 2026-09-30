from datetime import datetime,timedelta,timezone
from time import sleep

import pytest

from backend.app.agent.action import ActionRejected
from backend.app.agent.config import AgentConfig
from backend.app.agent.decision_snapshot import DecisionSnapshotBuilder,DeltaContextCompiler,compact
from backend.app.agent.models import AgentState
from backend.app.agent.optimized_orchestrator import OptimizedAgentOrchestrator
from backend.app.agent.options import DominanceGuard,OptionCatalog,future_support
from backend.app.agent.policy_router import PolicyModelRouter
from backend.app.agent.policy_tools import parse_policy_action,policy_registry,reason_consistency
from backend.app.agent.providers.base import ModelError
from backend.app.agent.providers.mock import MockModelProvider
from backend.app.agent.providers.openai_compatible import OpenAICompatibleProvider
from backend.app.agent.tool_registry import tool_schemas
from backend.app.agent.workspace import AgentWorkspace
from backend.app.reconstruction.common import digest
from backend.app.reconstruction.primitives import ReconstructionPrimitives
from scripts.benchmark_phase51 import policy_action,scripted_policy,summarize
from scripts.demo_phase5_agent import feedback_seed
from scripts.phase5_scenarios import manager_for_state,route_only,sc01,small_scenario,validator_feedback


def config(**updates):
    return AgentConfig(policy_profile="optimized",**updates)


def state_for(workspace):
    now=datetime.now(timezone.utc)
    return AgentState(session_id=workspace.session_id,base_state_version=workspace.base.version,current_state_version=workspace.base.version,
        objective="Restore with minimum lexicographic disruption",current_scope=workspace.scope,max_steps=16,
        start_time=now,deadline=now+timedelta(seconds=120))


def prepared(count=1):
    manager,session=sc01(count)
    workspace=AgentWorkspace(manager,session.session_id)
    catalog=OptionCatalog(config())
    catalog.prepare(workspace)
    return manager,session,workspace,catalog


def run_case(case,**updates):
    manager,session=sc01(1 if case=="A" else 2) if case in {"A","B"} else manager_for_state(route_only() if case=="C" else validator_feedback())
    seed=feedback_seed(manager,session) if case=="D" else None
    provider=MockModelProvider(scripted_policy(case,"optimized"))
    result=OptimizedAgentOrchestrator(manager,provider,config(**updates)).run(session.session_id,session.state.version,initial_proposal=seed)
    return result,provider,manager,session


def test_stable_ids_repeatable_unique_and_bound_to_revision():
    _,_,w,catalog=prepared()
    ids=[o.option_id for o in catalog.current]
    assert len(set(ids))==len(ids)>0
    assert catalog.prepare(w)==(0,0)
    assert ids==[o.option_id for o in catalog.current]
    another=OptionCatalog(config())
    another.prepare(w)
    assert ids==[o.option_id for o in another.current]
    selected=next(o for o in catalog.current if not o.dominated_by)
    bound=catalog.resolve(selected.option_id,w)
    catalog.apply(bound,w)
    with pytest.raises(ActionRejected,match="STALE_OPTION"):
        catalog.resolve(selected.option_id,w)
    w.discard()
    catalog.prepare(w)
    assert not set(ids)&{o.option_id for o in catalog.current}
    with pytest.raises(ActionRejected,match="STALE_OPTION"):
        catalog.resolve(selected.option_id,w)


def test_old_option_rejected_on_real_event_and_unknown_is_distinct():
    manager,session,w,catalog=prepared()
    option=catalog.current[0]
    manager.inject(session.session_id,1,session.events[1])
    with pytest.raises(ActionRejected,match="STALE_OPTION"):
        catalog.resolve(option.option_id,w)
    with pytest.raises(ActionRejected,match="UNKNOWN_OPTION"):
        catalog.resolve("invented",w)


def test_dominance_actual_case_b_and_more_effective_l3_not_forbidden():
    _,_,w,catalog=prepared(2)
    first=next(o for o in catalog.current if o.target_tasks==["T03"] and o.strategy_type=="IN_PLACE_REPAIR")
    catalog.apply(catalog.resolve(first.option_id,w),w)
    catalog.prepare(w)
    l3=[o for o in catalog.current if o.strategy_type=="FORMATION_RECONSTRUCTION"]
    assert l3 and all(o.dominated_by for o in l3)
    with pytest.raises(ActionRejected,match="DOMINATED_OPTION"):
        DominanceGuard().check(l3[0])
    manager,session=manager_for_state(small_scenario("new_formation"))
    other=OptionCatalog(config())
    other.prepare(AgentWorkspace(manager,session.session_id))
    assert other.current and all(o.strategy_type=="FORMATION_RECONSTRUCTION" for o in other.current)
    assert all(not o.dominated_by for o in other.current)


def test_lower_cost_cannot_dominate_better_constraint_effect():
    manager,session=manager_for_state(validator_feedback())
    catalog=OptionCatalog(config())
    catalog.prepare(AgentWorkspace(manager,session.session_id))
    bad=next(o for o in catalog.current if o.added_nodes==["U1"])
    good=next(o for o in catalog.current if o.added_nodes==["U2"])
    assert tuple(bad.disruption_cost_vector)<tuple(good.disruption_cost_vector)
    assert bad.hard_goal_deficits and not good.hard_goal_deficits
    assert not DominanceGuard().dominates(bad,good)


def test_future_unique_relay_prevents_false_domination():
    _,_,_,catalog=prepared()
    low=catalog.current[0].model_copy(deep=True)
    high=low.model_copy(deep=True)
    high.option_id="higher_cost_preserves_relay"
    high.disruption_cost_vector[-2]+=1
    # Same immediate constraints, different measured future-task opportunity.
    low.future_support={"T04:cap:relay":0,"T04:resource":10}
    high.future_support={"T04:cap:relay":1,"T04:resource":10,"T04:unique:relay:U59":1}
    assert not DominanceGuard().dominates(low,high)
    low.future_support=high.future_support.copy()
    assert DominanceGuard().dominates(low,high)


def test_guard_rejection_returns_better_options_then_model_reselects():
    manager,session=sc01()
    def worse(context):
        option=next(o for o in context["options"] if o["strategy"]=="FORMATION_RECONSTRUCTION" and o["dominated_by"])
        return policy_action("SELECT_OPTION",option_id=option["option_id"],finalize=True)
    def better(context):
        assert context["last_observation"]["reason_codes"]==["DOMINATED_OPTION"]
        option=next(o for o in context["options"] if not o["dominated_by"])
        return policy_action("SELECT_OPTION",option_id=option["option_id"],finalize=True)
    result=OptimizedAgentOrchestrator(manager,MockModelProvider([worse,better]),config()).run(session.session_id,1)
    assert result.termination_reason=="SUCCESS" and result.model_calls==2
    assert result.trace.steps[0].observation.data["better_options"]
    assert result.trace.steps[0].decision_record.disruption_cost[1]>0
    assert result.proposal.cost_breakdown.new_formations==0


def test_snapshot_readonly_deterministic_no_solver_inside_builder(monkeypatch):
    manager,session,w,catalog=prepared()
    before=digest(w.working)
    for name in OptionCatalog.METHODS.values():
        monkeypatch.setattr(w.tools,name,lambda *a,**k:pytest.fail("Builder must not solve"))
    builder=DecisionSnapshotBuilder(config())
    state=state_for(w)
    first=builder.build(w,state,catalog)
    assert first==builder.build(w,state,catalog)
    assert digest(w.working)==before and manager.get(session.session_id).state==session.state
    assert first.options and first.option_generation["solver_calls"]==3


def test_delta_context_removes_static_fields_keeps_live_handles_and_feedback():
    _,_,w,catalog=prepared()
    snapshot=DecisionSnapshotBuilder(config()).build(w,state_for(w),catalog)
    compiler=DeltaContextCompiler(config())
    args=dict(remaining_seconds=10,calls_remaining=2)
    base=compiler.compile(snapshot,state_for(w),**args)
    delta=compiler.compile(snapshot,state_for(w),**args)
    assert base["context_kind"]=="base" and delta["context_kind"]=="incremental"
    assert "affected_tasks" not in delta and "cost_order" not in delta
    assert len(compact(delta))<len(compact(base))
    assert {o["option_id"] for o in delta["options"]}=={o["option_id"] for o in base["options"]}
    assert len(compact(delta))<=config().delta_context_chars


@pytest.mark.parametrize("case,calls",[("A",1),("B",2),("C",1),("D",2)])
def test_reduced_calls_and_true_transaction(case,calls):
    result,provider,manager,session=run_case(case)
    assert result.termination_reason=="SUCCESS",[(s.observation.reason_codes,s.observation.data) for s in result.trace.steps]
    assert result.model_calls==calls and result.pure_agent_success and not result.fallback_used
    assert result.commit_receipt and not result.final_outstanding_violations
    assert result.final_state_version==session.state.version+1
    assert manager.get(session.session_id).state.nodes==session.state.nodes
    assert result.trace.steps[-1].decision_record.automatic_steps[-1]["tool"]=="commit_validated_proposal"
    assert result.trace.steps[-1].validation_feedback.validation_id!=result.commit_receipt.validation_id
    if case=="B":
        assert result.proposal.cost_breakdown.new_formations==result.proposal.cost_breakdown.task_reassignment_count==0
    if case=="D":
        assert result.validation_attempts==2
        assert result.trace.steps[0].validation_feedback.status=="FAIL"
        assert "UNRELATED_TASK_REGRESSION" in result.trace.steps[0].observation.reason_codes
        assert result.trace.scope_expansions[0].entity=="task:T06"
        assert result.trace.steps[1].action.option_id==result.trace.steps[1].decision_record.selected_option
        assert result.trace.steps[1].decision_record.reason_argument_consistency is True


def test_route_only_avoids_all_composition_preparation(monkeypatch):
    for name in [*OptionCatalog.METHODS.values(),"generate_candidates"]:
        monkeypatch.setattr(ReconstructionPrimitives,name,lambda *a,**k:pytest.fail("Unrelated primitive called"))
    result,*_=run_case("C")
    assert result.solver_calls==1


def test_no_auto_commit_means_verified_private_proposal_only():
    result,_,manager,session=run_case("A",auto_commit_after_validation=False)
    assert result.termination_reason=="VALIDATED_NOT_COMMITTED" and result.commit_receipt is None
    assert result.validation_status=="PASS_WITH_LIMITATIONS" and not result.overall_success
    assert manager.get(session.session_id).state==session.state


def test_reason_inconsistency_warns_never_changes_structured_selection():
    manager,session=sc01()
    def wrong_reason(context):
        option=next(o for o in context["options"] if not o["dominated_by"])
        return policy_action("SELECT_OPTION",option_id=option["option_id"],decision_reason="Select U47",finalize=True)
    result=OptimizedAgentOrchestrator(manager,MockModelProvider([wrong_reason]),config()).run(session.session_id,1)
    assert result.termination_reason=="SUCCESS"
    record=result.trace.steps[0].decision_record
    assert record.reason_argument_consistency is False and record.warnings==["REASON_ARGUMENT_INCONSISTENCY"]
    assert result.proposal.formation_changes[0].added_nodes==["U59"]


def test_fast_strong_router_only_selects_provider():
    _,_,w,catalog=prepared()
    snapshot=DecisionSnapshotBuilder(config()).build(w,state_for(w),catalog)
    fast,strong=MockModelProvider([]),MockModelProvider([])
    router=PolicyModelRouter(config(),fast,strong)
    assert router.choose(snapshot)[0] is fast
    assert router.choose(snapshot,2)[0] is strong
    snapshot.validator_feedback={"status":"FAIL","hard_failures":[]}
    assert router.choose(snapshot)[0] is strong
    assert w.proposal is None
    _,_,w,catalog=prepared(2)
    snapshot=DecisionSnapshotBuilder(config()).build(w,state_for(w),catalog)
    assert router.choose(snapshot)[0] is strong


@pytest.mark.parametrize("budget",["calls","time","timeout"])
def test_bounded_agent_explicit_fallback_not_pure_success(budget):
    manager,session=sc01()
    def wait_then_query(context):
        if budget=="time":
            sleep(.04)
        if budget=="timeout":
            raise ModelError("MODEL_TIMEOUT")
        return policy_action("CALL_TOOL",tool_name="get_current_state_summary")
    cfg=config(bounded_max_model_calls=1,bounded_agent_seconds=.03 if budget=="time" else 10)
    result=OptimizedAgentOrchestrator(manager,MockModelProvider([wait_then_query]),cfg).run(session.session_id,1,mode="bounded_agent")
    assert result.termination_reason=="FALLBACK_SUCCESS" and result.fallback_used
    assert result.fallback_success and result.overall_success and not result.pure_agent_success
    assert result.timing.fallback_ms>0 and result.trace.fallback_reason
    assert result.trace.steps[-1].observation.tool=="deterministic_fallback"


def test_adaptive_decision_timeout_never_applies_late_action():
    manager,session=sc01()
    def late(context):
        sleep(.04)
        return policy_action("SELECT_OPTION",option_id=context["options"][0]["option_id"],finalize=True)
    # Preparation is part of decision budget; tiny budget may expire before model.
    result=OptimizedAgentOrchestrator(manager,MockModelProvider([late]),config(decision_time_budget=.01)).run(session.session_id,1)
    assert result.termination_reason=="DECISION_TIME_BUDGET_EXCEEDED" and not result.fallback_used
    assert manager.get(session.session_id).state==session.state


def test_deterministic_realtime_has_no_model_calls():
    manager,session=sc01()
    provider=MockModelProvider([])
    result=OptimizedAgentOrchestrator(manager,provider,config()).run(session.session_id,1,mode="deterministic_realtime")
    assert result.termination_reason=="DETERMINISTIC_SUCCESS" and not provider.contexts
    assert result.overall_success and not result.fallback_used


def test_strict_schema_metrics_compression_and_old_index_rejected():
    _,_,w,catalog=prepared()
    snapshot=DecisionSnapshotBuilder(config()).build(w,state_for(w),catalog)
    context=DeltaContextCompiler(config()).compile(snapshot,state_for(w),remaining_seconds=10,calls_remaining=2)
    provider=OpenAICompatibleProvider(config())
    body,metrics=provider.build_request(context,tool_schemas(policy_registry()))
    assert body["response_format"]["json_schema"]["strict"]
    schema=body["response_format"]["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert "option_index" not in str(schema)
    assert "option_index" not in str(tool_schemas(policy_registry()))
    assert all(getattr(metrics,k)>0 for k in ["system_chars","tool_schema_chars","context_chars","estimated_input_tokens"])
    assert metrics.history_chars==0
    with pytest.raises(ActionRejected,match="INVALID_TOOL_ARGUMENTS"):
        parse_policy_action(policy_action("CALL_TOOL",tool_name="try_in_place_repair",arguments={"task_id":"T03","option_index":0}),policy_registry(),w)


def test_query_options_is_independent_and_nullable_fields_are_valid():
    manager,session=sc01()
    actions=[policy_action("CALL_TOOL",tool_name="get_reconstruction_options",arguments={"task_id":None,"strategy":None})]
    result=OptimizedAgentOrchestrator(manager,MockModelProvider(actions),config(max_agent_steps=1)).run(session.session_id,1)
    assert result.trace.steps[0].observation.status=="OK"
    assert result.trace.steps[0].observation.data["options"]
    assert manager.get(session.session_id).state==session.state


def test_profile_snapshot_keeps_manual_validation_and_commit():
    manager,session=sc01()
    cfg=AgentConfig(policy_profile="snapshot")
    provider=MockModelProvider(scripted_policy("A","snapshot"))
    result=OptimizedAgentOrchestrator(manager,provider,cfg).run(session.session_id,1)
    assert result.termination_reason=="SUCCESS" and result.model_calls==3
    assert "dominated_by" not in provider.contexts[0]["options"][0]


def test_stale_during_auto_commit_requires_refresh_and_replan():
    manager,session=sc01()
    class ConcurrentPrimitives(ReconstructionPrimitives):
        injected=False
        def commit_validated_proposal(self,session_id,proposal):
            if not self.injected:
                self.injected=True
                manager.inject(session_id,1,session.events[1])
            return super().commit_validated_proposal(session_id,proposal)
    choose_a=scripted_policy("A","optimized")[0]
    def refresh(context):
        assert context["needs_refresh"]
        return policy_action("CALL_TOOL",tool_name="get_current_state_summary")
    provider=MockModelProvider([choose_a,refresh,*scripted_policy("B","optimized")])
    result=OptimizedAgentOrchestrator(manager,provider,config(),primitives=ConcurrentPrimitives(manager)).run(session.session_id,1)
    assert result.termination_reason=="SUCCESS" and result.final_state_version==3
    assert result.trace.steps[0].observation.reason_codes==["STALE_PROPOSAL"]


def test_scope_only_metadata_change_retains_failed_plan_lineage():
    manager,session=manager_for_state(validator_feedback())
    seed=feedback_seed(manager,session)
    def select_after_scope(context):
        assert context["feedback"]["status"]=="FAIL"
        assert "T06" in context["scope"]
        option=next(o for o in context["options"] if o["replace_plan"] and not o["remaining"])
        assert option["add"]==["U2"] and option["task"]=="T03"
        return policy_action("SELECT_OPTION",option_id=option["option_id"],finalize=True)
    provider=MockModelProvider([policy_action("FINALIZE_PROPOSAL"),
        policy_action("CALL_TOOL",tool_name="request_scope_expansion",arguments={"entity":"task:T06",
            "reason":"Global regression","source_validation_id":"latest"}),select_after_scope])
    result=OptimizedAgentOrchestrator(manager,provider,config()).run(session.session_id,0,initial_proposal=seed)
    assert result.termination_reason=="SUCCESS" and result.model_calls==3 and result.validation_attempts==2


def test_catalog_never_offers_noop_as_repair_of_existing_failed_plan():
    manager,session=manager_for_state(validator_feedback())
    workspace=AgentWorkspace(manager,session.session_id)
    workspace.install_initial(feedback_seed(manager,session))
    catalog=OptionCatalog(config())
    catalog.prepare(workspace)
    for bound in catalog.handles.values():
        from backend.app.reconstruction.common import apply_option
        from backend.app.reconstruction.cost import describe_delta
        assert any(describe_delta(bound.origin,apply_option(bound.origin,bound.local)))


def test_replacement_without_finalize_marks_old_failure_as_historical():
    manager,session=manager_for_state(validator_feedback())
    seed=feedback_seed(manager,session)
    def replace(context):
        assert context["feedback"]["applies_to_current_plan"] is True
        option=next(o for o in context["options"] if o["replace_plan"] and not o["remaining"])
        return policy_action("SELECT_OPTION",option_id=option["option_id"],scope_expansions=[{
            "entity":"task:T06","reason":"Validated regression","source_validation_id":"latest"}])
    def finalize(context):
        assert context["feedback"] is None  # obsolete hard failures are audit-only
        assert context["historical_validation"]["status"]=="SUPERSEDED_BY_CURRENT_PLAN"
        assert context["proposal"]["validation"]=="NOT_VALIDATED"
        assert context["violations"]==[] and not context["needs_refresh"]
        return policy_action("FINALIZE_PROPOSAL")
    provider=MockModelProvider([policy_action("FINALIZE_PROPOSAL"),replace,finalize])
    result=OptimizedAgentOrchestrator(manager,provider,config()).run(session.session_id,0,initial_proposal=seed)
    assert result.termination_reason=="SUCCESS" and result.model_calls==3
    assert result.trace.steps[-1].decision_record.validator_feedback["status"]=="FAIL"
    assert result.trace.steps[-1].decision_record.validator_feedback["applies_to_current_plan"] is False
