from math import ceil

from .decision_snapshot import compact
from .policy_models import PromptSizeMetrics


def measure_prompt(system, schemas, action_schema, context, body):
    history_keys = {"recent_observations","last_observation","history"}
    history = {k:v for k,v in context.items() if k in history_keys and v is not None}
    current = {k:v for k,v in context.items() if k not in history_keys}
    return PromptSizeMetrics(system_chars=len(system),tool_schema_chars=len(compact(schemas)),
        action_schema_chars=len(compact(action_schema)),context_chars=len(compact(current)),
        history_chars=len(compact(history)) if history else 0,request_chars=len(compact(body)),
        estimated_input_tokens=ceil((len(system)+len(compact(schemas))+len(compact(action_schema))+len(compact(context)))/3))
