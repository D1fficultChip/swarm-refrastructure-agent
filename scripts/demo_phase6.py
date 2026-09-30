"""Run the final scenario through the live REST adapter and save its real result."""
import argparse
import json
from pathlib import Path
from time import monotonic, sleep

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    parser.add_argument('--mode', choices=['deterministic_realtime', 'adaptive_agent', 'bounded_agent'], default='deterministic_realtime')
    parser.add_argument('--output', type=Path, default=Path('artifacts/phase6/final-sc08.json'))
    args = parser.parse_args()
    with httpx.Client(base_url=args.url, timeout=30, trust_env=False) as client:
        def post(endpoint, data):
            response = client.post('/api/v1/demo/' + endpoint, json=data)
            response.raise_for_status()
            return response.json()
        state = post('session', {'scenario_id': 'SC08'})
        state = post('event', {'session_id': state['session_id'], 'expected_version': 0})
        job = post('reconstruct', {'session_id': state['session_id'], 'expected_version': state['state']['version'], 'mode': args.mode})
        deadline = monotonic() + 240
        while True:
            response = client.get('/api/v1/demo/run/' + job['run_id'])
            response.raise_for_status()
            result = response.json()
            if result['status'] != 'RUNNING':
                break
            if monotonic() > deadline:
                raise TimeoutError('Demo still running; retrieve run ' + job['run_id'])
            sleep(.25)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({key: result[key] for key in ['run_id', 'execution_mode', 'status', 'timing', 'policy_metrics']}, ensure_ascii=False, indent=2))
    if result['status'] != 'COMMITTED':
        raise SystemExit('Run did not commit; inspect the saved structured result')


if __name__ == '__main__':
    main()
