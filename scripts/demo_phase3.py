"""Real proposal examples; no session commit or Phase 4 validation."""
import argparse
import json
import platform
import statistics
from pathlib import Path

from backend.app.assessment.service import process_event
from backend.app.reconstruction.planner import HierarchicalReconstructionPlanner
from scripts.build_scenario import build_sc01
from scripts.phase3_scenarios import small_scenario


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs",type=int,default=10)
    parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    if args.runs<1:
        parser.error("--runs must be positive")
    scenario=build_sc01()
    state=scenario.initial_state
    receipts=[]
    examples=[]
    for event in scenario.events:
        receipt=process_event(state,event)
        receipts.append(receipt)
        state=receipt.application.state
        examples.append(HierarchicalReconstructionPlanner().propose(state,receipts))
    for kind in ["reassignment","new_formation","impossible"]:
        examples.append(HierarchicalReconstructionPlanner().propose(small_scenario(kind)))
    for proposal in examples:
        print("Scope:",proposal.reconstruction_scope.affected_tasks,"events:",proposal.source_events)
        print("Status:",proposal.solver_status.value,"strategy:",proposal.strategy_level)
        for candidates in proposal.trace.candidate_sets:
            print("Candidates (search input):",candidates.task_id,candidates.strategy_level.value,
                  [(c.node_id,round(c.distance,3)) for c in candidates.candidates])
        print("Selected:",[(s.task_id,s.level.value,s.selected_candidates) for s in proposal.trace.selected_strategies])
        print("Task deltas:",[change.model_dump(mode="json") for change in proposal.task_changes])
        print("Formation deltas:",[(f.formation_id,f.added_nodes,f.removed_nodes) for f in proposal.formation_changes])
        print("Route decisions:",[d.model_dump(mode="json") for d in proposal.trace.route_decisions])
        print("Cost:",proposal.cost_breakdown.model_dump_json())
        print("Timing:",proposal.trace.timing.model_dump_json())
        print("Global validation:",proposal.global_validation,"committed:",proposal.committed)
    samples=[HierarchicalReconstructionPlanner().propose(state,receipts).trace.timing for _ in range(args.runs)]
    observations={}
    for field in type(samples[0]).model_fields:
        values=sorted(getattr(s,field) for s in samples)
        observations[field]={"p50":statistics.median(values),"p95":values[(len(values)*95+99)//100-1],"max":max(values)}
    evidence={"phase":3,"scope":"proposal only, SC01 U17+U21; no Phase4/LLM/HTTP latency",
              "python":platform.python_version(),"platform":platform.platform(),"runs":args.runs,
              "examples":[p.model_dump(mode="json") for p in examples],
              "samples_ms":[s.model_dump() for s in samples],"observations_ms":observations}
    print("Observed phase3 ms:",json.dumps(observations))
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        print("Saved",args.output)


if __name__=="__main__":
    main()
