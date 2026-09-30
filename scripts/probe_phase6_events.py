"""Probe existing task-event contracts through unchanged P2–P4 engines."""
import json
from pathlib import Path

from backend.app.models.events import event_adapter
from backend.app.reconstruction.engine import DeterministicReconstructionEngine
from backend.app.scenarios.manager import ScenarioManager


def probe():
    manager = ScenarioManager(Path('scenarios/demo'))
    initial = manager.load('SC01').state
    added = initial.tasks[0]
    cases = [
        dict(type='TaskAdd', task=added.model_dump(mode='json')),
        dict(type='TaskCancel', task_id='T01'),
        dict(type='TaskPriorityChange', task_id='T01', priority=5),
        dict(type='TargetMove', task_id='T05', target={'x': 850, 'y': 620}),
    ]
    evidence = {}
    for case in cases:
        event = event_adapter.validate_python(dict(event_id='PROBE', occurred_at=10, **case))
        # TaskAdd starts before this task exists, with its referenced route already supplied.
        base = initial.model_copy(update={'tasks': initial.tasks[1:]}) if event.type == 'TaskAdd' else initial
        outcome = DeterministicReconstructionEngine(manager).reconstruct_input(base, event)
        evidence[event.type] = dict(initial_state=base.model_dump(mode='json'), event=event.model_dump(mode='json'), result=outcome.model_dump(mode='json'))
    return evidence


if __name__ == '__main__':
    results = probe()
    path = Path('artifacts/phase6/task-event-support.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n')
    for name, value in results.items():
        result = value['result']
        print(name, 'COMMITTED' if result['committed'] else 'FAILED', result['validation']['status'])
