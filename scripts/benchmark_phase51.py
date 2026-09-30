"""Predeclared repeated trials; every success AND failure is retained, never selected.

Real C default 20/20/20/10. Run B with 10/10/10/5 for a smaller real ablation.
Mock A/B/C exercises the same fixtures without claiming model quality.
"""
import argparse
from datetime import datetime,timezone
import json
from hashlib import sha256
from math import ceil
from pathlib import Path
from statistics import median

from backend.app.agent.config import AgentConfig
from backend.app.agent.evaluation import agent_metrics,measured_baseline
from backend.app.agent.optimized_orchestrator import OptimizedAgentOrchestrator
from backend.app.agent.orchestrator import AgentReconstructionOrchestrator
from backend.app.agent.providers.mock import MockModelProvider
from backend.app.agent.providers.openai_compatible import OpenAICompatibleProvider
from backend.app.agent.security import redact
from backend.app.reconstruction.common import digest
from scripts.demo_phase5_agent import case_setup,feedback_seed,mock_script
from scripts.model_env import load_credentials_file


def policy_action(kind,**kwargs):
    return {"action_type":kind,"decision_reason":"Explicit offline policy action.",**kwargs}


def scripted_policy(case,profile):
    if profile=="original":
        return mock_script(case)[1:] if case=="D" else mock_script(case)
    def choose(context):
        options=[o for o in context["options"] if not o.get("dominated_by")]
        option=min(options,key=lambda o:(len(o["remaining"]),o["cost"],o["option_id"]))
        result=policy_action("SELECT_OPTION",option_id=option["option_id"],
            decision_reason="Select members "+','.join(option["add"]),finalize=profile=="optimized" and not option["remaining"])
        if context["feedback"] and context["feedback"]["status"]=="FAIL":
            result["scope_expansions"]=[{"entity":"task:T06","reason":"Global validator reported T06 regression.","source_validation_id":"latest"}]
        return result
    actions=[choose]*(2 if case=="B" else 1)
    if case=="C":
        actions=[policy_action("CALL_TOOL",tool_name="replan_route",arguments={"task_id":"T0"},finalize=profile=="optimized")]
    if case=="D":
        first=policy_action("FINALIZE_PROPOSAL") if profile=="optimized" else policy_action("CALL_TOOL",tool_name="validate_proposal")
        actions.insert(0,first)
    if profile=="snapshot":
        actions.extend([policy_action("CALL_TOOL",tool_name="validate_proposal"),policy_action("CALL_TOOL",tool_name="commit_validated_proposal")])
    return actions


def percentiles(values):
    if not values:
        return {"p50":None,"p95":None,"max":None}
    values=sorted(values)
    return {"p50":median(values),"p95":values[max(0,ceil(.95*len(values))-1)],"max":values[-1]}


def summarize(rows):
    output={}
    for key in sorted({(r["profile"],r["case"]) for r in rows}):
        trials=[r for r in rows if (r["profile"],r["case"])==key]
        results=[r["result"] for r in trials]
        decisions=[s for r in results for s in r["trace"]["steps"] if s["model_name"]!="none"]
        rejected=[s for s in decisions if s["observation"]["status"]=="ACTION_REJECTED"]
        dominated=[s for s in decisions if "DOMINATED_OPTION" in s["observation"]["reason_codes"]]
        choices=[s for s in decisions if s.get("decision_record") and s["decision_record"]["selected_option"]]
        dominated_choices=[s for s in choices if s["decision_record"]["selected_option"] not in s["decision_record"]["non_dominated_options"]]
        n=len(trials)
        pure=[r for r in results if r["termination_reason"] in {"SUCCESS","NO_RECONSTRUCTION_REQUIRED"} and not r["fallback_used"]]
        gaps={label:sum(t["disruption_comparison"]==label for t in trials) for label in ["equal","agent_better","agent_worse","not_comparable"]}
        stats={"n":n,"pure_agent_success_rate":len(pure)/n,
            "fallback_success_rate":sum(r["termination_reason"]=="FALLBACK_SUCCESS" for r in results)/n,
            "overall_success_rate":sum(r["status"]=="COMMITTED" or r["termination_reason"]=="NO_RECONSTRUCTION_REQUIRED" for r in results)/n,
            "validation_success_rate":sum(r["validation_status"] in {"PASS","PASS_WITH_LIMITATIONS"} for r in results)/n,
            "fallback_rate":sum(r["fallback_used"] for r in results)/n,
            "invalid_action_rate":len(rejected)/len(decisions) if decisions else 0,
            "dominated_action_rejection_rate":len(dominated)/len(decisions) if decisions else 0,
            "dominated_choice_rate":len(dominated_choices)/len(choices) if choices else None,
            "reason_argument_inconsistencies":sum(bool(s.get("decision_record") and s["decision_record"]["reason_argument_consistency"] is False) for s in decisions),
            "model_calls":percentiles([r["model_calls"] for r in results]),
            "validation_retries":percentiles([max(0,r["validation_attempts"]-1) for r in results]),
            "tool_calls":percentiles([r["tool_calls"] for r in results]),
            "solver_calls":percentiles([r["solver_calls"] for r in results]),
            "agent_latency_ms":percentiles([r["timing"]["agent_total_ms"] for r in results]),
            "model_latency_ms":percentiles([r["timing"]["model_total_ms"] for r in results]),
            "model_each_call_ms":percentiles([s["model_latency_ms"] for s in decisions]),
            "pure_agent_latency_ms":percentiles([r["timing"]["agent_total_ms"] for r in pure]),
            "fallback_latency_ms":percentiles([r["timing"]["fallback_ms"] for r in results if r["fallback_used"]]),
            "algorithm_core_ms":percentiles([sum(r["timing"][k] for k in ["option_generation_ms","context_compile_ms","tool_total_ms","validation_total_ms","commit_ms"]) for r in results]),
            "under_5s_rate":sum(r["timing"]["agent_total_ms"]<5000 and r in pure for r in results)/n,
            "disruption_comparison":gaps,
            "input_tokens":sum(s["token_usage"].get("prompt_tokens",0) for s in decisions),
            "output_tokens":sum(s["token_usage"].get("completion_tokens",0) for s in decisions),
            "termination_counts":{reason:sum(r["termination_reason"]==reason for r in results) for reason in sorted({r["termination_reason"] for r in results})}}
        prompt_sizes=[s["decision_record"]["prompt_size"] if s.get("decision_record") else s["prompt_size"] for s in decisions
                      if s.get("decision_record") or s.get("prompt_size")]
        stats["prompt_size_mean"]={k:sum(p[k] for p in prompt_sizes)/max(1,len(prompt_sizes))
            for k in ["system_chars","tool_schema_chars","action_schema_chars","context_chars","history_chars","estimated_input_tokens"]}
        output["/".join(key)]=stats
    return output


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider",choices=["mock","real"],default="mock")
    parser.add_argument("--profiles",default="optimized")
    parser.add_argument("--counts",default="20,20,20,10")
    parser.add_argument("--credentials-file",type=Path)
    parser.add_argument("--output-dir",type=Path,default=Path("artifacts/phase51"))
    parser.add_argument("--mode",choices=["adaptive_agent","bounded_agent","deterministic_realtime"],default="adaptive_agent")
    parser.add_argument("--summarize-only",action="store_true",help="Recompute metrics from preserved raw trials without API calls")
    args=parser.parse_args()
    if args.summarize_only:
        rows_path=args.output_dir/"trials.jsonl"
        rows=[json.loads(line) for line in rows_path.read_text().splitlines()]
        (args.output_dir/"summary.json").write_text(json.dumps(summarize(rows),ensure_ascii=False,indent=2)+"\n")
        (args.output_dir/"aggregation.json").write_text(json.dumps({"raw_sha256":sha256(rows_path.read_bytes()).hexdigest(),
            "aggregator_sha256":sha256(Path(__file__).read_bytes()).hexdigest(),"timestamp":datetime.now(timezone.utc).isoformat()},indent=2)+"\n")
        print("Aggregated",len(rows),"trials; raw evidence unchanged")
        return
    profiles=args.profiles.split(",")
    if not set(profiles)<={"original","snapshot","optimized"}:
        parser.error("Unknown profile")
    counts=[int(v) for v in args.counts.split(",")]
    if len(counts)!=4 or min(counts)<0:
        parser.error("Use four nonnegative counts for A,B,C,D")
    if args.provider=="real" and not load_credentials_file(args.credentials_file):
        print("SKIPPED_REAL_MODEL")
        return
    args.output_dir.mkdir(parents=True,exist_ok=True)
    rows_path=args.output_dir/"trials.jsonl"
    if rows_path.exists():
        parser.error("Output directory already contains trials; choose a fresh directory to preserve evidence")
    base_config=AgentConfig.load()
    manifest={"phase":"5.1","provider":args.provider,"profiles":profiles,"counts":dict(zip("ABCD",counts)),
        "execution_mode":args.mode,"started_at":datetime.now(timezone.utc).isoformat(),
        "config":base_config.model_dump(mode="json"),"selection":"all consecutive trials; no failed trial removal"}
    manifest["sources_sha256"]={str(p):sha256(p.read_bytes()).hexdigest() for p in [
        *sorted(Path("backend/app/agent").rglob("*.py")),Path(__file__),Path("scripts/phase5_scenarios.py")]}
    (args.output_dir/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
    rows=[]
    for profile in profiles:
        config=base_config.model_copy(update={"policy_profile":profile,"execution_mode":args.mode,"fallback_enabled":False})
        for case,count in zip("ABCD",counts):
            for number in range(1,count+1):
                manager,session=case_setup(case)
                baseline_manager,baseline_session=case_setup(case)
                assert digest(session.state)==digest(baseline_session.state)
                _,baseline=measured_baseline(baseline_manager,baseline_session)
                seed=feedback_seed(manager,session) if case=="D" else None
                provider=MockModelProvider(scripted_policy(case,profile)) if args.provider=="mock" else None
                objective="Restore all ACTIVE tasks, minimize lexicographic disruption, validate and commit the complete plan."
                if seed:
                    objective+=" Diagnostic: first validate/finalize the existing supplied proposal; then use true feedback to expand scope and replace the bad plan."
                if profile=="original":
                    engine=AgentReconstructionOrchestrator(manager,provider or OpenAICompatibleProvider(config),config)
                    result=engine.run(session.session_id,session.state.version,objective=objective,initial_proposal=seed)
                else:
                    engine=OptimizedAgentOrchestrator(manager,provider,config)
                    result=engine.run(session.session_id,session.state.version,objective=objective,initial_proposal=seed)
                result.timing.deterministic_baseline_ms=baseline["total_ms"]
                metrics=agent_metrics(result,session.state,manager.get(session.session_id).state)
                comparison="not_comparable"
                if metrics["committed"] and baseline["committed"]:
                    agent_cost=tuple(metrics["disruption"]["lexicographic_cost"])
                    base_cost=tuple(baseline["disruption"]["lexicographic_cost"])
                    comparison="equal" if agent_cost==base_cost else "agent_better" if agent_cost<base_cost else "agent_worse"
                row={"profile":profile,"case":case,"trial":number,"input_digest":digest(session.state),
                    "seed_origin":"P3 locally feasible diagnostic; built outside Agent timing" if seed else None,
                    "baseline_metrics":baseline,"agent_metrics":metrics,"disruption_comparison":comparison,"result":result.model_dump(mode="json")}
                row=redact(row)
                with rows_path.open("a",encoding="utf-8") as file:
                    file.write(json.dumps(row,ensure_ascii=False,separators=(",", ":"))+"\n")
                    file.flush()
                rows.append(row)
                summary=summarize(rows)
                (args.output_dir/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n")
                print(f"{profile} {case} {number}/{count}: {result.termination_reason}, calls={result.model_calls}, "
                    f"ms={result.timing.agent_total_ms:.1f}, gap={comparison}",flush=True)
    manifest["finished_at"]=datetime.now(timezone.utc).isoformat()
    (args.output_dir/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")


if __name__=="__main__":
    main()
