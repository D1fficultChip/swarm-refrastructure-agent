"""Run a small, real-model Phase 6.2 smoke test through the public REST API."""
import argparse
import json
import time
from pathlib import Path

import httpx


def post(client, path, body):
    response = client.post(path, json=body)
    response.raise_for_status()
    return response.json()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--seeds", default="62001,62002")
    parser.add_argument("--output", default="artifacts/phase62/agent-smoke.json")
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    rows = []
    with httpx.Client(base_url=args.base_url, timeout=30, trust_env=False) as client:
        for offset, scenario_seed in enumerate(int(value) for value in args.seeds.split(",")):
            created = post(client, "/api/v1/dynamic/session", {
                "scenario_seed": scenario_seed,
                "event_seed": scenario_seed + 1000,
                "event_mode": "manual",
                "difficulty": "L3",
                "execution_mode": "adaptive_agent",
                "reconstruction_trigger": "manual",
                "simulation_speed": 2,
            })
            session_id = created["dynamic_session_id"]
            post(client, "/api/v1/dynamic/start", {"session_id": session_id})
            post(client, "/api/v1/dynamic/step", {"session_id": session_id, "seconds": offset + 1})
            broken = post(client, "/api/v1/dynamic/event", {
                "session_id": session_id, "difficulty": "L3", "mode": "semi_random"
            })
            post(client, "/api/v1/dynamic/reconstruct", {"session_id": session_id, "mode": "adaptive_agent"})
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                state = client.get(f"/api/v1/dynamic/state/{session_id}").json()
                if not state["current_reconstruction_run_id"]:
                    break
                time.sleep(.25)
            else:
                raise TimeoutError(f"adaptive reconstruction timed out for seed {scenario_seed}")
            run_id = state["reconstruction_history"][-1]
            run = client.get(f"/api/v1/demo/run/{run_id}").json()
            rows.append({
                "scenario_seed": scenario_seed,
                "event_seed": scenario_seed + 1000,
                "dynamic_run_id": state["run_id"],
                "reconstruction_run_id": run_id,
                "event_objects": [event.get("node_id") or event.get("region", {}).get("id") for event in broken["event_history"]],
                "status": run["status"],
                "feasible_tasks_after": state["planning_metrics"]["feasible_tasks"],
                "task_count": state["planning_metrics"]["task_count"],
                "model_calls": run["policy_metrics"].get("model_calls", 0),
                "pure_agent_success": run["policy_metrics"].get("pure_agent_success", False),
                "fallback_used": run["policy_metrics"].get("fallback_used", False),
                "formation_changes": run.get("reconstruction_proposal", {}).get("formation_changes", []),
                "route_changes": run.get("reconstruction_proposal", {}).get("route_changes", []),
                "timing": run.get("timing", {}),
            })
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {"provider": "real configured server", "difficulty": "L3", "trials": rows}
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "trials": [{"seed": r["scenario_seed"], "status": r["status"], "model_calls": r["model_calls"]} for r in rows]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
