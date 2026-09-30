"""Exercise Uvicorn's real protocol loading, which TestClient bypasses."""

import re
import subprocess
import sys
import time
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[2]


def test_uvicorn_default_protocols_load():
    result = subprocess.run(
        [sys.executable, "-c",
         "from uvicorn import Config; Config('backend.app.main:app').load()"],
        cwd=ROOT, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_uvicorn_cli_serves_scenario_over_http(tmp_path):
    """Use the documented CLI with an OS-assigned port; always stop the server."""
    log_path = tmp_path / "uvicorn.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "backend.app.main:app",
             "--host", "127.0.0.1", "--port", "0"],
            cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 10
            while True:
                output = log_path.read_text(encoding="utf-8")
                assert process.poll() is None, output
                match = re.search(r"Uvicorn running on (http://127\.0\.0\.1:\d+)", output)
                if match:
                    break
                assert time.monotonic() < deadline, f"Uvicorn startup timed out:\n{output}"
                time.sleep(0.05)
            with httpx.Client(base_url=match.group(1), trust_env=False, timeout=5) as client:
                health = client.get("/api/v1/health")
                health.raise_for_status()
                assert health.json()["status"] == "ok"
                assert client.get("/docs").status_code == 200
                response = client.post("/api/v1/scenario/load", json={"scenario_id": "SC01"})
                response.raise_for_status()
                snapshot = response.json()
                assert len(snapshot["state"]["nodes"]) == 60
                state = client.get("/api/v1/scenario/state", params={"session_id": snapshot["session_id"]})
                state.raise_for_status()
                assert state.json() == snapshot
                injected = client.post("/api/v1/event/inject", json={
                    "session_id": snapshot["session_id"], "expected_version": 0, "event": snapshot["events"][0],
                })
                injected.raise_for_status()
                assert injected.json()["application"]["state_version_after"] == 1
                assert injected.json()["reconstruction_required"]
                assessed = client.post("/api/v1/assessment", json={"session_id": snapshot["session_id"]})
                assessed.raise_for_status()
                assert assessed.json() == injected.json()
                proposal = client.post("/api/v1/reconstruction/propose", json={
                    "session_id": snapshot["session_id"], "expected_version": 1,
                })
                proposal.raise_for_status()
                assert proposal.json()["solver_status"] == "FEASIBLE"
                assert proposal.json()["committed"] is False
                assert client.get("/api/v1/scenario/state", params={"session_id": snapshot["session_id"]}).json()["state"] == injected.json()["application"]["state"]
                action = {"session_id": snapshot["session_id"], "proposal": proposal.json()}
                validation = client.post("/api/v1/reconstruction/validate", json=action)
                validation.raise_for_status()
                assert validation.json()["status"] == "PASS_WITH_LIMITATIONS"
                receipt = client.post("/api/v1/reconstruction/commit", json=action)
                receipt.raise_for_status()
                assert receipt.json()["committed_version"] == 2
                trace = client.get(f"/api/v1/trace/{receipt.json()['reconstruction_id']}")
                trace.raise_for_status()
                assert trace.json()["committed"]
                assert next(n for n in trace.json()["state"]["nodes"] if n["id"] == "U17")["status"] == "FAILED"
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
