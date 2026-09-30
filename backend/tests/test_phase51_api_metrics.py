import json

import pytest
from fastapi.testclient import TestClient

from backend.app.agent.config import AgentConfig
from backend.app.agent.providers.mock import MockModelProvider
from backend.app.main import create_app
from scripts.benchmark_phase51 import percentiles,policy_action,summarize


def factory(config):
    def choose(context):
        option=next(o for o in context["options"] if not o["dominated_by"] and not o["remaining"])
        return policy_action("SELECT_OPTION",option_id=option["option_id"],finalize=True)
    return MockModelProvider([choose]*8)


def ready(client):
    session=client.post("/api/v1/scenario/load",json={"scenario_id":"SC01"}).json()
    client.post("/api/v1/event/inject",json={"session_id":session["session_id"],"expected_version":0,
        "event":session["events"][0]}).raise_for_status()
    return {"session_id":session["session_id"],"expected_version":1}


@pytest.mark.parametrize("mode",["agent","adaptive_agent","bounded_agent","deterministic_realtime"])
def test_api_execution_modes_and_persisted_decision_record(mode):
    config=AgentConfig(policy_profile="optimized",bounded_agent_seconds=10)
    with TestClient(create_app(agent_config=config,agent_provider_factory=factory)) as client:
        payload=ready(client)
        response=client.post("/api/v1/agent/reconstruct",json={**payload,"mode":mode})
        assert response.status_code==200,response.text
        result=response.json()
        assert result["overall_success"] and not result["fallback_used"]
        assert result["model_calls"]==(0 if mode=="deterministic_realtime" else 1)
        if mode!="deterministic_realtime":
            record=result["trace"]["steps"][0]["decision_record"]
            assert record["selected_option"] and record["available_options"] and record["prompt_size"]["context_chars"]>0
        assert client.get("/api/v1/agent/runs/"+result["agent_run_id"]).json()==result
        assert client.post("/api/v1/agent/reconstruct",json=payload).status_code==409


def test_api_auto_commit_off_is_readonly_until_explicit_p4_commit():
    with TestClient(create_app(agent_config=AgentConfig(policy_profile="optimized",auto_commit_after_validation=False),
                               agent_provider_factory=factory)) as client:
        payload=ready(client)
        result=client.post("/api/v1/agent/reconstruct",json=payload).json()
        assert result["termination_reason"]=="VALIDATED_NOT_COMMITTED" and result["final_state_version"]==1
        committed=client.post("/api/v1/reconstruction/commit",json={"session_id":payload["session_id"],"proposal":result["proposal"]})
        assert committed.status_code==200 and committed.json()["committed_version"]==2


def test_router_environment_aliases_and_same_model_allowed(monkeypatch):
    monkeypatch.setenv("FAST_POLICY_MODEL","shared-model")
    monkeypatch.setenv("STRONG_MODEL_NAME","shared-model")
    monkeypatch.setenv("AGENT_EXECUTION_MODE","bounded_agent")
    monkeypatch.setenv("AUTO_COMMIT_AFTER_VALIDATION","false")
    config=AgentConfig.load()
    assert config.fast_model_name==config.strong_model_name=="shared-model"
    assert config.execution_mode=="bounded_agent" and not config.auto_commit_after_validation


def test_statistics_include_failure_and_lexicographic_gap_categories():
    # Metrics denominator includes failed trials; no survivor-only latency.
    def row(success,latency):
        return {"case":"A","profile":"optimized","disruption_comparison":"equal" if success else "not_comparable",
            "result":{"status":"COMMITTED" if success else "STOPPED","termination_reason":"SUCCESS" if success else "MODEL_ERROR",
                "fallback_used":False,"validation_status":"PASS_WITH_LIMITATIONS" if success else None,
                "model_calls":1,"validation_attempts":1 if success else 0,"tool_calls":3 if success else 0,"solver_calls":3,
                "timing":{"agent_total_ms":latency,"model_total_ms":latency-100,"fallback_ms":0,
                    "option_generation_ms":70,"context_compile_ms":5,"tool_total_ms":5,"validation_total_ms":10,"commit_ms":10},
                "trace":{"steps":[]}}}
    stats=summarize([row(True,1000),row(False,11000)])["optimized/A"]
    assert stats["pure_agent_success_rate"]==stats["overall_success_rate"]==.5
    assert stats["agent_latency_ms"]["p95"]==11000
    assert stats["disruption_comparison"]=={"equal":1,"agent_better":0,"agent_worse":0,"not_comparable":1}
    assert percentiles([1,2,3,4])["p95"]==4
