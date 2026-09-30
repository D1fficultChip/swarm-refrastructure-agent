"""P5.1 semantic tool surface; P5/P2–P4 interfaces remain available unchanged."""
import json
import re

from pydantic import Field, ValidationError

from .action import (ActionRejected, AgentActionValidator, EmptyArgs, StopArgs, TaskArgs, ToolArguments)
from .models import AgentAction
from .policy_models import PolicyAction
from .tool_guard import ToolGuard
from .tool_registry import ToolSpec, tool_registry


class OptionArgs(ToolArguments):
    option_id: str = Field(min_length=1,max_length=100)


class OptionsQuery(ToolArguments):
    task_id: str | None = None
    strategy: str | None = None


def policy_registry():
    registry = tool_registry()
    for name in ["try_in_place_repair","find_task_reassignment","try_formation_reconstruction"]:
        registry[name] = ToolSpec("Query stable options for this primitive; does not apply.",TaskArgs)
    registry["get_reconstruction_options"] = ToolSpec("Query current stable options, optional task/strategy filter.",OptionsQuery)
    registry["apply_reconstruction_option"] = ToolSpec("Apply current option_id; same as SELECT_OPTION.",OptionArgs)
    # Descriptions are intentionally short; exact typed schemas remain intact.
    for name in registry:
        if name not in {"get_reconstruction_options","apply_reconstruction_option"} and len(registry[name].description)>90:
            registry[name] = ToolSpec(registry[name].description.split(";")[0].split(". ")[0],registry[name].arguments)
    return registry


def parse_policy_action(raw, registry, workspace):
    from .security import redact
    try:
        if isinstance(raw,PolicyAction):
            raw = raw.model_dump(mode="json")
        if isinstance(raw,str):
            raw = json.loads(raw)
        action = PolicyAction.model_validate_json(json.dumps(redact(raw)))
    except ValidationError as exc:
        raise ActionRejected("ACTION_SCHEMA_INVALID",{"schema_errors":[
            {"location":list(e["loc"]),"type":e["type"],"message":e["msg"]}
            for e in exc.errors(include_input=False,include_url=False)],
            "hint":"Correct the listed fields; decision_reason must be under 300 characters, entities need a kind:ID prefix. Do not refresh unchanged state just for a schema error."}) from None
    except (ValueError,TypeError):
        raise ActionRejected("ACTION_SCHEMA_INVALID",{"hint":"Return one complete JSON object matching PolicyAction, without markdown."}) from None
    def mismatch():
        error=ActionRejected("ACTION_TYPE_TOOL_MISMATCH",{
            "received":{"action_type":action.action_type,"option_id":action.option_id,"tool_name":action.tool_name,"arguments":action.arguments},
            "expected":"SELECT_OPTION: option_id=exact ID, tool_name=null, arguments={}. CALL_TOOL: option_id=null, tool_name=tool, arguments=tool args. FINALIZE_PROPOSAL: option_id=null, tool_name=null, arguments={}."})
        error.action=action
        return error
    if action.action_type=="SELECT_OPTION":
        if not action.option_id or action.tool_name or action.arguments:
            raise mismatch()
        return action,None
    if action.action_type=="FINALIZE_PROPOSAL":
        if action.option_id or action.tool_name or action.arguments or action.scope_expansions:
            raise mismatch()
        return action,None
    if action.option_id is not None:
        raise mismatch()
    original,args = AgentActionValidator().validate({"action_type":action.action_type,"tool_name":action.tool_name,
        "arguments":action.arguments,"decision_reason":action.decision_reason},registry,workspace)
    if action.action_type=="STOP" and (action.finalize or action.scope_expansions):
        raise ActionRejected("ACTION_TYPE_TOOL_MISMATCH")
    return action,args


def reason_consistency(reason, option, node_ids):
    """Conservative literal-node diagnostic, never an execution instruction.

    Negative mentions ('avoid U1') aren't interpreted with NLP; retain a warning
    if exact mentioned node IDs are outside the selected option. Never rewrite it.
    """
    mentions = set(re.findall(r"\b[A-Za-z][A-Za-z0-9_-]*\b",reason)) & set(node_ids)
    selected = set(option.added_nodes)|set(option.removed_nodes)
    return None if not mentions else mentions <= selected
