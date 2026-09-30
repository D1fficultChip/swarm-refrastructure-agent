import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app


@pytest.fixture
def client(scenario_dir):
    with TestClient(create_app(scenario_dir)) as client:
        yield client


def ready(client):
    session=client.post("/api/v1/scenario/load",json={"scenario_id":"SC01"}).json()
    client.post("/api/v1/event/inject",json={"session_id":session["session_id"],"expected_version":0,
                                          "event":session["events"][0]}).raise_for_status()
    proposal=client.post("/api/v1/reconstruction/propose",json={"session_id":session["session_id"],"expected_version":1}).json()
    return session,{"session_id":session["session_id"],"proposal":proposal}


def state(client,session):
    return client.get("/api/v1/scenario/state",params={"session_id":session["session_id"]}).json()


def test_readonly_validate_commit_idempotency_conflict_and_trace(client):
    session,action=ready(client)
    before=state(client,session)
    validation=client.post("/api/v1/reconstruction/validate",json=action)
    assert validation.status_code==200
    assert validation.json()["status"]=="PASS_WITH_LIMITATIONS"
    assert state(client,session)==before
    response=client.post("/api/v1/reconstruction/commit",json=action)
    assert response.status_code==200,response.text
    receipt=response.json()
    assert receipt["base_version"]==1 and receipt["committed_version"]==2
    # Commit always revalidates; the earlier validation token is not trusted.
    assert receipt["validation_id"]!=validation.json()["validation_id"]
    replay=client.post("/api/v1/reconstruction/commit",json=action).json()
    assert replay["replayed"] and replay["commit_id"]==receipt["commit_id"]
    assert state(client,session)["state"]["version"]==2
    result=client.get(f"/api/v1/reconstruction/{receipt['reconstruction_id']}").json()
    assert client.get(f"/api/v1/trace/{receipt['reconstruction_id']}").json()==result
    assert result["event_traces"][0]["trace"]["event"]["event_id"]=="E001"
    assert result["validation"]["validation_id"]==receipt["validation_id"]
    action["proposal"]["explanations"].append("tamper")
    conflict=client.post("/api/v1/reconstruction/commit",json=action)
    assert conflict.status_code==409 and conflict.json()["detail"]["code"]=="PROPOSAL_ID_CONFLICT"


def test_stale_proposal_returns_409_without_overwriting_second_event(client):
    session,action=ready(client)
    client.post("/api/v1/event/inject",json={"session_id":session["session_id"],"expected_version":1,
                                          "event":session["events"][1]}).raise_for_status()
    before=state(client,session)
    validation=client.post("/api/v1/reconstruction/validate",json=action).json()
    assert validation["status"]=="FAIL"
    assert "STATE_VERSION_MISMATCH" in {c["code"] for c in validation["hard_failures"]}
    response=client.post("/api/v1/reconstruction/commit",json=action)
    assert response.status_code==409 and response.json()["detail"]["code"]=="STALE_PROPOSAL"
    assert state(client,session)==before


@pytest.mark.parametrize("tamper",["digest","cost","extra_observation"])
def test_commit_rejects_stale_bad_metadata_and_observation_fields(client,tamper):
    session,action=ready(client)
    before=state(client,session)
    if tamper=="digest": action["proposal"]["base_state_digest"]="fake"
    if tamper=="cost": action["proposal"]["cost_breakdown"]["route_change_count"]=99
    if tamper=="extra_observation": action["proposal"]["node_changes"][0]["status"]="NORMAL"
    response=client.post("/api/v1/reconstruction/commit",json=action)
    assert response.status_code==(409 if tamper=="digest" else 422)
    if tamper=="cost":
        assert response.json()["detail"]["validation"]["status"]=="FAIL"
    assert state(client,session)==before


def test_end_to_end_preview_then_default_commit_and_session_isolation(client):
    session,action=ready(client)
    before=state(client,session)
    other=client.post("/api/v1/scenario/load",json={"scenario_id":"SC01"}).json()
    payload={"session_id":session["session_id"],"expected_version":1}
    preview=client.post("/api/v1/reconstruct",json={**payload,"commit":False})
    assert preview.status_code==200,preview.text
    assert not preview.json()["committed"] and preview.json()["receipt"] is None
    assert state(client,session)==before
    response=client.post("/api/v1/reconstruct",json=payload)
    assert response.status_code==200,response.text
    assert response.json()["committed"] and response.json()["state"]["version"]==2
    assert client.post("/api/v1/reconstruct",json=payload).status_code==409
    assert state(client,other)==other


@pytest.mark.parametrize("endpoint",["validate","commit"])
def test_unknown_session(client,endpoint):
    _,action=ready(client)
    action["session_id"]="unknown"
    assert client.post(f"/api/v1/reconstruction/{endpoint}",json=action).status_code==404


def test_stateless_preview_preserves_baseline(client):
    session=client.post("/api/v1/scenario/load",json={"scenario_id":"SC01"}).json()
    response=client.post("/api/v1/reconstruct",json={"state":session["state"],"event":session["events"][0],"commit":False})
    assert response.status_code==200,response.text
    assert response.json()["session_id"] is None
    assert not response.json()["committed"] and response.json()["state"]["version"]==1
    assert state(client,session)==session
