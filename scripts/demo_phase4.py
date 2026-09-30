"""Phase 4: real commits, adversarial rejection, stale protection and timing."""
import argparse
import json
import platform
import statistics
from pathlib import Path

from backend.app.assessment.engine import TaskAssessmentEngine
from backend.app.main import DEFAULT_SCENARIO_DIR
from backend.app.reconstruction.common import digest
from backend.app.reconstruction.cost import DisruptionCostEvaluator, describe_delta
from backend.app.reconstruction.engine import ReconstructionError
from backend.app.reconstruction.materializer import ProposalMaterializer
from backend.app.scenarios.manager import ScenarioManager


def setup(manager, count):
    session = manager.load("SC01")
    for index in range(count):
        manager.inject(session.session_id, index, session.events[index])
    return session


def closed_loop(manager, count):
    session = setup(manager, count)
    before = manager.get(session.session_id).state
    result = manager.reconstruct(session.session_id, count)
    assert result.committed and result.state.version == count + 1
    assert result.validation.status == "PASS_WITH_LIMITATIONS"
    assert before.nodes == result.state.nodes and before.tasks == result.state.tasks
    assert before.routes == result.state.routes and before.environment == result.state.environment
    assert before.clock == result.state.clock
    changed = {f.formation_id for f in result.proposal.formation_changes}
    assert all(f == next(new for new in result.state.formations if new.id == f.id)
               for f in before.formations if f.id not in changed)
    assessments = TaskAssessmentEngine().assess(result.state, full_check=True)
    assert all(c.status != "FAIL" for a in assessments for c in a.constraints)
    additions = [n for f in result.proposal.formation_changes for n in f.added_nodes]
    assert len(set(additions)) == count
    return result, assessments


def summarize(values):
    ordered = sorted(values)
    return {"p50": statistics.median(ordered), "p95": ordered[(len(ordered)*95+99)//100-1], "max": max(ordered)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")
    manager = ScenarioManager(DEFAULT_SCENARIO_DIR)
    evidence = {"phase": 4, "scope": "deterministic core; no LLM or HTTP latency",
                "python": platform.python_version(), "platform": platform.platform(), "runs": args.runs, "examples": {}}
    for name, count in [("A_single_failure", 1), ("B_double_failure", 2)]:
        result, assessments = closed_loop(manager, count)
        evidence["examples"][name] = {"result": result.model_dump(mode="json"),
                                      "reassessment": [a.model_dump(mode="json") for a in assessments]}
        print(name, f"event version {count} -> commit version {result.state.version}", result.validation.status.value)
        for delta in result.proposal.formation_changes:
            print(" ", delta.formation_id, "remove", delta.removed_nodes, "add", delta.added_nodes)
        print("  Re-assessment:", [(a.task_id, a.decision.value) for a in assessments if a.task_id in result.proposal.reconstruction_scope.affected_tasks])
        print("  Facts preserved; unrelated tasks/routes/formations unchanged")
        print("  Timing:", result.metrics.model_dump_json())

    session = setup(manager, 1)
    before = manager.get(session.session_id).state
    proposal = manager.propose(session.session_id, 1)
    candidate = ProposalMaterializer().materialize(before, proposal)
    next(f for f in candidate.formations if f.id == "F03").node_ids.append("U17")
    proposal.task_changes, proposal.formation_changes, proposal.node_changes = describe_delta(before, candidate)
    proposal.cost_breakdown = DisruptionCostEvaluator().evaluate(before, candidate, proposal.route_changes)
    proposal.reconstruction_scope.affected_nodes = [n.node_id for n in proposal.node_changes]
    validation = manager.validate(session.session_id, proposal)
    assert "FAILED_NODE_ASSIGNED" in {c.code for c in validation.hard_failures}
    try:
        manager.commit(session.session_id, proposal)
        raise AssertionError("malicious proposal committed")
    except ReconstructionError as exc:
        assert exc.code == "VALIDATION_FAILED"
        rejected_status = exc.status_code
    assert manager.get(session.session_id).state == before
    evidence["examples"]["C_failed_node_reintroduced"] = {
        "proposal": proposal.model_dump(mode="json"), "validation": validation.model_dump(mode="json"),
        "http_status": rejected_status, "digest_before": digest(before),
        "digest_after": digest(manager.get(session.session_id).state), "state_unchanged": True}
    print("C_failed_node_reintroduced:", validation.status.value, [c.code for c in validation.hard_failures], "state unchanged")

    session = setup(manager, 1)
    proposal = manager.propose(session.session_id, 1)
    manager.inject(session.session_id, 1, session.events[1])
    before = manager.get(session.session_id).state
    try:
        manager.commit(session.session_id, proposal)
        raise AssertionError("stale proposal committed")
    except ReconstructionError as exc:
        assert exc.code == "STALE_PROPOSAL" and exc.status_code == 409
        stale = {"code": exc.code, "http_status": exc.status_code}
    assert manager.get(session.session_id).state == before
    evidence["examples"]["D_stale_proposal"] = {**stale, "base_version": proposal.base_state_version,
        "current_version": before.version, "digest_before_commit_attempt": digest(before),
        "digest_after_commit_attempt": digest(manager.get(session.session_id).state), "state_unchanged": True}
    print("D_stale_proposal: 409 STALE_PROPOSAL; version 2 and both failure facts preserved")

    samples = []
    for _ in range(args.runs):
        run_manager = ScenarioManager(DEFAULT_SCENARIO_DIR)
        result, _ = closed_loop(run_manager, 2)
        sample = result.metrics.model_dump()
        sample["phase2_total_ms"] = sum(t.metrics.total_phase2_ms for t in result.event_traces)
        sample["phase3_total_ms"] = result.proposal.trace.timing.total_phase3_ms
        sample["event_to_commit_core_ms"] = sample["phase2_total_ms"] + sample["deterministic_total_ms"]
        samples.append(sample)
    evidence["samples_ms"] = samples
    evidence["observations_ms"] = {name: summarize([s[name] for s in samples]) for name in samples[0]}
    print("Observed core ms:", json.dumps(evidence["observations_ms"], ensure_ascii=False))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        print("Saved", args.output)


if __name__ == "__main__":
    main()
