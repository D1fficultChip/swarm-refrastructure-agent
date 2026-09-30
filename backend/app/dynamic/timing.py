"""Timing policy for the dynamic mission demonstration.

The task deadline and the amount of effective work are deliberately separate:
events and reconstruction may consume slack without making recovery pointless.
"""

TASK_TIME_WINDOW_SECONDS = 600.0
TASK_EXECUTION_BUDGET_SECONDS = 300.0
DYNAMIC_NODE_AVAILABILITY_END = 3600.0


def execution_budget(window_start: float, window_end: float) -> float:
    """Return required effective execution time within a task's window."""
    return min(TASK_EXECUTION_BUDGET_SECONDS, max(1.0, window_end - window_start))
