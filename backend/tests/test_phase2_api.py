import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app


@pytest.fixture
def client(scenario_dir):
    with TestClient(create_app(scenario_dir)) as client:
        yield client


def load(client):
    response = client.post("/api/v1/scenario/load", json={"scenario_id": "SC01"})
    response.raise_for_status()
    return response.json()


def inject(client, session, event, version):
    return client.post("/api/v1/event/inject", json={
        "session_id": session["session_id"], "expected_version": version, "event": event,
    })


def test_load_inject_assess_sequential_events_over_api(client):
    session = load(client)
    assert client.get("/api/v1/health").json()["implemented_phase"] >= 2
    assert client.post("/api/v1/assessment", json={"session_id": session["session_id"]}).status_code == 404
    for index, event in enumerate(session["events"]):
        response = inject(client, session, event, index)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["application"]["state_version_before"] == index
        assert result["application"]["state_version_after"] == index + 1
        assert result["reconstruction_required"]
        assert len(result["task_assessment"]) == 1
        assessment = client.post("/api/v1/assessment", json={"session_id": session["session_id"], "event_id": event["event_id"]})
        assert assessment.json() == result
        current = client.get("/api/v1/scenario/state", params={"session_id": session["session_id"]}).json()
        assert current["state"] == result["application"]["state"]
    # Stateless boundary expects BEFORE-event state; replaying onto a later state is invalid.
    assert client.post("/api/v1/reconstruct", json={"state": current["state"], "event": session["events"][0]}).status_code == 409


def test_api_replay_is_historical_and_does_not_reset_session(client):
    session = load(client)
    first = inject(client, session, session["events"][0], 0).json()
    inject(client, session, session["events"][1], 1).raise_for_status()
    replay = inject(client, session, session["events"][0], 0).json()
    assert replay["replayed"]
    assert replay["application"] == first["application"]
    current = client.get("/api/v1/scenario/state", params={"session_id": session["session_id"]}).json()
    assert current["state"]["version"] == 2


@pytest.mark.parametrize("case,status,code", [
    ("target", 404, "TARGET_NOT_FOUND"),
    ("version", 409, "VERSION_CONFLICT"),
    ("id", 409, "EVENT_ID_CONFLICT"),
    ("repeat", 409, "INVALID_TRANSITION"),
    ("order", 409, "EVENT_OUT_OF_ORDER"),
    ("geometry", 422, "INVALID_EVENT_STATE"),
])
def test_api_event_errors_are_explicit_and_atomic(client, case, status, code):
    session = load(client)
    inject(client, session, session["events"][0], 0).raise_for_status()
    payload = {"event_id": "NEXT", "occurred_at": 11, "type": "NodeFailure", "node_id": "U21"}
    version = 1
    if case == "target":
        payload["node_id"] = "U999"
    elif case == "version":
        version = 0
    elif case == "id":
        payload["event_id"] = "E001"
    elif case == "repeat":
        payload["node_id"] = "U17"
    elif case == "order":
        payload["occurred_at"] = 9
    else:
        payload = {"event_id": "NEXT", "occurred_at": 11, "type": "TargetMove", "task_id": "T03", "target": {"x": 2000, "y": 0}}
    before = client.get("/api/v1/scenario/state", params={"session_id": session["session_id"]}).json()
    response = inject(client, session, payload, version)
    assert response.status_code == status, response.text
    assert response.json()["detail"]["code"] == code
    assert client.get("/api/v1/scenario/state", params={"session_id": session["session_id"]}).json() == before


def test_sessions_and_history_are_isolated(client):
    first, second = load(client), load(client)
    inject(client, first, first["events"][0], 0).raise_for_status()
    assert client.get("/api/v1/scenario/state", params={"session_id": second["session_id"]}).json() == second
    response = client.post("/api/v1/assessment", json={"session_id": second["session_id"], "event_id": "E001"})
    assert response.status_code == 404
    assert client.post("/api/v1/assessment", json={"session_id": "unknown"}).status_code == 404
    assert inject(client, {"session_id": "unknown"}, first["events"][0], 0).status_code == 404
