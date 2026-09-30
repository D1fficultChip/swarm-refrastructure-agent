"""Closed response schema; argument semantics remain locally enforced per tool."""
from copy import deepcopy

from ..action import StopArgs
from ..models import AgentAction
from ..policy_models import PolicyAction


def response_schema(tools, optimized=False):
    schema = (PolicyAction if optimized else AgentAction).model_json_schema()
    definitions = schema.pop("$defs", {})
    schema["required"] = list(schema["properties"])
    variants = []
    for source in [t["parameters"] for t in tools] + [StopArgs.model_json_schema()]:
        item = deepcopy(source)
        item["required"] = list(item.get("properties", {}))
        for prop in item.get("properties", {}).values():
            prop.pop("default", None)
        if item not in variants:
            variants.append(item)
    schema["properties"]["arguments"] = {"anyOf": variants}
    schema["properties"]["tool_name"] = {"enum": [t["name"] for t in tools]+[None]}
    for prop in schema["properties"].values():
        prop.pop("default", None)
    if optimized:
        def close(item):
            if isinstance(item,list):
                return [close(v) for v in item]
            if not isinstance(item,dict):
                return item
            if "$ref" in item:
                return close(definitions[item["$ref"].split("/")[-1]])
            result={k:close(v) for k,v in item.items() if k not in {"title","default","description"}}
            if result.get("type")=="object":
                result["required"]=list(result.get("properties",{}))
                result["additionalProperties"]=False
            return result
        schema=close(schema)
    return schema
