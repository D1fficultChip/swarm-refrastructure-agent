"""Run four audited policy cases; real API usage is explicit, offline mock is available."""
import argparse
import json
from pathlib import Path

from backend.app.agent.config import AgentConfig
from backend.app.agent.evaluation import agent_metrics, measured_baseline
from backend.app.agent.orchestrator import AgentReconstructionOrchestrator
from backend.app.agent.providers.mock import MockModelProvider
from backend.app.agent.providers.openai_compatible import OpenAICompatibleProvider
from backend.app.agent.security import redact
from backend.app.agent.workspace import AgentWorkspace
from backend.app.reconstruction.common import apply_option, digest
from scripts.model_env import load_credentials_file
from scripts.phase5_scenarios import action, feedback_actions, manager_for_state, route_only, sc01, validator_feedback


def case_setup(name):
    if name in {"A","B"}:
        return sc01(1 if name == "A" else 2)
    return manager_for_state(route_only() if name == "C" else validator_feedback())


def mock_script(name):
    if name in {"A","B"}:
        tasks = ["T03"] if name == "A" else ["T03","T04"]
        return [action("inspect_task_state",task_id=t) for t in tasks]+[
            action("generate_candidates",task_id=t) for t in tasks]+[
            action("try_in_place_repair",task_id=t) for t in tasks]+[
            action("validate_proposal"),action("commit_validated_proposal")]
    if name == "C":
        return [action("detect_route_impacts"),action("replan_route",task_id="T0"),
                action("validate_proposal"),action("commit_validated_proposal")]
    return feedback_actions()


def feedback_seed(manager, session):
    """Explicit diagnostic input: a real locally feasible, globally invalid plan.

    The model must review it. No mocked validator and no claimed model authorship
    of this seed. All subsequent actions come from the provider.
    """
    workspace = AgentWorkspace(manager,session.session_id)
    options = workspace.tools.try_in_place_repair(workspace.base,"T03")
    workspace.working = apply_option(workspace.base,options.options[0])
    workspace.trace = options.trace
    return workspace.build_proposal()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider",choices=["mock","real"],default="real")
    parser.add_argument("--credentials-file",type=Path)
    parser.add_argument("--case",choices=["A","B","C","D","all"],default="all")
    parser.add_argument("--output",type=Path)
    args = parser.parse_args()
    if args.provider == "real" and not load_credentials_file(args.credentials_file):
        print("SKIPPED_REAL_MODEL: set MODEL_API_KEY or supply --credentials-file; --provider mock runs offline.")
        return
    config = AgentConfig.load()
    evidence = {"phase":5, "provider":args.provider,"model":config.model_name if args.provider == "real" else "mock-scripted",
                "cases":{}, "benchmark_scope":"One diagnostic run per case; not a statistical performance claim."}
    for name in ["A","B","C","D"] if args.case == "all" else [args.case]:
        manager, session = case_setup(name)
        baseline_manager, baseline_session = case_setup(name)
        assert digest(session.state) == digest(baseline_session.state)
        baseline_result, baseline = measured_baseline(baseline_manager,baseline_session)
        seed = feedback_seed(manager,session) if name == "D" and args.provider == "real" else None
        provider = MockModelProvider(mock_script(name)) if args.provider == "mock" else OpenAICompatibleProvider(config)
        objective = "Restore all ACTIVE tasks with minimum disruption; use registered tools; globally validate and commit."
        if seed:
            objective += (" This diagnostic supplies an existing locally feasible proposal. First validate that proposal. "
                "If global validation fails, inspect its affected tasks, explicitly expand scope for a regression outside "
                "the current task scope, and choose a revised plan from tool evidence.")
        print(f"Case {name}: provider={args.provider}, model={provider.name}; starting",flush=True)
        result = AgentReconstructionOrchestrator(manager,provider,config).run(session.session_id,session.state.version,
            objective=objective,initial_proposal=seed)
        result.timing.deterministic_baseline_ms = baseline["total_ms"]
        metrics = agent_metrics(result,session.state,manager.get(session.session_id).state)
        evidence["cases"][name] = {"seed_origin":"explicit primitive L1 diagnostic input" if seed else None,
            "seed_proposal":seed.model_dump(mode="json") if seed else None,
            "input_digest":digest(session.state),"result":result.model_dump(mode="json"),"agent_metrics":metrics,
            "baseline_metrics":baseline,"baseline_result":baseline_result.model_dump(mode="json")}
        print(f"  {result.termination_reason}; version {session.state.version} -> {result.final_state_version}",flush=True)
        for step in result.trace.steps:
            print(f"  {step.step}. {step.tool_name or step.observation.tool}: {step.observation.status} "
                  f"{','.join(step.observation.reason_codes)}",flush=True)
        print("  ms: agent=%.1f model=%.1f baseline=%.1f" % (
            result.timing.agent_total_ms,result.timing.model_total_ms,baseline["total_ms"]),flush=True)
        # Preserve unsuccessful evidence too; no silent retries until success.
        if args.output:
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(redact(evidence),ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    failed = [k for k,v in evidence["cases"].items() if v["result"]["termination_reason"] != "SUCCESS"]
    if failed:
        raise SystemExit("Cases did not complete: "+",".join(failed))


if __name__ == "__main__":
    main()
