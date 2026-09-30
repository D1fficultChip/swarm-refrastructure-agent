import pytest
from fastapi.testclient import TestClient

from backend.app.agent.config import AgentConfig
from backend.app.agent.providers.mock import MockModelProvider
from backend.app.main import create_app
from scripts.phase5_scenarios import action


@pytest.fixture
def client(scenario_dir):
    def provider(config):
        return MockModelProvider([action("try_in_place_repair",task_id="T03"),action("validate_proposal"),
                                  action("commit_validated_proposal")])
    with TestClient(create_app(scenario_dir,agent_provider_factory=provider,agent_config=AgentConfig())) as client:
        yield client


def ready(client):
    session = client.post("/api/v1/scenario/load",json={"scenario_id":"SC01"}).json()
    client.post("/api/v1/event/inject",json={"session_id":session["session_id"],"expected_version":0,
                                          "event":session["events"][0]}).raise_for_status()
    return {"session_id":session["session_id"],"expected_version":1}


@pytest.mark.parametrize("mode",["agent","deterministic"])
def test_transport_independent_orchestrator_api_trace_and_stale(client,mode):
    body = ready(client)
    response = client.post("/api/v1/agent/reconstruct",json={**body,"mode":mode})
    assert response.status_code == 200,response.text
    result = response.json()
    assert result["termination_reason"] == ("SUCCESS" if mode == "agent" else "DETERMINISTIC_SUCCESS")
    assert result["final_state_version"] == 2
    assert result["trace"]["policy_mode"] == ("mock" if mode == "agent" else "deterministic")
    assert client.get("/api/v1/agent/runs/"+result["agent_run_id"]).json() == result
    assert client.get("/api/v1/reconstruction/"+result["reconstruction_id"]).json()["committed"]
    assert client.post("/api/v1/agent/reconstruct",json=body).status_code == 409


def test_unknown_session_invalid_mode_and_unknown_trace(client):
    assert client.post("/api/v1/agent/reconstruct",json={"session_id":"unknown","expected_version":0}).status_code == 404
    assert client.post("/api/v1/agent/reconstruct",json={"session_id":"unknown","expected_version":0,"mode":"fake"}).status_code == 422
    assert client.get("/api/v1/agent/runs/unknown").status_code == 404


def test_initial_proposal_is_typed_and_invalid_deltas_rejected(client):
    body = ready(client)
    proposal = client.post("/api/v1/reconstruction/propose",json=body).json()
    proposal["formation_changes"][0]["added_nodes"] = []
    response = client.post("/api/v1/agent/reconstruct",json={**body,"initial_proposal":proposal})
    assert response.status_code == 422,response.text
    assert response.json()["detail"]["code"] == "DELTA_MISMATCH"
    assert client.get("/api/v1/scenario/state",params={"session_id":body["session_id"]}).json()["state"]["version"] == 1


def test_runtime_missing_key_returns_audited_model_error_without_commit(scenario_dir,monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY",raising=False)
    with TestClient(create_app(scenario_dir,agent_config=AgentConfig())) as client:
        body = ready(client)
        result = client.post("/api/v1/agent/reconstruct",json=body).json()
        assert result["termination_reason"] == "MODEL_ERROR" and result["commit_receipt"] is None
        assert result["trace"]["steps"][0]["observation"]["reason_codes"] == ["MODEL_API_KEY_MISSING"]
        assert result["final_state_version"] == 1 and result["model_calls"] == 0
