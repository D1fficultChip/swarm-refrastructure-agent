import json
from typing import Literal

from pydantic import ConfigDict, Field, ValidationError

from ..models.domain import DomainModel, Identifier
from .models import AgentAction
from .security import redact


class ActionRejected(ValueError):
    def __init__(self, code, details=None):
        super().__init__(code)
        self.code, self.details = code, details or {}


class ToolArguments(DomainModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class EmptyArgs(ToolArguments):
    pass


class TaskArgs(ToolArguments):
    task_id: Identifier


class CandidateArgs(TaskArgs):
    level: Literal["IN_PLACE_REPAIR", "FORMATION_RECONSTRUCTION"] = "IN_PLACE_REPAIR"


class RepairArgs(TaskArgs):
    option_index: int = Field(default=0, ge=0, le=31)


class DetectArgs(ToolArguments):
    task_ids: list[Identifier] | None = Field(default=None, max_length=32)


class ExpansionArgs(ToolArguments):
    entity: str = Field(pattern=r"^(task|formation|node|route):[A-Za-z][A-Za-z0-9_-]{0,63}$")
    reason: str = Field(min_length=1, max_length=300)
    source_validation_id: str | None = None


class StopArgs(ToolArguments):
    reason: Literal["SUCCESS", "NO_RECONSTRUCTION_REQUIRED", "INFEASIBLE", "TOOL_ERROR"]


class AgentActionValidator:
    def validate(self, raw, registry, workspace):
        try:
            if isinstance(raw, AgentAction):
                raw = raw.model_dump(mode="json")
            if isinstance(raw, str):
                raw = json.loads(raw)
            action = AgentAction.model_validate_json(json.dumps(redact(raw)))
        except (ValueError, TypeError, ValidationError):
            raise ActionRejected("ACTION_SCHEMA_INVALID", {"required": ["action_type", "decision_reason"],
                "hint": "Return one JSON AgentAction; follow the supplied schema; no extra fields."}) from None
        if action.action_type in {"STOP", "USE_FALLBACK"}:
            if action.tool_name is not None:
                raise ActionRejected("ACTION_TYPE_TOOL_MISMATCH")
            schema = StopArgs if action.action_type == "STOP" else EmptyArgs
        else:
            entry = registry.get(action.tool_name)
            if entry is None:
                raise ActionRejected("UNKNOWN_TOOL", {"allowed_tools": sorted(registry)})
            schema = entry.arguments
            if action.action_type == "VALIDATE" and action.tool_name != "validate_proposal":
                raise ActionRejected("ACTION_TYPE_TOOL_MISMATCH")
            if action.action_type == "COMMIT" and action.tool_name != "commit_validated_proposal":
                raise ActionRejected("ACTION_TYPE_TOOL_MISMATCH")
        try:
            args = schema.model_validate_json(json.dumps(action.arguments))
        except ValidationError as exc:
            raise ActionRejected("INVALID_TOOL_ARGUMENTS", {"schema_error": [
                {"location": list(e["loc"]), "type": e["type"]} for e in exc.errors(include_input=False, include_url=False)]}) from None
        entities = {f"{kind}:{e.id}" for kind, values in [("task",workspace.working.tasks),
            ("formation",workspace.working.formations),("node",workspace.working.nodes),("route",workspace.working.routes)] for e in values}
        ids = {e.split(":",1)[1] for e in entities}
        if any(e not in entities and e not in ids for e in action.target_entities):
            raise ActionRejected("UNKNOWN_TARGET_ENTITY")
        task_ids = ([args.task_id] if getattr(args,"task_id",None) is not None else (args.task_ids or []) if isinstance(args, DetectArgs) else [])
        existing = {t.id for t in workspace.working.tasks}
        if not set(task_ids) <= existing:
            raise ActionRejected("UNKNOWN_TASK", {"task_ids": sorted(set(task_ids)-existing)})
        if isinstance(args, ExpansionArgs) and args.entity not in entities:
            raise ActionRejected("UNKNOWN_TARGET_ENTITY")
        return action, args
