import pytest
from fastapi.testclient import TestClient

from backend.app.main import create_app


@pytest.fixture
def client(scenario_dir):
    with TestClient(create_app(scenario_dir)) as test_client:
        yield test_client


def test_load_and_read_scenario(client):
    assert client.get("/api/v1/health").json()["reconstruction_available"] is True
    summary = client.get("/api/v1/scenarios").json()[0]
    assert summary["scenario_id"] == "SC01"
    assert summary["node_count"] == 60
    response = client.post("/api/v1/scenario/load", json={"scenario_id": "SC01"})
    assert response.status_code == 200
    snapshot = response.json()
    assert len(snapshot["state"]["nodes"]) == 60
    assert len(snapshot["state"]["tasks"]) == 8
    assert len(snapshot["state"]["formations"]) == 6
    assert client.get("/api/v1/scenario/state", params={"session_id": snapshot["session_id"]}).json() == snapshot


def test_http_sessions_are_independent(client):
    first = client.post("/api/v1/scenario/load", json={"scenario_id": "SC01"}).json()
    second = client.post("/api/v1/scenario/load", json={"scenario_id": "SC01"}).json()
    assert first["session_id"] != second["session_id"]
    assert first["state"] == second["state"]


@pytest.mark.parametrize("payload,status", [
    ({"scenario_id": "UNKNOWN"}, 404),
    ({"scenario_id": "../../etc/passwd"}, 422),
    ({"scenario_id": "SC01", "filename": "elsewhere"}, 422),
    ({}, 422),
])
def test_load_errors(client, payload, status):
    assert client.post("/api/v1/scenario/load", json=payload).status_code == status


def test_unknown_session_and_missing_session(client):
    response = client.get("/api/v1/scenario/state", params={"session_id": "unknown"})
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "SESSION_NOT_FOUND"
    assert client.get("/api/v1/scenario/state").status_code == 422


def test_stateless_reconstruct_preserves_loaded_session_and_unknown_trace_is_404(client):
    snapshot = client.post("/api/v1/scenario/load", json={"scenario_id": "SC01"}).json()
    response = client.post("/api/v1/reconstruct", json={"state": snapshot["state"], "event": snapshot["events"][0]})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["session_id"] is None
    assert result["committed"] and result["state"]["version"] == 2
    assert client.get(f"/api/v1/trace/{result['reconstruction_id']}").json() == result
    responses = [
        client.get("/api/v1/reconstruction/example"),
        client.get("/api/v1/trace/example"),
    ]
    for response in responses:
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "RECONSTRUCTION_NOT_FOUND"
    assert client.get("/api/v1/scenario/state", params={"session_id": snapshot["session_id"]}).json() == snapshot


def test_openapi_exposes_event_discriminator(client):
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schemas = response.json()["components"]["schemas"]
    event_schema = schemas["InjectEventRequest"]["properties"]["event"]
    assert event_schema["discriminator"]["propertyName"] == "type"
    assert len(event_schema["oneOf"]) == 8
