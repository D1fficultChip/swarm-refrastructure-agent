"""Minimal in-process HTTP smoke demo; requires the dev dependencies."""

from fastapi.testclient import TestClient

from backend.app.main import create_app


def main():
    with TestClient(create_app()) as client:
        response = client.post("/api/v1/scenario/load", json={"scenario_id": "SC01"})
        response.raise_for_status()
        snapshot = response.json()
        response = client.get("/api/v1/scenario/state", params={"session_id": snapshot["session_id"]})
        response.raise_for_status()
        assert response.json() == snapshot
        state = snapshot["state"]
        print(f"Loaded {state['scenario_id']}: {len(state['nodes'])} nodes, "
              f"{len(state['tasks'])} tasks, {len(state['formations'])} formations")
        print(f"seed={state['seed']}, state.version={state['version']}, "
              f"planned events={len(snapshot['events'])}")
        print("State round-trip: PASS")
        print("For the complete reconstruction loop, run scripts.demo_phase4")


if __name__ == "__main__":
    main()
