"""Reproducible Phase 2 examples and optional observed latency samples."""

import argparse
import json
import platform
import statistics
from pathlib import Path

from backend.app.assessment.service import process_event
from backend.app.assessment.engine import TaskAssessmentEngine
from backend.app.models.domain import ScenarioState
from backend.app.models.events import NodeFailure, ScenarioDefinition
from backend.app.models.phase2 import Phase2Result


def redundancy_example() -> ScenarioState:
    return ScenarioState.model_validate({
        "scenario_id": "REDUNDANCY", "seed": 1,
        "capabilities": [{"id": "relay", "description": "relay"}],
        "nodes": [{"id": key, "position": {"x": 0, "y": 0}, "capabilities": {"relay": 1},
                   "availability": {"start": 0, "end": 100}, "resource_remaining": 10} for key in ["U1", "U2"]],
        "formations": [{"id": "F1", "node_ids": ["U1", "U2"], "minimum_capabilities": {"relay": 1}}],
        "tasks": [{"id": "T1", "name": "Redundant relay", "requirements": {"relay": 1}, "min_nodes": 1,
                   "window": {"start": 0, "end": 100}, "start": {"x": 0, "y": 0}, "target": {"x": 10, "y": 0},
                   "formation_id": "F1", "route_id": "R1", "resource_required": 5}],
        "routes": [{"id": "R1", "waypoints": [{"x": 0, "y": 0}, {"x": 10, "y": 0}]}],
    })


def print_result(result: Phase2Result):
    print(f"Event: {result.trace.event.type} / {result.application.event_id}")
    print(f"Version: {result.application.state_version_before} -> {result.application.state_version_after}")
    print(f"Changed: {result.application.changed_entities}")
    for record in result.trace.propagation_records:
        if record.reason_code == "REQUIRED_CAPABILITY_MAY_CHANGE":
            print("Path:", " -> ".join(record.path))
    for assessment in result.task_assessment:
        print(f"Task {assessment.task_id}: {assessment.decision.value}; reasons={assessment.reason_codes}")
        for check in assessment.constraints:
            print(f"  {check.type}: {check.status.value} {check.details.model_dump_json()}")
    print("Timing:", result.metrics.model_dump_json())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Save full typed examples and latency observations as JSON")
    parser.add_argument("--runs", type=int, default=20)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")
    path = Path(__file__).resolve().parents[1] / "scenarios/sc01_node_failure.json"
    scenario = ScenarioDefinition.model_validate_json(path.read_text(encoding="utf-8"))
    current = scenario.initial_state
    examples = []
    for event in scenario.events:
        result = process_event(current, event)
        examples.append(result)
        print_result(result)
        current = result.application.state
    redundant = process_event(redundancy_example(), NodeFailure(event_id="ER", occurred_at=10, node_id="U1"))
    examples.append(redundant)
    print_result(redundant)
    samples = [process_event(scenario.initial_state, scenario.events[0]).metrics for _ in range(args.runs)]
    observations = {}
    for field in type(samples[0]).model_fields:
        values = sorted(getattr(sample, field) for sample in samples)
        observations[field] = {"p50": statistics.median(values), "p95": values[max(0, (len(values) * 95 + 99) // 100 - 1)], "max": max(values)}
    evidence = {
        "scope": "Phase 2 only; no solver, provider, HTTP serialization, or session queueing",
        "python": platform.python_version(), "platform": platform.platform(), "machine": platform.machine(),
        "seed": scenario.initial_state.seed, "runs": args.runs, "warmup": "SC01 sequential examples above",
        "baseline_assessment": [item.model_dump(mode="json") for item in
                                TaskAssessmentEngine().assess(scenario.initial_state, full_check=True)],
        "examples": [example.model_dump(mode="json") for example in examples],
        "samples_ms": [sample.model_dump() for sample in samples], "observations_ms": observations,
    }
    print("Observations (ms):", json.dumps(observations))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Saved structured evidence to {args.output}")


if __name__ == "__main__":
    main()
