"""Reproduce formal Phase 6 evaluations without modifying the scenario or policy."""
import argparse
from pathlib import Path
from backend.app.demo.evaluation import Phase6EvaluationHarness
from scripts.model_env import load_credentials_file


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--scenarios',default='SC01,SC03,SC04,SC08')
    p.add_argument('--modes',default='deterministic_realtime')
    p.add_argument('--trials',type=int,default=100)
    p.add_argument('--output',type=Path,default=Path('artifacts/phase6/deterministic'))
    p.add_argument('--credentials-file',type=Path)
    args=p.parse_args();modes=args.modes.split(',')
    if any(m!='deterministic_realtime' for m in modes) and not load_credentials_file(args.credentials_file):
        raise SystemExit('MODEL_NOT_CONFIGURED: provide --credentials-file or MODEL_API_KEY')
    Phase6EvaluationHarness(args.output).run(args.scenarios.split(','),modes,args.trials)


if __name__=='__main__':main()
